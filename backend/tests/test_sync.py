"""Hybrid sync acceptance tests: a real cloud server + fault-injected network + real PostgreSQL.

Each test ends by proving convergence: every replicated row in the cloud replica is identical to
the branch (``assert_converged``), and integrity checks pass on BOTH sides.
"""

import hashlib
import subprocess
import threading
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select, text, update

from app.models import OutboxEvent, StoredFile, SyncState
from app.sync import client as sync
from conftest import buy, make_ingredient, make_supplier, make_user
from sync_harness import CLOUD_URL, CloudServer, Flaky, assert_converged, cloud_api, prepare_cloud_db


# ───────────────────────── fixtures ─────────────────────────
@pytest.fixture(scope="module")
def cloud_db():
    return prepare_cloud_db()


@pytest.fixture(scope="module")
def cloud(cloud_db):
    s = CloudServer().start()
    yield s
    s.stop()


@pytest.fixture
def linked(owner, cloud, cloud_db, engine):
    """A branch registered with the cloud, talking through a controllable network."""
    from app.main import app
    net = Flaky()
    http = httpx.Client(transport=net, timeout=30)
    hq = cloud_api(cloud)
    r = hq.post("/cloud/branches", json={"code": "B" + uuid.uuid4().hex[:8], "name": "Kampala"})
    assert r.status_code == 201, r.text
    out = sync.register(app.state.Session, cloud.base, r.json()["registration_code"], http)
    me = owner.ok(owner.get("/me"))
    L = SimpleNamespace(app=app, S=app.state.Session, http=http, net=net, hq=hq, cloud=cloud, owner=owner,
                        cloud_engine=cloud_db, branch_engine=engine, cbid=out["cloud_branch_id"],
                        schema="br_" + uuid.UUID(me["branch"]["id"]).hex)
    L.cycle = lambda: sync.run_cycle(L.S, L.http)
    yield L
    http.close()


def drain(L, max_cycles=20):
    for _ in range(max_cycles):
        out = L.cycle()
        with L.S() as db:
            q = sync.queue_status(db)
        if q["pending"] + q["retry"] + q["failed"] == 0 and q["files_pending"] == 0 and out.get("state") == "ok":
            return out
        with L.S() as db:  # skip backoff waits in tests
            db.execute(update(OutboxEvent).where(OutboxEvent.status == "retry").values(next_attempt_at=None))
            db.commit()
    raise AssertionError(f"did not converge: {out} {q}")


def integrity_both(L):
    assert L.owner.ok(L.owner.get("/reports/integrity"))["ok"] is True
    dash = L.hq.get(f"/cloud/branches/{L.cbid}/dashboard")
    assert dash.status_code == 200, dash.text
    assert dash.json()["integrity_ok"] is True
    return dash.json()


def day_of_business(api, n=1):
    sup = make_supplier(api, "S" + uuid.uuid4().hex[:5], "Fresh Ltd")
    beef = make_ingredient(api, "B" + uuid.uuid4().hex[:5], "Beef")
    onion = make_ingredient(api, "O" + uuid.uuid4().hex[:5], "Onion")
    invs = [buy(api, sup["id"], [(beef["id"], 10, "kg", 9 + i), (onion["id"], 5, "kg", 2)], day=f"2026-01-0{i + 1}")
            for i in range(n)]
    api.ok(api.post("/stock/consumptions", {"lines": [{"ingredient_id": beef["id"], "quantity": "4", "unit": "kg"}]}))
    api.ok(api.post("/payments", {"supplier_id": sup["id"], "paid_on": "2026-01-05", "amount": "30", "method": "cash",
                                  "allocations": [{"invoice_id": invs[0]["id"], "amount": "30"}]}))
    api.ok(api.post("/stock/adjustments", {"ingredient_id": onion["id"], "quantity": "500", "unit": "g",
                                           "reason": "spoilage"}))
    return SimpleNamespace(sup=sup, beef=beef, onion=onion, invs=invs)


