"""Recipes, versions and CURRENT-basis costing (ADR-0006)."""

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.costing import (
    CostingError, IngredientCost, QuantityBasis, Recipe as DRecipe, RecipeLine as DLine,
    cost_recipe, gross_quantity,
)
from ..domain.units import Dimension, UnitConversionError, convert
from ..errors import BusinessRuleError, Conflict, NotFound
from ..models import (
    Branch, Ingredient, IngredientCurrentCost, Recipe, RecipeCostSnapshot, RecipeLine, RecipeVersion,
)
from ..security import Principal
from .common import ZERO, money, now
from .ingredients import base_unit, rules_for, to_base_qty
from .platform import audit, emit

ONE = Decimal(1)


def compute_cost(db: Session, branch: Branch, v: RecipeVersion) -> dict:
    """Full cost + yield breakdown on the CURRENT cost basis. Missing prices are reported."""
    ing_ids = [ln.ingredient_id for ln in v.lines]
    costs = {c.ingredient_id: c for c in db.scalars(select(IngredientCurrentCost).where(
        IngredientCurrentCost.branch_id == branch.id, IngredientCurrentCost.ingredient_id.in_(ing_ids)))}
    missing, d_costs, d_lines = [], {}, []
    for ln in v.lines:
        ing = ln.ingredient
        c = costs.get(ing.id)
        if c is None:
            missing.append(ing.name)
        d_costs[str(ing.id)] = IngredientCost(
            str(ing.id), Dimension(ing.base_dimension), Decimal(c.cost_per_base_unit) if c else ZERO,
            rules_for(ing))
        d_lines.append(DLine(str(ing.id), Decimal(ln.quantity), ln.unit, QuantityBasis(ln.basis),
                             Decimal(ln.prep_loss_rate)))
    try:
        r = cost_recipe(DRecipe(str(v.id), tuple(d_lines), Decimal(v.portions_per_batch),
                                Decimal(v.target_food_cost_rate),
                                Decimal(v.selling_price) if v.selling_price is not None else None),
                        d_costs)
    except (CostingError, UnitConversionError) as e:
        raise BusinessRuleError(str(e)) from e

    # Yield (informational, mass-based lines only). Losses are never re-applied to cost.
    raw_g = usable_g = ZERO
    lines_out = []
    for ln, lc, dl in zip(v.lines, r.lines, d_lines):
        ing = ln.ingredient
        gross = gross_quantity(dl)
        usable = dl.quantity if dl.basis is QuantityBasis.NET else gross * (ONE - dl.prep_loss_rate)
        try:
            g_gross = convert(gross, ln.unit, "g", rules_for(ing))
            g_usable = convert(usable, ln.unit, "g", rules_for(ing))
            raw_g += g_gross
            usable_g += g_usable
        except UnitConversionError:
            g_gross = g_usable = None
        c = costs.get(ing.id)
        lines_out.append({
            "ingredient_id": ing.id, "ingredient_name": ing.name, "quantity": ln.quantity,
            "unit": ln.unit, "basis": ln.basis, "prep_loss_rate": ln.prep_loss_rate,
            "gross_quantity": gross, "usable_quantity": usable,
            "gross_base_quantity": lc.gross_base_quantity, "base_unit": base_unit(ing),
            "cost_per_base_unit": c.cost_per_base_unit if c else None,
            "cost": money(lc.cost), "has_price": c is not None,
            "share_of_cost": (lc.cost / r.batch_cost) if r.batch_cost else ZERO,
        })
    cooked_g = usable_g * (ONE - Decimal(v.cooking_shrinkage_rate))
    portions = Decimal(v.portions_per_batch)
    return {
        "cost_basis": "current", "currency": branch.currency,
        "recipe_version_id": v.id, "complete": not missing, "missing_prices": missing,
        "lines": lines_out,
        "batch_cost": money(r.batch_cost), "cost_per_portion": money(r.cost_per_portion),
        "target_food_cost_rate": r.target_food_cost_rate,
        "recommended_price": money(r.recommended_price),
        "selling_price": r.selling_price,
        "food_cost_rate": r.food_cost_rate.quantize(Decimal("0.000001")) if r.food_cost_rate is not None else None,
        "gross_contribution": money(r.gross_contribution) if r.gross_contribution is not None else None,
        "exceeds_target": r.exceeds_target,
        "yield": {
            "raw_mass_g": raw_g, "usable_mass_g": usable_g,
            "cooking_shrinkage_rate": v.cooking_shrinkage_rate, "cooked_mass_g": cooked_g,
            "portions_per_batch": portions,
            "cooked_mass_per_portion_g": cooked_g / portions if portions else None,
        },
    }


def snapshot(db: Session, branch: Branch, v: RecipeVersion, trigger: str, ref: str | None) -> RecipeCostSnapshot:
    c = compute_cost(db, branch, v)
    s = RecipeCostSnapshot(
        recipe_version_id=v.id, branch_id=branch.id, trigger=trigger, trigger_ref=ref,
        batch_cost=c["batch_cost"], cost_per_portion=c["cost_per_portion"],
        recommended_price=c["recommended_price"], food_cost_rate=c["food_cost_rate"],
        exceeds_target=c["exceeds_target"], currency=branch.currency,
        detail={"lines": [{"ingredient": l["ingredient_name"], "cost": str(l["cost"]),
                           "cost_per_base_unit": str(l["cost_per_base_unit"])} for l in c["lines"]],
                "missing_prices": c["missing_prices"]})
    db.add(s)
    return s


