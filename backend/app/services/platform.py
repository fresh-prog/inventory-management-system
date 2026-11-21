"""Audit trail, transactional outbox and idempotency helpers."""

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any

from ..jsonenc import enc as jsonable_encoder
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..errors import BusinessRuleError, Conflict
from ..models import AuditLog, IdempotencyRecord
from ..security import Principal


def audit(db: Session, p: Principal | None, action: str, entity: str,
          entity_id: uuid.UUID | None, detail: dict | None = None) -> None:
    db.add(AuditLog(
        branch_id=p.branch.id if p else None, user_id=p.user.id if p else None,
        action=action, entity=entity, entity_id=entity_id,
        detail=jsonable_encoder(detail) if detail else None))


def emit(db: Session, branch_id: uuid.UUID, topic: str, aggregate_id: uuid.UUID | None,
         payload: dict) -> None:
    """Name the business operation of the current transaction. The change capture writes the
    outbox record (with full row images) at commit, atomically with the change itself."""
    db.info.setdefault("op", topic)
    db.info.setdefault("events", []).append(
        {"topic": topic, "aggregate_id": str(aggregate_id) if aggregate_id else None,
         "summary": jsonable_encoder(payload)})


def _hash(body: Any) -> str:
    return hashlib.sha256(
        json.dumps(jsonable_encoder(body), sort_keys=True).encode()).hexdigest()


def run_idempotent(
    db: Session, p: Principal, key: str | None, operation: str, body: Any,
    fn: Callable[[], Any], status_code: int = 201,
) -> tuple[int, Any, bool]:
    """Execute ``fn`` at most once per (branch, key). Returns (status, response, replayed).

    The key row is inserted first in the same transaction. A concurrent request with
    the same key blocks on the unique index until the first commits, then replays its
    stored response. If the first rolls back, the second proceeds normally.
    """
    if not key or len(key) < 8 or len(key) > 128:
        raise BusinessRuleError("An Idempotency-Key header (8-128 chars) is required.")
    req_hash = _hash({"op": operation, "body": body})
    inserted = db.execute(
        insert(IdempotencyRecord).values(
            branch_id=p.branch.id, key=key, operation=operation, request_hash=req_hash)
        .on_conflict_do_nothing().returning(IdempotencyRecord.key)
    ).scalar() is not None
    if not inserted:
        rec = db.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.branch_id == p.branch.id, IdempotencyRecord.key == key))
        db.rollback()
        if rec is None or rec.request_hash != req_hash or rec.operation != operation:
            raise Conflict("This Idempotency-Key was already used for a different request.")
        return rec.status_code or 200, rec.response, True
    result = jsonable_encoder(fn())
    db.execute(
        IdempotencyRecord.__table__.update()
        .where(IdempotencyRecord.branch_id == p.branch.id, IdempotencyRecord.key == key)
        .values(status_code=status_code, response=result))
    db.commit()
    return status_code, result, False