# ───────────────────────── 3. identification & atomic outbox ─────────────────────────
def test_every_transaction_is_identified_and_written_atomically(owner, engine, monkeypatch):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    buy(owner, sup["id"], [(ing["id"], 10, "kg", 9)], ref="INV-1")
    with engine.connect() as c:
        ev = c.execute(select(OutboxEvent).where(OutboxEvent.topic == "purchase.confirmed")).one()
    assert ev.device_id == uuid.UUID(owner.device) and ev.user_id is not None and ev.branch_id is not None
    assert ev.schema_version == "0002sync" and ev.status == "pending" and ev.created_at is not None
    tables = {r["t"] for r in ev.payload["rows"]}
    assert {"purchase_invoice", "purchase_line", "stock_batch", "stock_movement", "supplier_ledger_entry",
            "ingredient_price_history", "ingredient_current_cost", "audit_log"} <= tables
    # a failure mid-transaction leaves neither the business rows nor an outbox record
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(OutboxEvent)).scalar()
    from app.services import purchasing
    monkeypatch.setattr(purchasing, "_ledger", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        buy(owner, sup["id"], [(ing["id"], 1, "kg", 9)], ref="INV-2")
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(OutboxEvent)).scalar() == before
    # sequences follow commit order and are unique
    with engine.connect() as c:
        seqs = c.execute(select(OutboxEvent.sequence).order_by(OutboxEvent.created_at)).scalars().all()
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_writes_require_a_registered_unrevoked_device(owner, client):
    from conftest import Api
    raw = Api(client, owner.token, device=False)
    assert raw.post("/suppliers", {"code": "X1", "name": "X"}).status_code == 403
    owner.ok(owner.post(f"/devices/{owner.device}/revoke"))
    r = owner.post("/suppliers", {"code": "X2", "name": "X"})
    assert r.status_code == 403 and "revoked" in r.json()["detail"]


# ───────────────────────── 8. offline operation, then convergence ─────────────────────────
def test_offline_day_then_full_convergence(linked):
    L = linked
    drain(L)  # initial snapshot while online
    L.net.rule = lambda req: "down"  # internet is down all day
    day_of_business(L.owner, n=3)
    out = L.cycle()
    assert out["state"] == "error"
    with L.S() as db:
        q = sync.queue_status(db)
        head = db.scalar(select(OutboxEvent).where(OutboxEvent.status == "retry"))
    assert q["pending"] + q["retry"] >= 8 and q["failed"] == 0
    assert head.attempts == 1 and head.next_attempt_at is not None  # exponential backoff scheduled
    assert L.cycle()["push"]["stopped"]["reason"] == "backoff"  # respects backoff, sends nothing
    assert L.owner.ok(L.owner.get("/reports/integrity"))["ok"]  # local operations fully functional
    L.net.rule = None  # internet back
    drain(L)
    n = assert_converged(L.branch_engine, L.cloud_engine, L.schema)
    assert n > 50
    dash = integrity_both(L)
    local = L.owner.ok(L.owner.get("/dashboard"))
    for k in ("outstanding_payables", "month_purchases"):
        assert dash[k] == local[k]
    assert dash["inventory_value"] == local["inventory_value"]
    assert dash["consumption"] == local["consumption"]  # FIFO costs identical


def test_lost_acknowledgements_never_duplicate(linked):
    L = linked
    day_of_business(L.owner)
    L.net.rule = lambda req: "drop" if req.url.path.endswith("/events") else None  # cloud applies, ack lost
    assert L.cycle()["state"] == "error"
    with L.cloud_engine.connect() as c:
        applied = c.execute(text("SELECT count(*) FROM cloud_inbox WHERE cloud_branch_id = :b"), {"b": L.cbid}).scalar()
    assert applied > 0
    L.net.rule = None
    drain(L)
    with L.S() as db:
        total = db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == "acked"))
    with L.cloud_engine.connect() as c:
        received = c.execute(text("SELECT count(*) FROM cloud_inbox WHERE cloud_branch_id = :b"), {"b": L.cbid}).scalar()
    assert received == total  # every event exactly once
    # force a full re-send of everything already acknowledged: still no duplicates
    with L.S() as db:
        db.execute(update(OutboxEvent).where(OutboxEvent.status == "acked").values(status="pending", acked_at=None))
        db.commit()
    drain(L)
    with L.cloud_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM cloud_inbox WHERE cloud_branch_id = :b"), {"b": L.cbid}).scalar() == total
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)
    integrity_both(L)


def test_forged_acknowledgement_is_not_trusted(linked):
    L = linked
    make_supplier(L.owner, "FAKE1", "X")
    with L.S() as db:
        ev = db.scalar(select(OutboxEvent).where(OutboxEvent.status == "pending").order_by(OutboxEvent.sequence.desc()))
        eid, seq = str(ev.id), ev.sequence
    assert sync._record_acks(L.S, "wrong-secret-xx", [{"id": eid, "sequence": seq, "signature": "0" * 64}]) == 0
    with L.S() as db:
        assert db.get(OutboxEvent, uuid.UUID(eid)).status == "pending"


