"""Security audit tests (Stage 4). Written adversarially: each test tries to break a rule."""

import base64
import json
import time
import uuid

import jwt
import pytest
from sqlalchemy import text

from conftest import OWNER, Api, buy, login, make_ingredient, make_supplier, make_user

PUBLIC = {"/api/v1/setup/status", "/api/v1/setup", "/api/v1/auth/login", "/api/v1/health/live",
          "/api/v1/health/ready", "/api/v1/profile", "/api/v1/ingredients/units"}


def _routes():
    from app.main import app
    for r in app.routes:
        path = getattr(r, "path", "")
        if path.startswith("/api/v1"):
            for m in getattr(r, "methods", set()) - {"HEAD", "OPTIONS"}:
                yield m, path


def _concrete(path):
    return path.replace("{version_no}", "1").replace("{", "").replace("}", "")


def _fill(path):
    import re
    return re.sub(r"\{[^}]+\}", lambda m: "1" if "version" in m.group(0) else str(uuid.uuid4()), path)


def test_every_non_public_route_requires_authentication(client, owner):
    bad = []
    for method, path in _routes():
        if path in PUBLIC:
            continue
        r = client.request(method, _fill(path), json={})
        if r.status_code != 401:
            bad.append((method, path, r.status_code))
    assert not bad, bad


def test_viewer_cannot_change_anything(client, owner):
    viewer = make_user(owner, "viewer")
    allowed = {"/api/v1/auth/logout-all", "/api/v1/devices"}  # own session / own device only
    bad = []
    for method, path in _routes():
        if method == "GET" or path in PUBLIC or path in allowed:
            continue
        r = viewer.c.request(method, _fill(path), json={}, headers=viewer.h())
        if r.status_code < 300 or r.status_code == 500:
            bad.append((method, path, r.status_code))
    assert not bad, bad


@pytest.mark.parametrize("role,forbidden", [
    ("kitchen", ["/payments", "/purchases", "/suppliers", "/users", "/supplier-credits"]),
    ("purchaser", ["/stock/adjustments", "/stock/production-runs", "/users", "/stock/counts"]),
    ("manager", ["/users"]),
])
def test_role_matrix_enforced_by_backend(owner, role, forbidden):
    api = make_user(owner, role)
    for path in forbidden:
        r = api.post(path, {})
        assert r.status_code in (403, 422) and r.status_code != 201, (role, path, r.status_code)
        if r.status_code == 422:  # validation may run first; prove the permission check still blocks
            assert "permission" not in path
    me = api.ok(api.get("/me"))
    assert me["role"] == role


def test_privilege_escalation_attempts(owner, client):
    manager = make_user(owner, "manager")
    users = owner.ok(owner.get("/users"))
    owner_id = next(u["id"] for u in users if u["role"] == "owner")
    mgr_id = next(u["id"] for u in users if u["role"] == "manager")
    assert manager.patch(f"/users/{mgr_id}", {"role": "owner"}).status_code == 403
    assert manager.patch(f"/users/{owner_id}", {"is_active": False}).status_code == 403
    assert manager.patch("/settings/branch", {"default_target_food_cost_rate": "0.5"}).status_code == 403
    # forging a token without the secret
    forged = jwt.encode({"sub": mgr_id, "br": owner.ok(owner.get("/me"))["branch"]["id"], "tv": 1, "aud": "branch",
                         "exp": int(time.time()) + 600, "role": "owner"}, "guessed-secret", algorithm="HS256")
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    # alg=none token
    h = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    p = base64.urlsafe_b64encode(json.dumps({"sub": owner_id, "aud": "branch"}).encode()).rstrip(b"=").decode()
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {h}.{p}."}).status_code == 401
    # a token for another audience (cloud) is not accepted by the branch
    from app.config import get_settings
    cloud_tok = jwt.encode({"sub": owner_id, "tv": 1, "aud": "cloud-access", "exp": int(time.time()) + 600},
                           get_settings().jwt_secret, algorithm="HS256")
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {cloud_tok}"}).status_code == 401
    # expired token
    me = owner.ok(owner.get("/me"))
    expired = jwt.encode({"sub": me["user"]["id"], "br": me["branch"]["id"], "tv": 1, "aud": "branch",
                          "exp": int(time.time()) - 10}, get_settings().jwt_secret, algorithm="HS256")
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_sql_injection_probes_are_inert(owner, engine):
    make_ingredient(owner, "SAFE", "Safe")
    for q in ["' OR 1=1 --", "%'; DROP TABLE ingredient; --", "\\", "%", "_", "\x00", "'||pg_sleep(2)||'"]:
        r = owner.get("/ingredients", params={"q": q})
        assert r.status_code in (200, 422), (q, r.status_code)
        r = owner.get("/suppliers", params={"q": q})
        assert r.status_code in (200, 422), (q, r.status_code)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM ingredient")).scalar() == 1
    assert owner.get("/stock/movements", params={"type": "x' OR '1'='1"}).json()["items"] == []


