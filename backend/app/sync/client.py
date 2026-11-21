"""Branch → cloud synchronisation engine.

One cycle = snapshot (if needed) → push events → reconcile high-water → pull & apply commands →
upload files → housekeeping. Safe to run concurrently (a session advisory lock elects one runner),
safe to kill at any point (every step is idempotent and resumes from durable state).

Queues are states of ``outbox_event.status``:
  pending  — not yet sent                retry — failed to send, waiting for next_attempt_at
  failed   — rejected 10 times: blocks later events (dependency order) until an operator acts
  acked    — cloud acknowledged with a valid signature (kept for the retention period)
  superseded — replaced by a full snapshot (state already included in the snapshot)
"""

import hashlib
import json
import logging
import random
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import Table, and_, delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import Base
from ..jsonenc import enc
from ..models import Branch, OutboxEvent, StoredFile, SyncState
from . import signing
from .capture import OUTBOX_LOCK
from .rowcodec import to_json
from .spec import EXCLUDED_COLUMNS, REPLICATED_TABLES, SCHEMA_VERSION, SNAPSHOT

log = logging.getLogger("app.sync")
RUNNER_LOCK = 7_204_118_002
MAX_ATTEMPTS = 10
SNAPSHOT_CHUNK = 500
FILE_CHUNK = 256 * 1024


class SyncError(Exception):
    pass


def now() -> datetime:
    return datetime.now(UTC)


def backoff(attempts: int) -> timedelta:
    """Exponential backoff with full jitter: uniform(1, min(cap, 2 * 2^attempts)) seconds."""
    cap = get_settings().sync_max_backoff_seconds
    return timedelta(seconds=random.uniform(1, max(1, min(cap, 2 * (2 ** attempts)))))


# ───────────────────────── transport ─────────────────────────
class CloudClient:
    def __init__(self, base_url: str, cloud_branch_id: str, secret: str, http: httpx.Client | None = None):
        self.base = base_url.rstrip("/")
        self.bid, self.secret = cloud_branch_id, secret
        self.http = http or httpx.Client(timeout=httpx.Timeout(30, connect=10))

    def call(self, method: str, path: str, *, json_body=None, content: bytes | None = None, params=None):
        full = "/api/v1/cloud-sync" + path
        body = content if content is not None else (json.dumps(json_body).encode() if json_body is not None else b"")
        hdr = signing.headers(self.secret, self.bid, method, full, body)
        if json_body is not None:
            hdr["Content-Type"] = "application/json"
        try:
            r = self.http.request(method, self.base + full, content=body, headers=hdr, params=params)
        except httpx.HTTPError as e:
            raise SyncError(f"cloud unreachable: {type(e).__name__}") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:200]
            err = SyncError(f"cloud returned {r.status_code}: {detail}")
            try:
                err.body = r.json()
            except ValueError:
                err.body = {}
            err.status = r.status_code
            raise err
        return r.json()


def register(SessionMaker, cloud_url: str, registration_code: str, http: httpx.Client | None = None) -> dict:
    with SessionMaker() as db:
        st = db.get(SyncState, 1)
        if st.registered_at:
            raise SyncError("This branch server is already registered with a cloud.")
        b = db.scalar(select(Branch).limit(1))
        info = {"branch_id": str(b.id), "code": b.code, "name": b.name, "country": b.country, "currency": b.currency}
    client = http or httpx.Client(timeout=60)
    try:
        r = client.post(cloud_url.rstrip("/") + "/api/v1/cloud-sync/register",
                        json={"registration_code": registration_code, "branch": info})
    except httpx.HTTPError as e:
        raise SyncError(f"cloud unreachable: {type(e).__name__}") from e
    if r.status_code >= 400:
        raise SyncError(r.json().get("detail", f"registration failed ({r.status_code})"))
    data = r.json()
    with SessionMaker() as db:
        st = db.get(SyncState, 1, with_for_update=True)
        st.cloud_url, st.cloud_branch_id = cloud_url.rstrip("/"), uuid.UUID(data["cloud_branch_id"])
        st.branch_secret, st.registered_at, st.needs_snapshot = data["branch_secret"], now(), True
        st.cloud_high_water, st.last_error, st.halted_reason = data.get("high_water", 0), None, None
        db.commit()
    return {"cloud_branch_id": data["cloud_branch_id"]}