def test_cloud_restart_and_branch_restart_mid_sync(linked):
    L = linked
    day_of_business(L.owner)
    L.cloud.stop()  # cloud server restart (down)
    assert L.cycle()["state"] == "error"
    L.cloud.start()
    # branch "crashes" after the cloud applied a batch but before recording acks
    L.net.rule = lambda req: "drop" if req.url.path.endswith("/events") else None
    L.cycle()
    L.net.rule = None
    from app.main import create_app
    restarted = create_app()  # new process: fresh engine, nothing in memory
    L.S = restarted.state.Session
    L.cycle = lambda: sync.run_cycle(L.S, L.http)
    drain(L)
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)
    restarted.state.engine.dispose()


# ───────────────────────── queues, backoff, failed queue, dependency order ─────────────────────────
def test_failed_event_blocks_later_events_until_operator_acts(linked):
    L = linked
    drain(L)
    make_supplier(L.owner, "GOOD1", "Before")
    with L.S() as db:  # corrupt one pending event so the cloud cannot apply it
        bad = db.scalar(select(OutboxEvent).where(OutboxEvent.status == "pending"))
        bad.payload = {"rows": [{"t": "no_such_table", "op": "upsert", "row": {}}], "events": []}
        db.commit()
    make_supplier(L.owner, "GOOD2", "After")
    for _ in range(sync.MAX_ATTEMPTS):
        L.cycle()
        with L.S() as db:
            db.execute(update(OutboxEvent).where(OutboxEvent.status == "retry").values(next_attempt_at=None))
            db.commit()
    with L.S() as db:
        assert db.get(OutboxEvent, bad.id).status == "failed"
        later = db.scalars(select(OutboxEvent).where(OutboxEvent.sequence > bad.sequence)).all()
    assert later and all(e.status == "pending" for e in later)  # dependency order: nothing jumps the queue
    status = L.owner.ok(L.owner.get("/sync/status"))
    assert status["state"] == "blocked" and status["queue"]["failed"] == 1
    # operator: send a fresh snapshot (supersedes the broken event)
    L.owner.ok(L.owner.post("/sync/resnapshot"))
    drain(L)
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)


def test_backoff_is_exponential_with_jitter():
    samples = [sync.backoff(a).total_seconds() for a in range(1, 12) for _ in range(20)]
    assert min(samples) >= 1
    assert max(sync.backoff(3).total_seconds() for _ in range(50)) <= 16
    assert len({round(s, 3) for s in samples}) > 100  # jittered
    assert max(samples) <= 900


# ───────────────────────── 7. offline media ─────────────────────────
def test_receipt_stored_locally_and_resumable_checksum_verified_upload(linked):
    L = linked
    sup = make_supplier(L.owner)
    ing = make_ingredient(L.owner)
    inv = buy(L.owner, sup["id"], [(ing["id"], 10, "kg", 9)])
    photo = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 2400  # ~600 KB "JPEG" → 3 chunks
    L.net.rule = lambda req: "down"
    r = L.owner.c.post(f"/api/v1/purchases/{inv['id']}/attachments", headers=L.owner.h(False),
                       files={"file": ("receipt.jpg", photo, "image/jpeg")})
    assert r.status_code == 201 and r.json()["cloud_status"] == "pending"  # stored locally, offline
    fid = r.json()["id"]
    got = L.owner.c.get(f"/api/v1/files/{fid}", headers=L.owner.h(False))
    assert got.content == photo and got.headers["x-content-sha256"] == hashlib.sha256(photo).hexdigest()
    bad = L.owner.c.post(f"/api/v1/purchases/{inv['id']}/attachments", headers=L.owner.h(False),
                         files={"file": ("evil.jpg", b"MZ\x90\x00not an image", "image/jpeg")})
    assert bad.status_code == 422
    # connection drops after the first chunk
    seen = {"puts": 0}

    def flaky(req):
        if req.method == "PUT":
            seen["puts"] += 1
            return "down" if seen["puts"] == 2 else None
        return None
    L.net.rule = flaky
    assert L.cycle()["state"] == "error"
    assert L.owner.ok(L.owner.get(f"/purchases/{inv['id']}/attachments"))[0]["cloud_status"] == "pending"
    with L.cloud_engine.connect() as c:
        partial = c.execute(text("SELECT received_bytes, status FROM cloud_file WHERE id = :f"), {"f": fid}).one()
    assert 0 < partial.received_bytes < len(photo) and partial.status == "uploading"
    L.net.rule = None
    L.net.log.clear()
    drain(L)
    offsets = [int(p["offset"]) for m, path, p in L.net.log if m == "PUT"]
    assert offsets and min(offsets) == partial.received_bytes  # resumed, did not restart from 0
    att = L.owner.ok(L.owner.get(f"/purchases/{inv['id']}/attachments"))[0]
    assert att["cloud_status"] == "verified"
    from app.config import get_settings
    from pathlib import Path
    stored = Path(get_settings().files_dir).resolve() / "cloud"
    cloud_copy = next(stored.rglob(fid))
    assert hashlib.sha256(cloud_copy.read_bytes()).hexdigest() == hashlib.sha256(photo).hexdigest()
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)


