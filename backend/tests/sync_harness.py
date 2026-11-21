"""Test harness: a real cloud server (uvicorn, own database) + a fault-injecting network."""

import json
import os
import socket
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text

from app.cloud import totp
from app.cloud.models import CloudUser
from app.config import Settings
from app.db import Base
from app.security import hash_password
from app.sync.rowcodec import to_json
from app.sync.spec import EXCLUDED_COLUMNS, REPLICATED_TABLES

CLOUD_URL = os.environ.get("APP_CLOUD_TEST_DATABASE_URL",
                           "postgresql+psycopg://app:app@localhost:5432/restaurant_cloud_test")
BACKEND = Path(__file__).resolve().parents[1]
ADMIN = {"email": "hq@example.com", "password": "cloud-admin-password", "secret": totp.new_secret()}


def prepare_cloud_db():
    eng = create_engine(CLOUD_URL)
    with eng.connect() as c:
        schemas = c.execute(text("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'br\\_%'")).scalars().all()
    for schema in schemas:  # one transaction per schema (lock table limits)
        with eng.begin() as c:
            c.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
    with eng.begin() as c:
        c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    cfg = Config(str(BACKEND / "alembic_cloud.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations_cloud"))
    cfg.attributes["url"] = CLOUD_URL
    command.upgrade(cfg, "head")
    with eng.begin() as c:
        c.execute(CloudUser.__table__.insert().values(
            id=__import__("uuid").uuid4(), email=ADMIN["email"], display_name="HQ", role="admin",
            password_hash=hash_password(ADMIN["password"]), mfa_secret=ADMIN["secret"], mfa_enabled=True,
            is_active=True, failed_logins=0, token_version=1))
    return eng


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CloudServer:
    def __init__(self):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.server = self.thread = self.app = None

    def start(self):
        from app.main import create_app
        self.app = create_app(Settings(deployment_profile="cloud", env="test"), CLOUD_URL)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port,
                                                    log_level="warning", lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                return self
            time.sleep(0.02)
        raise RuntimeError("cloud server did not start")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(10)
        self.app.state.engine.dispose()

    def restart(self):
        self.stop()
        self.start()


class Flaky(httpx.BaseTransport):
    """Network between branch and cloud. ``rule(request) -> None | 'down' | 'drop'``:
    'down' = connection refused before reaching the cloud; 'drop' = the cloud processes the request
    but the response is lost (e.g. timeout) — the nastiest case for acknowledgements."""

    def __init__(self):
        self.inner = httpx.HTTPTransport()
        self.rule = None
        self.log: list[tuple[str, str, dict]] = []

    def handle_request(self, request):
        self.log.append((request.method, request.url.path, dict(request.url.params)))
        action = self.rule(request) if self.rule else None
        if action == "down":
            raise httpx.ConnectError("network unreachable (simulated)", request=request)
        resp = self.inner.handle_request(request)
        if action == "drop":
            resp.read()
            raise httpx.ReadTimeout("response lost (simulated)", request=request)
        return resp


def cloud_api(cloud: CloudServer):
    c = httpx.Client(base_url=cloud.base + "/api/v1", timeout=60)
    r = c.post("/cloud/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"],
                                          "code": totp.code_at(ADMIN["secret"], time.time())})
    assert r.status_code == 200 and "access_token" in r.json(), r.text
    c.headers["Authorization"] = "Bearer " + r.json()["access_token"]
    return c


def images(engine, schema: str | None = None) -> dict[str, list[str]]:
    """Every replicated row (minus local-only columns) as canonical JSON — for exact comparison."""
    out = {}
    with engine.connect() as c:
        if schema:
            c.exec_driver_sql(f'SET search_path TO "{schema}"')
        for name in REPLICATED_TABLES:
            t = Base.metadata.tables[name]
            cols = [col for col in t.columns if col.name not in EXCLUDED_COLUMNS.get(name, {})]
            rows = c.execute(select(*cols)).mappings().all()
            out[name] = sorted(json.dumps({k: to_json(v) for k, v in r.items()}, sort_keys=True) for r in rows)
    return out


def assert_converged(branch_engine, cloud_engine, schema: str):
    a, b = images(branch_engine), images(cloud_engine, schema)
    diff = {t: (len(a[t]), len(b[t])) for t in a if a[t] != b[t]}
    assert not diff, f"replica differs from branch (branch rows, cloud rows): {diff}"
    return sum(len(v) for v in a.values())
