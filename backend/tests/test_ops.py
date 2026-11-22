"""Operations: metrics endpoint for monitoring/alerting."""

import time
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import buy, make_ingredient, make_supplier


def test_metrics_disabled_without_token_and_protected(owner):
    from app.config import Settings
    from app.main import app, create_app
    assert TestClient(app).get("/api/v1/metrics").status_code == 404  # off unless configured
    a = create_app(Settings(metrics_token="s3cret-token"))
    c = TestClient(a)
    assert c.get("/api/v1/metrics").status_code == 401
    assert c.get("/api/v1/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    a.state.engine.dispose()


def test_metrics_report_backlog_backup_age_and_disk(owner, tmp_path):
    from app.config import Settings
    from app.main import create_app
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    buy(owner, sup["id"], [(ing["id"], 1, "kg", 9)])
    (tmp_path / "restaurant-1").mkdir()
    (tmp_path / "restaurant-1" / "MANIFEST").write_text("x")
    a = create_app(Settings(metrics_token="t0ken", backup_dir=str(tmp_path)))
    body = TestClient(a).get("/api/v1/metrics", headers={"Authorization": "Bearer t0ken"}).text
    a.state.engine.dispose()
    m = dict(l.rsplit(" ", 1) for l in body.strip().splitlines())
    assert m["kl_database_up"] == "1"
    assert int(m['kl_sync_events{status="pending"}']) >= 1
    assert 0 <= float(m["kl_last_backup_age_seconds"]) < 60
    assert 0 < float(m["kl_disk_free_ratio"]) <= 1
    assert m["kl_sync_halted"] == "0"
