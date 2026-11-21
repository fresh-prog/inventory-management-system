import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, ForeignKey, Integer, Sequence, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, Rate, created_at_col
from ..ids import uuid7


class Branch(Base):
    __tablename__ = "branch"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    country: Mapped[str] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3))
    currency_decimals: Mapped[int] = mapped_column(Integer, default=2)
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Kampala")
    default_target_food_cost_rate: Mapped[Decimal] = mapped_column(Rate, default=Decimal("0.30"))
    include_tax_in_cost: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at_col()


class Device(Base):
    __tablename__ = "device"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    name: Mapped[str] = mapped_column(String(120))
    last_seen_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_col()


class User(Base):
    __tablename__ = "app_user"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower-case
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None]
    token_version: Mapped[int] = mapped_column(Integer, default=1)  # bump to revoke sessions
    created_at: Mapped[datetime] = created_at_col()


class UserBranchRole(Base):
    __tablename__ = "user_branch_role"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"), primary_key=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32))
    __table_args__ = (
        CheckConstraint("role IN ('owner','manager','purchaser','kitchen','viewer')", name="role"),
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    operation: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int | None]
    response: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_col()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branch.id"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(server_default=func.now())


outbox_seq = Sequence("outbox_event_seq", metadata=Base.metadata)


class OutboxEvent(Base):
    """Durable synchronisation record, written in the same transaction as the change.

    Never deleted before a verified acknowledgement (ADR-0003)."""

    __tablename__ = "outbox_event"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    sequence: Mapped[int] = mapped_column(
        BigInteger, outbox_seq, server_default=outbox_seq.next_value(), unique=True)
    topic: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[uuid.UUID | None]
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_col()
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    acked_at: Mapped[datetime | None] = mapped_column(index=True)
