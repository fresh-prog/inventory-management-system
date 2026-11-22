"""Dashboard and reports. Every figure states its cost basis (ADR-0006)."""

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Ingredient, IngredientBranchSetting, IngredientCategory, IngredientConversion, IngredientCurrentCost, IngredientPriceHistory,
    OutboxEvent, PurchaseInvoice, Recipe, RecipeCostSnapshot, RecipeVersion, StockAllocation, StockBatch,
    StockItem, StockMovement, Supplier, SupplierLedgerEntry,
)
from .common import ZERO, money, now
from .ingredients import base_unit, from_base_qty
from .purchasing import invoice_balance, paid_and_credited


def _month_bounds(today: date) -> tuple[datetime, datetime]:
    start = today.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    tz = now().tzinfo
    return datetime.combine(start, time(), tz), datetime.combine(nxt, time(), tz)


def stock_levels(db: Session, branch_id: uuid.UUID, include_inactive=False) -> list[dict]:
    """One row per ingredient. Plain column queries (no ORM objects): this runs on every stock and
    dashboard view, for catalogues of thousands of ingredients."""
    from ..domain.units import BASE_UNIT, CrossDimensionRule, Dimension, UnitConversionError, convert, get_unit
    agg = (select(StockBatch.stock_item_id.label("sid"), func.sum(StockBatch.remaining_quantity).label("q"),
                  func.sum(StockBatch.remaining_quantity * StockBatch.unit_cost).label("v"), func.count().label("n"))
           .where(StockBatch.branch_id == branch_id, StockBatch.remaining_quantity > 0)
           .group_by(StockBatch.stock_item_id).subquery())
    q = (select(Ingredient.id, Ingredient.code, Ingredient.name, Ingredient.base_dimension, Ingredient.consumption_unit,
                Ingredient.is_active, IngredientCategory.name.label("category"),
                func.coalesce(IngredientBranchSetting.min_stock_base, 0).label("min_base"),
                IngredientCurrentCost.cost_per_base_unit, func.coalesce(agg.c.q, 0).label("on_hand"),
                func.coalesce(agg.c.v, 0).label("value"), func.coalesce(agg.c.n, 0).label("batches"))
         .join(StockItem, StockItem.ingredient_id == Ingredient.id)
         .outerjoin(agg, agg.c.sid == StockItem.id)
         .outerjoin(IngredientBranchSetting, (IngredientBranchSetting.ingredient_id == Ingredient.id)
                    & (IngredientBranchSetting.branch_id == branch_id))
         .outerjoin(IngredientCurrentCost, (IngredientCurrentCost.ingredient_id == Ingredient.id)
                    & (IngredientCurrentCost.branch_id == branch_id))
         .outerjoin(IngredientCategory, IngredientCategory.id == Ingredient.category_id)
         .order_by(Ingredient.name))
    if not include_inactive:
        q = q.where(Ingredient.is_active)
    rows = db.execute(q).all()
    # conversion rules are only needed when the display unit is in another dimension (rare)
    cross = [r.id for r in rows if get_unit(r.consumption_unit).dimension.value != r.base_dimension]
    rules: dict = {}
    if cross:
        for c in db.scalars(select(IngredientConversion).where(IngredientConversion.ingredient_id.in_(cross))):
            rules.setdefault(c.ingredient_id, []).append(
                CrossDimensionRule(c.from_unit, Decimal(c.from_qty), c.to_unit, Decimal(c.to_qty)))
    out = []
    for r in rows:
        base = BASE_UNIT[Dimension(r.base_dimension)]
        on_hand, min_base = Decimal(r.on_hand), Decimal(r.min_base)
        unit = r.consumption_unit
        try:
            rr = tuple(rules.get(r.id, ()))
            display, display_min = convert(on_hand, base, unit, rr), convert(min_base, base, unit, rr)
        except UnitConversionError:
            unit, display, display_min = base, on_hand, min_base
        out.append({
            "ingredient_id": r.id, "code": r.code, "name": r.name, "category": r.category,
            "base_unit": base, "on_hand_base": on_hand, "min_stock_base": min_base,
            "display_unit": unit, "on_hand": display, "min_stock": display_min,
            "inventory_value": money(Decimal(r.value)), "open_batches": r.batches,
            "current_cost_per_base_unit": r.cost_per_base_unit, "is_low": min_base > 0 and on_hand < min_base,
            "is_out": on_hand <= 0, "is_active": r.is_active,
        })
    return out


def inventory_value(db: Session, branch_id: uuid.UUID) -> Decimal:
    return money(Decimal(db.scalar(select(func.coalesce(
        func.sum(StockBatch.remaining_quantity * StockBatch.unit_cost), 0)).where(
        StockBatch.branch_id == branch_id))))


def payables(db: Session, branch_id: uuid.UUID) -> Decimal:
    return money(Decimal(db.scalar(select(func.coalesce(func.sum(SupplierLedgerEntry.amount), 0))
                                   .where(SupplierLedgerEntry.branch_id == branch_id))))


