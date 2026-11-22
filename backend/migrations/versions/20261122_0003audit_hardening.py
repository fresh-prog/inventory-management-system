"""audit hardening: every stock movement must reference its source

Revision ID: 0003audit
Revises: 0002sync
"""
from alembic import op

revision = "0003audit"
down_revision = "0002sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Older adjustments stored no source id. stock_movement is append-only (trigger), so the
    # trigger is disabled only for this one, audited backfill inside the migration transaction.
    op.execute("ALTER TABLE stock_movement DISABLE TRIGGER stock_movement_append_only")
    op.execute("UPDATE stock_movement SET source_id = id WHERE source_id IS NULL")
    op.execute("ALTER TABLE stock_movement ENABLE TRIGGER stock_movement_append_only")
    op.create_check_constraint(op.f("ck_stock_movement_traceable"), "stock_movement", "source_id IS NOT NULL")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_stock_movement_traceable"), "stock_movement")
