"""Generate a large, realistic restaurant dataset THROUGH THE BUSINESS SERVICES (FIFO, ledgers,
recipe re-costing and change capture all run exactly as in production).

Usage:  APP_DATABASE_URL=... python scripts/bench/generate.py [scale]
scale=1: 3,000 ingredients · 60 suppliers · 300 recipes · 10,000 invoices (30,000 lines/batches)
         · 25,000 consumptions · 2,000 production runs · 4,000 payments · 3,000 waste entries
"""
import os
import random
import sys
import time
from datetime import UTC, date, datetime, time as dtime, timedelta
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db import make_engine, make_sessionmaker  # noqa: E402
from app.models import Branch, Device, User, UserBranchRole  # noqa: E402
from app.security import Principal, hash_password  # noqa: E402
from app.services import ingredients as ingsvc, purchasing, recipes, stock_ops  # noqa: E402
from app.models import Supplier  # noqa: E402
from app.services.purchasing import invoice_balance, paid_and_credited  # noqa: E402

scale = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
N = lambda n: max(1, int(n * scale))
rnd = random.Random(42)
S = make_sessionmaker(make_engine(os.environ["APP_DATABASE_URL"]), capture=True)


def principal(db):
    b = db.scalar(select(Branch))
    u = db.scalar(select(User).where(User.email == "bench@example.com"))
    d = db.scalar(select(Device))
    p = Principal(user=u, branch=b, role="owner", device=d)
    db.info["ctx"] = {"branch_id": b.id, "user_id": u.id, "device_id": d.id, "op": "bench"}
    return p


def timed(label, n, fn):
    t = time.perf_counter()
    for i in range(n):
        fn(i)
    dt = time.perf_counter() - t
    print(f"{label:<28} {n:>7} in {dt:7.1f}s  ({n / dt:7.1f}/s, {dt / n * 1000:6.1f} ms each)", flush=True)


with S() as db:
    b = Branch(code="BENCH", name="Benchmark Restaurant", country="UG", currency="USD", currency_decimals=2)
    u = User(email="bench@example.com", display_name="Bench Owner", password_hash=hash_password("bench-password-1"))
    db.add_all([b, u]); db.flush()
    db.add(UserBranchRole(user_id=u.id, branch_id=b.id, role="owner"))
    db.add(Device(branch_id=b.id, name="bench"))
    db.commit()

ING, SUP, REC, INV = [], [], [], []
PRICED: dict = {}
cats = ["Meat", "Fish", "Vegetables", "Fruit", "Dairy", "Dry goods", "Spices", "Oils", "Bakery", "Drinks"]


def mk_sup(i):
    with S() as db:
        p = principal(db)
        s = Supplier(code=f"SUP{i:04d}", name=f"Supplier {i:04d}", phone="+256 700 000 000", payment_terms_days=14)
        db.add(s); db.commit(); SUP.append(s.id)


def mk_ing(i):
    with S() as db:
        p = principal(db)
        dim = rnd.choices(["mass", "volume", "count"], [70, 20, 10])[0]
        pu, cu = {"mass": ("kg", "g"), "volume": ("l", "ml"), "count": ("unit", "unit")}[dim]
        ing = ingsvc.create_ingredient(db, p, {"code": f"ING{i:05d}", "name": f"{rnd.choice(cats)} item {i:05d}",
                                              "category": rnd.choice(cats), "base_dimension": dim, "purchase_unit": pu,
                                              "consumption_unit": cu, "min_stock": D(rnd.randint(0, 20)), "min_stock_unit": pu,
                                              "suppliers": [{"supplier_id": rnd.choice(SUP)}]})
        db.commit(); ING.append((ing.id, pu, cu))


def mk_purchase(i):
    with S() as db:
        p = principal(db)
        day = date(2025, 1, 1) + timedelta(days=int(i * 365 / N(10000)))
        chosen = rnd.sample(ING, 3)
        lines = [{"ingredient_id": iid, "quantity": D(rnd.randint(5, 400)) / 10, "unit": pu,
                  "unit_price": D(rnd.randint(50, 3000)) / 100, "line_discount": D(0)}
                 for iid, pu, _ in chosen]
        inv = purchasing.confirm_purchase(db, p, {"supplier_id": rnd.choice(SUP), "invoice_ref": f"INV-{i:06d}",
                                                  "invoice_date": day, "lines": lines})
        db.commit(); INV.append(inv.id)
        for c in chosen:
            PRICED[c[0]] = c


def mk_recipe(i):
    with S() as db:
        p = principal(db)
        lines = [{"ingredient_id": iid, "quantity": D(rnd.randint(5, 500)), "unit": cu, "basis": "gross",
                  "prep_loss_rate": D(0)} for iid, _, cu in rnd.sample(list(PRICED.values()), rnd.randint(8, 15))]
        r = recipes.create_recipe(db, p, {"code": f"REC{i:04d}", "name": f"Dish {i:04d}", "version": {
            "portions_per_batch": D(rnd.randint(4, 40)), "selling_price": D(rnd.randint(500, 4000)) / 100,
            "target_food_cost_rate": D("0.3"), "lines": lines}})
        db.flush()
        recipes.publish(db, p, r, 1)
        db.commit(); REC.append(r.id)


def mk_consumption(i):
    with S() as db:
        p = principal(db)
        iid, _, cu = rnd.choice(ING)
        try:
            stock_ops.record_consumption(db, p, {"lines": [{"ingredient_id": iid, "quantity": D(rnd.randint(1, 50)), "unit": cu}]})
            db.commit()
        except Exception:
            db.rollback()


def mk_production(i):
    with S() as db:
        p = principal(db)
        try:
            stock_ops.record_production(db, p, {"recipe_id": rnd.choice(REC), "batches": D(1), "allow_shortfall": True,
                                                "override_reason": "bench"})
            db.commit()
        except Exception:
            db.rollback()


def mk_payment(i):
    with S() as db:
        p = principal(db)
        inv = purchasing.get_purchase(db, p, rnd.choice(INV))
        out = invoice_balance(inv, *paid_and_credited(db, [inv.id])[inv.id])["outstanding"]
        if out <= 0:
            return
        amt = min(out, (out * D(rnd.randint(30, 100)) / 100).quantize(D("0.01")))
        if amt <= 0:
            return
        purchasing.post_payment(db, p, {"supplier_id": inv.supplier_id, "paid_on": date(2025, 12, 31), "amount": amt,
                                        "method": "bank_transfer", "reference": f"TX{i:06d}",
                                        "allocations": [{"invoice_id": inv.id, "amount": amt}]})
        db.commit()


def mk_waste(i):
    with S() as db:
        p = principal(db)
        iid, _, cu = rnd.choice(ING)
        try:
            stock_ops.record_adjustment(db, p, {"ingredient_id": iid, "quantity": D(rnd.randint(1, 20)), "unit": cu,
                                                "reason": "spoilage", "note": None})
            db.commit()
        except Exception:
            db.rollback()


t0 = time.perf_counter()
timed("suppliers", N(60), mk_sup)
timed("ingredients", N(3000), mk_ing)
timed("purchases (3 lines each)", N(2000), mk_purchase)   # stock before recipes exist
timed("recipes (8-15 lines, published)", N(300), mk_recipe)
timed("purchases (with re-costing)", N(8000), lambda i: mk_purchase(i + N(2000)))
timed("consumptions", N(25000), mk_consumption)
timed("production runs", N(2000), mk_production)
timed("payments", N(4000), mk_payment)
timed("waste adjustments", N(3000), mk_waste)
print(f"total {time.perf_counter() - t0:.0f}s")
