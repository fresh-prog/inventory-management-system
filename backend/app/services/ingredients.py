import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.units import BASE_UNIT, CrossDimensionRule, Dimension, UnitConversionError, convert, get_unit
from ..errors import BusinessRuleError, Conflict, NotFound
from ..models import (
    Ingredient, IngredientBranchSetting, IngredientCategory, IngredientConversion,
    IngredientCurrentCost, IngredientPriceHistory, StockItem, Supplier, SupplierIngredient,
)
from ..security import Principal
from .common import money, now, qty, unit_cost
from .platform import audit, emit


def rules_for(ing: Ingredient) -> tuple[CrossDimensionRule, ...]:
    return tuple(CrossDimensionRule(c.from_unit, Decimal(c.from_qty), c.to_unit, Decimal(c.to_qty))
                 for c in ing.conversions)


def base_unit(ing: Ingredient) -> str:
    return BASE_UNIT[Dimension(ing.base_dimension)]


def to_base_qty(ing: Ingredient, quantity: Decimal, unit: str) -> Decimal:
    try:
        return convert(quantity, unit, base_unit(ing), rules_for(ing))
    except UnitConversionError as e:
        raise BusinessRuleError(f"{ing.name}: {e}") from e


def from_base_qty(ing: Ingredient, quantity_base: Decimal, unit: str) -> Decimal:
    return convert(quantity_base, base_unit(ing), unit, rules_for(ing))


def check_unit_usable(ing: Ingredient, unit: str) -> None:
    get_unit(unit)
    to_base_qty(ing, Decimal(1), unit)


def get_ingredient(db: Session, ingredient_id: uuid.UUID) -> Ingredient:
    ing = db.get(Ingredient, ingredient_id)
    if not ing:
        raise NotFound("Ingredient not found")
    return ing


def stock_item_for(db: Session, ingredient_id: uuid.UUID) -> StockItem:
    return db.scalar(select(StockItem).where(StockItem.ingredient_id == ingredient_id))


def get_or_create_category(db: Session, name: str | None) -> uuid.UUID | None:
    if not name or not name.strip():
        return None
    name = name.strip()
    cat = db.scalar(select(IngredientCategory).where(func.lower(IngredientCategory.name) == name.lower()))
    if not cat:
        cat = IngredientCategory(name=name)
        db.add(cat)
        db.flush()
    return cat.id


def _apply_conversions(db: Session, ing: Ingredient, conversions: list[dict]) -> None:
    ing.conversions.clear()
    db.flush()
    for c in conversions:
        rule = CrossDimensionRule(c["from_unit"], c["from_qty"], c["to_unit"], c["to_qty"])  # validates
        ing.conversions.append(IngredientConversion(
            from_unit=rule.from_unit, from_qty=rule.from_qty, to_unit=rule.to_unit,
            to_qty=rule.to_qty, note=c.get("note")))
    db.flush()


def _set_min_stock(db: Session, ing: Ingredient, branch_id: uuid.UUID, min_qty: Decimal | None,
                   unit: str | None) -> None:
    setting = db.get(IngredientBranchSetting, (ing.id, branch_id))
    if not setting:
        setting = IngredientBranchSetting(ingredient_id=ing.id, branch_id=branch_id)
        db.add(setting)
    if min_qty is not None:
        if min_qty < 0:
            raise BusinessRuleError("Minimum stock cannot be negative")
        setting.min_stock_base = qty(to_base_qty(ing, min_qty, unit or ing.consumption_unit))


def _set_suppliers(db: Session, ing: Ingredient, suppliers: list[dict]) -> None:
    for old in db.scalars(select(SupplierIngredient).where(SupplierIngredient.ingredient_id == ing.id)).all():
        db.delete(old)  # ORM deletes, so change capture sees them (no bulk Core deletes)
    db.flush()
    for s in suppliers:
        if not db.get(Supplier, s["supplier_id"]):
            raise BusinessRuleError("Unknown supplier in supplier list")
        db.add(SupplierIngredient(ingredient_id=ing.id, supplier_id=s["supplier_id"],
                                  supplier_sku=s.get("supplier_sku"),
                                  is_preferred=bool(s.get("is_preferred"))))


def create_ingredient(db: Session, p: Principal, data: dict) -> Ingredient:
    dim = Dimension(data["base_dimension"])
    ing = Ingredient(
        code=data["code"].strip().upper(), name=data["name"].strip(),
        category_id=get_or_create_category(db, data.get("category")),
        description=data.get("description"), base_dimension=dim.value,
        purchase_unit=data["purchase_unit"], consumption_unit=data["consumption_unit"],
        is_active=data.get("is_active", True))
    db.add(ing)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict(f"An ingredient with code {ing.code} already exists.")
    _apply_conversions(db, ing, data.get("conversions") or [])
    check_unit_usable(ing, ing.purchase_unit)
    check_unit_usable(ing, ing.consumption_unit)
    db.add(StockItem(kind="ingredient", ingredient_id=ing.id, base_dimension=dim.value))
    _set_min_stock(db, ing, p.branch.id, data.get("min_stock"), data.get("min_stock_unit"))
    _set_suppliers(db, ing, data.get("suppliers") or [])
    ref = data.get("reference_price")
    if ref:
        record_manual_price(db, p, ing, ref["quantity"], ref["unit"], ref["amount"],
                            ref.get("effective_date") or date.today(), "Initial reference price")
    audit(db, p, "ingredient.create", "ingredient", ing.id, {"code": ing.code, "name": ing.name})
    emit(db, p.branch.id, "ingredient.upserted", ing.id, {"id": ing.id, "version": ing.version})
    return ing


