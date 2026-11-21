"""Cloud control plane (public schema). Branch business data lives in per-branch schemas
(``br_<branch-id-hex>``) that only the sync applier writes to."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, MetaData, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..db import NAMING
from ..ids import uuid7


class CloudBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {datetime: DateTime(timezone=True)}


def _now():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CloudUser(CloudBase):
    __tablename__ = "cloud_user"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16))  # admin | manager | viewer
    branch_scope: Mapped[list | None] = mapped_column(JSONB)  # None = all branches
    mfa_secret: Mapped[str | None] = mapped_column(String(64))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None]
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (CheckConstraint("role IN ('admin','manager','viewer')", name="role"),)


class CloudBranch(CloudBase):
    __tablename__ = "cloud_branch"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), default="awaiting_registration")
    registration_code_hash: Mapped[str | None] = mapped_column(String(64))
    registration_expires_at: Mapped[datetime | None]
    branch_id: Mapped[uuid.UUID | None] = mapped_column(unique=True)  # the branch server's own id
    schema_name: Mapped[str | None] = mapped_column(String(63), unique=True)
    secret_version: Mapped[int] = mapped_column(Integer, default=1)
    currency: Mapped[str | None] = mapped_column(String(3))
    registered_at: Mapped[datetime | None]
    last_seen_at: Mapped[datetime | None]
    last_applied_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    last_event_at: Mapped[datetime | None]  # branch commit time of the newest applied event
    last_schema_version: Mapped[str | None] = mapped_column(String(40))
    branch_reported: Mapped[dict | None] = mapped_column(JSONB)  # queue sizes reported by the branch
    halted_reason: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (CheckConstraint(
        "status IN ('awaiting_registration','active','halted','suspended')", name="status"),)


class CloudInbox(CloudBase):
    """Every event received from a branch (the cloud-side event store). PK = duplicate detection."""

    __tablename__ = "cloud_inbox"
    cloud_branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cloud_branch.id"), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16))
    topic: Mapped[str] = mapped_column(String(64))
    device_id: Mapped[uuid.UUID | None]
    user_id: Mapped[uuid.UUID | None]
    schema_version: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime]  # at the branch
    received_at: Mapped[datetime] = _now()
    row_count: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("cloud_branch_id", "sequence"),)


class BranchCommand(CloudBase):
    """A remote-management request. The BRANCH validates and executes it with its own business
    rules; the result comes back through the normal event stream. Never applied cloud-side."""

    __tablename__ = "branch_command"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    cloud_branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cloud_branch.id"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("cloud_user.id"))
    created_by_name: Mapped[str] = mapped_column(String(254))
    created_at: Mapped[datetime] = _now()
    status: Mapped[str] = mapped_column(String(12), default="queued")
    delivered_at: Mapped[datetime | None]
    resolved_at: Mapped[datetime | None]
    result: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (CheckConstraint(
        "status IN ('queued','delivered','applied','rejected','conflict')", name="status"),)


class CloudFile(CloudBase):
    __tablename__ = "cloud_file"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)  # same id as the branch's stored_file
    cloud_branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cloud_branch.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    received_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(12), default="uploading")
    storage_key: Mapped[str] = mapped_column(String(200))
    verified_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _now()
    __table_args__ = (CheckConstraint("status IN ('uploading','verified')", name="status"),)


class CloudAudit(CloudBase):
    __tablename__ = "cloud_audit"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    at: Mapped[datetime] = _now()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cloud_user.id"))
    cloud_branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cloud_branch.id"))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (Index("ix_cloud_audit_at", "at"),)