def test_corrupted_upload_is_rejected_and_resent(linked):
    L = linked
    sup = make_supplier(L.owner)
    ing = make_ingredient(L.owner)
    inv = buy(L.owner, sup["id"], [(ing["id"], 1, "kg", 9)])
    photo = b"\x89PNG\r\n\x1a\n" + b"x" * 1000
    fid = L.owner.c.post(f"/api/v1/purchases/{inv['id']}/attachments", headers=L.owner.h(False),
                         files={"file": ("r.png", photo, "image/png")}).json()["id"]
    original = httpx.HTTPTransport.handle_request

    def corrupt_put(req):
        if req.method == "PUT":  # flip bytes in transit
            req = httpx.Request(req.method, req.url, headers={k: v for k, v in req.headers.items() if k != "content-length"},
                                content=req.read().replace(b"x", b"y", 3))
            # signature covers the body, so the cloud must reject the tampered chunk
        return original(L.net.inner, req)
    L.net.inner.handle_request = corrupt_put
    L.cycle()
    with L.S() as db:
        assert db.get(StoredFile, uuid.UUID(fid)).cloud_verified_at is None  # never reported as synced
    L.net.inner.handle_request = lambda req: original(L.net.inner, req)
    drain(L)
    with L.S() as db:
        assert db.get(StoredFile, uuid.UUID(fid)).cloud_verified_at is not None


# ───────────────────────── 6. conflict policies ─────────────────────────
def _cmd(L, ctype, payload):
    r = L.hq.post(f"/cloud/branches/{L.cbid}/commands", json={"type": ctype, "payload": payload})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _cmd_status(L, cid):
    cmds = L.hq.get(f"/cloud/branches/{L.cbid}/commands").json()
    return next(c for c in cmds if c["id"] == cid)


def test_remote_ingredient_edit_conflict_and_success(linked):
    L = linked
    ing = make_ingredient(L.owner, "RICE", "Rice")
    drain(L)
    stale = _cmd(L, "ingredient.update", {"ingredient_id": ing["id"], "changes": {"name": "Rice (cloud)"}})
    # meanwhile a manager at the branch renames it
    L.owner.ok(L.owner.patch(f"/ingredients/{ing['id']}", {"name": "Rice (branch)"}))
    drain(L)
    c = _cmd_status(L, stale)
    assert c["status"] == "conflict" and "changed at the branch" in c["result"]["error"]
    assert L.owner.ok(L.owner.get(f"/ingredients/{ing['id']}"))["name"] == "Rice (branch)"  # nothing overwritten
    fresh = _cmd(L, "ingredient.update", {"ingredient_id": ing["id"], "changes": {"min_stock": "5", "min_stock_unit": "kg"}})
    drain(L)
    assert _cmd_status(L, fresh)["status"] == "applied"
    assert L.owner.ok(L.owner.get(f"/ingredients/{ing['id']}"))["min_stock_base"] == "5000.000000"
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)


