from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..jsonenc import enc
from ..models import InboxEvent, OutboxEvent, SyncState
from ..security import Principal, require
from ..services.platform import audit
from ..sync import client as sync

router = APIRouter(prefix="/sync", tags=["synchronisation (branch)"])


def _status(db: Session) -> dict:
    st = db.get(SyncState, 1)
    q = sync.queue_status(db)
    if not st.registered_at:
        state = "not_registered"
    elif st.halted_reason:
        state = "halted"
    elif q["failed"]:
        state = "blocked"
    elif st.last_error:
        state = "offline"
    elif q["pending"] or q["retry"] or q["files_pending"]:
        state = "syncing"
    else:
        state = "in_sync"
    return {"state": state, "mode": "local_branch", "registered": bool(st.registered_at), "cloud_url": st.cloud_url,
            "cloud_branch_id": st.cloud_branch_id, "registered_at": st.registered_at,
            "last_attempt_at": st.last_attempt_at, "last_success_at": st.last_success_at,
            "last_error": st.last_error, "halted_reason": st.halted_reason, "cloud_high_water": st.cloud_high_water,
            "needs_snapshot": st.needs_snapshot, "queue": q, "pending_events": q["pending"] + q["retry"] + q["failed"]}


@router.get("/status")
def status(_: Principal = Depends(require("report.read")), db: Session = Depends(get_db)):
    return enc(_status(db))


@router.get("/queue")
def queue(state: str = "unsent", p: Principal = Depends(require("settings.manage")), db: Session = Depends(get_db)):
    states = {"unsent": ["pending", "retry", "failed"], "failed": ["failed"], "recent": ["acked"]}.get(state, [state])
    rows = db.scalars(select(OutboxEvent).where(OutboxEvent.status.in_(states))
                      .order_by(OutboxEvent.sequence.desc() if state == "recent" else OutboxEvent.sequence).limit(100)).all()
    return enc([{"id": e.id, "sequence": e.sequence, "kind": e.kind, "topic": e.topic, "status": e.status,
                 "attempts": e.attempts, "next_attempt_at": e.next_attempt_at, "last_error": e.last_error,
                 "created_at": e.created_at, "acked_at": e.acked_at, "device_id": e.device_id, "user_id": e.user_id,
                 "schema_version": e.schema_version, "rows": len(e.payload.get("rows", []))} for e in rows])


@router.get("/commands")
def commands(p: Principal = Depends(require("settings.manage")), db: Session = Depends(get_db)):
    rows = db.scalars(select(InboxEvent).order_by(InboxEvent.received_at.desc()).limit(50)).all()
    return enc([{"id": c.event_id, "sequence": c.sequence, "type": c.type, "status": c.status, "result": c.result,
                 "received_at": c.received_at} for c in rows])


class RegisterIn(BaseModel):
    cloud_url: str = Field(min_length=8, max_length=300, pattern=r"^https?://")
    registration_code: str = Field(min_length=4, max_length=40)


@router.post("/register")
def register(body: RegisterIn, request: Request, p: Principal = Depends(require("settings.manage")),
             db: Session = Depends(get_db)):
    from ..errors import BusinessRuleError
    try:
        sync.register(request.app.state.Session, body.cloud_url, body.registration_code,
                      getattr(request.app.state, "cloud_http", None))
    except sync.SyncError as e:
        raise BusinessRuleError(str(e))
    audit(db, p, "sync.register", "sync_state", None, {"cloud_url": body.cloud_url})
    db.commit()
    return enc(_status(db))


@router.post("/run")
def run_now(request: Request, p: Principal = Depends(require("settings.manage")), db: Session = Depends(get_db)):
    out = sync.run_cycle(request.app.state.Session, getattr(request.app.state, "cloud_http", None))
    db.expire_all()
    return enc({"result": out, "status": _status(db)})


@router.post("/retry-failed")
def retry_failed(request: Request, p: Principal = Depends(require("settings.manage")), db: Session = Depends(get_db)):
    n = sync.retry_failed(request.app.state.Session)
    audit(db, p, "sync.retry_failed", "outbox_event", None, {"events": n})
    db.commit()
    return {"requeued": n}


@router.post("/resnapshot")
def resnapshot(request: Request, p: Principal = Depends(require("settings.manage")), db: Session = Depends(get_db)):
    """Re-send the full current state (e.g. after a cloud restore beyond the retention window)."""
    sync.request_resnapshot(request.app.state.Session)
    audit(db, p, "sync.resnapshot", "sync_state", None, {})
    db.commit()
    return {"ok": True}
