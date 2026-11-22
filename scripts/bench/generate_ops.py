"""Business activity helpers for sync_bench (uses the real services, with change capture)."""
import random
from datetime import date
from decimal import Decimal as D

from sqlalchemy import select

from app.models import Branch, Device, Ingredient, Supplier, User
from app.security import Principal
from app.services import purchasing, stock_ops


def offline_activity(S, consumptions: int, purchases: int) -> int:
    rnd = random.Random(7)
    with S() as db:
        ings = db.execute(select(Ingredient.id, Ingredient.purchase_unit, Ingredient.consumption_unit)).all()
        sups = db.scalars(select(Supplier.id)).all()
    n = 0
    for i in range(consumptions + purchases):
        with S() as db:
            b, u, d = db.scalar(select(Branch)), db.scalar(select(User).where(User.email == "bench@example.com")), db.scalar(select(Device))
            p = Principal(user=u, branch=b, role="owner", device=d)
            db.info["ctx"] = {"branch_id": b.id, "user_id": u.id, "device_id": d.id, "op": "offline"}
            try:
                if i % 10 == 0 and purchases:
                    lines = [{"ingredient_id": x.id, "quantity": D(rnd.randint(1, 50)), "unit": x.purchase_unit,
                              "unit_price": D(rnd.randint(50, 900)) / 100} for x in rnd.sample(ings, 3)]
                    purchasing.confirm_purchase(db, p, {"supplier_id": rnd.choice(sups), "invoice_ref": f"OFF-{i:06d}",
                                                        "invoice_date": date(2026, 2, 1), "lines": lines})
                else:
                    x = rnd.choice(ings)
                    stock_ops.record_consumption(db, p, {"lines": [{"ingredient_id": x.id, "quantity": D(rnd.randint(1, 30)),
                                                                     "unit": x.consumption_unit}]})
                db.commit()
                n += 1
            except Exception:
                db.rollback()
    return n