def client_for(st: SyncState, http: httpx.Client | None) -> CloudClient:
    return CloudClient(st.cloud_url, str(st.cloud_branch_id), st.branch_secret, http)


# ───────────────────────── snapshot ─────────────────────────
def create_snapshot(SessionMaker, reason: str) -> int:
    """Write the full current state as ordered snapshot events, superseding unsent changesets
    (their effects are contained in the snapshot). Done under the outbox lock, so no transaction
    can commit in between."""
    with SessionMaker() as db:
        db.info["capture"] = False
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": OUTBOX_LOCK})
        branch_id = db.scalar(select(Branch.id).limit(1))
        db.execute(update(OutboxEvent).where(OutboxEvent.status.in_(["pending", "retry", "failed"]))
                   .values(status="superseded", last_error=f"superseded by snapshot ({reason})"))
        chunks, current = [], []
        for name in REPLICATED_TABLES:
            table: Table = Base.metadata.tables[name]
            excluded = EXCLUDED_COLUMNS.get(name, {})
            cols = [c for c in table.columns if c.name not in excluded]
            pk = list(table.primary_key.columns)
            for row in db.execute(select(*cols).order_by(*pk)).mappings():
                current.append({"t": name, "op": "upsert", "row": {k: to_json(v) for k, v in row.items()}})
                if len(current) >= SNAPSHOT_CHUNK:
                    chunks.append(current)
                    current = []
        chunks.append(current)
        snapshot_id = str(uuid.uuid4())
        for i, rows in enumerate(chunks):
            db.add(OutboxEvent(branch_id=branch_id, kind=SNAPSHOT, topic="snapshot", schema_version=SCHEMA_VERSION,
                               payload={"reset": i == 0, "snapshot_id": snapshot_id, "part": i + 1,
                                        "parts": len(chunks), "reason": reason, "rows": rows}, status="pending"))
            db.flush()
        st = db.get(SyncState, 1)
        st.needs_snapshot = False
        db.commit()
        return len(chunks)


# ───────────────────────── push ─────────────────────────
def queue_status(db: Session) -> dict:
    counts = dict(db.execute(select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)).all())
    oldest = db.scalar(select(func.min(OutboxEvent.created_at)).where(OutboxEvent.status.in_(["pending", "retry", "failed"])))
    files = db.scalar(select(func.count()).select_from(StoredFile).where(StoredFile.cloud_verified_at.is_(None)))
    return {"pending": counts.get("pending", 0), "retry": counts.get("retry", 0), "failed": counts.get("failed", 0),
            "acked": counts.get("acked", 0), "superseded": counts.get("superseded", 0),
            "oldest_unsent_at": oldest.isoformat() if oldest else None, "files_pending": files}


def _event_json(e: OutboxEvent) -> dict:
    return enc({"id": e.id, "sequence": e.sequence, "kind": e.kind, "topic": e.topic, "device_id": e.device_id,
                "user_id": e.user_id, "schema_version": e.schema_version, "created_at": e.created_at,
                "payload": e.payload})


