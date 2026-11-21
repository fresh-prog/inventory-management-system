"""Dashboard and reports. Every figure states its cost basis (ADR-0006)."""

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Ingredient, IngredientBranchSetting, IngredientCategory, IngredientCurrentCost, IngredientPriceHistory,
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
    q = (select(Ingredient, StockItem.id, IngredientBranchSetting.min_stock_base,
                IngredientCurrentCost.cost_per_base_unit, IngredientCategory.name)
         .join(StockItem, StockItem.ingredient_id == Ingredient.id)
         .outerjoin(IngredientBranchSetting, (IngredientBranchSetting.ingredient_id == Ingredient.id)
                    & (IngredientBranchSetting.branch_id == branch_id))
         .outerjoin(IngredientCurrentCost, (IngredientCurrentCost.ingredient_id == Ingredient.id)
                    & (IngredientCurrentCost.branch_id == branch_id))
         .outerjoin(IngredientCategory, IngredientCategory.id == Ingredient.category_id)
         .order_by(Ingredient.name))
    if not include_inactive:
        q = q.where(Ingredient.is_active)
    agg = dict((sid, (q_, v, n)) for sid, q_, v, n in db.execute(
        select(StockBatch.stock_item_id, func.sum(StockBatch.remaining_quantity),
               func.sum(StockBatch.remaining_quantity * StockBatch.unit_cost), func.count())
        .where(StockBatch.branch_id == branch_id, StockBatch.remaining_quantity > 0)
        .group_by(StockBatch.stock_item_id)))
    out = []
    for ing, sid, min_base, cpb, cat in db.execute(q):
        on_hand, value, batches = agg.get(sid, (ZERO, ZERO, 0))
        on_hand, min_base = Decimal(on_hand), Decimal(min_base or 0)
        unit = ing.consumption_unit
        try:
            display = from_base_qty(ing, on_hand, unit)
            display_min = from_base_qty(ing, min_base, unit)
        except Exception:
            unit, display, display_min = base_unit(ing), on_hand, min_base
        out.append({
            "ingredient_id": ing.id, "code": ing.code, "name": ing.name, "category": cat,
            "base_unit": base_unit(ing), "on_hand_base": on_hand, "min_stock_base": min_base,
            "display_unit": unit, "on_hand": display, "min_stock": display_min,
            "inventory_value": money(Decimal(value)), "open_batches": batches,
            "current_cost_per_base_unit": cpb, "is_low": min_base > 0 and on_hand < min_base,
            "is_out": on_hand <= 0, "is_active": ing.is_active,
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
        select(Recipe, RecipeVersion, RecipeCostSnapshot)
        .join(RecipeVersion, RecipeVersion.recipe_id == Recipe.id)
        .join(latest, latest.c.recipe_version_id == RecipeVersion.id)
        .join(RecipeCostSnapshot, (RecipeCostSnapshot.recipe_version_id == RecipeVersion.id)
              & (RecipeCostSnapshot.computed_at == latest.c.at) & (RecipeCostSnapshot.branch_id == branch_id))
        .where(RecipeVersion.status == "published").order_by(Recipe.name)).all()
    return [{"recipe_id": r.id, "name": r.name, "version_no": v.version_no, "cost_basis": "current",
             "cost_per_portion": s.cost_per_portion, "selling_price": v.selling_price,
             "food_cost_rate": s.food_cost_rate, "target_food_cost_rate": v.target_food_cost_rate,
             "recommended_price": s.recommended_price, "exceeds_target": s.exceeds_target,
             "computed_at": s.computed_at} for r, v, s in rows]


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
        OutboxEvent.branch_id == branch_id, OutboxEvent.acked_at.is_(None)))
    return {
        "as_of": now(), "period": {"from": start.date(), "to": (end - timedelta(days=1)).date()},
        "inventory_value": {"value": inventory_value(db, branch_id), "cost_basis": "fifo"},
        "month_purchases": money(Decimal(month_purchases)),
        "outstanding_payables": payables(db, branch_id),
        "low_stock": [l for l in levels if l["is_low"] or (l["is_out"] and l["min_stock_base"] > 0)],
        "stock": levels,
        "recent_purchases": recent_purchases(db, branch_id),
        "price_changes": price_changes(db, branch_id),
        "recipes": recipes,
        "recipes_over_target": [r for r in recipes if r["exceeds_target"]],
        "consumption": consumption_summary(db, branch_id, start, end),
        "sync": {"pending_events": pending},
    }