def consumption_summary(db: Session, branch_id: uuid.UUID, start: datetime, end: datetime) -> dict:
    rows = db.execute(
        select(StockMovement.type, StockMovement.reason, func.sum(-StockMovement.total_cost))
        .where(StockMovement.branch_id == branch_id, StockMovement.quantity < 0,
               StockMovement.type.in_(["consumption", "production", "adjustment", "count_adjustment"]),
               StockMovement.occurred_at >= start, StockMovement.occurred_at < end)
        .group_by(StockMovement.type, StockMovement.reason)).all()
    out = {"production": ZERO, "consumption": ZERO, "waste": ZERO, "count_shortage": ZERO}
    by_reason: dict[str, Decimal] = {}
    for t, reason, cost in rows:
        cost = Decimal(cost)
        if t == "adjustment":
            out["waste"] += cost
            by_reason[reason] = by_reason.get(reason, ZERO) + cost
        elif t == "count_adjustment":
            out["count_shortage"] += cost
        else:
            out[t] += cost
    total = sum(out.values(), ZERO)
    return {"cost_basis": "fifo", **{k: money(v) for k, v in out.items()}, "total": money(total),
            "waste_by_reason": {k: money(v) for k, v in by_reason.items()}}


def recipe_food_costs(db: Session, branch_id: uuid.UUID) -> list[dict]:
    latest = (select(RecipeCostSnapshot.recipe_version_id, func.max(RecipeCostSnapshot.computed_at).label("at"))
              .where(RecipeCostSnapshot.branch_id == branch_id)
              .group_by(RecipeCostSnapshot.recipe_version_id).subquery())
    rows = db.execute(
        select(Recipe.id, Recipe.name, RecipeVersion.version_no, RecipeVersion.selling_price,
               RecipeVersion.target_food_cost_rate, RecipeCostSnapshot.cost_per_portion, RecipeCostSnapshot.food_cost_rate,
               RecipeCostSnapshot.recommended_price, RecipeCostSnapshot.exceeds_target, RecipeCostSnapshot.computed_at)
        .select_from(Recipe)
        .join(RecipeVersion, RecipeVersion.recipe_id == Recipe.id)
        .join(latest, latest.c.recipe_version_id == RecipeVersion.id)
        .join(RecipeCostSnapshot, (RecipeCostSnapshot.recipe_version_id == RecipeVersion.id)
              & (RecipeCostSnapshot.computed_at == latest.c.at) & (RecipeCostSnapshot.branch_id == branch_id))
        .where(RecipeVersion.status == "published").order_by(Recipe.name)).all()
    return [{"recipe_id": r.id, "name": r.name, "version_no": r.version_no, "cost_basis": "current",
             "cost_per_portion": r.cost_per_portion, "selling_price": r.selling_price,
             "food_cost_rate": r.food_cost_rate, "target_food_cost_rate": r.target_food_cost_rate,
             "recommended_price": r.recommended_price, "exceeds_target": r.exceeds_target,
             "computed_at": r.computed_at} for r in rows]


def price_changes(db: Session, branch_id: uuid.UUID, limit=10) -> list[dict]:
    rows = db.execute(
        select(IngredientPriceHistory, Ingredient.name, Ingredient.base_dimension, Supplier.name)
        .join(Ingredient, Ingredient.id == IngredientPriceHistory.ingredient_id)
        .outerjoin(Supplier, Supplier.id == IngredientPriceHistory.supplier_id)
        .where(IngredientPriceHistory.branch_id == branch_id, IngredientPriceHistory.is_voided.is_(False),
               IngredientPriceHistory.previous_cost_per_base_unit.is_not(None),
               IngredientPriceHistory.previous_cost_per_base_unit != IngredientPriceHistory.cost_per_base_unit)
        .order_by(IngredientPriceHistory.created_at.desc()).limit(limit)).all()
    out = []
    for h, name, dim, sup in rows:
        prev, new = Decimal(h.previous_cost_per_base_unit), Decimal(h.cost_per_base_unit)
        out.append({"ingredient_id": h.ingredient_id, "ingredient": name, "supplier": sup,
                    "effective_date": h.effective_date, "previous_cost_per_base_unit": prev,
                    "cost_per_base_unit": new, "base_unit": {"mass": "g", "volume": "ml"}.get(dim, "unit"),
                    "change_rate": ((new - prev) / prev) if prev else None})
    return out


def recent_purchases(db: Session, branch_id: uuid.UUID, limit=8) -> list[dict]:
    invs = db.scalars(select(PurchaseInvoice).where(PurchaseInvoice.branch_id == branch_id)
                      .order_by(PurchaseInvoice.created_at.desc()).limit(limit)).all()
    bal = paid_and_credited(db, [i.id for i in invs])
    return [{"id": i.id, "invoice_ref": i.invoice_ref, "supplier": i.supplier.name, "invoice_date": i.invoice_date,
             "total": i.total, "status": i.status, **invoice_balance(i, *bal[i.id])} for i in invs]