def push(SessionMaker, client: CloudClient, max_batches: int = 50) -> dict:
    batch_size = get_settings().sync_batch_size
    sent = acked = 0
    stopped = None
    heartbeat_sent = False
    for _ in range(max_batches):
        with SessionMaker() as db:
            head = db.scalar(select(OutboxEvent).where(OutboxEvent.status.in_(["pending", "retry", "failed"]))
                             .order_by(OutboxEvent.sequence).limit(1))
            if head is None:
                if heartbeat_sent or sent:
                    break
                # nothing to send: heartbeat, so the cloud sees us and we learn its high-water mark
                # (if the cloud was restored from an older backup, this re-queues events)
                heartbeat_sent = True
                resp = client.call("POST", "/events", json_body={"events": [], "status": queue_status(db)})
                db.close()
                if _reconcile_high_water(SessionMaker, resp.get("high_water")):
                    continue
                break
            if head.status == "failed":
                stopped = {"reason": "failed_event_blocks_queue", "event_id": str(head.id), "detail": head.last_error}
                break
            if head.status == "retry" and head.next_attempt_at and head.next_attempt_at > now():
                stopped = {"reason": "backoff", "until": head.next_attempt_at.isoformat()}
                break
            batch = db.scalars(select(OutboxEvent).where(OutboxEvent.status.in_(["pending", "retry", "failed"]))
                               .order_by(OutboxEvent.sequence).limit(batch_size)).all()
            batch = batch[:next((i for i, e in enumerate(batch) if e.status == "failed"), len(batch))]
            body = {"events": [_event_json(e) for e in batch], "status": queue_status(db)}
            ids = [e.id for e in batch]
        try:
            resp = client.call("POST", "/events", json_body=body)
        except SyncError as e:
            # network/cloud outage: back off and retry forever — never "failed" for connectivity
            _mark_failure(SessionMaker, ids[0], str(e), count_attempt=True, permanent_after=False)
            raise
        sent += len(ids)
        acked += _record_acks(SessionMaker, client.secret, resp.get("acks", []))
        _reconcile_high_water(SessionMaker, resp.get("high_water"))
        if resp.get("stopped"):
            stopped = resp["stopped"]
            if stopped.get("event_id") and stopped["reason"] in ("apply_failed", "schema_version"):
                _mark_failure(SessionMaker, uuid.UUID(stopped["event_id"]), f"{stopped['reason']}: {stopped.get('detail')}",
                              count_attempt=True, permanent_after=stopped["reason"] == "apply_failed")
            if stopped["reason"] in ("halted", "sequence_regression"):
                with SessionMaker() as db:
                    db.get(SyncState, 1).halted_reason = stopped.get("detail")
                    db.commit()
            break
        if len(ids) < batch_size:
            break
    return {"sent": sent, "acked": acked, "stopped": stopped}


def _record_acks(SessionMaker, secret: str, acks: list[dict]) -> int:
    n = 0
    with SessionMaker() as db:
        for a in acks:
            if not signing.hmac.compare_digest(signing.ack_signature(secret, a["id"], int(a["sequence"])), a.get("signature", "")):
                log.warning("ignoring ack with an invalid signature for %s", a["id"])
                continue
            n += db.execute(update(OutboxEvent).where(OutboxEvent.id == uuid.UUID(a["id"]),
                                                      OutboxEvent.sequence == int(a["sequence"]),
                                                      OutboxEvent.status != "acked")
                            .values(status="acked", acked_at=now(), ack_signature=a["signature"], last_error=None)).rowcount
        db.commit()
    return n


def _mark_failure(SessionMaker, event_id: uuid.UUID, error: str, count_attempt: bool, permanent_after: bool = True):
    with SessionMaker() as db:
        e = db.get(OutboxEvent, event_id, with_for_update=True)
        if e is None or e.status == "acked":
            return
        if count_attempt:
            e.attempts += 1
        e.last_error = error[:1000]
        if permanent_after and e.attempts >= MAX_ATTEMPTS:
            e.status = "failed"
        else:
            e.status, e.next_attempt_at = "retry", now() + backoff(e.attempts)
        db.commit()


def _reconcile_high_water(SessionMaker, high_water: int | None) -> int:
    """If the cloud reports it has LESS than we know was acknowledged (cloud restored from an
    older backup), re-queue everything after its high-water mark. Nothing acked is ever deleted
    before the retention period, so this is always possible within it."""
    if high_water is None:
        return 0
    n = 0
    with SessionMaker() as db:
        st = db.get(SyncState, 1, with_for_update=True)
        st.cloud_high_water = high_water
        max_acked = db.scalar(select(func.max(OutboxEvent.sequence)).where(OutboxEvent.status == "acked")) or 0
        if max_acked > high_water:
            oldest_needed = db.scalar(select(func.min(OutboxEvent.sequence)).where(
                OutboxEvent.sequence > high_water, OutboxEvent.status.in_(["acked", "pending", "retry", "failed"])))
            n = db.execute(update(OutboxEvent).where(OutboxEvent.sequence > high_water, OutboxEvent.status == "acked")
                           .values(status="pending", acked_at=None, ack_signature=None, attempts=0,
                                   last_error="re-queued: cloud high-water mark went backwards")).rowcount
            log.warning("cloud high-water %s < acked %s: re-queued %s events", high_water, max_acked, n)
            if oldest_needed is None:
                st.needs_snapshot = True
        db.commit()
    return n


