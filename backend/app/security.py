"""Authentication (Argon2id + signed bearer tokens) and role-based permissions (ADR-0008)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .errors import Forbidden, Unauthorized
from .models import Branch, Device, User, UserBranchRole

_hasher = PasswordHasher()  # Argon2id defaults

ALL_PERMISSIONS = {
    "ingredient.read", "ingredient.write",
    "recipe.read", "recipe.write", "recipe.publish",
    "supplier.read", "supplier.write",
    "purchase.read", "purchase.create", "purchase.void",
    "payment.post", "payment.void",
    "stock.read", "stock.consume", "stock.adjust", "stock.override",
    "stock.count", "stock.count.approve", "production.record",
    "report.read", "user.manage", "settings.manage",
}
READ = {p for p in ALL_PERMISSIONS if p.endswith(".read")}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "owner": set(ALL_PERMISSIONS),
    "manager": ALL_PERMISSIONS - {"user.manage", "settings.manage", "payment.void"},
    "purchaser": READ | {"supplier.write", "purchase.create", "payment.post", "ingredient.write"},
    "kitchen": {"ingredient.read", "recipe.read", "recipe.write", "stock.read", "stock.consume",
                "stock.adjust", "stock.count", "production.record"},
    "viewer": READ,
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(user: User, branch_id: uuid.UUID) -> tuple[str, datetime]:
    s = get_settings()
    exp = datetime.now(UTC) + timedelta(minutes=s.access_token_minutes)
    token = jwt.encode(
        {"sub": str(user.id), "br": str(branch_id), "tv": user.token_version, "exp": exp, "aud": "branch"},
        s.jwt_secret, algorithm="HS256")
    return token, exp


@dataclass
class Principal:
    user: User
    branch: Branch
    role: str
    device: "Device | None" = None

    @property
    def permissions(self) -> set[str]:
        return ROLE_PERMISSIONS.get(self.role, set())

    def has(self, perm: str) -> bool:
        return perm in self.permissions

    def require(self, perm: str) -> None:
        if not self.has(perm):
            raise Forbidden(f"Your role ({self.role}) does not allow '{perm}'.")


# Writes allowed without a registered device (bootstrap / session endpoints)
DEVICE_EXEMPT = ("/api/v1/auth/", "/api/v1/devices", "/api/v1/setup")


def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_device_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Please sign in.")
    try:
        claims = jwt.decode(authorization[7:], get_settings().jwt_secret, algorithms=["HS256"],
                            audience="branch")
        user_id, branch_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["br"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise Unauthorized("Your session has expired. Please sign in again.")
    user = db.get(User, user_id)
    if not user or not user.is_active or user.token_version != claims.get("tv"):
        raise Unauthorized("Your session is no longer valid. Please sign in again.")
    ubr = db.scalar(select(UserBranchRole).where(
        UserBranchRole.user_id == user_id, UserBranchRole.branch_id == branch_id))
    if not ubr:
        raise Forbidden("You do not have access to this branch.")
    device = None
    if x_device_id:
        try:
            device = db.get(Device, uuid.UUID(x_device_id))
        except ValueError:
            device = None
        if device is None or device.branch_id != branch_id:
            raise Forbidden("This device is not registered. Please sign in again.", code_hint="DEVICE_UNKNOWN")
        if device.revoked_at is not None:
            raise Forbidden("This device has been revoked by the owner.", code_hint="DEVICE_REVOKED")
    elif request.method not in ("GET", "HEAD", "OPTIONS") and not request.url.path.startswith(DEVICE_EXEMPT):
        raise Forbidden("Changes must come from a registered device (X-Device-Id header).",
                        code_hint="DEVICE_REQUIRED")
    p = Principal(user=user, branch=db.get(Branch, branch_id), role=ubr.role, device=device)
    # identification attached to every captured transaction (outbox record)
    db.info["ctx"] = {"branch_id": branch_id, "user_id": user.id, "device_id": device.id if device else None,
                      "op": f"{request.method} {getattr(request.scope.get('route'), 'path', request.url.path)}"}
    return p


def require(perm: str):
    def dep(p: Principal = Depends(get_principal)) -> Principal:
        p.require(perm)
        return p
    return dep