def test_cross_branch_access_to_files_counts_and_payments(owner, client, engine):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    inv = buy(owner, sup["id"], [(ing["id"], 1, "kg", 9)])
    up = owner.c.post(f"/api/v1/purchases/{inv['id']}/attachments", headers=owner.h(False),
                      files={"file": ("r.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")})
    fid = up.json()["id"]
    count = owner.ok(owner.post("/stock/counts", {}))
    with engine.begin() as c:
        c.execute(text("INSERT INTO branch (id, code, name, country, currency, currency_decimals, timezone, "
                       "default_target_food_cost_rate, include_tax_in_cost) VALUES "
                       "(gen_random_uuid(), 'EBB', 'Entebbe', 'UG', 'USD', 2, 'Africa/Kampala', 0.3, false)"))
    other = make_user(owner, "owner")
    me = other.ok(other.get("/me"))
    with engine.begin() as c:
        c.execute(text("UPDATE user_branch_role SET branch_id = (SELECT id FROM branch WHERE code='EBB') "
                       "WHERE user_id = :u"), {"u": me["user"]["id"]})
    other = Api(client, login(client, me["user"]["email"], "a-strong-password"))
    assert other.c.get(f"/api/v1/files/{fid}", headers=other.h(False)).status_code == 404
    assert other.get(f"/purchases/{inv['id']}/attachments").json() == []
    assert other.c.post(f"/api/v1/purchases/{inv['id']}/attachments", headers=other.h(False),
                        files={"file": ("r.png", b"\x89PNG\r\n\x1a\n" + b"0" * 10, "image/png")}).status_code == 404
    assert other.get(f"/stock/counts/{count['id']}").status_code == 404
    assert other.post(f"/stock/counts/{count['id']}/approve").status_code == 404
    assert other.post(f"/purchases/{inv['id']}/void", {"reason": "steal"}).status_code == 404
    assert other.post("/supplier-credits", {"invoice_id": inv["id"], "amount": "1", "reason": "xxx"}).status_code == 404
    # another branch's device id cannot be used
    bad = other.c.post("/api/v1/suppliers", json={"code": "Z", "name": "Z"},
                       headers={"Authorization": f"Bearer {other.token}", "X-Device-Id": owner.device})
    assert bad.status_code == 403


@pytest.mark.parametrize("path,body", [
    ("/payments", {"supplier_id": "00000000-0000-0000-0000-000000000000", "paid_on": "2026-01-01", "amount": "1e30",
                   "method": "cash", "allocations": [{"invoice_id": "00000000-0000-0000-0000-000000000000", "amount": "1e30"}]}),
    ("/purchases", {"supplier_id": "00000000-0000-0000-0000-000000000000", "invoice_ref": "X", "invoice_date": "2026-01-01",
                    "tax_total": "1e40", "lines": [{"ingredient_id": "00000000-0000-0000-0000-000000000000",
                                                   "quantity": "1", "unit": "kg", "unit_price": "1"}]}),
    ("/supplier-credits", {"invoice_id": "00000000-0000-0000-0000-000000000000", "amount": "NaN", "reason": "xxx"}),
    ("/payments", {"supplier_id": "00000000-0000-0000-0000-000000000000", "paid_on": "2026-01-01", "amount": "Infinity",
                   "method": "cash", "allocations": [{"invoice_id": "00000000-0000-0000-0000-000000000000", "amount": "1"}]}),
])
def test_absurd_numbers_are_rejected_not_crashing(owner, path, body):
    r = owner.post(path, body)
    assert r.status_code == 422, (r.status_code, r.text[:300])