def dashboard(db: Session, branch_id: uuid.UUID, today: date | None = None) -> dict:
    today = today or date.today()
    start, end = _month_bounds(today)
    month_purchases = db.scalar(select(func.coalesce(func.sum(PurchaseInvoice.total), 0)).where(
        PurchaseInvoice.branch_id == branch_id, PurchaseInvoice.status == "confirmed",
        PurchaseInvoice.invoice_date >= start.date(), PurchaseInvoice.invoice_date < end.date()))
    levels = stock_levels(db, branch_id)
    recipes = recipe_food_costs(db, branch_id)
    pending = db.scalar(select(func.count()).select_from(OutboxEvent).where(
        OutboxEvent.branch_id == branch_id, OutboxEvent.status.in_(["pending", "retry", "failed"])))
    return {
        "as_of": now(), "period": {"from": start.date(), "to": (end - timedelta(days=1)).date()},
        "inventory_value": {"value": inventory_value(db, branch_id), "cost_basis": "fifo"},
        "month_purchases": money(Decimal(month_purchases)),
        "outstanding_payables": payables(db, branch_id),
        "low_stock": [l for l in levels if l["is_low"] or (l["is_out"] and l["min_stock_base"] > 0)][:100],
        "low_stock_count": sum(1 for l in levels if l["is_low"] or (l["is_out"] and l["min_stock_base"] > 0)),
        "recent_purchases": recent_purchases(db, branch_id),
        "price_changes": price_changes(db, branch_id),
        "recipes": recipes,
        "recipes_over_target": [r for r in recipes if r["exceeds_target"]],
        "consumption": consumption_summary(db, branch_id, start, end),
        "sync": {"pending_events": pending},
    }


def integrity_check(db: Session, branch_id: uuid.UUID) -> dict:
    """Reconciles derived figures with the underlying records, in SQL (fast on large histories)."""
    from sqlalchemy import text as sql
    checks = []
    bad = db.execute(sql("""
        SELECT b.id FROM stock_batch b
        LEFT JOIN (SELECT batch_id, sum(quantity) q FROM stock_allocation GROUP BY batch_id) a ON a.batch_id = b.id
        WHERE b.branch_id = :b AND b.remaining_quantity <> b.original_quantity - COALESCE(a.q, 0) LIMIT 20"""),
        {"b": branch_id}).scalars().all()
    checks.append({"name": "batch_remaining_matches_allocations", "ok": not bad, "detail": [str(x) for x in bad]})
    receipts, allocated, n_in = db.execute(sql("""
        SELECT (SELECT COALESCE(sum(total_cost), 0) FROM stock_movement WHERE branch_id = :b AND quantity > 0),
               (SELECT COALESCE(sum(a.quantity * a.unit_cost), 0) FROM stock_allocation a
                  JOIN stock_batch sb ON sb.id = a.batch_id WHERE sb.branch_id = :b),
               (SELECT count(*) FROM stock_movement WHERE branch_id = :b AND quantity > 0)"""), {"b": branch_id}).one()
    inv_val = inventory_value(db, branch_id)
    tol = Decimal("0.0001") * (n_in + 1)
    diff = abs(inv_val - (Decimal(receipts) - Decimal(allocated)))
    checks.append({"name": "inventory_value_matches_movements", "ok": diff <= tol,
                   "detail": {"batches": inv_val, "receipts_minus_allocations": money(Decimal(receipts) - Decimal(allocated)),
                              "difference": diff, "tolerance": tol}})
    mism = db.execute(sql("""
        WITH o AS (
          SELECT i.supplier_id, sum(CASE WHEN i.status = 'voided' THEN 0
                     ELSE i.total - COALESCE(p.v, 0) - COALESCE(c.v, 0) END) AS outstanding
          FROM purchase_invoice i
          LEFT JOIN (SELECT a.invoice_id, sum(a.amount) v FROM payment_allocation a JOIN supplier_payment sp
                     ON sp.id = a.payment_id WHERE sp.status = 'confirmed' GROUP BY a.invoice_id) p ON p.invoice_id = i.id
          LEFT JOIN (SELECT invoice_id, sum(amount) v FROM supplier_credit WHERE status = 'confirmed'
                     GROUP BY invoice_id) c ON c.invoice_id = i.id
          WHERE i.branch_id = :b GROUP BY i.supplier_id),
        l AS (SELECT supplier_id, sum(amount) AS ledger FROM supplier_ledger_entry WHERE branch_id = :b GROUP BY supplier_id)
        SELECT COALESCE(o.supplier_id, l.supplier_id), COALESCE(l.ledger, 0), COALESCE(o.outstanding, 0)
        FROM o FULL JOIN l ON l.supplier_id = o.supplier_id
        WHERE COALESCE(l.ledger, 0) <> COALESCE(o.outstanding, 0)"""), {"b": branch_id}).all()
    checks.append({"name": "supplier_ledger_matches_invoices", "ok": not mism,
                   "detail": [{"supplier_id": str(s), "ledger": l, "invoices": o} for s, l, o in mism]})
    neg = db.scalar(select(func.count()).select_from(StockBatch).where(StockBatch.remaining_quantity < 0))
    checks.append({"name": "no_negative_batches", "ok": neg == 0, "detail": neg})
    untraced = db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.source_id.is_(None)))
    checks.append({"name": "every_movement_has_a_source", "ok": untraced == 0, "detail": untraced})
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "checked_at": now()}