def test_remote_payment_cannot_overpay_when_branch_paid_meanwhile(linked):
    L = linked
    sup = make_supplier(L.owner)
    ing = make_ingredient(L.owner)
    inv = buy(L.owner, sup["id"], [(ing["id"], 10, "kg", 10)])  # 100 owed
    drain(L)
    cid = _cmd(L, "payment.post", {"payment": {"supplier_id": sup["id"], "paid_on": "2026-01-10", "amount": "100",
                                               "method": "bank_transfer", "reference": "HQ-1",
                                               "allocations": [{"invoice_id": inv["id"], "amount": "100"}]}})
    L.owner.ok(L.owner.post("/payments", {"supplier_id": sup["id"], "paid_on": "2026-01-10", "amount": "60",
                                          "method": "cash", "allocations": [{"invoice_id": inv["id"], "amount": "60"}]}))
    drain(L)
    c = _cmd_status(L, cid)
    assert c["status"] == "rejected" and "exceeds the outstanding" in c["result"]["error"]
    assert L.owner.ok(L.owner.get(f"/suppliers/{sup['id']}"))["balance"] == "40.0000"
    ok = _cmd(L, "payment.post", {"payment": {"supplier_id": sup["id"], "paid_on": "2026-01-11", "amount": "40",
                                              "method": "bank_transfer", "reference": "HQ-2",
                                              "allocations": [{"invoice_id": inv["id"], "amount": "40"}]}})
    drain(L)
    assert _cmd_status(L, ok)["status"] == "applied"
    assert L.owner.ok(L.owner.get(f"/suppliers/{sup['id']}"))["balance"] == "0.0000"
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)
    integrity_both(L)


def test_price_updates_newest_effective_date_wins_regardless_of_arrival(linked):
    L = linked
    sup = make_supplier(L.owner)
    ing = make_ingredient(L.owner)
    buy(L.owner, sup["id"], [(ing["id"], 10, "kg", 12)], day="2026-03-10")  # 0.012/g, newer
    drain(L)
    cid = _cmd(L, "ingredient.price", {"ingredient_id": ing["id"], "price": {
        "quantity": "1", "unit": "kg", "amount": "8", "effective_date": "2026-03-01", "note": "old quote"}})
    drain(L)
    assert _cmd_status(L, cid)["status"] == "applied"
    cur = L.owner.ok(L.owner.get(f"/ingredients/{ing['id']}"))["current_cost"]
    assert cur["cost_per_base_unit"] == "0.012000000000"  # the older-dated quote is history only
    assert len(L.owner.ok(L.owner.get(f"/ingredients/{ing['id']}/price-history"))) == 2
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)


def test_recipe_version_and_branch_settings_conflicts(linked):
    L = linked
    ing = make_ingredient(L.owner, reference_price={"quantity": "1", "unit": "kg", "amount": "10",
                                                    "effective_date": "2026-01-01"})
    r = L.owner.ok(L.owner.post("/recipes", {"code": "SOUP", "name": "Soup", "version": {
        "portions_per_batch": "4", "lines": [{"ingredient_id": ing["id"], "quantity": "1", "unit": "kg"}]}}))
    L.owner.ok(L.owner.post(f"/recipes/{r['id']}/versions/1/publish"))
    drain(L)
    draft = {"portions_per_batch": "5", "lines": [{"ingredient_id": ing["id"], "quantity": "1.2", "unit": "kg"}]}
    stale = _cmd(L, "recipe.save_draft", {"recipe_id": r["id"], "draft": draft})  # base = v1
    L.owner.ok(L.owner.put(f"/recipes/{r['id']}/draft", {"portions_per_batch": "6", "lines": draft["lines"]}))  # v2 at branch
    settings_cmd = _cmd(L, "branch_settings.update", {"changes": {"default_target_food_cost_rate": "0.28"}})
    L.owner.ok(L.owner.patch("/settings/branch", {"include_tax_in_cost": True}))  # branch changed settings first
    drain(L)
    assert _cmd_status(L, stale)["status"] == "conflict"
    assert _cmd_status(L, settings_cmd)["status"] == "conflict"
    me = L.owner.ok(L.owner.get("/me"))["branch"]
    assert me["default_target_food_cost_rate"] == "0.300000" and me["include_tax_in_cost"] is True
    fresh = _cmd(L, "branch_settings.update", {"changes": {"default_target_food_cost_rate": "0.28"}})
    drain(L)
    assert _cmd_status(L, fresh)["status"] == "applied"
    assert L.owner.ok(L.owner.get("/me"))["branch"]["default_target_food_cost_rate"] == "0.280000"
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)