def integrity_check(db: Session, branch_id: uuid.UUID) -> dict:
    """Reconciles derived figures with the underlying records. All checks must pass."""
    checks = []
    # 1. every batch: original - Σ allocations + Σ receipts-into-same-batch == remaining
    alloc = dict(db.execute(select(StockAllocation.batch_id, func.sum(StockAllocation.quantity))
                            .group_by(StockAllocation.batch_id)).all())
    bad = []
    for b in db.scalars(select(StockBatch).where(StockBatch.branch_id == branch_id)):
        expected = Decimal(b.original_quantity) - Decimal(alloc.get(b.id, 0))
        if expected != Decimal(b.remaining_quantity):
            bad.append(str(b.id))
    checks.append({"name": "batch_remaining_matches_allocations", "ok": not bad, "detail": bad[:20]})
    # 2. inventory value: Σ batch value == Σ receipts − Σ FIFO allocations (rounding tolerance)
    receipts = Decimal(db.scalar(select(func.coalesce(func.sum(StockMovement.total_cost), 0)).where(
        StockMovement.branch_id == branch_id, StockMovement.quantity > 0)))
    allocated = Decimal(db.scalar(
        select(func.coalesce(func.sum(StockAllocation.quantity * StockAllocation.unit_cost), 0))
        .join(StockBatch, StockBatch.id == StockAllocation.batch_id).where(StockBatch.branch_id == branch_id)))
    inv_val = inventory_value(db, branch_id)
    n_in = db.scalar(select(func.count()).select_from(StockMovement).where(
        StockMovement.branch_id == branch_id, StockMovement.quantity > 0))
    tol = Decimal("0.0001") * (n_in + 1)
    diff = abs(inv_val - (receipts - allocated))
    checks.append({"name": "inventory_value_matches_movements", "ok": diff <= tol,
                   "detail": {"batches": inv_val, "receipts_minus_allocations": money(receipts - allocated),
                              "difference": diff, "tolerance": tol}})
    # 3. payables: Σ ledger == Σ invoice outstanding (per supplier)
    invs = db.scalars(select(PurchaseInvoice).where(PurchaseInvoice.branch_id == branch_id)).all()
    bal = paid_and_credited(db, [i.id for i in invs])
    per_sup: dict[uuid.UUID, Decimal] = {}
    for i in invs:
        per_sup[i.supplier_id] = per_sup.get(i.supplier_id, ZERO) + invoice_balance(i, *bal[i.id])["outstanding"]
    ledger = dict(db.execute(select(SupplierLedgerEntry.supplier_id, func.sum(SupplierLedgerEntry.amount))
                             .where(SupplierLedgerEntry.branch_id == branch_id)
                             .group_by(SupplierLedgerEntry.supplier_id)).all())
    mismatches = [{"supplier_id": str(s), "ledger": Decimal(ledger.get(s, 0)), "invoices": v}
                  for s, v in per_sup.items() if Decimal(ledger.get(s, 0)) != v]
    mismatches += [{"supplier_id": str(s), "ledger": Decimal(v), "invoices": ZERO}
                   for s, v in ledger.items() if s not in per_sup and Decimal(v) != 0]
    checks.append({"name": "supplier_ledger_matches_invoices", "ok": not mismatches, "detail": mismatches})
    # 4. no negative stock
    neg = db.scalar(select(func.count()).select_from(StockBatch).where(StockBatch.remaining_quantity < 0))
    checks.append({"name": "no_negative_batches", "ok": neg == 0, "detail": neg})
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "checked_at": now()}
