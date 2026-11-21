"""Cloud HTTP API.

/api/v1/cloud/*       — people: remote management and central reporting (password + mandatory TOTP)
/api/v1/cloud-sync/*  — branch servers: HMAC-signed machine endpoints
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Body, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..errors import BusinessRuleError, Forbidden, NotFound, Unauthorized
from ..jsonenc import enc
from ..security import hash_password, verify_password
from ..sync import signing
from . import service as svc
from . import totp
from .models import BranchCommand, CloudAudit, CloudBranch, CloudFile, CloudInbox, CloudUser

people = APIRouter(prefix="/cloud", tags=["cloud: remote management"])
machine = APIRouter(prefix="/cloud-sync", tags=["cloud: branch sync (machine)"])
_DUMMY = hash_password("timing-equaliser")


# ───────────────────────── people auth ─────────────────────────
def _token(u: CloudUser, purpose: str = "access", minutes: int = 60) -> str:
    exp = datetime.now(UTC) + timedelta(minutes=minutes)
    return jwt.encode({"sub": str(u.id), "tv": u.token_version, "aud": f"cloud-{purpose}", "exp": exp},
                      get_settings().jwt_secret, algorithm="HS256")


def _decode(token: str, purpose: str) -> dict:
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"], audience=f"cloud-{purpose}")
    except jwt.PyJWTError:
        raise Unauthorized("Your session has expired. Please sign in again.")


def cloud_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> CloudUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Please sign in.")
    c = _decode(authorization[7:], "access")
    u = db.get(CloudUser, uuid.UUID(c["sub"]))
    if not u or not u.is_active or u.token_version != c.get("tv") or not u.mfa_enabled:
        raise Unauthorized("Please sign in again.")
    return u


def _branch_for(db: Session, u: CloudUser, branch_id: uuid.UUID) -> CloudBranch:
    b = db.get(CloudBranch, branch_id)
    if not b or (u.branch_scope is not None and str(b.id) not in u.branch_scope):
        raise NotFound("Branch not found")
    return b


def _need(u: CloudUser, *roles: str):
    if u.role not in roles:
        raise Forbidden(f"Your cloud role ({u.role}) does not allow this.")


class LoginIn(BaseModel):
    email: str
    password: str
    code: str | None = None


@people.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.scalar(select(CloudUser).where(CloudUser.email == body.email.lower()).with_for_update())
    bad = Unauthorized("Email, password or code is incorrect.")
    if not u:
        verify_password(_DUMMY, body.password)
        raise bad
    if u.locked_until and u.locked_until > datetime.now(UTC):
        raise Unauthorized("Too many failed attempts. Try again later.")
    ok = u.is_active and verify_password(u.password_hash, body.password)
    if ok and u.mfa_enabled:
        if not body.code:
            db.rollback()
            return {"mfa_required": True}
        ok = totp.verify(u.mfa_secret, body.code)
    if not ok:
        u.failed_logins += 1
        if u.failed_logins >= get_settings().login_max_failures:
            u.locked_until, u.failed_logins = datetime.now(UTC) + timedelta(minutes=get_settings().login_lock_minutes), 0
        db.commit()
        raise bad
    u.failed_logins, u.locked_until = 0, None
    db.commit()
    if not u.mfa_enabled:  # remote access always needs MFA: enrol first
        if not u.mfa_secret:
            u.mfa_secret = totp.new_secret()
            db.commit()
        return {"mfa_enrollment_required": True, "enrollment_token": _token(u, "enroll", 10),
                "secret": u.mfa_secret, "otpauth_uri": totp.otpauth_uri(u.mfa_secret, u.email)}
    return {"access_token": _token(u, "access", 60), "token_type": "bearer"}


@people.post("/auth/mfa/enroll")
def enroll(enrollment_token: str = Body(...), code: str = Body(...), db: Session = Depends(get_db)):
    c = _decode(enrollment_token, "enroll")
    u = db.get(CloudUser, uuid.UUID(c["sub"]))
    if not u or not u.mfa_secret or not totp.verify(u.mfa_secret, code):
        raise Unauthorized("The code is not correct. Check the time on your phone and try again.")
    u.mfa_enabled = True
    svc.audit(db, u.id, None, "user.mfa_enrolled")
    db.commit()
    return {"access_token": _token(u, "access", 60), "token_type": "bearer"}


@people.get("/me")
def me(u: CloudUser = Depends(cloud_user)):
    return enc({"id": u.id, "email": u.email, "display_name": u.display_name, "role": u.role, "profile": "cloud"})


# ───────────────────────── branches & monitoring ─────────────────────────
def _health(b: CloudBranch) -> dict:
    rep = b.branch_reported or {}
    stale = b.last_seen_at is None or b.last_seen_at < datetime.now(UTC) - timedelta(minutes=15)
    if b.status == "awaiting_registration":
        state = "awaiting_registration"
    elif b.status == "halted":
        state = "halted"
    elif rep.get("failed", 0) or b.last_error:
        state = "error"
    elif stale:
        state = "offline"
    elif rep.get("pending", 0) or rep.get("retry", 0):
        state = "catching_up"
    else:
        state = "in_sync"
    return {"state": state, "last_seen_at": b.last_seen_at, "data_as_of": b.last_event_at,
            "last_applied_seq": b.last_applied_seq, "branch_queue": rep, "halted_reason": b.halted_reason,
            "last_error": b.last_error, "schema_version": b.last_schema_version}


def _branch_out(b: CloudBranch) -> dict:
    return {"id": b.id, "code": b.code, "name": b.name, "status": b.status, "currency": b.currency,
            "registered_at": b.registered_at, "sync": _health(b)}


@people.get("/branches")
def branches(u: CloudUser = Depends(cloud_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(CloudBranch).order_by(CloudBranch.name)).all()
    return enc([_branch_out(b) for b in rows if u.branch_scope is None or str(b.id) in u.branch_scope])


class BranchIn(BaseModel):
    code: str = Field(min_length=2, max_length=20, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=2, max_length=120)


@people.post("/branches", status_code=201)
def create_branch(body: BranchIn, u: CloudUser = Depends(cloud_user), db: Session = Depends(get_db)):
    _need(u, "admin")
    b, code = svc.create_branch(db, u.id, body.code, body.name)
    db.commit()
    return enc({**_branch_out(b), "registration_code": code,
                "registration_expires_at": b.registration_expires_at})


@people.post("/branches/{branch_id}/registration-code")
def registration_code(branch_id: uuid.UUID, u: CloudUser = Depends(cloud_user), db: Session = Depends(get_db)):
    _need(u, "admin")
    code = svc.new_registration_code(db, u.id, _branch_for(db, u, branch_id))
    db.commit()
    return {"registration_code": code}


@people.post("/branches/{branch_id}/resume")
def resume(branch_id: uuid.UUID, u: CloudUser = Depends(cloud_user), db: Session = Depends(get_db)):
    """Clear a halt after an operator has reconciled the branch (see the recovery runbook)."""
    _need(u, "admin")
    b = _branch_for(db, u, branch_id)
    svc.audit(db, u.id, b.id, "branch.resume", {"halted_reason": b.halted_reason})
    b.status, b.halted_reason = "active", None
    db.commit()
    return {"ok": True}


@people.get("/branches/{branch_id}")
def branch_detail(branch_id: uuid.UUID, request: Request, u: CloudUser = Depends(cloud_user),
                  db: Session = Depends(get_db)):
    b = _branch_for(db, u, branch_id)
    recent = db.scalars(select(CloudInbox).where(CloudInbox.cloud_branch_id == b.id)
                        .order_by(CloudInbox.sequence.desc()).limit(25)).all()
    cmds = db.scalars(select(BranchCommand).where(BranchCommand.cloud_branch_id == b.id)
                      .order_by(BranchCommand.sequence.desc()).limit(25)).all()
    files = db.execute(select(CloudFile.status, func.count()).where(CloudFile.cloud_branch_id == b.id)
                       .group_by(CloudFile.status)).all()
    total_events = db.scalar(select(func.count()).select_from(CloudInbox).where(CloudInbox.cloud_branch_id == b.id))
    return enc({**_branch_out(b), "events_received": total_events, "files": dict(files),
                "recent_events": [{"sequence": e.sequence, "id": e.event_id, "kind": e.kind, "topic": e.topic,
                                   "rows": e.row_count, "created_at": e.created_at, "received_at": e.received_at,
                                   "device_id": e.device_id, "user_id": e.user_id} for e in recent],
                "commands": [_cmd_out(c) for c in cmds]})


@people.get("/branches/{branch_id}/dashboard")
def branch_dashboard(branch_id: uuid.UUID, request: Request, u: CloudUser = Depends(cloud_user),
                     db: Session = Depends(get_db)):
    """The branch's own dashboard, computed from the replica. Labelled with how fresh it is."""
    from ..services import reports
    b = _branch_for(db, u, branch_id)
    with svc.replica_session(request.app.state.Session, b) as rdb:
        d = reports.dashboard(rdb, b.branch_id)
        integrity = reports.integrity_check(rdb, b.branch_id)
    d["sync"] = _health(b)
    d["source"] = "cloud_replica"
    d["integrity_ok"] = integrity["ok"]
    return enc(d)