def test_duplicate_command_delivery_applies_once(linked):
    L = linked
    ing = make_ingredient(L.owner)
    drain(L)
    cid = _cmd(L, "ingredient.price", {"ingredient_id": ing["id"], "price": {"quantity": "1", "unit": "kg", "amount": "5"}})
    drain(L)
    with L.S() as db:  # simulate the branch losing its command cursor (e.g. restored config)
        db.get(SyncState, 1).last_command_seq = 0
        db.commit()
    drain(L)
    assert len(L.owner.ok(L.owner.get(f"/ingredients/{ing['id']}/price-history"))) == 1
    assert _cmd_status(L, cid)["status"] == "applied"


def test_simultaneous_devices_and_fifo_stay_correct_through_sync(linked, client):
    L = linked
    second = make_user(L.owner, "manager")  # another person, another device
    sup = make_supplier(L.owner)
    beef = make_ingredient(L.owner)
    buy(L.owner, sup["id"], [(beef["id"], 5, "kg", 9)], ref="A", day="2026-01-01")
    buy(L.owner, sup["id"], [(beef["id"], 5, "kg", 11)], ref="B", day="2026-01-02")
    body = {"supplier_id": sup["id"], "invoice_ref": "DUP", "invoice_date": "2026-01-03",
            "lines": [{"ingredient_id": beef["id"], "quantity": "1", "unit": "kg", "unit_price": "12"}]}
    codes, results = [], []

    def submit(api):
        codes.append(api.post("/purchases", body, key="same-paper-invoice").status_code)

    def consume(api):
        results.append(api.post("/stock/consumptions", {"lines": [{"ingredient_id": beef["id"], "quantity": "1",
                                                                   "unit": "kg"}]}))
    ts = [threading.Thread(target=submit, args=(a,)) for a in (L.owner, second)]
    ts += [threading.Thread(target=consume, args=(a,)) for a in (L.owner, second) * 5]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert codes.count(201) == 2  # second device gets the replayed result, not a second purchase
    assert all(r.status_code == 201 for r in results)
    drain(L)
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)
    dash = integrity_both(L)
    assert dash["inventory_value"]["value"] == "12.0000"  # 10 kg FIFO-consumed: 5@9 + 5@11 → only the 1 kg @12 left
    with L.cloud_engine.connect() as c:
        c.exec_driver_sql(f'SET search_path TO "{L.schema}"')
        assert c.execute(text("SELECT count(*) FROM purchase_invoice WHERE invoice_ref='DUP'")).scalar() == 1
        assert c.execute(text("SELECT min(remaining_quantity) FROM stock_batch")).scalar() >= 0


# ───────────────────────── 8. upgrade, database recovery, extended offline ─────────────────────────
def test_schema_versions_unknown_to_cloud_wait_instead_of_failing(linked, monkeypatch):
    L = linked
    drain(L)
    make_supplier(L.owner, "NEWVER", "Future")
    with L.S() as db:
        db.execute(update(OutboxEvent).where(OutboxEvent.status == "pending").values(schema_version="0099future"))
        db.commit()
    for _ in range(sync.MAX_ATTEMPTS + 2):
        out = L.cycle()
        with L.S() as db:
            db.execute(update(OutboxEvent).where(OutboxEvent.status == "retry").values(next_attempt_at=None))
            db.commit()
    assert out["push"]["stopped"]["reason"] == "schema_version"
    with L.S() as db:
        assert db.scalar(select(func.count()).where(OutboxEvent.status == "failed")) == 0  # waits, never dropped
    from app.sync import spec
    monkeypatch.setattr("app.cloud.service.KNOWN_SCHEMA_VERSIONS", spec.KNOWN_SCHEMA_VERSIONS + ("0099future",))
    drain(L)  # cloud upgraded → applies
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)


def test_upgrade_migration_keeps_pending_transactions(engine, tmp_path):
    """Branch software upgrade with unsynchronised events: pre-capture events are superseded by a
    snapshot (which contains their effects) and nothing is deleted."""
    from alembic import command
    from alembic.config import Config
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "511ffdd9a91e")
    try:
        with engine.begin() as c:
            bid = uuid.uuid4()
            c.execute(text("INSERT INTO branch (id, code, name, country, currency, currency_decimals, timezone, "
                           "default_target_food_cost_rate, include_tax_in_cost) VALUES (:i,'OLD','Old','UG','UGX',0,"
                           "'Africa/Kampala',0.3,false)"), {"i": bid})
            c.execute(text("INSERT INTO outbox_event (id, branch_id, topic, payload, attempts) VALUES "
                           "(gen_random_uuid(), :i, 'purchase.confirmed', '{}', 0)"), {"i": bid})
        command.upgrade(cfg, "head")
        with engine.connect() as c:
            row = c.execute(text("SELECT status, kind FROM outbox_event")).one()
            assert row.status == "superseded" and row.kind == "changeset"
            assert c.execute(text("SELECT needs_snapshot FROM sync_state")).scalar() is True
    finally:
        command.upgrade(cfg, "head")


