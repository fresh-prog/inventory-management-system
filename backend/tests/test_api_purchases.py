"""Purchases, supplier balances, partial payments, duplicates, atomicity (MVP acceptance 5-7, 11)."""

import threading
import uuid

import pytest
from sqlalchemy import text

from conftest import Api, buy, login, make_ingredient, make_supplier, OWNER


def counts(engine):
    with engine.connect() as c:
        return {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in (
            "purchase_invoice", "purchase_line", "stock_batch", "stock_movement",
            "ingredient_price_history", "supplier_ledger_entry", "outbox_event", "idempotency_record")}


def test_confirm_purchase_updates_everything_once(owner, engine):
    sup = make_supplier(owner)
    beef, onion, oil = (make_ingredient(owner, "BEEF", "Beef"), make_ingredient(owner, "ONION", "Onion"),
                        make_ingredient(owner, "OIL", "Oil", "volume", "l", "ml"))
    before = counts(engine)
    inv = buy(owner, sup["id"], [(beef["id"], 10, "kg", 9), (onion["id"], "2.5", "kg", 4), (oil["id"], 5, "l", 3)],
              ref="INV-001", discount_total="5", tax_total="16.92", total="126.92")
    after = counts(engine)
    delta = {k: after[k] - before[k] for k in after}
    assert delta["purchase_invoice"] == 1 and delta["purchase_line"] == 3 and delta["stock_batch"] == 3
    assert delta["stock_movement"] == 3 and delta["ingredient_price_history"] == 3
    assert delta["supplier_ledger_entry"] == 1 and delta["outbox_event"] >= 1
    assert inv["subtotal"] == "115.0000" and inv["total"] == "126.9200"
    assert inv["payment_status"] == "unpaid" and inv["outstanding"] == "126.9200"
    assert inv["supplier_balance"] == "126.9200"
    # invoice discount spread over lines; VAT excluded from stock cost by default
    assert sum(float(l["stock_cost"]) for l in inv["lines"]) == pytest.approx(110.0)
    stock = {s["code"]: s for s in owner.ok(owner.get("/stock"))["items"]}
    assert stock["BEEF"]["on_hand_base"] == "10000.000000"
    assert stock["OIL"]["on_hand_base"] == "5000.000000"


def test_idempotent_replay_and_key_reuse(owner, engine):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    body = {"supplier_id": sup["id"], "invoice_ref": "R1", "invoice_date": "2026-01-01",
            "lines": [{"ingredient_id": ing["id"], "quantity": "10", "unit": "kg", "unit_price": "9"}]}
    r1 = owner.post("/purchases", body, key="key-aaaaaaaa")
    r2 = owner.post("/purchases", body, key="key-aaaaaaaa")
    assert r1.status_code == 201 and r2.status_code == 201
    assert r2.headers.get("Idempotent-Replay") == "true" and r1.json()["id"] == r2.json()["id"]
    assert counts(engine)["purchase_invoice"] == 1
    r3 = owner.post("/purchases", {**body, "invoice_ref": "R2"}, key="key-aaaaaaaa")
    assert r3.status_code == 409
    assert owner.post("/purchases", body).status_code == 409  # same supplier invoice, new key
    assert owner.post("/purchases", body, key=False).status_code == 422  # key required


def test_concurrent_duplicate_submissions_post_once(client, owner, engine):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    body = {"supplier_id": sup["id"], "invoice_ref": "DUP", "invoice_date": "2026-01-01",
            "lines": [{"ingredient_id": ing["id"], "quantity": "10", "unit": "kg", "unit_price": "9"}]}
    results = []

    def go():
        results.append(owner.post("/purchases", body, key="same-key-123").status_code)
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert counts(engine)["purchase_invoice"] == 1
    assert all(s == 201 for s in results)


def test_invoice_totals_validated(owner):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    r = owner.post("/purchases", {"supplier_id": sup["id"], "invoice_ref": "BAD", "invoice_date": "2026-01-01",
                                  "total": "100", "lines": [{"ingredient_id": ing["id"], "quantity": "10",
                                                             "unit": "kg", "unit_price": "9", "line_total": "95"}]})
    assert r.status_code == 422 and len(r.json()["errors"]) == 2


def test_failure_mid_transaction_leaves_nothing(owner, engine, monkeypatch):
    from app.services import purchasing
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    before = counts(engine)

    def boom(*a, **k):
        raise RuntimeError("disk full (simulated)")
    monkeypatch.setattr(purchasing, "_ledger", boom)  # fails after batches + price history are written
    with pytest.raises(RuntimeError):
        owner.post("/purchases", {"supplier_id": sup["id"], "invoice_ref": "X", "invoice_date": "2026-01-01",
                                  "lines": [{"ingredient_id": ing["id"], "quantity": "1", "unit": "kg",
                                             "unit_price": "9"}]})
    assert counts(engine) == before
    assert owner.ok(owner.get(f"/ingredients/{ing['id']}"))["current_cost"] is None


