"""Change capture into the transactional outbox (branch profile only).

* Mapper events record every INSERT/UPDATE/DELETE of a replicated table, in the unit-of-work's
  dependency order.
* ``before_commit`` flushes, takes a transaction-scoped advisory lock, and writes ONE outbox row
  containing the final row images. The lock is held until COMMIT, so outbox ``sequence`` order is
  exactly commit order (no reordering between concurrent writers).
* Because the outbox row is written in the same transaction, the business change and its sync
  record commit or roll back together.
"""

import uuid

from sqlalchemy import event, inspect, select, text
from sqlalchemy.orm import Mapper, Session, object_session

from ..db import Base
from .rowcodec import to_json
from .spec import CHANGESET, EXCLUDED_COLUMNS, REPLICATED_TABLES, SCHEMA_VERSION

OUTBOX_LOCK = 7_204_118_001
_REPLICATED = set(REPLICATED_TABLES)


def _replicated_columns(mapper: Mapper, table: str):
    excluded = EXCLUDED_COLUMNS.get(table, {})
    return [(attr.key, attr.columns[0].name) for attr in mapper.column_attrs
            if attr.columns[0].table is mapper.local_table and attr.columns[0].name not in excluded]


def _record(op: str):
    def handler(mapper: Mapper, connection, target):
        s = object_session(target)
        if s is None or not s.info.get("capture"):
            return
        table = mapper.local_table.name
        if table not in _REPLICATED:
            return
        if op == "update":
            state = inspect(target)
            if not any(state.attrs[k].history.has_changes() for k, _ in _replicated_columns(mapper, table)):
                return  # only local-only columns changed (e.g. login counters)
        pk = tuple(mapper.primary_key_from_instance(target))
        s.info.setdefault("changes", []).append((op, table, pk, target, mapper))
    return handler


for _op in ("insert", "update", "delete"):
    event.listen(Base, f"after_{_op}", _record(_op), propagate=True)


def image(obj, mapper: Mapper, table: str) -> dict:
    return {col: to_json(getattr(obj, key)) for key, col in _replicated_columns(mapper, table)}


def build_rows(changes) -> list[dict]:
    """Collapse the transaction's mutations to one entry per row, keeping dependency order:
    insert+update → upsert at the insert position · insert+delete → nothing ·
    delete+insert (same key, e.g. links re-saved) → upsert at the re-insert position."""
    ordered: dict = {}
    for op, table, pk, obj, mapper in changes:
        key = (table, pk)
        prev = ordered.get(key)
        if prev is None:
            ordered[key] = (op, obj, mapper)
        elif op == "delete":
            ordered.pop(key)
            if prev[0] != "insert":  # existed before this transaction → must be deleted remotely
                ordered[key] = ("delete", obj, mapper)
        elif prev[0] == "delete":
            ordered.pop(key)  # re-created after being deleted: final state is "exists"
            ordered[key] = ("update", obj, mapper)
    rows = []
    for (table, pk), (op, obj, mapper) in ordered.items():
        if op == "delete":
            names = [c.name for c in mapper.local_table.primary_key.columns]
            rows.append({"t": table, "op": "delete", "pk": {n: to_json(v) for n, v in zip(names, pk)}})
        else:
            rows.append({"t": table, "op": "upsert", "row": image(obj, mapper, table)})
    return rows


@event.listens_for(Session, "before_commit")
def _write_outbox(session: Session):
    if not session.info.get("capture"):
        return
    session.flush()
    changes = session.info.pop("changes", [])
    command_result = session.info.pop("command_result", None)
    if not changes and not command_result:
        session.info.pop("op", None)
        session.info.pop("events", None)
        return
    rows = build_rows(changes)
    ctx = session.info.get("ctx") or {}
    branch_id = ctx.get("branch_id") or next(
        (uuid.UUID(r["row"]["id"]) for r in rows if r["t"] == "branch" and r["op"] == "upsert"), None)
    if branch_id is None:
        from ..models import Branch
        branch_id = session.scalar(select(Branch.id).limit(1))
    if branch_id is None:
        return
    from ..models import OutboxEvent
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": OUTBOX_LOCK})
    events = session.info.pop("events", [])
    payload = {"rows": rows, "events": events}
    if command_result:
        payload["command_result"] = command_result
    session.add(OutboxEvent(
        branch_id=branch_id, kind=CHANGESET, topic=(session.info.pop("op", None) or ctx.get("op") or "change")[:64],
        aggregate_id=uuid.UUID(events[0]["aggregate_id"]) if events and events[0].get("aggregate_id") else None,
        device_id=ctx.get("device_id"), user_id=ctx.get("user_id"), schema_version=SCHEMA_VERSION,
        payload=payload, status="pending"))
    session.flush()


@event.listens_for(Session, "after_rollback")
def _clear_on_rollback(session: Session):
    for k in ("changes", "op", "events", "command_result"):
        session.info.pop(k, None)
