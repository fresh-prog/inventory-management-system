"""Data-integrity audit (Stage 4).

1. Model-based randomized simulation: an independent, deliberately simple Python model of FIFO
   batches and supplier ledgers is driven with the same random operations as the real API.
   After every few steps every batch, cost, balance and invoice status must match exactly.
2. Traceability, immutability of history, and rollback of failed operations.
"""

import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal as D

import pytest
from sqlalchemy import text

from conftest import buy, make_ingredient, make_supplier

Q4, Q12 = D("0.0001"), D("0.000000000001")


def m4(x: D) -> D:
    return x.quantize(Q4, rounding=ROUND_HALF_UP)


class Model:
    def __init__(self):
        self.batches: dict[str, list[dict]] = {}  # ingredient -> FIFO list
        self.n = 0
        self.invoices: dict[str, dict] = {}  # id -> {supplier, total, paid, batch_keys, voided}

    def receive(self, ing, day, qty_g, cost_per_g, inv_id):
        self.n += 1
        b = {"key": (day, self.n), "qty": qty_g, "orig": qty_g, "cost": cost_per_g, "inv": inv_id}
        self.batches.setdefault(ing, []).append(b)
        self.batches[ing].sort(key=lambda x: x["key"])
        return b

    def available(self, ing):
        return sum((b["qty"] for b in self.batches.get(ing, [])), D(0))

    def consume(self, ing, qty):
        out, left = [], qty
        for b in self.batches.get(ing, []):
            if left == 0:
                break
            take = min(b["qty"], left)
            if take > 0:
                b["qty"] -= take
                left -= take
                out.append((take, b["cost"]))
        return out

    def value(self):
        return sum((b["qty"] * b["cost"] for bs in self.batches.values() for b in bs), D(0))

    def outstanding(self, inv):
        i = self.invoices[inv]
        return D(0) if i["voided"] else i["total"] - i["paid"]

    def supplier_balance(self, sup):
        return sum((self.outstanding(k) for k, v in self.invoices.items() if v["supplier"] == sup), D(0))


def compare(owner, model: Model, ings, sups):
    for ing in ings:
        api = owner.ok(owner.get(f"/stock/ingredients/{ing}/batches"))["batches"]
        got = [(D(b["remaining_quantity"]), D(b["unit_cost"])) for b in api]
        exp = [(b["qty"], b["cost"]) for b in model.batches.get(ing, []) if b["qty"] > 0]
        assert got == exp, f"batches differ for {ing}:\nAPI   {got}\nmodel {exp}"
    stock = owner.ok(owner.get("/stock"))
    assert D(stock["total_value"]) == m4(model.value()), (stock["total_value"], model.value())
    for s in sups:
        prof = owner.ok(owner.get(f"/suppliers/{s}"))
        assert D(prof["balance"]) == model.supplier_balance(s)
        for inv in prof["invoices"]:
            exp = model.outstanding(inv["id"])
            assert D(inv["outstanding"]) == exp
            total = model.invoices[inv["id"]]["total"]
            status = "voided" if model.invoices[inv["id"]]["voided"] else \
                "paid" if exp <= 0 else "unpaid" if exp == total else "partially_paid"
            assert inv["payment_status"] == status
    chk = owner.ok(owner.get("/reports/integrity"))
    assert chk["ok"], chk


