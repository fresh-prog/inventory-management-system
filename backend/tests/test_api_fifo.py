"""FIFO inventory, production, adjustments, counts, dashboard reconciliation (MVP acceptance 7-11)."""

import threading
from decimal import Decimal

from conftest import buy, make_ingredient, make_supplier, make_user


def abc(owner):
    sup = make_supplier(owner)
    beef = make_ingredient(owner)
    buy(owner, sup["id"], [(beef["id"], 10, "kg", 9)], ref="A", day="2026-01-01")
    buy(owner, sup["id"], [(beef["id"], 10, "kg", 11)], ref="B", day="2026-01-02")
    buy(owner, sup["id"], [(beef["id"], 10, "kg", 12)], ref="C", day="2026-01-03")
    return sup, beef


def batches(owner, ing_id):
    return owner.ok(owner.get(f"/stock/ingredients/{ing_id}/batches"))["batches"]


def consume(owner, ing_id, q, unit="kg", **kw):
    return owner.post("/stock/consumptions", {"lines": [{"ingredient_id": ing_id, "quantity": str(q), "unit": unit}], **kw})


def test_master_spec_fifo_example(owner):
    _, beef = abc(owner)
    r = owner.ok(consume(owner, beef["id"], 15))
    assert r["cost_basis"] == "fifo" and r["total_cost"] == "145.0000"
    allocs = r["lines"][0]["allocations"]
    assert [(a["quantity"], a["cost"]) for a in allocs] == [("10000.000000", "90.0000"), ("5000.000000", "55.0000")]
    left = batches(owner, beef["id"])
    assert [(b["remaining_quantity"], b["unit_cost"]) for b in left] == [
        ("5000.000000", "0.011000000000"), ("10000.000000", "0.012000000000")]
    stock = owner.ok(owner.get("/stock"))
    assert stock["total_value"] == "175.0000"  # 5 kg @ 11 + 10 kg @ 12


def test_partial_batch_and_spanning_three_batches(owner):
    _, beef = abc(owner)
    r1 = owner.ok(consume(owner, beef["id"], 2500, "g"))          # partial batch A
    assert r1["total_cost"] == "22.5000"
    r2 = owner.ok(consume(owner, beef["id"], "20"))               # 7.5 A + 10 B + 2.5 C
    assert r2["total_cost"] == str(Decimal("67.5") + Decimal("110") + Decimal("30")) + "000"
    assert [b["remaining_quantity"] for b in batches(owner, beef["id"])] == ["7500.000000"]
    assert owner.ok(owner.get("/stock"))["total_value"] == "90.0000"


def test_insufficient_stock_blocked_and_override(owner):
    _, beef = abc(owner)
    r = consume(owner, beef["id"], 31)
    assert r.status_code == 422 and r.json()["code"] == "INSUFFICIENT_STOCK"
    assert owner.ok(owner.get("/stock"))["total_value"] == "320.0000"  # nothing changed
    assert consume(owner, beef["id"], 31, allow_shortfall=True).status_code == 422  # reason required
    r = owner.ok(consume(owner, beef["id"], 31, allow_shortfall=True, override_reason="delivery late"))
    assert r["lines"][0]["shortfall_quantity"] == "1000.000000"
    assert owner.ok(owner.get("/reports/integrity"))["ok"] is True


