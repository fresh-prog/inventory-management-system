"""Database migration safety: a failing migration leaves the database exactly at the previous
revision (each migration runs in its own transaction), and every migration can be rolled back."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

BACKEND = Path(__file__).resolve().parents[1]

FAILING = '''
"""deliberately failing migration (test only)"""
from alembic import op
import sqlalchemy as sa
revision = "zz_fail"
down_revision = "{head}"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("half_done", sa.Column("id", sa.Integer, primary_key=True))
    op.execute("UPDATE branch SET name = name || ' (corrupted)'")
    raise RuntimeError("simulated failure in the middle of a migration")

def downgrade():
    op.drop_table("half_done")
'''


def _cfg(extra: Path | None = None) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    if extra:
        import os
        cfg.set_main_option("version_locations", os.pathsep.join([str(BACKEND / "migrations" / "versions"), str(extra)]))
        cfg.set_main_option("path_separator", "os")
    return cfg


def _rev(engine):
    with engine.connect() as c:
        return c.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_failed_migration_leaves_previous_revision_intact(engine, owner, tmp_path):
    head = ScriptDirectory.from_config(_cfg()).get_current_head()
    before = _rev(engine)
    assert before == head
    with engine.connect() as c:
        name_before = c.execute(text("SELECT name FROM branch")).scalar()
    (tmp_path / "zz_fail.py").write_text(FAILING.format(head=head))
    with pytest.raises(RuntimeError, match="simulated failure"):
        command.upgrade(_cfg(tmp_path), "heads")
    assert _rev(engine) == before  # still on the previous revision
    assert "half_done" not in inspect(engine).get_table_names()  # nothing half-applied
    with engine.connect() as c:
        assert c.execute(text("SELECT name FROM branch")).scalar() == name_before  # data untouched
    assert owner.ok(owner.get("/reports/integrity"))["ok"]  # application still fully working


def test_every_migration_downgrades_and_upgrades_cleanly(engine):
    cfg = _cfg()
    revs = [s.revision for s in ScriptDirectory.from_config(cfg).walk_revisions()]
    for target in revs[1:] + ["base"]:  # step down one revision at a time
        command.downgrade(cfg, target if target != "base" else "base")
    command.upgrade(cfg, "head")
    assert _rev(engine) == revs[0]
