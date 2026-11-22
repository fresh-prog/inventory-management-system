from datetime import timedelta

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..errors import BusinessRuleError, Conflict, NotFound, Unauthorized
from ..jsonenc import J, enc
from ..models import Branch, User, UserBranchRole
from ..schemas.inputs import BranchSettingsIn, LoginIn, SetupIn, UserIn, UserPatch
from ..security import (
    Principal, create_access_token, get_principal, hash_password, require, verify_password,
)
from ..services.common import now
from ..services.platform import audit

router = APIRouter(tags=["auth & users"])
_DUMMY_HASH = hash_password("timing-equaliser-not-a-password")


@router.get("/setup/status")
def setup_status(db: Session = Depends(get_db)):
    return {"needs_setup": db.scalar(select(func.count()).select_from(User)) == 0}


@router.post("/setup", status_code=201)
def setup(body: SetupIn, db: Session = Depends(get_db)):
    """First-run only: creates the branch and its owner. Refused once any user exists."""
    db.execute(select(func.pg_advisory_xact_lock(424242)))
    if db.scalar(select(func.count()).select_from(User)):
        raise Conflict("Setup has already been completed.")
    br = Branch(code=body.branch_code.upper(), name=body.branch_name, country=body.country.upper(),
                currency=body.currency.upper(), currency_decimals=body.currency_decimals,
                timezone=body.timezone)
    user = User(email=body.owner_email.lower(), display_name=body.owner_name,
                password_hash=hash_password(body.owner_password))
    db.add_all([br, user])
    db.flush()
    db.add(UserBranchRole(user_id=user.id, branch_id=br.id, role="owner"))
    audit(db, None, "setup.complete", "branch", br.id, {"owner": user.email})
    db.commit()
    return {"branch_id": str(br.id), "user_id": str(user.id)}


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    s = get_settings()
    user = db.scalar(select(User).where(User.email == body.email.lower()).with_for_update())
    generic = Unauthorized("Email or password is incorrect.")
    if user is None:
        verify_password(_DUMMY_HASH, body.password)  # equalise timing
        raise generic
    if user.locked_until and user.locked_until > now():
        raise Unauthorized("Too many failed attempts. Try again in a few minutes.")
    if not user.is_active or not verify_password(user.password_hash, body.password):
        user.failed_logins += 1
        if user.failed_logins >= s.login_max_failures:
            user.locked_until = now() + timedelta(minutes=s.login_lock_minutes)
            user.failed_logins = 0
        db.commit()
        raise generic
    user.failed_logins, user.locked_until = 0, None
    q = select(UserBranchRole, Branch).join(Branch, Branch.id == UserBranchRole.branch_id).where(
        UserBranchRole.user_id == user.id)
    if body.branch_code:
        q = q.where(Branch.code == body.branch_code.upper())
    row = db.execute(q.order_by(Branch.code)).first()
    if not row:
        raise Unauthorized("You do not have access to this branch.")
    ubr, branch = row
    token, exp = create_access_token(user, branch.id)
    db.commit()
    return {"access_token": token, "token_type": "bearer", "expires_at": exp.isoformat()}


@router.post("/auth/logout-all", status_code=204)
def logout_all(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    p.user.token_version += 1
    db.commit()


@router.get("/me")
def me(p: Principal = Depends(get_principal)):
    b = p.branch
    return J({
        "user": {"id": p.user.id, "email": p.user.email, "display_name": p.user.display_name},
        "role": p.role, "permissions": sorted(p.permissions),
        "branch": {"id": b.id, "code": b.code, "name": b.name, "country": b.country,
                   "currency": b.currency, "currency_decimals": b.currency_decimals,
                   "default_target_food_cost_rate": b.default_target_food_cost_rate,
                   "include_tax_in_cost": b.include_tax_in_cost},
    })


@router.patch("/settings/branch")
def update_branch(body: BranchSettingsIn, if_match: int | None = Header(default=None),
                  p: Principal = Depends(require("settings.manage")), db: Session = Depends(get_db)):
    b = db.get(Branch, p.branch.id, with_for_update=True)
    if if_match is not None and if_match != b.settings_version:
        raise Conflict("Settings were changed by someone else. Reload and try again.")
    before = {"target": b.default_target_food_cost_rate, "tax": b.include_tax_in_cost, "name": b.name}
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(b, k, v)
    b.settings_version += 1  # remote (cloud) edits based on an older version become conflicts
    audit(db, p, "settings.update", "branch", b.id, {"before": before, "after": body.model_dump(exclude_none=True)})
    db.commit()
    return {"ok": True}


@router.get("/users")
def list_users(p: Principal = Depends(require("user.manage")), db: Session = Depends(get_db)):
    rows = db.execute(select(User, UserBranchRole.role).join(UserBranchRole, UserBranchRole.user_id == User.id)
                      .where(UserBranchRole.branch_id == p.branch.id).order_by(User.display_name)).all()
    return J([{"id": u.id, "email": u.email, "display_name": u.display_name, "role": r,
                 "is_active": u.is_active, "created_at": u.created_at} for u, r in rows])


@router.post("/users", status_code=201)
def create_user(body: UserIn, p: Principal = Depends(require("user.manage")), db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise Conflict("A user with this email already exists.")
    u = User(email=body.email.lower(), display_name=body.display_name, password_hash=hash_password(body.password))
    db.add(u)
    db.flush()
    db.add(UserBranchRole(user_id=u.id, branch_id=p.branch.id, role=body.role))
    audit(db, p, "user.create", "app_user", u.id, {"email": u.email, "role": body.role})
    db.commit()
    return J({"id": u.id}, 201)


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserPatch, p: Principal = Depends(require("user.manage")),
                db: Session = Depends(get_db)):
    ubr = db.scalar(select(UserBranchRole).where(UserBranchRole.user_id == user_id,
                                                 UserBranchRole.branch_id == p.branch.id))
    if not ubr:
        raise NotFound("User not found")
    u = db.get(User, ubr.user_id)
    if u.id == p.user.id and (body.is_active is False or (body.role and body.role != "owner")):
        raise BusinessRuleError("You cannot deactivate or demote yourself.")
    changes = body.model_dump(exclude_none=True, exclude={"password"})
    if body.display_name:
        u.display_name = body.display_name
    if body.role:
        ubr.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
    if body.password:
        u.password_hash = hash_password(body.password)
        changes["password"] = "changed"
    if body.role or body.is_active is not None or body.password:
        u.token_version += 1  # force re-login with new rights
    audit(db, p, "user.update", "app_user", u.id, changes)
    db.commit()
    return {"ok": True}
