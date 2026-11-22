"""Recipe costing, yield, recommended price, versions and cost snapshots (MVP acceptance 2-4)."""

from conftest import buy, make_ingredient, make_supplier


def setup_stew(owner):
    ref = lambda amt: {"quantity": "1", "unit": "kg", "amount": amt, "effective_date": "2026-01-01"}
    beef = make_ingredient(owner, "BEEF", "Beef", reference_price=ref("18"))
    onion = make_ingredient(owner, "ONION", "Onion", reference_price=ref("2"))
    r = owner.ok(owner.post("/recipes", {"code": "STEW", "name": "Beef stew", "version": {
        "portions_per_batch": "20", "target_food_cost_rate": "0.30", "selling_price": "15",
        "cooking_shrinkage_rate": "0.25",
        "lines": [{"ingredient_id": beef["id"], "quantity": "5", "unit": "kg"},
                  {"ingredient_id": onion["id"], "quantity": "800", "unit": "g", "basis": "net",
                   "prep_loss_rate": "0.20"}]}}))
    return beef, onion, r


def test_recipe_cost_portion_price_and_yield(owner):
    beef, onion, r = setup_stew(owner)
    c = r["draft_cost"]
    assert c["cost_basis"] == "current"
    assert c["batch_cost"] == "92.0000"           # 5 kg * 18 + 1 kg (800 g net / 0.8) * 2
    assert c["cost_per_portion"] == "4.6000"
    assert c["recommended_price"] == "15.3333"    # 4.60 / 0.30
    assert c["food_cost_rate"] == "0.306667"
    assert c["gross_contribution"] == "10.4000"
    assert c["exceeds_target"] is True
    y = c["yield"]
    assert y["raw_mass_g"] == "6000" or y["raw_mass_g"].startswith("6000")
    assert y["usable_mass_g"].startswith("5800")  # 5000 + 800
    assert y["cooked_mass_g"].startswith("4350")  # 5800 * 0.75
    assert y["cooked_mass_per_portion_g"].startswith("217.5")


def test_brief_example_recommended_price(owner):
    ing = make_ingredient(owner, reference_price={"quantity": "1", "unit": "kg", "amount": "18"})
    r = owner.ok(owner.post("/recipes", {"code": "B", "name": "Burger", "version": {
        "portions_per_batch": "1", "target_food_cost_rate": "0.30",
        "lines": [{"ingredient_id": ing["id"], "quantity": "250", "unit": "g"}]}}))
    assert r["draft_cost"]["cost_per_portion"] == "4.5000"
    assert r["draft_cost"]["recommended_price"] == "15.0000"


def test_publish_and_cost_change_creates_snapshots_preserving_history(owner):
    beef, onion, r = setup_stew(owner)
    pub = owner.ok(owner.post(f"/recipes/{r['id']}/versions/1/publish"))
    assert pub["published_version"]["version_no"] == 1
    sup = make_supplier(owner)
    buy(owner, sup["id"], [(beef["id"], 10, "kg", 20)], day="2026-03-01")  # beef 18 -> 20 /kg
    hist = owner.ok(owner.get(f"/recipes/{r['id']}/cost-history"))
    assert [h["trigger"] for h in hist] == ["cost_change", "publish"]
    assert hist[1]["cost_per_portion"] == "4.6000"
    assert hist[0]["cost_per_portion"] == "5.1000"        # (100 + 2) / 20
    assert hist[0]["change_per_portion"] == "0.5000"
    # new version keeps v1 immutable
    lines = [{"ingredient_id": beef["id"], "quantity": "4", "unit": "kg"},
             {"ingredient_id": onion["id"], "quantity": "1", "unit": "kg"}]
    d = owner.ok(owner.put(f"/recipes/{r['id']}/draft", {"portions_per_batch": "20", "lines": lines,
                                                         "selling_price": "15", "change_note": "less beef"}))
    assert d["draft_version"]["version_no"] == 2
    owner.ok(owner.post(f"/recipes/{r['id']}/versions/2/publish"))
    v1 = owner.ok(owner.get(f"/recipes/{r['id']}/versions/1"))
    assert v1["status"] == "retired" and v1["lines"][0]["quantity"] == "5.000000"
    lst = owner.ok(owner.get("/recipes"))
    assert lst[0]["published_version_no"] == 2


def test_cannot_publish_without_prices(owner):
    salt = make_ingredient(owner, "SALT", "Salt")
    r = owner.ok(owner.post("/recipes", {"code": "S", "name": "Salted", "version": {
        "portions_per_batch": "1", "lines": [{"ingredient_id": salt["id"], "quantity": "5", "unit": "g"}]}}))
    assert r["draft_cost"]["missing_prices"] == ["Salt"]
    resp = owner.post(f"/recipes/{r['id']}/versions/1/publish")
    assert resp.status_code == 422 and "no price" in resp.json()["detail"]


def test_cost_preview_writes_nothing(owner):
    ing = make_ingredient(owner, reference_price={"quantity": "1", "unit": "kg", "amount": "10"})
    c = owner.ok(owner.post("/recipes/cost-preview", {"portions_per_batch": "4",
                                                      "lines": [{"ingredient_id": ing["id"], "quantity": "1", "unit": "kg"}]}))
    assert c["cost_per_portion"] == "2.5000"
    assert owner.ok(owner.get("/recipes")) == []


def test_recipe_list_cost_equals_live_cost_including_conversion_changes(owner):
    """The list shows the latest snapshot; it must always equal a live re-costing."""
    oil = make_ingredient(owner, "OIL", "Oil", "mass", "l", "g",
                          conversions=[{"from_unit": "l", "from_qty": "1", "to_unit": "g", "to_qty": "900"}],
                          reference_price={"quantity": "1", "unit": "kg", "amount": "9", "effective_date": "2026-01-01"})
    r = owner.ok(owner.post("/recipes", {"code": "FRY", "name": "Fry", "version": {
        "portions_per_batch": "2", "selling_price": "10", "lines": [{"ingredient_id": oil["id"], "quantity": "1", "unit": "l"}]}}))
    owner.ok(owner.post(f"/recipes/{r['id']}/versions/1/publish"))

    def listed():
        return next(x for x in owner.ok(owner.get("/recipes")) if x["id"] == r["id"])["cost_per_portion"]

    def live():
        return owner.ok(owner.get(f"/recipes/{r['id']}"))["cost"]["cost_per_portion"]
    assert listed() == live() == "4.0500"   # 900 g * 0.009 / 2
    owner.ok(owner.patch(f"/ingredients/{oil['id']}", {"conversions": [
        {"from_unit": "l", "from_qty": "1", "to_unit": "g", "to_qty": "920"}]}))
    assert listed() == live() == "4.1400"   # 920 g * 0.009 / 2 — re-costed by the conversion change
