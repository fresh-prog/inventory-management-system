import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer, Sequence, String, Text, func,
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
    settings_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = created_at_col()


class Device(Base):
    """A registered phone/tablet/PC. Every posted operation records the device it came from."""

    __tablename__ = "device"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    name: Mapped[str] = mapped_column(String(120))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    registered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    revoked_at: Mapped[datetime | None]
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
    """Durable synchronisation record = one committed business transaction.

    Written in the SAME database transaction as the change it describes (transactional outbox).
    ``sequence`` is assigned under a lock at commit time, so sequence order == commit order.
    Never deleted before a verified (signed) acknowledgement; kept for the retention period after.
    status: pending -> (retry ->)* acked | failed (needs an operator) | superseded (replaced by a snapshot)
    """

    __tablename__ = "outbox_event"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)  # global transaction id
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    sequence: Mapped[int] = mapped_column(
        BigInteger, outbox_seq, server_default=outbox_seq.next_value(), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="changeset", server_default="changeset")
    topic: Mapped[str] = mapped_column(String(64))  # operation type, e.g. purchase.confirmed
    aggregate_id: Mapped[uuid.UUID | None] = mapped_column(index=True)  # per-record sync status
    device_id: Mapped[uuid.UUID | None]
    user_id: Mapped[uuid.UUID | None]
    schema_version: Mapped[str] = mapped_column(String(40), default="", server_default="")
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_col()
    status: Mapped[str] = mapped_column(String(12), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    acked_at: Mapped[datetime | None] = mapped_column(index=True)
    ack_signature: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint("status IN ('pending','retry','failed','acked','superseded')", name="status"),
        Index("ix_outbox_queue", "status", "sequence", postgresql_where="status IN ('pending','retry')"),
    )
