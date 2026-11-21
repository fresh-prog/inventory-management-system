"""Ingredient database and unit costs (MVP acceptance 1)."""

from conftest import buy, make_ingredient, make_supplier


def test_reference_price_gives_cost_per_base_unit(owner):
    ing = make_ingredient(owner, "RICE", "Rice", reference_price={"quantity": "25", "unit": "kg", "amount": "50"},
                          category="Dry goods", min_stock="10", min_stock_unit="kg")
    assert ing["current_cost"]["cost_per_base_unit"] == "0.002000000000"  # 50 / 25000 g
    assert ing["category"] == "Dry goods"
    assert ing["min_stock_base"] == "10000.000000"
    hist = owner.ok(owner.get(f"/ingredients/{ing['id']}/price-history"))
    assert len(hist) == 1 and hist[0]["source"] == "manual"


def test_volume_and_count_costs(owner):
    milk = make_ingredient(owner, "MILK", "Milk", "volume", "l", "ml",
                           reference_price={"quantity": "1", "unit": "l", "amount": "1.20"})
    assert milk["current_cost"]["cost_per_base_unit"] == "0.001200000000"
    eggs = make_ingredient(owner, "EGG", "Eggs", "count", "unit", "unit",
                           reference_price={"quantity": "30", "unit": "unit", "amount": "4.50"})
    assert eggs["current_cost"]["cost_per_base_unit"] == "0.150000000000"


def test_cross_dimension_requires_rule(owner):
    r = owner.post("/ingredients", {"code": "OIL", "name": "Oil", "base_dimension": "mass",
                                    "purchase_unit": "l", "consumption_unit": "g"})
    assert r.status_code == 422 and "without an ingredient-specific rule" in r.json()["detail"]
    oil = make_ingredient(owner, "OIL", "Oil", "mass", "l", "g",
                          conversions=[{"from_unit": "l", "from_qty": "1", "to_unit": "g", "to_qty": "910"}],
                          reference_price={"quantity": "5", "unit": "l", "amount": "9.10"})
    assert oil["current_cost"]["cost_per_base_unit"] == "0.002000000000"  # 9.10 / 4550 g


def test_purchase_updates_cost_and_history_newer_date_wins(owner):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    buy(owner, sup["id"], [(ing["id"], 10, "kg", 9)], day="2026-02-01")
    buy(owner, sup["id"], [(ing["id"], 10, "kg", 8)], day="2026-01-15")  # older invoice entered late
    cur = owner.ok(owner.get(f"/ingredients/{ing['id']}"))["current_cost"]
    assert cur["cost_per_base_unit"] == "0.009000000000"  # the newer price stays current
    hist = owner.ok(owner.get(f"/ingredients/{ing['id']}/price-history"))
    assert [h["cost_per_base_unit"] for h in hist] == ["0.009000000000", "0.008000000000"]


def test_edit_with_version_check_and_suppliers(owner):
    sup = make_supplier(owner)
    ing = make_ingredient(owner)
    upd = owner.ok(owner.patch(f"/ingredients/{ing['id']}", {"name": "Beef (lean)", "suppliers": [
        {"supplier_id": sup["id"], "is_preferred": True}]}, headers={"If-Match": str(ing["version"])}))
    assert upd["name"] == "Beef (lean)" and upd["suppliers"][0]["supplier_name"] == "Fresh Foods Ltd"
    stale = owner.patch(f"/ingredients/{ing['id']}", {"name": "X"}, headers={"If-Match": str(ing["version"])})
    assert stale.status_code == 409


def test_duplicate_code_rejected(owner):
    make_ingredient(owner)
    assert owner.post("/ingredients", {"code": "beef", "name": "B", "base_dimension": "mass",
                                       "purchase_unit": "kg", "consumption_unit": "g"}).status_code == 409


def test_decimals_are_strings_not_floats(owner):
    ing = make_ingredient(owner, reference_price={"quantity": "3", "unit": "kg", "amount": "10"})
    assert isinstance(ing["current_cost"]["cost_per_base_unit"], str)