def recalc_for_ingredients(db: Session, branch: Branch, ingredient_ids: list[uuid.UUID], ref: str) -> int:
    """Re-cost every PUBLISHED recipe version that uses any of these ingredients."""
    versions = db.scalars(
        select(RecipeVersion).where(RecipeVersion.status == "published",
                                    RecipeVersion.id.in_(select(RecipeLine.recipe_version_id)
                                                         .where(RecipeLine.ingredient_id.in_(ingredient_ids))))
    ).all()
    for v in versions:
        snapshot(db, branch, v, "cost_change", ref[:200])
    db.flush()
    return len(versions)


def _validate_lines(db: Session, lines: list[dict]) -> None:
    if not lines:
        raise BusinessRuleError("Add at least one ingredient")
    for ln in lines:
        ing = db.get(Ingredient, ln["ingredient_id"])
        if not ing:
            raise BusinessRuleError("Unknown ingredient in recipe")
        if not ing.is_active:
            raise BusinessRuleError(f"{ing.name} is inactive")
        if ln["quantity"] <= 0:
            raise BusinessRuleError(f"{ing.name}: quantity must be positive")
        if not (0 <= ln.get("prep_loss_rate", 0) < 1):
            raise BusinessRuleError(f"{ing.name}: preparation loss must be between 0 and 100%")
        to_base_qty(ing, Decimal(1), ln["unit"])  # unit must be convertible


def _fill_version(v: RecipeVersion, data: dict) -> None:
    for f in ("portions_per_batch", "portion_size_qty", "portion_size_unit", "cooking_shrinkage_rate",
              "target_food_cost_rate", "selling_price", "prep_instructions", "cooking_instructions",
              "change_note"):
        if f in data:
            setattr(v, f, data[f])
    v.lines = [RecipeLine(ingredient_id=ln["ingredient_id"], quantity=ln["quantity"], unit=ln["unit"],
                          basis=ln.get("basis", "gross"), prep_loss_rate=ln.get("prep_loss_rate", ZERO),
                          sort_order=i) for i, ln in enumerate(data["lines"])]


def create_recipe(db: Session, p: Principal, data: dict) -> Recipe:
    _validate_lines(db, data["version"]["lines"])
    r = Recipe(code=data["code"].strip().upper(), name=data["name"].strip(), category=data.get("category"))
    db.add(r)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict(f"A recipe with code {data['code']} already exists.")
    vd = {k: v for k, v in data["version"].items() if v is not None or k == "selling_price"}
    vd.setdefault("target_food_cost_rate", p.branch.default_target_food_cost_rate)
    v = RecipeVersion(recipe_id=r.id, version_no=1, status="draft", created_by=p.user.id)
    _fill_version(v, vd)
    db.add(v)
    db.flush()
    audit(db, p, "recipe.create", "recipe", r.id, {"code": r.code})
    return r


def get_version(db: Session, recipe_id: uuid.UUID, version_no: int) -> RecipeVersion:
    v = db.scalar(select(RecipeVersion).where(RecipeVersion.recipe_id == recipe_id,
                                              RecipeVersion.version_no == version_no))
    if not v:
        raise NotFound("Recipe version not found")
    return v


def save_draft(db: Session, p: Principal, recipe: Recipe, data: dict) -> RecipeVersion:
    """Edit the open draft, or start a new draft version from the latest one."""
    _validate_lines(db, data["lines"])
    if "name" in data and data["name"]:
        recipe.name = data["name"]
    if "category" in data:
        recipe.category = data["category"]
    draft = next((v for v in recipe.versions if v.status == "draft"), None)
    if draft is None:
        latest = max(v.version_no for v in recipe.versions)
        draft = RecipeVersion(recipe_id=recipe.id, version_no=latest + 1, status="draft",
                              created_by=p.user.id,
                              portions_per_batch=recipe.versions[-1].portions_per_batch,
                              target_food_cost_rate=recipe.versions[-1].target_food_cost_rate)
        db.add(draft)
    _fill_version(draft, data)
    db.flush()
    audit(db, p, "recipe.draft_save", "recipe_version", draft.id, {"version_no": draft.version_no})
    return draft


def publish(db: Session, p: Principal, recipe: Recipe, version_no: int) -> RecipeVersion:
    v = get_version(db, recipe.id, version_no)
    if v.status != "draft":
        raise BusinessRuleError("Only a draft version can be published")
    cost = compute_cost(db, p.branch, v)
    if not cost["complete"]:
        raise BusinessRuleError("Cannot publish: these ingredients have no price yet: "
                                + ", ".join(cost["missing_prices"]))
    for other in recipe.versions:
        if other.status == "published":
            other.status = "retired"
    db.flush()
    v.status, v.published_by, v.published_at = "published", p.user.id, now()
    db.flush()
    snapshot(db, p.branch, v, "publish", f"version {v.version_no} published")
    audit(db, p, "recipe.publish", "recipe_version", v.id, {"version_no": v.version_no})
    emit(db, p.branch.id, "recipe.published", recipe.id, {"recipe_id": recipe.id, "version_no": v.version_no})
    return v


def published_version(db: Session, recipe_id: uuid.UUID) -> RecipeVersion | None:
    return db.scalar(select(RecipeVersion).where(RecipeVersion.recipe_id == recipe_id,
                                                 RecipeVersion.status == "published"))


def snapshots(db: Session, branch_id: uuid.UUID, recipe_id: uuid.UUID, limit: int = 50):
    return db.execute(
        select(RecipeCostSnapshot, RecipeVersion.version_no)
        .join(RecipeVersion, RecipeVersion.id == RecipeCostSnapshot.recipe_version_id)
        .where(RecipeVersion.recipe_id == recipe_id, RecipeCostSnapshot.branch_id == branch_id)
        .order_by(RecipeCostSnapshot.computed_at.desc()).limit(limit)).all()


def count_versions(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(RecipeVersion))
