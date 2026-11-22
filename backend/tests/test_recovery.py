"""Backup & disaster recovery: encrypted backup of DB + documents → off-site copy → restore into a
fresh database → checksums, physical index check, business-integrity SQL, figures identical."""

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


def _env(engine, tmp_path):
    url = engine.url
    pw = tmp_path / "pass"
    pw.write_text("test-passphrase")
    return {**os.environ, "PGHOST": url.host, "PGPORT": str(url.port or 5432), "PGUSER": url.username,
            "PGPASSWORD": url.password, "PGDATABASE": url.database, "BACKUP_PASSPHRASE_FILE": str(pw)}


def test_encrypted_backup_offsite_copy_and_verified_restore(owner, engine, tmp_path):
    from app.config import get_settings
    sup = make_supplier(owner)
    beef = make_ingredient(owner)
    inv = buy(owner, sup["id"], [(beef["id"], 10, "kg", 9)], ref="A", day="2026-01-01")
    buy(owner, sup["id"], [(beef["id"], 10, "kg", 11)], ref="B", day="2026-01-02")
    owner.ok(owner.post("/stock/consumptions", {"lines": [{"ingredient_id": beef["id"], "quantity": "15", "unit": "kg"}]}))
    owner.c.post(f"/api/v1/purchases/{inv['id']}/attachments", headers=owner.h(False),
                 files={"file": ("r.png", b"\x89PNG\r\n\x1a\n" + b"1" * 500, "image/png")})
    env = _env(engine, tmp_path)
    env["BACKUP_FILES_DIR"] = get_settings().files_dir
    env["BACKUP_OFFSITE_DIR"] = str(tmp_path / "offsite")
    out = subprocess.run([ROOT / "scripts/backup.sh", tmp_path / "bk"], env=env, check=True,
                         capture_output=True, text=True).stdout.strip()
    bdir = Path(out)
    assert {p.name for p in bdir.iterdir()} == {"db.dump.enc", "files.tar.gz.enc", "SHA256SUMS", "MANIFEST"}
    assert not any(b"PNG" in p.read_bytes()[:4096] for p in bdir.iterdir() if p.suffix == ".enc")  # really encrypted
    offsite = tmp_path / "offsite" / bdir.name
    assert offsite.is_dir()
    r = subprocess.run([ROOT / "scripts/verify_restore.sh", str(offsite)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    for line in ("checksums: ok", "decrypt: ok", "restore: ok", "business integrity: ok", "documents archive: ok", "PASSED"):
        assert line in r.stdout, r.stdout


def test_tampered_backup_is_detected(owner, engine, tmp_path):
    make_supplier(owner)
    env = _env(engine, tmp_path)
    bdir = Path(subprocess.run([ROOT / "scripts/backup.sh", tmp_path / "bk"], env=env, check=True,
                               capture_output=True, text=True).stdout.strip())
    f = bdir / "db.dump.enc"
    data = bytearray(f.read_bytes())
    data[len(data) // 2] ^= 0xFF
    f.write_bytes(bytes(data))
    r = subprocess.run([ROOT / "scripts/verify_restore.sh", str(bdir)], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "PASSED" not in r.stdout


def test_restore_detects_business_corruption(owner, engine, tmp_path):
    """The integrity SQL is an independent second opinion: corrupt a restored copy and it must fail."""
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    buy(owner, sup["id"], [(ing["id"], 10, "kg", 9)])
    env = _env(engine, tmp_path)
    env.pop("BACKUP_PASSPHRASE_FILE")
    bdir = Path(subprocess.run([ROOT / "scripts/backup.sh", tmp_path / "bk"], env=env, check=True,
                               capture_output=True, text=True).stdout.strip())
    target = f"corrupt_{uuid.uuid4().hex[:8]}"
    subprocess.run(["createdb", target], env=env, check=True)
    try:
        subprocess.run(["pg_restore", "--no-owner", "-d", target, str(bdir / "db.dump")], env=env, check=True)
        eng = create_engine(engine.url.set(database=target))
        with eng.begin() as c:
            c.execute(text("UPDATE stock_batch SET remaining_quantity = remaining_quantity - 1"))
        eng.dispose()
        out = subprocess.run(["psql", "-tA", "-f", str(ROOT / "scripts/sql/integrity.sql"), target],
                             env=env, capture_output=True, text=True, check=True).stdout
        assert "batch remaining != original - allocations" in out
    finally:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)'))