def pay(api, sup, inv_amounts, amount, ref=None, method="bank_transfer", key=None):
    return api.post("/payments", {"supplier_id": sup, "paid_on": "2026-01-10", "amount": str(amount),
                                  "method": method, "reference": ref,
                                  "allocations": [{"invoice_id": i, "amount": str(a)} for i, a in inv_amounts]}, key=key)


def test_partial_and_full_payments_and_balances(owner):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    a = buy(owner, sup["id"], [(ing["id"], 10, "kg", 10)], ref="A")   # 100
    b = buy(owner, sup["id"], [(ing["id"], 5, "kg", 10)], ref="B")    # 50
    owner.ok(pay(owner, sup["id"], [(a["id"], 30)], 30, ref="T1"))
    s = owner.ok(owner.get(f"/suppliers/{sup['id']}"))
    inv = {i["invoice_ref"]: i for i in s["invoices"]}
    assert inv["A"]["payment_status"] == "partially_paid" and inv["A"]["outstanding"] == "70.0000"
    assert s["balance"] == "120.0000"
    owner.ok(pay(owner, sup["id"], [(a["id"], 70), (b["id"], 20)], 90, ref="T2"))
    s = owner.ok(owner.get(f"/suppliers/{sup['id']}"))
    inv = {i["invoice_ref"]: i for i in s["invoices"]}
    assert inv["A"]["payment_status"] == "paid" and inv["B"]["outstanding"] == "30.0000"
    assert s["balance"] == "30.0000" and s["statement"][-1]["balance"] == "30.0000"
    # over-allocation rejected
    r = pay(owner, sup["id"], [(b["id"], "30.01")], "30.01")
    assert r.status_code == 422 and "exceeds the outstanding" in r.json()["detail"]
    # allocations must equal payment amount
    assert pay(owner, sup["id"], [(b["id"], 10)], 20).status_code == 422
    # duplicate payment reference rejected
    assert pay(owner, sup["id"], [(b["id"], 5)], 5, ref="T2").status_code == 409
    # credit reduces outstanding
    owner.ok(owner.post("/supplier-credits", {"invoice_id": b["id"], "amount": "10", "reason": "2 kg spoiled"}))
    s = owner.ok(owner.get(f"/suppliers/{sup['id']}"))
    assert s["balance"] == "20.0000"
    assert owner.ok(owner.get("/reports/integrity"))["ok"] is True


def test_concurrent_payments_cannot_overpay(owner):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    inv = buy(owner, sup["id"], [(ing["id"], 5, "kg", 10)])  # 50
    codes = []

    def go(i):
        codes.append(pay(owner, sup["id"], [(inv["id"], 50)], 50, ref=f"P{i}").status_code)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codes) == [201, 422, 422, 422, 422]
    assert owner.ok(owner.get(f"/purchases/{inv['id']}"))["outstanding"] == "0.0000"


def test_void_payment_and_purchase(owner):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    inv = buy(owner, sup["id"], [(ing["id"], 10, "kg", 10)], day="2026-01-01")
    p = owner.ok(pay(owner, sup["id"], [(inv["id"], 40)], 40))
    assert owner.post(f"/purchases/{inv['id']}/void", {"reason": "wrong supplier"}).status_code == 422
    owner.ok(owner.post(f"/payments/{p['id']}/void", {"reason": "bounced"}))
    v = owner.ok(owner.post(f"/purchases/{inv['id']}/void", {"reason": "wrong supplier"}))
    assert v["status"] == "voided"
    assert owner.ok(owner.get(f"/suppliers/{sup['id']}"))["balance"] == "0.0000"
    assert owner.ok(owner.get("/stock"))["total_value"] == "0.0000"
    assert owner.ok(owner.get(f"/ingredients/{ing['id']}"))["current_cost"] is None
    assert owner.ok(owner.get("/reports/integrity"))["ok"] is True


def test_cannot_void_consumed_purchase(owner):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    inv = buy(owner, sup["id"], [(ing["id"], 10, "kg", 10)])
    owner.ok(owner.post("/stock/consumptions", {"lines": [{"ingredient_id": ing["id"], "quantity": "1", "unit": "kg"}]}))
    r = owner.post(f"/purchases/{inv['id']}/void", {"reason": "mistake"})
    assert r.status_code == 422 and "already been used" in r.json()["detail"]


def test_history_tables_are_append_only(owner, engine):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    buy(owner, sup["id"], [(ing["id"], 10, "kg", 10)])
    for stmt in ("UPDATE stock_movement SET total_cost = 0", "DELETE FROM supplier_ledger_entry",
                 "DELETE FROM audit_log"):
        with pytest.raises(Exception, match="append-only"):
            with engine.begin() as c:
                c.execute(text(stmt))
