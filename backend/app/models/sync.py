import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, created_at_col
from ..ids import uuid7


class StoredFile(Base):
    """A document kept on the branch server (e.g. a receipt photo). Bytes live on local disk;
    metadata replicates. ``cloud_verified_at`` is set ONLY after the cloud confirmed the SHA-256."""

    __tablename__ = "stored_file"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    original_name: Mapped[str] = mapped_column(String(200))
    mime_type: Mapped[str] = mapped_column(String(60))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(200), unique=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device.id"))
    created_at: Mapped[datetime] = created_at_col()
    cloud_verified_at: Mapped[datetime | None]  # local-only column, never replicated
    __table_args__ = (CheckConstraint("size_bytes > 0", name="size"),)


class PurchaseAttachment(Base):
    __tablename__ = "purchase_attachment"
    purchase_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_invoice.id"), primary_key=True)
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stored_file.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="receipt")
    created_at: Mapped[datetime] = created_at_col()


class SyncState(Base):
    """Branch-side sync configuration and progress (single row, id=1). Never replicated."""

    __tablename__ = "sync_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cloud_url: Mapped[str | None] = mapped_column(String(300))
    cloud_branch_id: Mapped[uuid.UUID | None]
    branch_secret: Mapped[str | None] = mapped_column(String(128))
    registered_at: Mapped[datetime | None]
    last_attempt_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    cloud_high_water: Mapped[int | None] = mapped_column(BigInteger)
    last_command_seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    halted_reason: Mapped[str | None] = mapped_column(Text)
    needs_snapshot: Mapped[bool] = mapped_column(default=True, server_default="true")


class InboxEvent(Base):
    """Commands received from the cloud. The primary key makes re-delivery harmless."""

    __tablename__ = "inbox_event"
    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    sequence: Mapped[int | None] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12))  # applied | rejected | conflict
    result: Mapped[dict | None] = mapped_column(JSONB)
    received_at: Mapped[datetime] = created_at_col()
    __table_args__ = (CheckConstraint("status IN ('applied','rejected','conflict')", name="status"),)
