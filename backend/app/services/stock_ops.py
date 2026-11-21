"""Manual consumption, adjustments (waste), recipe production and stock counts."""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.costing import QuantityBasis, RecipeLine as DLine, gross_quantity
from ..errors import BusinessRuleError, NotFound
from ..models import (
    Ingredient, IngredientCurrentCost, ProductionRun, Recipe, RecipeVersion, StockCount, StockCountLine,
    StockItem,
)
from ..security import Principal
from .common import ZERO, money, now, qty, unit_cost
from .ingredients import base_unit, get_ingredient, stock_item_for, to_base_qty
from .inventory import consume, lock_items, on_hand, receive
from .platform import audit, emit

ADJUSTMENT_REASONS = {"spoilage", "expired", "damaged", "prep_waste", "staff_meal", "other"}


def _override(p: Principal, allow_shortfall: bool, override_reason: str | None) -> bool:
    if not allow_shortfall:
        return False
    p.require("stock.override")
    if not override_reason or len(override_reason.strip()) < 3:
        raise BusinessRuleError("An override needs a reason")
    return True


def record_consumption(db: Session, p: Principal, data: dict) -> dict:
    allow = _override(p, data.get("allow_shortfall", False), data.get("override_reason"))
    group_id = uuid.uuid4()
    prepared = []
    for ln in data["lines"]:
        ing = get_ingredient(db, ln["ingredient_id"])
        prepared.append((ing, stock_item_for(db, ing.id), qty(to_base_qty(ing, ln["quantity"], ln["unit"]))))
    lock_items(db, [it.id for _, it, _ in prepared])
    out, total = [], ZERO
    for ing, item, base in prepared:
        mv = consume(db, p, item, base, movement_type="consumption", source_type="manual_consumption",
                     source_id=group_id, note=data.get("note"), allow_shortfall=allow, label=ing.name,
                     occurred_at=data.get("occurred_at"))
        total += -Decimal(mv.total_cost)
        out.append(_movement_out(ing, mv))
    audit(db, p, "stock.consume", "stock_movement", group_id,
          {"lines": data["lines"], "override": data.get("override_reason") if allow else None})
    emit(db, p.branch.id, "stock.consumed", group_id, {"group_id": group_id, "total_cost": total})
    return {"group_id": group_id, "cost_basis": "fifo", "total_cost": money(total), "lines": out}


def record_adjustment(db: Session, p: Principal, data: dict) -> dict:
    if data["reason"] not in ADJUSTMENT_REASONS:
        raise BusinessRuleError("Choose a valid reason")
    if data["reason"] == "other" and not (data.get("note") or "").strip():
        raise BusinessRuleError("Please describe the reason in the note")
    ing = get_ingredient(db, data["ingredient_id"])
    item = stock_item_for(db, ing.id)
    base = qty(to_base_qty(ing, data["quantity"], data["unit"]))
    mv = consume(db, p, item, base, movement_type="adjustment", source_type="adjustment", source_id=None,
                 reason=data["reason"], note=data.get("note"), label=ing.name)
    audit(db, p, "stock.adjust", "stock_movement", mv.id,
          {"ingredient": ing.name, "quantity": data["quantity"], "unit": data["unit"], "reason": data["reason"]})
    emit(db, p.branch.id, "stock.adjusted", mv.id, {"id": mv.id, "reason": data["reason"]})
    return {"cost_basis": "fifo", **_movement_out(ing, mv)}


def _movement_out(ing: Ingredient, mv) -> dict:
    return {"movement_id": mv.id, "ingredient_id": ing.id, "ingredient_name": ing.name, "base_unit": base_unit(ing),
            "quantity_base": -Decimal(mv.quantity), "total_cost": -Decimal(mv.total_cost),
            "shortfall_quantity": mv.shortfall_quantity,
            "allocations": [{"batch_id": a.batch_id, "quantity": a.quantity, "unit_cost": a.unit_cost,
                             "cost": money(Decimal(a.quantity) * Decimal(a.unit_cost))} for a in mv.allocations]}


def production_requirements(db: Session, v: RecipeVersion, batches: Decimal) -> list[tuple]:
    """Gross ingredient quantities needed for ``batches`` of an approved recipe version."""
    reqs = []
    for ln in v.lines:
        dl = DLine(str(ln.ingredient_id), Decimal(ln.quantity), ln.unit, QuantityBasis(ln.basis),
                   Decimal(ln.prep_loss_rate))
        base = qty(to_base_qty(ln.ingredient, gross_quantity(dl), ln.unit) * batches)
        reqs.append((ln.ingredient, base))
    merged: dict[uuid.UUID, list] = {}
    for ing, base in reqs:
        merged.setdefault(ing.id, [ing, ZERO])[1] += base
    return [tuple(x) for x in merged.values()]