@people.get("/branches/{branch_id}/replica/{resource}")
def replica_list(branch_id: uuid.UUID, resource: str, request: Request, u: CloudUser = Depends(cloud_user),
                 db: Session = Depends(get_db)):
    """Small read-only lists used by remote-management forms (ingredients, suppliers, recipes, invoices)."""
    from ..models import Branch, Device, Ingredient, PurchaseInvoice, Recipe, Supplier
    from ..services.purchasing import invoice_balance, paid_and_credited
    b = _branch_for(db, u, branch_id)
    with svc.replica_session(request.app.state.Session, b) as r:
        if resource == "ingredients":
            out = [{"id": i.id, "code": i.code, "name": i.name, "version": i.version, "is_active": i.is_active,
                    "purchase_unit": i.purchase_unit} for i in r.scalars(select(Ingredient).order_by(Ingredient.name))]
        elif resource == "suppliers":
            out = [{"id": s.id, "code": s.code, "name": s.name, "version": s.version, "is_active": s.is_active,
                    "phone": s.phone, "contact_person": s.contact_person, "email": s.email,
                    "payment_terms_days": s.payment_terms_days}
                   for s in r.scalars(select(Supplier).order_by(Supplier.name))]
        elif resource == "recipes":
            out = [{"id": x.id, "name": x.name, "versions": [{"version_no": v.version_no, "status": v.status}
                                                             for v in x.versions]} for x in r.scalars(select(Recipe))]
        elif resource == "open-invoices":
            invs = r.scalars(select(PurchaseInvoice).where(PurchaseInvoice.status == "confirmed")
                             .order_by(PurchaseInvoice.invoice_date)).all()
            bal = paid_and_credited(r, [i.id for i in invs])
            out = [{"id": i.id, "supplier_id": i.supplier_id, "supplier": i.supplier.name, "invoice_ref": i.invoice_ref,
                    "invoice_date": i.invoice_date, "total": i.total, **invoice_balance(i, *bal[i.id])}
                   for i in invs]
            out = [o for o in out if o["outstanding"] > 0]
        elif resource == "settings":
            br = r.get(Branch, b.branch_id)
            out = {"settings_version": br.settings_version, "default_target_food_cost_rate": br.default_target_food_cost_rate,
                   "include_tax_in_cost": br.include_tax_in_cost, "name": br.name}
        elif resource == "devices":
            out = [{"id": d.id, "name": d.name, "revoked_at": d.revoked_at, "created_at": d.created_at}
                   for d in r.scalars(select(Device).order_by(Device.created_at))]
        else:
            raise NotFound("Unknown resource")
    return enc(out)