@pytest.mark.parametrize("seed", [11, 23, 47])
def test_randomized_operations_match_independent_model(owner, engine, seed):
    rnd = random.Random(seed)
    model = Model()
    sups = [make_supplier(owner, f"S{i}", f"Supplier {i}")["id"] for i in range(3)]
    ings = [make_ingredient(owner, f"I{i}", f"Ingredient {i}")["id"] for i in range(4)]
    stats = {"purchase": 0, "consume": 0, "blocked": 0, "waste": 0, "pay": 0, "overpay": 0, "void": 0}
    for step in range(160):
        op = rnd.choices(["purchase", "consume", "waste", "pay", "void"], [30, 35, 10, 20, 5])[0]
        if op == "purchase" or not any(model.available(i) for i in ings):
            day = date(2026, 1, 1) + timedelta(days=rnd.randint(0, 80))
            chosen = rnd.sample(ings, rnd.randint(1, 3))
            lines = [(i, D(rnd.randint(5, 200)) / 10, "kg", D(rnd.randint(100, 2000)) / 100) for i in chosen]
            sup = rnd.choice(sups)
            inv = buy(owner, sup, lines, day=day.isoformat())
            total = sum((m4(q * p) for _, q, _, p in lines), D(0))
            assert D(inv["total"]) == total
            model.invoices[inv["id"]] = {"supplier": sup, "total": total, "paid": D(0), "voided": False}
            for (i, q, _, p), line in zip(lines, inv["lines"]):
                cpg = (m4(q * p) / (q * 1000)).quantize(Q12, rounding=ROUND_HALF_UP)
                assert D(line["cost_per_base_unit"]) == cpg and D(line["base_quantity"]) == q * 1000
                model.receive(i, day, q * 1000, cpg, inv["id"])
            stats["purchase"] += 1
        elif op in ("consume", "waste"):
            i = rnd.choice([x for x in ings if model.available(x) > 0] or ings)
            avail = model.available(i)
            qty = D(rnd.randint(1, int(avail * D("1.3")) + 1))  # sometimes more than available
            if op == "consume":
                r = owner.post("/stock/consumptions", {"lines": [{"ingredient_id": i, "quantity": str(qty), "unit": "g"}]})
            else:
                r = owner.post("/stock/adjustments", {"ingredient_id": i, "quantity": str(qty), "unit": "g",
                                                      "reason": rnd.choice(["spoilage", "expired", "damaged"])})
            if qty > avail:
                assert r.status_code == 422 and r.json()["code"] == "INSUFFICIENT_STOCK"
                stats["blocked"] += 1
                continue
            assert r.status_code == 201, r.text
            body = r.json()
            line = body["lines"][0] if op == "consume" else body
            exp = model.consume(i, qty)
            assert [(D(a["quantity"]), D(a["unit_cost"])) for a in line["allocations"]] == exp
            assert D(line["total_cost"]) == m4(sum((q * c for q, c in exp), D(0)))
            stats[op] += 1
        elif op == "pay":
            open_ = [k for k in model.invoices if model.outstanding(k) > 0]
            if not open_:
                continue
            inv = rnd.choice(open_)
            out = model.outstanding(inv)
            if rnd.random() < 0.15:  # try to overpay
                amt = out + D("0.01")
                r = owner.post("/payments", {"supplier_id": model.invoices[inv]["supplier"], "paid_on": "2026-03-30",
                                             "amount": str(amt), "method": "cash",
                                             "allocations": [{"invoice_id": inv, "amount": str(amt)}]})
                assert r.status_code == 422
                stats["overpay"] += 1
                continue
            amt = max(D("0.01"), (out * D(rnd.randint(10, 100)) / 100).quantize(D("0.01")))
            amt = min(amt, out)
            owner.ok(owner.post("/payments", {"supplier_id": model.invoices[inv]["supplier"], "paid_on": "2026-03-30",
                                              "amount": str(amt), "method": "cash",
                                              "allocations": [{"invoice_id": inv, "amount": str(amt)}]}))
            model.invoices[inv]["paid"] += amt
            stats["pay"] += 1
        elif op == "void":
            cands = [k for k, v in model.invoices.items() if not v["voided"] and v["paid"] == 0 and all(
                b["qty"] == b["orig"] for bs in model.batches.values() for b in bs if b["inv"] == k)]
            if not cands:
                continue
            inv = rnd.choice(cands)
            owner.ok(owner.post(f"/purchases/{inv}/void", {"reason": "entered twice"}))
            model.invoices[inv]["voided"] = True
            for bs in model.batches.values():
                for b in bs:
                    if b["inv"] == inv:
                        b["qty"] = D(0)
            stats["void"] += 1
        if step % 20 == 19:
            compare(owner, model, ings, sups)
    compare(owner, model, ings, sups)
    assert stats["purchase"] > 20 and stats["consume"] > 20 and stats["pay"] > 10, stats
    print(f"\n  seed {seed}: {stats}")


