"""Authentication, roles and branch isolation (MVP acceptance 12: no unauthorised changes)."""

from conftest import OWNER, Api, buy, login, make_ingredient, make_supplier, make_user


def test_setup_runs_once_and_login_works(client, owner):
    assert client.get("/api/v1/setup/status").json() == {"needs_setup": False}
    r = client.post("/api/v1/setup", json={
        "branch_code": "XX", "branch_name": "Other", "country": "UG", "currency": "USD",
        "owner_name": "Mallory", "owner_email": "m@example.com", "owner_password": "0123456789ab"})
    assert r.status_code == 409
    me = owner.ok(owner.get("/me"))
    assert me["role"] == "owner" and "payment.void" in me["permissions"]


def test_wrong_password_is_generic_and_locks_out(client, owner):
    for _ in range(5):
        r = client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": "nope"})
        assert r.status_code == 401
        assert "incorrect" in r.json()["detail"] or "Too many" in r.json()["detail"]
    r = client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    assert r.status_code == 401 and "Too many" in r.json()["detail"]
    r = client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": "x"})
    assert r.status_code == 401


def test_requires_token(client, owner):
    assert client.get("/api/v1/ingredients").status_code == 401
    r = client.get("/api/v1/ingredients", headers={"Authorization": "Bearer forged.token.value"})
    assert r.status_code == 401


def test_role_permissions_on_financial_actions(client, owner):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    inv = buy(owner, sup["id"], [(ing["id"], 10, "kg", 9)])
    kitchen, viewer, manager = make_user(owner, "kitchen"), make_user(owner, "viewer"), make_user(owner, "manager")
    pay = {"supplier_id": sup["id"], "paid_on": "2026-01-02", "amount": "10", "method": "cash",
           "allocations": [{"invoice_id": inv["id"], "amount": "10"}]}
    assert kitchen.post("/payments", pay).status_code == 403
    assert viewer.post("/payments", pay).status_code == 403
    assert viewer.post("/purchases", {"supplier_id": sup["id"], "invoice_ref": "Z", "invoice_date": "2026-01-01",
                                      "lines": [{"ingredient_id": ing["id"], "quantity": "1", "unit": "kg",
                                                 "unit_price": "1"}]}).status_code == 403
    assert kitchen.post("/purchases/" + inv["id"] + "/void", {"reason": "test"}).status_code == 403
    # manager can post payments but cannot void them (owner only)
    p = manager.ok(manager.post("/payments", pay))
    assert manager.post(f"/payments/{p['id']}/void", {"reason": "oops"}).status_code == 403
    assert owner.post(f"/payments/{p['id']}/void", {"reason": "oops"}).status_code == 200
    # kitchen can record waste but not override stock
    assert kitchen.post("/stock/adjustments", {"ingredient_id": ing["id"], "quantity": "1", "unit": "kg",
                                               "reason": "spoilage"}).status_code == 201
    r = kitchen.post("/stock/consumptions", {"lines": [{"ingredient_id": ing["id"], "quantity": "100", "unit": "kg"}],
                                             "allow_shortfall": True, "override_reason": "rush"})
    assert r.status_code == 403
    assert viewer.get("/users").status_code == 403
    assert manager.patch("/settings/branch", {"include_tax_in_cost": True}).status_code == 403


def test_deactivated_user_and_role_change_revoke_sessions(client, owner):
    kitchen = make_user(owner, "kitchen")
    users = owner.ok(owner.get("/users"))
    kid = next(u["id"] for u in users if u["role"] == "kitchen")
    owner.ok(owner.patch(f"/users/{kid}", {"is_active": False}))
    assert kitchen.get("/ingredients").status_code == 401


def test_branch_isolation(client, owner, engine):
    from sqlalchemy import text
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    inv = buy(owner, sup["id"], [(ing["id"], 10, "kg", 9)])
    # a second branch with its own user
    with engine.begin() as c:
        c.execute(text("INSERT INTO branch (id, code, name, country, currency, currency_decimals, timezone, "
                       "default_target_food_cost_rate, include_tax_in_cost) VALUES "
                       "(gen_random_uuid(), 'EBB', 'Entebbe', 'UG', 'USD', 2, 'Africa/Kampala', 0.3, false)"))
    other = make_user(owner, "manager")
    me = other.ok(other.get("/me"))
    with engine.begin() as c:
        c.execute(text("UPDATE user_branch_role SET branch_id = (SELECT id FROM branch WHERE code='EBB') "
                       "WHERE user_id = :u"), {"u": me["user"]["id"]})
    other = Api(client, login(client, me["user"]["email"], "a-strong-password"))
    assert other.ok(other.get("/me"))["branch"]["code"] == "EBB"
    assert other.get(f"/purchases/{inv['id']}").status_code == 404
    assert other.ok(other.get("/purchases"))["total"] == 0
    assert other.ok(other.get("/stock"))["total_value"] == "0.0000"
    pay = {"supplier_id": sup["id"], "paid_on": "2026-01-02", "amount": "10", "method": "cash",
           "allocations": [{"invoice_id": inv["id"], "amount": "10"}]}
    assert other.post("/payments", pay).status_code == 422  # invoice belongs to another branch
