from decimal import Decimal as D

import pytest

from app.domain.costing import (
    CostingError, IngredientCost, QuantityBasis, Recipe, RecipeLine, cost_recipe, round_money,
)
from app.domain.units import Dimension


def costs():
    return {
        # beef bought at 18.00/kg -> 0.018 per g
        "beef": IngredientCost("beef", Dimension.MASS, D("0.018")),
        # onions 2.00/kg
        "onion": IngredientCost("onion", Dimension.MASS, D("0.002")),
    }


def test_brief_example_recommended_price():
    # cost per portion 4.50, target 30 % -> 15.00
    recipe = Recipe("r", (RecipeLine("beef", D("250"), "g"),), D("1"), D("0.30"))
    r = cost_recipe(recipe, costs())
    assert r.cost_per_portion == D("4.5")
    assert round_money(r.recommended_price) == D("15.00")


def test_batch_cost_per_portion_food_cost_and_contribution():
    recipe = Recipe(
        "stew",
        (RecipeLine("beef", D("5"), "kg"), RecipeLine("onion", D("1"), "kg")),
        portions_per_batch=D("20"),
        target_food_cost_rate=D("0.30"),
        selling_price=D("15"),
    )
    r = cost_recipe(recipe, costs())
    assert r.batch_cost == D("92")  # 90 + 2
    assert r.cost_per_portion == D("4.6")
    assert r.food_cost_rate == D("4.6") / D("15")
    assert r.gross_contribution == D("10.4")
    assert r.exceeds_target is True  # 30.67 % > 30 %


def test_net_quantity_grossed_up_once():
    # need 800 g usable onion, 20 % trim loss -> issue 1000 g
    line = RecipeLine("onion", D("800"), "g", QuantityBasis.NET, D("0.20"))
    r = cost_recipe(Recipe("x", (line,), D("1"), D("0.3")), costs())
    assert r.lines[0].gross_base_quantity == D("1000")
    assert r.batch_cost == D("2")


def test_gross_line_prep_loss_not_applied_again():
    line = RecipeLine("onion", D("1000"), "g", QuantityBasis.GROSS, D("0.20"))
    r = cost_recipe(Recipe("x", (line,), D("1"), D("0.3")), costs())
    assert r.lines[0].gross_base_quantity == D("1000")


def test_invalid_inputs():
    with pytest.raises(CostingError):
        cost_recipe(Recipe("x", (RecipeLine("beef", D("1"), "g"),), D("0"), D("0.3")), costs())
    with pytest.raises(CostingError):
        cost_recipe(Recipe("x", (RecipeLine("beef", D("1"), "g"),), D("1"), D("1.2")), costs())
    with pytest.raises(CostingError):
        cost_recipe(Recipe("x", (RecipeLine("nope", D("1"), "g"),), D("1"), D("0.3")), costs())