def test_every_purchase_line_has_exactly_one_matching_batch_and_receipt(owner, engine):
    sup = make_supplier(owner)
    ings = [make_ingredient(owner, f"P{i}", f"P{i}")["id"] for i in range(3)]
    for k in range(5):
        buy(owner, sup["id"], [(i, k + 1, "kg", 10 + k) for i in ings], ref=f"R{k}", discount_total="1", tax_total="2")
    with engine.connect() as c:
        bad = c.execute(text("""
            SELECT pl.id FROM purchase_line pl
            LEFT JOIN stock_batch b ON b.purchase_line_id = pl.id
            LEFT JOIN stock_movement m ON m.source_id = pl.invoice_id AND m.type = 'receipt' AND m.stock_item_id = b.stock_item_id
            WHERE b.id IS NULL OR b.original_quantity <> pl.base_quantity OR b.unit_cost <> pl.cost_per_base_unit
               OR m.id IS NULL OR m.quantity <> pl.base_quantity
               OR b.supplier_id <> (SELECT supplier_id FROM purchase_invoice WHERE id = pl.invoice_id)""")).all()
        assert bad == []
        # stock cost reconciles with the invoice: Σ stock_cost = subtotal - discount (VAT excluded)
        rows = c.execute(text("""SELECT i.subtotal - i.discount_total, sum(pl.stock_cost) FROM purchase_invoice i
                                 JOIN purchase_line pl ON pl.invoice_id = i.id GROUP BY i.id""")).all()
        assert all(a == b for a, b in rows)
        # each posted payment allocation reduces exactly one invoice of the same supplier
        assert c.execute(text("""SELECT count(*) FROM payment_allocation a JOIN supplier_payment p ON p.id = a.payment_id
                                 JOIN purchase_invoice i ON i.id = a.invoice_id WHERE i.supplier_id <> p.supplier_id""")).scalar() == 0


def test_every_stock_movement_has_a_traceable_source(owner, engine):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    inv = buy(owner, sup["id"], [(ing["id"], 10, "kg", 10)])
    owner.ok(owner.post("/stock/consumptions", {"lines": [{"ingredient_id": ing["id"], "quantity": "1", "unit": "kg"}]}))
    owner.ok(owner.post("/stock/adjustments", {"ingredient_id": ing["id"], "quantity": "1", "unit": "kg", "reason": "damaged"}))
    c_ = owner.ok(owner.post("/stock/counts", {}))
    owner.ok(owner.put(f"/stock/counts/{c_['id']}/lines", {"lines": [{"ingredient_id": ing["id"], "quantity": "7", "unit": "kg"}]}))
    owner.ok(owner.post(f"/stock/counts/{c_['id']}/approve"))
    inv2 = buy(owner, sup["id"], [(ing["id"], 1, "kg", 10)])
    owner.ok(owner.post(f"/purchases/{inv2['id']}/void", {"reason": "wrong"}))
    with engine.connect() as c:
        rows = c.execute(text("SELECT type, source_type, source_id FROM stock_movement")).all()
        assert rows and all(r.source_type and r.source_id for r in rows), rows
        # every source resolves to a real record or an audit entry
        unresolved = c.execute(text("""
            SELECT m.type, m.source_type FROM stock_movement m WHERE NOT (
              (m.source_type = 'purchase' AND EXISTS (SELECT 1 FROM purchase_invoice i WHERE i.id = m.source_id)) OR
              (m.source_type = 'stock_count' AND EXISTS (SELECT 1 FROM stock_count s WHERE s.id = m.source_id)) OR
              (m.source_type = 'production_run' AND EXISTS (SELECT 1 FROM production_run r WHERE r.id = m.source_id)) OR
              (m.source_type IN ('manual_consumption', 'adjustment') AND EXISTS (
                   SELECT 1 FROM audit_log a WHERE a.entity_id = m.source_id)))""")).all()
        assert unresolved == []