@people.get("/summary")
def central_summary(request: Request, u: CloudUser = Depends(cloud_user), db: Session = Depends(get_db)):
    """Central reporting across branches (per currency — no FX conversion)."""
    from ..services import reports
    out = []
    for b in db.scalars(select(CloudBranch).where(CloudBranch.status.in_(["active", "halted"]))
                        .order_by(CloudBranch.name)):
        if u.branch_scope is not None and str(b.id) not in u.branch_scope:
            continue
        row = {**_branch_out(b)}
        if b.schema_name:
            with svc.replica_session(request.app.state.Session, b) as rdb:
                d = reports.dashboard(rdb, b.branch_id)
            row.update({"inventory_value": d["inventory_value"]["value"], "month_purchases": d["month_purchases"],
                        "outstanding_payables": d["outstanding_payables"], "low_stock": len(d["low_stock"]),
                        "recipes_over_target": len(d["recipes_over_target"]),
                        "consumption_cost": d["consumption"]["total"]})
        out.append(row)
    return enc({"as_of": datetime.now(UTC), "branches": out})


# ───────────────────────── commands (remote management) ─────────────────────────
def _cmd_out(c: BranchCommand) -> dict:
    return {"id": c.id, "sequence": c.sequence, "type": c.type, "payload": c.payload, "status": c.status,
            "created_by": c.created_by_name, "created_at": c.created_at, "delivered_at": c.delivered_at,
            "resolved_at": c.resolved_at, "result": c.result}


class CommandIn(BaseModel):
    type: str
    payload: dict


@people.post("/branches/{branch_id}/commands", status_code=201)
def create_command(branch_id: uuid.UUID, body: CommandIn, request: Request, u: CloudUser = Depends(cloud_user),
                   db: Session = Depends(get_db)):
    _need(u, "admin", "manager")
    b = _branch_for(db, u, branch_id)
    cmd = svc.create_command(db, request.app.state.Session, b, u, body.type, body.payload)
    db.commit()
    return enc(_cmd_out(cmd))


