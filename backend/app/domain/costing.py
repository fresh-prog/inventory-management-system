"""Recipe costing on the CURRENT COST basis.

This module answers "what should this dish cost and sell for, at today's
prices?". It never touches stock and is NOT the actual consumption cost
(that is FIFO, see ``fifo.py``). See ADR-0006.

Yield handling (BR-YIELD-*):

* A recipe line is entered either as GROSS (quantity issued from stock,
  as purchased/untrimmed) or NET (usable quantity after trimming/prep loss).
* NET lines are grossed up once: gross = net / (1 - prep_loss_rate).
* GROSS lines are costed as entered; their prep loss is informational only
  and is NEVER applied again.
* Cooking shrinkage changes the cooked output weight, not the input cost.
  The batch cost is divided by the number of portions the batch yields,
  so shrinkage is never multiplied into ingredient cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from .units import CrossDimensionRule, Dimension, to_base

ZERO = Decimal("0")
ONE = Decimal("1")


class CostingError(ValueError):
    pass


class QuantityBasis(str, Enum):
    GROSS = "gross"
    NET = "net"


@dataclass(frozen=True)
class IngredientCost:
    """Current cost basis for one ingredient: price per BASE unit."""

    ingredient_id: str
    base_dimension: Dimension
    cost_per_base_unit: Decimal  # e.g. cost per gram
    rules: tuple[CrossDimensionRule, ...] = ()


@dataclass(frozen=True)
class RecipeLine:
    ingredient_id: str
    quantity: Decimal
    unit: str
    basis: QuantityBasis = QuantityBasis.GROSS
    prep_loss_rate: Decimal = ZERO  # 0.20 = 20 % trimmed away


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    lines: tuple[RecipeLine, ...]
    portions_per_batch: Decimal
    target_food_cost_rate: Decimal  # 0.30 = 30 %
    selling_price: Decimal | None = None  # tax-exclusive menu price


@dataclass(frozen=True)
class LineCost:
    ingredient_id: str
    gross_base_quantity: Decimal
    cost: Decimal


@dataclass(frozen=True)
class RecipeCostResult:
    recipe_id: str
    lines: tuple[LineCost, ...]
    batch_cost: Decimal
    cost_per_portion: Decimal
    target_food_cost_rate: Decimal
    recommended_price: Decimal
    selling_price: Decimal | None
    food_cost_rate: Decimal | None
    gross_contribution: Decimal | None
    exceeds_target: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)


def gross_quantity(line: RecipeLine) -> Decimal:
    if line.quantity <= ZERO:
        raise CostingError(f"Quantity for {line.ingredient_id} must be positive")
    if not (ZERO <= line.prep_loss_rate < ONE):
        raise CostingError("prep_loss_rate must be in [0, 1)")
    if line.basis is QuantityBasis.NET:
        return line.quantity / (ONE - line.prep_loss_rate)
    return line.quantity


def cost_recipe(recipe: Recipe, costs: dict[str, IngredientCost]) -> RecipeCostResult:
    if recipe.portions_per_batch <= ZERO:
        raise CostingError("portions_per_batch must be positive")
    if not (ZERO < recipe.target_food_cost_rate < ONE):
        raise CostingError("target_food_cost_rate must be between 0 and 1")
    if not recipe.lines:
        raise CostingError("A recipe needs at least one ingredient line")

    warnings: list[str] = []
    line_costs: list[LineCost] = []
    for line in recipe.lines:
        ic = costs.get(line.ingredient_id)
        if ic is None:
            raise CostingError(f"No current cost for ingredient {line.ingredient_id}")
        gross = gross_quantity(line)
        base_qty = to_base(gross, line.unit, ic.base_dimension, ic.rules)
        if ic.cost_per_base_unit == ZERO:
            warnings.append(f"Ingredient {line.ingredient_id} has zero cost")
        line_costs.append(
            LineCost(line.ingredient_id, base_qty, base_qty * ic.cost_per_base_unit)
        )

    batch_cost = sum((lc.cost for lc in line_costs), ZERO)
    per_portion = batch_cost / recipe.portions_per_batch
    recommended = per_portion / recipe.target_food_cost_rate

    food_cost_rate = contribution = None
    exceeds = False
    if recipe.selling_price is not None:
        if recipe.selling_price <= ZERO:
            raise CostingError("selling_price must be positive")
        food_cost_rate = per_portion / recipe.selling_price
        contribution = recipe.selling_price - per_portion
        exceeds = food_cost_rate > recipe.target_food_cost_rate

    return RecipeCostResult(
        recipe_id=recipe.recipe_id,
        lines=tuple(line_costs),
        batch_cost=batch_cost,
        cost_per_portion=per_portion,
        target_food_cost_rate=recipe.target_food_cost_rate,
        recommended_price=recommended,
        selling_price=recipe.selling_price,
        food_cost_rate=food_cost_rate,
        gross_contribution=contribution,
        exceeds_target=exceeds,
        warnings=tuple(warnings),
    )


def round_money(value: Decimal, minor_units: int = 2) -> Decimal:
    """Round for display/posting only. Internal maths keeps full precision."""
    return value.quantize(Decimal(1).scaleb(-minor_units), rounding=ROUND_HALF_UP)