def test_history_is_never_rewritten(owner, engine):
    """Historical prices, recipe versions, cost snapshots and past FIFO costs stay exactly as they were."""
    sup = make_supplier(owner)
    beef = make_ingredient(owner)
    buy(owner, sup["id"], [(beef["id"], 10, "kg", 10)], day="2026-01-01")
    r = owner.ok(owner.post("/recipes", {"code": "R", "name": "R", "version": {
        "portions_per_batch": "10", "lines": [{"ingredient_id": beef["id"], "quantity": "2", "unit": "kg"}]}}))
    owner.ok(owner.post(f"/recipes/{r['id']}/versions/1/publish"))
    run1 = owner.ok(owner.post("/stock/production-runs", {"recipe_id": r["id"], "batches": "1"}))
    import json
    snap = lambda: {t: [json.dumps(list(x), default=str, sort_keys=True) for x in
                        engine.connect().execute(text(f"SELECT * FROM {t} ORDER BY 1")).all()]
                    for t in ("ingredient_price_history", "recipe_line", "recipe_cost_snapshot", "stock_movement",
                              "stock_allocation", "production_run", "supplier_ledger_entry")}
    before = snap()
    # later: price rises, recipe changes to v2 and is published, more production
    buy(owner, sup["id"], [(beef["id"], 10, "kg", 20)], day="2026-02-01")
    owner.ok(owner.put(f"/recipes/{r['id']}/draft", {"portions_per_batch": "10",
                                                     "lines": [{"ingredient_id": beef["id"], "quantity": "3", "unit": "kg"}]}))
    owner.ok(owner.post(f"/recipes/{r['id']}/versions/2/publish"))
    owner.ok(owner.post("/stock/production-runs", {"recipe_id": r["id"], "batches": "1"}))
    after = snap()
    for t, rows in before.items():
        assert set(rows) <= set(after[t]), f"{t}: historical rows changed or disappeared"
    runs = owner.ok(owner.get(f"/recipes/{r['id']}/production-runs"))
    first = next(x for x in runs if x["id"] == run1["id"])
    assert first["total_cost"] == "20.0000" and first["version_no"] == 1  # still v1 at the old FIFO cost
    v1 = owner.ok(owner.get(f"/recipes/{r['id']}/versions/1"))
    assert v1["status"] == "retired" and v1["lines"][0]["quantity"] == "2.000000"


def test_recipe_costing_uses_current_basis_not_fifo(owner):
    sup = make_supplier(owner)
    beef = make_ingredient(owner)
    buy(owner, sup["id"], [(beef["id"], 10, "kg", 10)], day="2026-01-01")  # old stock @10
    buy(owner, sup["id"], [(beef["id"], 10, "kg", 30)], day="2026-02-01")  # latest price @30
    r = owner.ok(owner.post("/recipes", {"code": "C", "name": "C", "version": {
        "portions_per_batch": "1", "lines": [{"ingredient_id": beef["id"], "quantity": "1", "unit": "kg"}]}}))
    assert r["draft_cost"]["cost_basis"] == "current" and r["draft_cost"]["batch_cost"] == "30.0000"
    owner.ok(owner.post(f"/recipes/{r['id']}/versions/1/publish"))
    run = owner.ok(owner.post("/stock/production-runs", {"recipe_id": r["id"], "batches": "1"}))
    assert run["cost_basis"] == "fifo" and run["total_cost"] == "10.0000"  # actual stock used was the old @10


FAULT_POINTS = [
    ("purchase", "app.services.purchasing._ledger"),
    ("purchase", "app.services.purchasing.receive"),
    ("purchase", "app.services.recipes.recalc_for_ingredients"),
    ("purchase", "app.services.purchasing.audit"),
    ("consume", "app.services.inventory.StockAllocation"),
    ("production", "app.services.stock_ops.audit"),
    ("payment", "app.services.purchasing.PaymentAllocation"),
    ("count", "app.services.stock_ops.receive"),
    ("void", "app.services.purchasing.recompute_current_cost"),
]