# ───────────────────────── commands ─────────────────────────
def pull_commands(SessionMaker, client: CloudClient) -> dict:
    from .commands import apply_command
    results: dict[str, int] = {}
    with SessionMaker() as db:
        after = db.get(SyncState, 1).last_command_seq
        branch_id = db.scalar(select(Branch.id).limit(1))
    for cmd in client.call("GET", "/commands", params={"after": after}):
        status = apply_command(SessionMaker, branch_id, cmd)
        results[status] = results.get(status, 0) + 1
    return results


# ───────────────────────── files ─────────────────────────
def upload_files(SessionMaker, client: CloudClient, limit: int = 20) -> dict:
    from ..services.files import path_for
    done = waiting = 0
    with SessionMaker() as db:
        files = db.scalars(select(StoredFile).where(StoredFile.cloud_verified_at.is_(None))
                           .order_by(StoredFile.created_at).limit(limit)).all()
        db.expunge_all()
    for f in files:
        path = path_for(f)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != f.sha256:
            log.error("local file %s is corrupt; not uploading", f.id)
            continue
        status = client.call("GET", f"/files/{f.id}")
        offset = status.get("received_bytes", 0) if status.get("status") != "verified" else len(data)
        while offset < len(data):
            chunk = data[offset:offset + FILE_CHUNK]
            try:
                r = client.call("PUT", f"/files/{f.id}", content=chunk,
                                params={"offset": offset, "sha256": f.sha256, "size": len(data)})
            except SyncError as e:
                if getattr(e, "status", None) == 409 and "received_bytes" in getattr(e, "body", {}):
                    offset = e.body["received_bytes"]  # resume where the cloud is
                    continue
                raise
            offset = r["received_bytes"]
        try:
            r = client.call("POST", f"/files/{f.id}/complete")
        except SyncError as e:
            if getattr(e, "status", None) == 409:
                waiting += 1  # e.g. metadata not synced yet — try next cycle
                continue
            raise
        if r.get("receipt") == signing.file_receipt(client.secret, str(f.id), f.sha256):
            with SessionMaker() as db:
                db.execute(update(StoredFile).where(StoredFile.id == f.id).values(cloud_verified_at=now()))
                db.commit()
            done += 1
    return {"verified": done, "waiting": waiting}


# ───────────────────────── one full cycle ─────────────────────────
def run_cycle(SessionMaker, http: httpx.Client | None = None) -> dict:
    with SessionMaker() as lockdb:
        if not lockdb.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": RUNNER_LOCK}).scalar():
            return {"skipped": "another sync runner is active"}
        try:
            return _cycle(SessionMaker, http)
        finally:
            lockdb.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": RUNNER_LOCK})
            lockdb.commit()


def _cycle(SessionMaker, http) -> dict:
    with SessionMaker() as db:
        st = db.get(SyncState, 1)
        if not st.registered_at:
            return {"state": "not_registered"}
        if st.halted_reason:
            return {"state": "halted", "reason": st.halted_reason}
        client = client_for(st, http)
        needs_snapshot = st.needs_snapshot
    out: dict = {"state": "ok"}
    try:
        if needs_snapshot:
            out["snapshot_parts"] = create_snapshot(SessionMaker, "initial or recovery")
        out["push"] = push(SessionMaker, client)
        with SessionMaker() as db:
            halted = db.get(SyncState, 1).halted_reason
        if not halted:
            out["commands"] = pull_commands(SessionMaker, client)
            if out["commands"]:
                out["push_after_commands"] = push(SessionMaker, client)
            out["files"] = upload_files(SessionMaker, client)
        _housekeeping(SessionMaker)
        _set_state(SessionMaker, error=None, success=True)
    except SyncError as e:
        out.update(state="error", error=str(e))
        _set_state(SessionMaker, error=str(e), success=False)
    return out