def update_ingredient(db: Session, p: Principal, ing: Ingredient, data: dict, if_version: int | None):
    if if_version is not None and if_version != ing.version:
        raise Conflict("This ingredient was changed by someone else. Reload and try again.",
                       current_version=ing.version)
    before = {"name": ing.name, "code": ing.code, "is_active": ing.is_active}
    for f in ("name", "description", "is_active", "purchase_unit", "consumption_unit"):
        if f in data and data[f] is not None:
            setattr(ing, f, data[f])
    if "code" in data and data["code"]:
        ing.code = data["code"].strip().upper()
    if "category" in data:
        ing.category_id = get_or_create_category(db, data["category"])
    if "base_dimension" in data and data["base_dimension"] and data["base_dimension"] != ing.base_dimension:
        has_stock = db.scalar(select(func.count()).select_from(IngredientPriceHistory)
                              .where(IngredientPriceHistory.ingredient_id == ing.id))
        if has_stock:
            raise BusinessRuleError("The stock dimension cannot change once prices or stock exist.")
        ing.base_dimension = data["base_dimension"]
        stock_item_for(db, ing.id).base_dimension = data["base_dimension"]
    if data.get("conversions") is not None:
        _apply_conversions(db, ing, data["conversions"])
    check_unit_usable(ing, ing.purchase_unit)
    check_unit_usable(ing, ing.consumption_unit)
    if "min_stock" in data:
        _set_min_stock(db, ing, p.branch.id, data.get("min_stock"), data.get("min_stock_unit"))
    if data.get("suppliers") is not None:
        _set_suppliers(db, ing, data["suppliers"])
    ing.version += 1
    ing.updated_at = now()
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict("Another ingredient already uses this code.")
    audit(db, p, "ingredient.update", "ingredient", ing.id, {"before": before, "version": ing.version})
    emit(db, p.branch.id, "ingredient.upserted", ing.id, {"id": ing.id, "version": ing.version})
    return ing


def apply_cost(db: Session, p: Principal, ing: Ingredient, cost_per_base: Decimal,
               effective_date: date, history: IngredientPriceHistory, trigger_ref: str) -> bool:
    """Update the CURRENT cost unless a newer-dated price is already in force.
    Returns True if the current cost changed (recipes are then re-costed)."""
    from .recipes import recalc_for_ingredients

    cur = db.get(IngredientCurrentCost, (ing.id, p.branch.id), with_for_update=True)
    history.previous_cost_per_base_unit = cur.cost_per_base_unit if cur else None
    if cur and effective_date < cur.effective_date:
        return False
    changed = cur is None or Decimal(cur.cost_per_base_unit) != cost_per_base
    if cur is None:
        cur = IngredientCurrentCost(ingredient_id=ing.id, branch_id=p.branch.id)
        db.add(cur)
    cur.cost_per_base_unit = cost_per_base
    cur.currency = p.branch.currency
    cur.effective_date = effective_date
    cur.price_history_id = history.id
    cur.updated_at = now()
    db.flush()
    if changed:
        recalc_for_ingredients(db, p.branch, [ing.id], trigger_ref)
    return changed


def record_manual_price(db: Session, p: Principal, ing: Ingredient, quantity: Decimal, unit: str,
                        amount: Decimal, effective_date: date, note: str | None) -> IngredientPriceHistory:
    if quantity <= 0 or amount < 0:
        raise BusinessRuleError("Quantity must be positive and price cannot be negative")
    base = to_base_qty(ing, quantity, unit)
    cpb = unit_cost(amount / base)
    h = IngredientPriceHistory(
        ingredient_id=ing.id, branch_id=p.branch.id, source="manual", effective_date=effective_date,
        purchase_quantity=quantity, purchase_unit=unit, purchase_amount=money(amount),
        cost_per_base_unit=cpb, currency=p.branch.currency, note=note, created_by=p.user.id)
    db.add(h)
    db.flush()
    apply_cost(db, p, ing, cpb, effective_date, h, f"manual price: {ing.name}")
    audit(db, p, "ingredient.price_manual", "ingredient", ing.id,
          {"quantity": quantity, "unit": unit, "amount": amount, "cost_per_base_unit": cpb})
    return h


def recompute_current_cost(db: Session, p: Principal, ing_id: uuid.UUID, trigger_ref: str) -> None:
    """After a void: current cost = latest non-voided price history entry."""
    from .recipes import recalc_for_ingredients

    latest = db.scalar(
        select(IngredientPriceHistory)
        .where(IngredientPriceHistory.ingredient_id == ing_id,
               IngredientPriceHistory.branch_id == p.branch.id,
               IngredientPriceHistory.is_voided.is_(False))
        .order_by(IngredientPriceHistory.effective_date.desc(), IngredientPriceHistory.created_at.desc())
        .limit(1))
    cur = db.get(IngredientCurrentCost, (ing_id, p.branch.id), with_for_update=True)
    if latest is None:
        if cur:
            db.delete(cur)
    else:
        if cur is None:
            cur = IngredientCurrentCost(ingredient_id=ing_id, branch_id=p.branch.id)
            db.add(cur)
        cur.cost_per_base_unit = latest.cost_per_base_unit
        cur.currency = latest.currency
        cur.effective_date = latest.effective_date
        cur.price_history_id = latest.id
        cur.updated_at = now()
    db.flush()
    recalc_for_ingredients(db, p.branch, [ing_id], trigger_ref)