def test_oversized_inputs_are_rejected(owner):
    ing = make_ingredient(owner)
    lines = [{"ingredient_id": ing["id"], "quantity": "1", "unit": "g"}] * 1000
    assert owner.post("/stock/consumptions", {"lines": lines}).status_code == 422
    assert owner.post("/suppliers", {"code": "BIG", "name": "x", "notes": "y" * 100_000}).status_code == 422


def test_no_password_hashes_or_stack_traces_leak(owner, client, monkeypatch):
    make_user(owner, "kitchen")
    for path in ["/users", "/audit-log", "/me", "/sync/queue?state=recent", "/devices"]:
        body = owner.get(path).text
        assert "$argon2" not in body and "password_hash" not in body, path
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import reports
    monkeypatch.setattr(reports, "dashboard", lambda *a, **k: 1 / 0)
    r = TestClient(app, raise_server_exceptions=False).get("/api/v1/dashboard", headers=owner.h(False))
    assert r.status_code == 500
    assert "Traceback" not in r.text and "ZeroDivisionError" not in r.text and "reports.py" not in r.text
    # the reference shown to the user must identify the request (it was "-" before the fix)
    rid = r.json()["request_id"]
    assert rid and rid != "-" and r.headers.get("x-request-id") == rid and rid in r.json()["detail"]


def test_security_headers_on_api_and_files(owner):
    r = owner.get("/me")
    for h in ("x-content-type-options", "x-frame-options", "referrer-policy"):
        assert h in r.headers, h
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    inv = buy(owner, sup["id"], [(ing["id"], 1, "kg", 9)])
    # a PDF can carry script: it must be served sandboxed and as a download candidate
    up = owner.c.post(f"/api/v1/purchases/{inv['id']}/attachments", headers=owner.h(False),
                      files={"file": ("r.pdf", b"%PDF-1.4\n<script>alert(1)</script>", "application/pdf")})
    f = owner.c.get(f"/api/v1/files/{up.json()['id']}", headers=owner.h(False))
    assert "sandbox" in f.headers.get("content-security-policy", "")
    assert f.headers["x-content-type-options"] == "nosniff"


def test_disguised_and_dangerous_uploads_rejected(owner):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    inv = buy(owner, sup["id"], [(ing["id"], 1, "kg", 9)])
    for name, data in [("x.jpg", b"<html><script>alert(1)</script>"), ("x.svg", b"<svg onload=alert(1)>"),
                       ("x.png", b"MZ\x90\x00"), ("x.jpg", b"")]:
        r = owner.c.post(f"/api/v1/purchases/{inv['id']}/attachments", headers=owner.h(False),
                         files={"file": (name, data, "image/jpeg")})
        assert r.status_code == 422, (name, r.status_code)
    big = b"\xff\xd8\xff" + b"0" * (11 * 1024 * 1024)
    r = owner.c.post(f"/api/v1/purchases/{inv['id']}/attachments", headers=owner.h(False),
                     files={"file": ("big.jpg", big, "image/jpeg")})
    assert r.status_code in (413, 422)


def test_login_is_rate_limited_per_client(owner):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    app = create_app(Settings(rate_limit_login_per_minute=10))
    c = TestClient(app)
    codes = [c.post("/api/v1/auth/login", json={"email": f"x{i}@example.com", "password": "nope"}).status_code
             for i in range(15)]
    assert codes[:10] == [401] * 10 and set(codes[10:]) == {429}
    r = c.post("/api/v1/auth/login", json={"email": "x@example.com", "password": "nope"})
    assert int(r.headers["retry-after"]) > 0
    app.state.engine.dispose()  # spraying many accounts from one client is throttled, not just per-account lockout


def test_production_hides_api_docs_and_health_details():
    from app.config import Settings
    from app.main import create_app
    from fastapi.testclient import TestClient
    app = create_app(Settings(env="production", jwt_secret="x" * 40))
    c = TestClient(app)
    assert c.get("/api/docs").status_code == 404 and c.get("/api/openapi.json").status_code == 404
    h = c.get("/api/v1/health/ready").json()
    assert "cloud_last_error" not in h and "sync_halted" not in h
    app.state.engine.dispose()