def _pg_env(url):
    from sqlalchemy.engine import make_url
    u = make_url(url)
    return {"PGHOST": u.host, "PGPORT": str(u.port or 5432), "PGUSER": u.username, "PGPASSWORD": u.password,
            "PATH": "/usr/bin:/bin:/usr/local/bin"}, u.database


def test_cloud_database_restored_from_older_backup_reconverges(linked, tmp_path):
    L = linked
    day_of_business(L.owner)
    drain(L)
    env, dbname = _pg_env(CLOUD_URL)
    dump = tmp_path / "cloud.dump"
    subprocess.run(["pg_dump", "-Fc", "-f", str(dump), dbname], env=env, check=True)
    day_of_business(L.owner)  # more history the backup does not contain
    drain(L)
    def count():
        with L.cloud_engine.connect() as c:
            return c.execute(text("SELECT count(*) FROM cloud_inbox WHERE cloud_branch_id = :b"), {"b": L.cbid}).scalar()
    full = count()
    L.cloud.stop()  # disaster: cloud database restored from the older backup
    L.cloud_engine.dispose()
    subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "-d", dbname, str(dump)],
                   env=env, check=True, capture_output=True)
    assert count() < full  # the cloud really lost acknowledged events
    L.cloud.start()
    out = L.cycle()  # heartbeat: branch sees the high-water mark went backwards, re-queues and re-sends
    assert out["push"]["sent"] > 0 and out["push"]["acked"] > 0
    drain(L)
    assert count() == full
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)
    integrity_both(L)


def test_branch_database_restored_from_older_backup(linked, tmp_path):
    """Acknowledged transactions are not lost: the cloud holds them and gives them back."""
    from app.config import get_settings
    L = linked
    day_of_business(L.owner)
    drain(L)
    env, dbname = _pg_env(get_settings().database_url)
    dump = tmp_path / "branch.dump"
    subprocess.run(["pg_dump", "-Fc", "-f", str(dump), dbname], env=env, check=True)
    later = day_of_business(L.owner)  # synced to the cloud, then the branch disk dies
    drain(L)
    expected = L.owner.ok(L.owner.get("/dashboard"))
    L.branch_engine.dispose()
    L.app.state.engine.dispose()
    subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "-d", dbname, str(dump)],
                   env=env, check=True, capture_output=True)
    # runbook step: restore-from-cloud BEFORE opening the restaurant
    out = sync.restore_from_cloud(L.S, L.http)
    assert out["events_restored"] > 0
    got = L.owner.ok(L.owner.get("/dashboard"))
    for k in ("inventory_value", "outstanding_payables", "month_purchases", "consumption"):
        assert got[k] == expected[k], k
    assert L.owner.ok(L.owner.get(f"/suppliers/{later.sup['id']}"))["name"] == "Fresh Ltd"
    make_supplier(L.owner, "AFTER", "After restore")  # business continues; sequences moved past the cloud
    drain(L)
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)
    integrity_both(L)


def test_restored_branch_that_skips_the_runbook_is_halted_not_merged(linked, tmp_path):
    from app.config import get_settings
    L = linked
    make_supplier(L.owner, "ONE", "One")
    drain(L)
    env, dbname = _pg_env(get_settings().database_url)
    dump = tmp_path / "b.dump"
    subprocess.run(["pg_dump", "-Fc", "-f", str(dump), dbname], env=env, check=True)
    make_supplier(L.owner, "TWO", "Two")
    drain(L)
    L.app.state.engine.dispose()
    L.branch_engine.dispose()
    subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "-d", dbname, str(dump)],
                   env=env, check=True, capture_output=True)
    make_supplier(L.owner, "THREE", "Three")  # new local transaction reuses an old sequence number
    out = L.cycle()
    assert out["push"]["stopped"]["reason"] == "sequence_regression"
    st = L.owner.ok(L.owner.get("/sync/status"))
    assert st["state"] == "halted" and "restored" in st["halted_reason"]
    with L.S() as db:  # the new local transaction is preserved, not dropped
        assert db.scalar(select(func.count()).where(OutboxEvent.topic == "supplier.upserted",
                                                     OutboxEvent.status.in_(["pending", "retry"]))) >= 1
    branches = L.hq.get("/cloud/branches").json()
    assert next(b for b in branches if b["id"] == L.cbid)["sync"]["state"] == "halted"