def record_production(db: Session, p: Principal, data: dict) -> ProductionRun:
    recipe = db.get(Recipe, data["recipe_id"])
    if not recipe:
        raise NotFound("Recipe not found")
    v = db.scalar(select(RecipeVersion).where(RecipeVersion.recipe_id == recipe.id,
                                              RecipeVersion.status == "published"))
    if not v:
        raise BusinessRuleError(f"{recipe.name} has no approved (published) version to produce from")
    ppb = Decimal(v.portions_per_batch)
    if data.get("batches"):
        batches = Decimal(data["batches"])
    elif data.get("portions"):
        batches = Decimal(data["portions"]) / ppb
    else:
        raise BusinessRuleError("Enter the number of batches or portions prepared")
    if batches <= 0:
        raise BusinessRuleError("Batches must be positive")
    allow = _override(p, data.get("allow_shortfall", False), data.get("override_reason"))
    reqs = production_requirements(db, v, batches)
    items = {ing.id: stock_item_for(db, ing.id) for ing, _ in reqs}
    lock_items(db, [it.id for it in items.values()])
    run = ProductionRun(
        branch_id=p.branch.id, recipe_version_id=v.id, batches=qty(batches),
        expected_portions=qty(batches * ppb), actual_portions=data.get("actual_portions"),
        total_cost=ZERO, cost_per_portion=ZERO, currency=p.branch.currency, note=data.get("note"),
        created_by=p.user.id, produced_at=data.get("produced_at") or now())
    db.add(run)
    db.flush()
    total = ZERO
    for ing, base in reqs:
        mv = consume(db, p, items[ing.id], base, movement_type="production", source_type="production_run",
                     source_id=run.id, note=f"{recipe.name} v{v.version_no} x{qty(batches).normalize():f}",
                     allow_shortfall=allow, label=ing.name, occurred_at=run.produced_at)
        total += -Decimal(mv.total_cost)
    portions = Decimal(run.actual_portions or run.expected_portions)
    run.total_cost = money(total)
    run.cost_per_portion = money(total / portions) if portions else ZERO
    audit(db, p, "production.record", "production_run", run.id,
          {"recipe": recipe.name, "version": v.version_no, "batches": batches, "total_cost": total})
    emit(db, p.branch.id, "production.recorded", run.id, {"id": run.id, "total_cost": total})
    db.flush()
    return run


# ───────────────────────────── stock counts ─────────────────────────────
def start_count(db: Session, p: Principal, note: str | None, ingredient_ids: list[uuid.UUID] | None):
    q = select(StockItem).join(Ingredient, Ingredient.id == StockItem.ingredient_id).where(Ingredient.is_active)
    if ingredient_ids:
        q = q.where(Ingredient.id.in_(ingredient_ids))
    items = db.scalars(q).all()
    if not items:
        raise BusinessRuleError("No ingredients to count")
    oh = on_hand(db, p.branch.id, [i.id for i in items])
    c = StockCount(branch_id=p.branch.id, note=note, started_by=p.user.id)
    db.add(c)
    db.flush()
    for it in items:
        db.add(StockCountLine(count_id=c.id, stock_item_id=it.id, expected_at_start=oh.get(it.id, (ZERO, ZERO))[0]))
    audit(db, p, "count.start", "stock_count", c.id, {"items": len(items)})
    db.flush()
    return c


def get_count(db: Session, p: Principal, count_id: uuid.UUID, lock=False) -> StockCount:
    q = select(StockCount).where(StockCount.id == count_id, StockCount.branch_id == p.branch.id)
    if lock:
        q = q.with_for_update()
    c = db.scalar(q)
    if not c:
        raise NotFound("Stock count not found")
    return c


def enter_counts(db: Session, p: Principal, count_id: uuid.UUID, lines: list[dict]) -> StockCount:
    c = get_count(db, p, count_id, lock=True)
    if c.status != "open":
        raise BusinessRuleError("This count is closed")
    by_item = {ln.stock_item_id: ln for ln in c.lines}
    for entry in lines:
        ing = get_ingredient(db, entry["ingredient_id"])
        item = stock_item_for(db, ing.id)
        line = by_item.get(item.id)
        if not line:
            raise BusinessRuleError(f"{ing.name} is not part of this count")
        if entry.get("quantity") is None:
            line.counted_quantity = None
            continue
        if entry["quantity"] < 0:
            raise BusinessRuleError(f"{ing.name}: counted quantity cannot be negative")
        line.counted_quantity = qty(to_base_qty(ing, entry["quantity"], entry["unit"]))
    audit(db, p, "count.enter", "stock_count", c.id, {"lines": len(lines)})
    db.flush()
    return c


def approve_count(db: Session, p: Principal, count_id: uuid.UUID) -> StockCount:
    c = get_count(db, p, count_id, lock=True)
    if c.status != "open":
        raise BusinessRuleError("This count is already closed")
    counted = [ln for ln in c.lines if ln.counted_quantity is not None]
    if not counted:
        raise BusinessRuleError("Enter at least one counted quantity before approving")
    lock_items(db, [ln.stock_item_id for ln in counted])
    oh = on_hand(db, p.branch.id, [ln.stock_item_id for ln in counted])
    for ln in counted:
        item = db.get(StockItem, ln.stock_item_id)
        ing = db.get(Ingredient, item.ingredient_id)
        system_qty = oh.get(item.id, (ZERO, ZERO))[0]
        variance = qty(Decimal(ln.counted_quantity) - system_qty)
        ln.expected_at_approval = system_qty
        ln.variance_quantity = variance
        if variance < 0:
            mv = consume(db, p, item, -variance, movement_type="count_adjustment", source_type="stock_count",
                         source_id=c.id, reason="count_variance", note="Stock count shortage", label=ing.name)
            ln.adjustment_movement_id, ln.variance_value = mv.id, Decimal(mv.total_cost)
        elif variance > 0:
            cur = db.get(IngredientCurrentCost, (ing.id, p.branch.id))
            cost = unit_cost(Decimal(cur.cost_per_base_unit)) if cur else ZERO
            receive(db, p, item, variance, cost, "stock_count", c.id, now(),
                    movement_type="count_adjustment", reason="count_variance", note="Stock count surplus")
            db.flush()
            ln.variance_value = money(variance * cost)
        else:
            ln.variance_value = ZERO
    c.status, c.approved_by, c.approved_at = "approved", p.user.id, now()
    audit(db, p, "count.approve", "stock_count", c.id,
          {"variances": [{"item": str(ln.stock_item_id), "qty": ln.variance_quantity} for ln in counted]})
    emit(db, p.branch.id, "count.approved", c.id, {"id": c.id})
    db.flush()
    return c