@pytest.mark.parametrize("op,target", FAULT_POINTS)
def test_failure_at_any_point_rolls_back_everything(owner, engine, monkeypatch, op, target):
    from fastapi.testclient import TestClient
    from app.main import app
    sup = make_supplier(owner)
    beef = make_ingredient(owner)
    inv = buy(owner, sup["id"], [(beef["id"], 10, "kg", 10)])
    r = owner.ok(owner.post("/recipes", {"code": "X", "name": "X", "version": {
        "portions_per_batch": "1", "lines": [{"ingredient_id": beef["id"], "quantity": "1", "unit": "kg"}]}}))
    owner.ok(owner.post(f"/recipes/{r['id']}/versions/1/publish"))
    cnt = owner.ok(owner.post("/stock/counts", {}))
    owner.ok(owner.put(f"/stock/counts/{cnt['id']}/lines", {"lines": [{"ingredient_id": beef["id"], "quantity": "12", "unit": "kg"}]}))
    void_target = buy(owner, sup["id"], [(beef["id"], 1, "kg", 10)], ref="VOIDME") if op == "void" else None
    tables = [t for (t,) in engine.connect().execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN ('alembic_version')"))]

    def state():
        with engine.connect() as c:
            out = {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in tables}
            out["remaining"] = c.execute(text("SELECT sum(remaining_quantity) FROM stock_batch")).scalar()
            out["ledger"] = c.execute(text("SELECT sum(amount) FROM supplier_ledger_entry")).scalar()
            out["costs"] = c.execute(text("SELECT string_agg(cost_per_base_unit::text, ',') FROM ingredient_current_cost")).scalar()
        return out
    before = state()
    mod, name = target.rsplit(".", 1)

    def boom(*a, **k):
        raise RuntimeError("simulated crash")
    monkeypatch.setattr(mod + "." + name, boom)
    c = TestClient(app, raise_server_exceptions=False)
    h = owner.h()
    calls = {
        "purchase": lambda: c.post("/api/v1/purchases", headers=h, json={"supplier_id": sup["id"], "invoice_ref": "NEW",
                                   "invoice_date": "2026-02-01", "lines": [{"ingredient_id": beef["id"], "quantity": "2",
                                                                           "unit": "kg", "unit_price": "11"}]}),
        "consume": lambda: c.post("/api/v1/stock/consumptions", headers=h, json={"lines": [
            {"ingredient_id": beef["id"], "quantity": "1", "unit": "kg"}]}),
        "production": lambda: c.post("/api/v1/stock/production-runs", headers=h, json={"recipe_id": r["id"], "batches": "1"}),
        "payment": lambda: c.post("/api/v1/payments", headers=h, json={"supplier_id": sup["id"], "paid_on": "2026-02-01",
                                  "amount": "5", "method": "cash", "allocations": [{"invoice_id": inv["id"], "amount": "5"}]}),
        "count": lambda: c.post(f"/api/v1/stock/counts/{cnt['id']}/approve", headers=h),
        "void": lambda: c.post(f"/api/v1/purchases/{void_target['id']}/void", headers=h, json={"reason": "test"}),
    }
    resp = calls[op]()
    assert resp.status_code == 500 and resp.json()["code"] == "INTERNAL_ERROR", (op, target, resp.status_code)
    assert state() == before  # nothing partially written: no rows, no outbox, no idempotency key, no balances
    monkeypatch.undo()
    assert owner.ok(owner.get("/reports/integrity"))["ok"]
    # and the same request succeeds afterwards (the idempotency key was not burnt by the failure)
    import json
    body = json.loads(resp.request.content) if resp.request.content else None
    retry = c.request(resp.request.method, str(resp.request.url), headers=h, json=body)
    assert retry.status_code in (200, 201), retry.text