def test_extended_offline_period_drains_in_batches(linked):
    L = linked
    sup = make_supplier(L.owner)
    beef = make_ingredient(L.owner)
    drain(L)
    L.net.rule = lambda req: "down"
    buy(L.owner, sup["id"], [(beef["id"], 500, "kg", 9)])
    for _ in range(300):  # a long outage: 300 separate usage entries
        L.owner.ok(L.owner.post("/stock/consumptions", {"lines": [{"ingredient_id": beef["id"], "quantity": "1",
                                                                   "unit": "kg"}]}))
    with L.S() as db:
        assert sync.queue_status(db)["pending"] + sync.queue_status(db)["retry"] >= 300
    L.net.rule = None
    t0 = time.perf_counter()
    drain(L)
    elapsed = time.perf_counter() - t0
    posts = [x for x in L.net.log if x[1].endswith("/events")]
    assert len(posts) >= 3  # batched (100 per request), in order
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)
    integrity_both(L)
    print(f"\n  drained {len(posts)} batches in {elapsed:.1f}s")


def test_cloud_people_api_requires_mfa_and_isolates_branches(cloud, cloud_db):
    from app.cloud import totp as t
    from sync_harness import ADMIN
    c = httpx.Client(base_url=cloud.base + "/api/v1")
    assert c.post("/cloud/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]}).json() == {"mfa_required": True}
    assert c.post("/cloud/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"],
                                             "code": "000000"}).status_code == 401
    assert c.get("/cloud/branches").status_code == 401
    # branch tokens are not valid in the cloud and unsigned machine calls are refused
    assert c.post("/cloud-sync/events", json={"events": []}).status_code == 401
    assert c.post("/cloud-sync/events", json={"events": []}, headers={
        "X-Branch-Id": str(uuid.uuid4()), "X-Timestamp": str(int(time.time())), "X-Signature": "x"}).status_code == 401
    # new users must enrol MFA before getting any access token
    from app.cloud.models import CloudUser
    from app.security import hash_password
    with cloud_db.begin() as conn:
        conn.execute(CloudUser.__table__.insert().values(id=uuid.uuid4(), email="viewer@example.com", display_name="V",
                     role="viewer", password_hash=hash_password("viewer-password-1"), mfa_enabled=False,
                     is_active=True, failed_logins=0, token_version=1, branch_scope=[]))
    r = c.post("/cloud/auth/login", json={"email": "viewer@example.com", "password": "viewer-password-1"}).json()
    assert r.get("mfa_enrollment_required") and "access_token" not in r
    tok = c.post("/cloud/auth/mfa/enroll", json={"enrollment_token": r["enrollment_token"],
                                                 "code": t.code_at(r["secret"], time.time())}).json()["access_token"]
    assert c.get("/cloud/branches", headers={"Authorization": f"Bearer {tok}"}).json() == []  # scoped to no branches


def test_delete_and_reinsert_of_same_row_in_one_transaction_replicates(linked):
    """Regression (found in the live two-server run): re-saving an ingredient's supplier links
    deletes and re-inserts rows with the same key in one transaction."""
    L = linked
    s1, s2 = make_supplier(L.owner, "S1", "One"), make_supplier(L.owner, "S2", "Two")
    ing = make_ingredient(L.owner, suppliers=[{"supplier_id": s1["id"]}, {"supplier_id": s2["id"]}])
    drain(L)
    L.owner.ok(L.owner.patch(f"/ingredients/{ing['id']}", {"description": "x", "suppliers": [
        {"supplier_id": s1["id"], "is_preferred": True}, {"supplier_id": s2["id"]}]}))
    L.owner.ok(L.owner.patch(f"/ingredients/{ing['id']}", {"suppliers": [{"supplier_id": s2["id"]}]}))
    drain(L)
    assert_converged(L.branch_engine, L.cloud_engine, L.schema)
