"""hybrid sync: richer outbox, devices, local files, sync state, inbox

Revision ID: 0002sync
Revises: 511ffdd9a91e
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002sync"
down_revision: Union[str, Sequence[str], None] = "511ffdd9a91e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # outbox: identification + queue state (sequence remains commit-ordered)
    with op.batch_alter_table("outbox_event") as b:
        b.add_column(sa.Column("kind", sa.String(16), nullable=False, server_default="changeset"))
        b.add_column(sa.Column("device_id", sa.Uuid(), nullable=True))
        b.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
        b.add_column(sa.Column("schema_version", sa.String(40), nullable=False, server_default=""))
        b.add_column(sa.Column("status", sa.String(12), nullable=False, server_default="pending"))
        b.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("last_error", sa.Text(), nullable=True))
        b.add_column(sa.Column("ack_signature", sa.String(64), nullable=True))
    op.create_check_constraint(op.f("ck_outbox_event_status"), "outbox_event",
                               "status IN ('pending','retry','failed','acked','superseded')")
    op.create_index("ix_outbox_queue", "outbox_event", ["status", "sequence"],
                    postgresql_where=sa.text("status IN ('pending','retry')"))
    # events written before change capture existed carry no row images: a snapshot replaces them
    op.execute("UPDATE outbox_event SET status = 'superseded' WHERE acked_at IS NULL")

    op.add_column("branch", sa.Column("settings_version", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("device") as b:
        b.add_column(sa.Column("user_agent", sa.String(300), nullable=True))
        b.add_column(sa.Column("registered_by", sa.Uuid(), sa.ForeignKey("app_user.id",
                     name="fk_device_registered_by_app_user"), nullable=True))
        b.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "stored_file",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branch.id", name="fk_stored_file_branch_id_branch"), nullable=False),
        sa.Column("original_name", sa.String(200), nullable=False),
        sa.Column("mime_type", sa.String(60), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(200), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), sa.ForeignKey("app_user.id", name="fk_stored_file_uploaded_by_app_user"), nullable=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("device.id", name="fk_stored_file_device_id_device"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cloud_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_stored_file_size")),
        sa.UniqueConstraint("storage_key", name="uq_stored_file_storage_key"),
    )
    op.create_table(
        "purchase_attachment",
        sa.Column("purchase_id", sa.Uuid(), sa.ForeignKey("purchase_invoice.id", name="fk_purchase_attachment_purchase_id_purchase_invoice"), primary_key=True),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("stored_file.id", name="fk_purchase_attachment_file_id_stored_file"), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cloud_url", sa.String(300)),
        sa.Column("cloud_branch_id", sa.Uuid()),
        sa.Column("branch_secret", sa.String(128)),
        sa.Column("registered_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("cloud_high_water", sa.BigInteger()),
        sa.Column("last_command_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("halted_reason", sa.Text()),
        sa.Column("needs_snapshot", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.execute("INSERT INTO sync_state (id) VALUES (1)")
    op.create_table(
        "inbox_event",
        sa.Column("source", sa.String(20), primary_key=True),
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("sequence", sa.BigInteger()),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('applied','rejected','conflict')", name=op.f("ck_inbox_event_status")),
    )


def downgrade() -> None:
    op.drop_table("inbox_event")
    op.drop_table("sync_state")
    op.drop_table("purchase_attachment")
    op.drop_table("stored_file")
    with op.batch_alter_table("device") as b:
        b.drop_column("revoked_at")
        b.drop_column("registered_by")
        b.drop_column("user_agent")
    op.drop_column("branch", "settings_version")
    op.drop_index("ix_outbox_queue", table_name="outbox_event")
    op.drop_constraint(op.f("ck_outbox_event_status"), "outbox_event")
    with op.batch_alter_table("outbox_event") as b:
        for c in ("ack_signature", "last_error", "next_attempt_at", "status", "schema_version",
                  "user_id", "device_id", "kind"):
            b.drop_column(c)