def _set_state(SessionMaker, error: str | None, success: bool) -> None:
    with SessionMaker() as db:
        st = db.get(SyncState, 1, with_for_update=True)
        st.last_attempt_at, st.last_error = now(), error
        if success:
            st.last_success_at = now()
        db.commit()


def _housekeeping(SessionMaker) -> None:
    """Delete only ACKED (or superseded) events older than the retention period."""
    cutoff = now() - timedelta(days=get_settings().outbox_retention_days)
    with SessionMaker() as db:
        db.execute(delete(OutboxEvent).where(
            or_(and_(OutboxEvent.status == "acked", OutboxEvent.acked_at < cutoff),
                and_(OutboxEvent.status == "superseded", OutboxEvent.created_at < cutoff))))
        db.commit()


# ───────────────────────── operator actions ─────────────────────────
def retry_failed(SessionMaker) -> int:
    with SessionMaker() as db:
        n = db.execute(update(OutboxEvent).where(OutboxEvent.status.in_(["failed", "retry"]))
                       .values(status="pending", attempts=0, next_attempt_at=None)).rowcount
        db.commit()
        return n


def request_resnapshot(SessionMaker) -> None:
    with SessionMaker() as db:
        st = db.get(SyncState, 1, with_for_update=True)
        st.needs_snapshot = True
        db.commit()


# ───────────────────────── branch database recovery ─────────────────────────
def restore_from_cloud(SessionMaker, http: httpx.Client | None = None) -> dict:
    """After restoring the BRANCH database from an older backup, replay the cloud's copy of every
    newer event onto it, so no acknowledged transaction is lost. Run BEFORE starting the app
    (no new local transactions may happen until it completes)."""
    from .apply import apply_rows
    with SessionMaker() as db:
        st = db.get(SyncState, 1)
        if not st.registered_at:
            raise SyncError("Not registered with a cloud: nothing to restore from")
        client = client_for(st, http)
        local_max = db.scalar(select(func.max(OutboxEvent.sequence))) or 0
    applied, after = 0, local_max
    while True:
        events = client.call("GET", "/events", params={"after": after, "limit": 200})
        if not events:
            break
        for ev in events:
            if ev["kind"] == SNAPSHOT:
                raise SyncError("The cloud history after this backup contains a full snapshot; restore the branch "
                                "from a newer backup (see the recovery runbook).")
            with SessionMaker() as db:
                db.info["capture"] = False
                conn = db.connection()
                apply_rows(conn, ev["payload"].get("rows", []))
                db.add(OutboxEvent(id=uuid.UUID(ev["id"]), branch_id=db.scalar(select(Branch.id).limit(1)),
                                   sequence=ev["sequence"], kind=ev["kind"], topic=ev["topic"],
                                   device_id=ev.get("device_id"), user_id=ev.get("user_id"),
                                   schema_version=ev["schema_version"], payload=ev["payload"], status="acked",
                                   acked_at=now(), created_at=datetime.fromisoformat(ev["created_at"]),
                                   last_error="restored from cloud"))
                db.commit()
            applied, after = applied + 1, ev["sequence"]
    with SessionMaker() as db:
        # move sequences past everything the cloud has seen, so new events never collide
        db.execute(text("SELECT setval('outbox_event_seq', GREATEST((SELECT COALESCE(MAX(sequence), 1) FROM outbox_event), "
                        ":hw))"), {"hw": max(after, 1)})
        db.execute(text("SELECT setval('stock_batch_seq', (SELECT COALESCE(MAX(sequence), 1) FROM stock_batch))"))
        st = db.get(SyncState, 1)
        st.halted_reason, st.last_error = None, None
        db.commit()
    return {"events_restored": applied, "now_at_sequence": after}