def test_concurrent_consumption_never_oversells(owner):
    sup = make_supplier(owner)
    beef = make_ingredient(owner)
    buy(owner, sup["id"], [(beef["id"], 4, "kg", 10)], ref="A", day="2026-01-01")
    buy(owner, sup["id"], [(beef["id"], 6, "kg", 12)], ref="B", day="2026-01-02")
    codes = []

    def go():
        codes.append(consume(owner, beef["id"], 1).status_code)
    ts = [threading.Thread(target=go) for _ in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert codes.count(201) == 10 and codes.count(422) == 10
    assert batches(owner, beef["id"]) == []
    mv = owner.ok(owner.get("/stock/movements", params={"type": "consumption"}))["items"]
    assert sum(Decimal(m["total_cost"]) for m in mv) == Decimal("-112")  # 4*10 + 6*12
    assert owner.ok(owner.get("/reports/integrity"))["ok"] is True


def test_consumption_is_idempotent(owner):
    _, beef = abc(owner)
    body = {"lines": [{"ingredient_id": beef["id"], "quantity": "1", "unit": "kg"}]}
    a = owner.post("/stock/consumptions", body, key="consume-key-1")
    b = owner.post("/stock/consumptions", body, key="consume-key-1")
    assert a.json() == b.json() and b.headers["Idempotent-Replay"] == "true"
    assert owner.ok(owner.get("/stock"))["items"][0]["on_hand_base"] == "29000.000000"


def make_published_recipe(owner, beef, onion):
    r = owner.ok(owner.post("/recipes", {"code": "STEW", "name": "Stew", "version": {
        "portions_per_batch": "50", "selling_price": "15",
        "lines": [{"ingredient_id": beef["id"], "quantity": "5", "unit": "kg"},
                  {"ingredient_id": onion["id"], "quantity": "800", "unit": "g", "basis": "net", "prep_loss_rate": "0.2"}]}}))
    owner.ok(owner.post(f"/recipes/{r['id']}/versions/1/publish"))
    return r


def test_production_deducts_ingredients_once_with_fifo_cost(owner):
    sup, beef = abc(owner)
    onion = make_ingredient(owner, "ONION", "Onion")
    buy(owner, sup["id"], [(onion["id"], 5, "kg", 2)], ref="O1", day="2026-01-01")
    r = make_published_recipe(owner, beef, onion)
    req = owner.ok(owner.get("/stock/production/requirements", params={"recipe_id": r["id"], "portions": 50}))
    assert [l["required_base"] for l in req["lines"]] == ["5000.000000", "1000.000000"]
    run = owner.ok(owner.post("/stock/production-runs", {"recipe_id": r["id"], "portions": "50"}, key="prod-key-01"))
    assert run["total_cost"] == "47.0000"  # 5 kg beef from batch A @ 9 + 1 kg onion @ 2
    assert run["cost_per_portion"] == "0.9400" and run["cost_basis"] == "fifo"
    again = owner.post("/stock/production-runs", {"recipe_id": r["id"], "portions": "50"}, key="prod-key-01")
    assert again.headers["Idempotent-Replay"] == "true"
    on = {s["code"]: s["on_hand_base"] for s in owner.ok(owner.get("/stock"))["items"]}
    assert on == {"BEEF": "25000.000000", "ONION": "4000.000000"}
    # separation: production movements are typed 'production', never 'sale'
    types = {m["type"] for m in owner.ok(owner.get("/stock/movements"))["items"]}
    assert "production" in types and "sale" not in types


def test_production_requires_published_version_and_permission(owner):
    _, beef = abc(owner)
    r = owner.ok(owner.post("/recipes", {"code": "D", "name": "Draft only", "version": {
        "portions_per_batch": "1", "lines": [{"ingredient_id": beef["id"], "quantity": "1", "unit": "kg"}]}}))
    assert owner.post("/stock/production-runs", {"recipe_id": r["id"], "batches": "1"}).status_code == 422
    viewer = make_user(owner, "viewer")
    assert viewer.post("/stock/production-runs", {"recipe_id": r["id"], "batches": "1"}).status_code == 403


def test_adjustments_need_reason_and_are_fifo_costed(owner):
    _, beef = abc(owner)
    assert owner.post("/stock/adjustments", {"ingredient_id": beef["id"], "quantity": "1", "unit": "kg"}).status_code == 422
    assert owner.post("/stock/adjustments", {"ingredient_id": beef["id"], "quantity": "1", "unit": "kg",
                                             "reason": "other"}).status_code == 422  # 'other' needs a note
    r = owner.ok(owner.post("/stock/adjustments", {"ingredient_id": beef["id"], "quantity": "2", "unit": "kg",
                                                   "reason": "spoilage", "note": "fridge failure"}))
    assert r["total_cost"] == "18.0000"
    rep = owner.ok(owner.get("/reports/consumption", params={"date_from": "2020-01-01", "date_to": "2030-12-31"}))
    assert rep["waste"] == "18.0000" and rep["waste_by_reason"] == {"spoilage": "18.0000"}


def test_stock_count_variances(owner):
    _, beef = abc(owner)
    onion = make_ingredient(owner, "ONION", "Onion", reference_price={"quantity": "1", "unit": "kg", "amount": "2"})
    c = owner.ok(owner.post("/stock/counts", {"note": "month end"}))
    owner.ok(owner.put(f"/stock/counts/{c['id']}/lines", {"lines": [
        {"ingredient_id": beef["id"], "quantity": "28.5", "unit": "kg"},
        {"ingredient_id": onion["id"], "quantity": "500", "unit": "g"}]}))
    kitchen = make_user(owner, "kitchen")
    assert kitchen.post(f"/stock/counts/{c['id']}/approve").status_code == 403
    done = owner.ok(owner.post(f"/stock/counts/{c['id']}/approve"))
    lines = {l["name"]: l for l in done["lines"]}
    assert lines["Beef"]["variance"].startswith("-1500") and lines["Beef"]["variance_value"] == "-13.5000"
    assert lines["Onion"]["variance"].startswith("500") and lines["Onion"]["variance_value"] == "1.0000"
    on = {s["code"]: s["on_hand_base"] for s in owner.ok(owner.get("/stock"))["items"]}
    assert on == {"BEEF": "28500.000000", "ONION": "500.000000"}
    assert owner.post(f"/stock/counts/{c['id']}/approve").status_code == 422
    assert owner.ok(owner.get("/reports/integrity"))["ok"] is True


def test_dashboard_reconciles_with_records(owner, engine):
    from sqlalchemy import text
    sup, beef = abc(owner)
    owner.ok(owner.patch(f"/ingredients/{beef['id']}", {"min_stock": "20", "min_stock_unit": "kg"}))
    owner.ok(consume(owner, beef["id"], 15))
    d = owner.ok(owner.get("/dashboard"))
    with engine.connect() as c:
        val = c.execute(text("SELECT round(sum(remaining_quantity*unit_cost),4) FROM stock_batch")).scalar()
        owed = c.execute(text("SELECT sum(amount) FROM supplier_ledger_entry")).scalar()
    assert d["inventory_value"]["value"] == f"{val:.4f}" == "175.0000"
    assert d["outstanding_payables"] == f"{owed:.4f}" == "320.0000"
    assert d["consumption"]["consumption"] == "145.0000"  # recorded today, so in this month
    assert [l["code"] for l in d["low_stock"]] == ["BEEF"]  # 15 kg < 20 kg
    assert d["sync"]["pending_events"] > 0
    assert owner.ok(owner.get("/reports/integrity"))["ok"] is True
