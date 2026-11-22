import os
import uuid

os.environ.setdefault("APP_DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/restaurant_test")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_RATE_LIMIT_API_PER_MINUTE", "1000000")
os.environ.setdefault("APP_RATE_LIMIT_LOGIN_PER_MINUTE", "100000")
os.environ.setdefault("APP_RATE_LIMIT_REGISTER_PER_HOUR", "100000")
import tempfile  # noqa: E402
os.environ.setdefault("APP_FILES_DIR", tempfile.mkdtemp(prefix="kl-files-"))

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_engine

TABLES_SQL = """SELECT string_agg(quote_ident(tablename), ', ') FROM pg_tables
                WHERE schemaname='public' AND tablename <> 'alembic_version'"""

OWNER = {"email": "owner@example.com", "password": "correct-horse-battery"}


@pytest.fixture(scope="session")
def engine():
    here = os.path.dirname(os.path.dirname(__file__))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")  # the real migrations are what the tests run against
    return get_engine()


@pytest.fixture(autouse=True)
def clean_db(request, engine):
    if "engine" not in request.fixturenames and "client" not in request.fixturenames:
        yield
        return
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {c.execute(text(TABLES_SQL)).scalar()} RESTART IDENTITY CASCADE"))
        c.execute(text("INSERT INTO sync_state (id) VALUES (1)"))
    yield


@pytest.fixture
def client(engine):
    from app.main import app
    return TestClient(app)


class Api:
    """Small helper around TestClient with auth and idempotency keys."""

    def __init__(self, client, token, device=True):
        self.c, self.token, self.device = client, token, None
        if device:  # every write must come from a registered device
            r = client.post("/api/v1/devices", json={"name": "test device"},
                            headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 201, r.text
            self.device = r.json()["id"]

    def h(self, key=None, extra=None):
        h = {"Authorization": f"Bearer {self.token}"}
        if self.device:
            h["X-Device-Id"] = self.device
        if key is not False:
            h["Idempotency-Key"] = key or str(uuid.uuid4())
        return {**h, **(extra or {})}

    def get(self, url, **kw):
        return self.c.get("/api/v1" + url, headers=self.h(False), **kw)

    def post(self, url, json=None, key=None, **kw):
        return self.c.post("/api/v1" + url, json=json, headers=self.h(key), **kw)

    def put(self, url, json=None, **kw):
        return self.c.put("/api/v1" + url, json=json, headers=self.h(False), **kw)

    def patch(self, url, json=None, headers=None):
        return self.c.patch("/api/v1" + url, json=json, headers=self.h(False, headers))

    def ok(self, resp, status=(200, 201)):
        assert resp.status_code in ((status,) if isinstance(status, int) else status), resp.text
        return resp.json()


def login(client, email, password, branch_code=None):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password,
                                                "branch_code": branch_code})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture
def owner(client):
    r = client.post("/api/v1/setup", json={
        "branch_code": "KLA", "branch_name": "Kampala Central", "country": "UG", "currency": "USD",
        "owner_name": "Owner", "owner_email": OWNER["email"], "owner_password": OWNER["password"]})
    assert r.status_code == 201, r.text
    return Api(client, login(client, OWNER["email"], OWNER["password"]))


def make_user(owner: Api, role: str) -> Api:
    email = f"{role}-{uuid.uuid4().hex[:6]}@example.com"
    owner.ok(owner.post("/users", {"email": email, "display_name": role.title(),
                                   "password": "a-strong-password", "role": role}))
    return Api(owner.c, login(owner.c, email, "a-strong-password"))


def make_supplier(api: Api, code="FRESH", name="Fresh Foods Ltd") -> dict:
    return api.ok(api.post("/suppliers", {"code": code, "name": name}))


def make_ingredient(api: Api, code="BEEF", name="Beef", dim="mass", pu="kg", cu="g", **kw) -> dict:
    return api.ok(api.post("/ingredients", {"code": code, "name": name, "base_dimension": dim,
                                            "purchase_unit": pu, "consumption_unit": cu, **kw}))


def buy(api: Api, supplier_id, lines, ref=None, day="2026-01-01", **kw) -> dict:
    body = {"supplier_id": supplier_id, "invoice_ref": ref or uuid.uuid4().hex[:8], "invoice_date": day,
            "lines": [{"ingredient_id": i, "quantity": str(q), "unit": u, "unit_price": str(pr)}
                      for i, q, u, pr in lines], **kw}
    return api.ok(api.post("/purchases", body))