@people.get("/branches/{branch_id}/commands")
def list_commands(branch_id: uuid.UUID, u: CloudUser = Depends(cloud_user), db: Session = Depends(get_db)):
    b = _branch_for(db, u, branch_id)
    return enc([_cmd_out(c) for c in db.scalars(select(BranchCommand).where(BranchCommand.cloud_branch_id == b.id)
                                                 .order_by(BranchCommand.sequence.desc()).limit(200))])


@people.get("/audit")
def cloud_audit(u: CloudUser = Depends(cloud_user), db: Session = Depends(get_db)):
    _need(u, "admin")
    return enc([{"at": a.at, "user_id": a.user_id, "branch_id": a.cloud_branch_id, "action": a.action,
                 "detail": a.detail} for a in db.scalars(select(CloudAudit).order_by(CloudAudit.at.desc()).limit(200))])


# ───────────────────────── machine API (branch servers) ─────────────────────────
async def signed_branch(request: Request, db: Session = Depends(get_db)) -> tuple[CloudBranch, bytes]:
    body = await request.body()
    bid = request.headers.get("x-branch-id")
    try:
        b = db.get(CloudBranch, uuid.UUID(bid or ""))
    except ValueError:
        b = None
    if not b or b.status not in ("active", "halted"):
        raise Unauthorized("Unknown or inactive branch")
    if not signing.verify(svc.branch_secret(b), request.method, request.url.path,
                          request.headers.get("x-timestamp", ""), body, request.headers.get("x-signature", "")):
        raise Unauthorized("Bad signature or clock skew")
    return b, body


class RegisterIn(BaseModel):
    registration_code: str
    branch: dict


@machine.post("/register")
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    b, secret = svc.register(db, request.app.state.engine, body.registration_code, body.branch)
    return enc({"cloud_branch_id": b.id, "branch_secret": secret, "high_water": b.last_applied_seq,
                "schema": b.schema_name})


@machine.post("/events")
def push_events(request: Request, auth=Depends(signed_branch)):
    import json
    b, raw = auth
    data = json.loads(raw or b"{}")
    events = data.get("events", [])
    if len(events) > 500:
        raise BusinessRuleError("At most 500 events per batch")
    return enc(svc.apply_events(request.app.state.engine, request.app.state.Session, b.id, events, data.get("status")))


@machine.get("/commands")
def pull_commands(after: int = Query(0, ge=0), auth=Depends(signed_branch), db: Session = Depends(get_db)):
    b, _ = auth
    b = db.get(CloudBranch, b.id)
    return enc([{"id": c.id, "sequence": c.sequence, "type": c.type, "payload": c.payload,
                 "created_by": c.created_by_name, "created_at": c.created_at}
                for c in svc.pending_commands(db, b, after)])


@machine.get("/files/{file_id}")
def file_status(file_id: uuid.UUID, auth=Depends(signed_branch), db: Session = Depends(get_db)):
    return svc.file_status(db, auth[0], file_id)


@machine.put("/files/{file_id}")
def upload_chunk(file_id: uuid.UUID, offset: int = Query(..., ge=0), sha256: str = Query(..., min_length=64, max_length=64),
                 size: int = Query(..., gt=0), auth=Depends(signed_branch), db: Session = Depends(get_db)):
    b, chunk = auth
    if size > get_settings().max_upload_mb * 1024 * 1024:
        raise BusinessRuleError("File too large")
    return svc.receive_chunk(db, db.get(CloudBranch, b.id), file_id, offset, sha256, size, chunk)


@machine.post("/files/{file_id}/complete")
def complete(file_id: uuid.UUID, request: Request, auth=Depends(signed_branch), db: Session = Depends(get_db)):
    return svc.complete_file(db, db.get(CloudBranch, auth[0].id), request.app.state.Session, file_id)


@machine.get("/events")
def export_events(after: int = Query(0, ge=0), limit: int = Query(200, le=1000), auth=Depends(signed_branch),
                  db: Session = Depends(get_db)):
    """Recovery: the branch fetches the cloud's copy of its own events (restore-from-cloud)."""
    b, _ = auth
    rows = db.scalars(select(CloudInbox).where(CloudInbox.cloud_branch_id == b.id, CloudInbox.sequence > after)
                      .order_by(CloudInbox.sequence).limit(limit)).all()
    return enc([{"id": e.event_id, "sequence": e.sequence, "kind": e.kind, "topic": e.topic, "device_id": e.device_id,
                 "user_id": e.user_id, "schema_version": e.schema_version, "created_at": e.created_at,
                 "payload": e.payload} for e in rows])
