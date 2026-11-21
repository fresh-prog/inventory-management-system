"""MVP acceptance 13: database recovery. Backup -> restore into a fresh DB -> integrity checks pass
and all figures match the original."""

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from conftest import buy, make_ingredient, make_supplier

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("pg_dump") is None, reason="PostgreSQL client tools not installed")


def test_backup_and_restore_round_trip(owner, engine, tmp_path):
    sup = make_supplier(owner)
    beef = make_ingredient(owner)
    buy(owner, sup["id"], [(beef["id"], 10, "kg", 9)], ref="A", day="2026-01-01")
    buy(owner, sup["id"], [(beef["id"], 10, "kg", 11)], ref="B", day="2026-01-02")
    owner.ok(owner.post("/stock/consumptions", {"lines": [{"ingredient_id": beef["id"], "quantity": "15", "unit": "kg"}]}))
    url = engine.url
    env = {**os.environ, "PGHOST": url.host, "PGPORT": str(url.port or 5432), "PGUSER": url.username,
           "PGPASSWORD": url.password, "PGDATABASE": url.database}
    passfile = tmp_path / "pass"
    passfile.write_text("test-passphrase")
    env["BACKUP_PASSPHRASE_FILE"] = str(passfile)
    out = subprocess.run([ROOT / "scripts/backup.sh", tmp_path / "bk"], env=env, check=True,
                         capture_output=True, text=True).stdout.strip()
    assert out.endswith(".enc")
    target = f"restore_{uuid.uuid4().hex[:8]}"
    subprocess.run([ROOT / "scripts/restore.sh", out, target], env=env, check=True, capture_output=True)
    restored = create_engine(url.set(database=target))
    q = ("SELECT (SELECT count(*) FROM stock_movement), (SELECT round(sum(remaining_quantity*unit_cost),4) "
         "FROM stock_batch), (SELECT sum(amount) FROM supplier_ledger_entry), (SELECT count(*) FROM audit_log)")
    try:
        with engine.connect() as a, restored.connect() as b:
            assert a.execute(text(q)).one() == b.execute(text(q)).one()
            assert b.execute(text(q)).one()[1] == 55  # 5 kg @ 11 left
            # append-only triggers survive the restore
            with pytest.raises(Exception, match="append-only"):
                b.execute(text("DELETE FROM stock_movement"))
    finally:
        restored.dispose()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)'))
