"""Stock receipts and FIFO consumption with row-level locking (ADR-0005)."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.fifo import (
    InsufficientStockError, InsufficientStockPolicy, StockBatch as DomainBatch, allocate_fifo,
)
from ..errors import BusinessRuleError, InsufficientStock
from ..models import Ingredient, IngredientCurrentCost, StockAllocation, StockBatch, StockItem, StockMovement
from ..security import Principal
from .common import ZERO, money, now, qty


def on_hand(db: Session, branch_id: uuid.UUID, stock_item_ids: list[uuid.UUID] | None = None):
    q = (select(StockBatch.stock_item_id,
                func.coalesce(func.sum(StockBatch.remaining_quantity), 0),
                func.coalesce(func.sum(StockBatch.remaining_quantity * StockBatch.unit_cost), 0))
         .where(StockBatch.branch_id == branch_id, StockBatch.remaining_quantity > 0)
         .group_by(StockBatch.stock_item_id))
    if stock_item_ids is not None:
        q = q.where(StockBatch.stock_item_id.in_(stock_item_ids))
    return {sid: (Decimal(qt), Decimal(v)) for sid, qt, v in db.execute(q)}


def receive(db: Session, p: Principal, item: StockItem, quantity_base: Decimal, unit_cost: Decimal,
            source_type: str, source_id: uuid.UUID, received_at: datetime, *,
            supplier_id=None, purchase_id=None, purchase_line_id=None, purchase_date=None,
            movement_type="receipt", reason=None, note=None) -> StockBatch:
    if quantity_base <= 0:
        raise BusinessRuleError("Received quantity must be positive")
    batch = StockBatch(
        branch_id=p.branch.id, stock_item_id=item.id, source_type=source_type, source_id=source_id,
        supplier_id=supplier_id, purchase_id=purchase_id, purchase_line_id=purchase_line_id,
        purchase_date=purchase_date, received_at=received_at,
        original_quantity=quantity_base, remaining_quantity=quantity_base,
        unit_cost=unit_cost, currency=p.branch.currency)
    db.add(batch)
    db.flush()
    db.add(StockMovement(
        branch_id=p.branch.id, stock_item_id=item.id, type=movement_type, quantity=quantity_base,
        total_cost=money(quantity_base * unit_cost), currency=p.branch.currency, reason=reason,
        note=note, source_type=source_type, source_id=source_id, created_by=p.user.id,
        occurred_at=received_at))
    return batch


def lock_items(db: Session, item_ids: list[uuid.UUID]) -> None:
    """Lock the stock_item rows in a deterministic order. Every writer of an item's stock
    takes this lock first, so concurrent writers serialise per ingredient and multi-ingredient
    transactions cannot deadlock."""
    for sid in sorted(set(item_ids)):
        db.execute(select(StockItem.id).where(StockItem.id == sid).with_for_update())


def consume(db: Session, p: Principal, item: StockItem, quantity_base: Decimal, *,
            movement_type: str, source_type: str, source_id: uuid.UUID | None,
            reason: str | None = None, note: str | None = None,
            allow_shortfall: bool = False, occurred_at: datetime | None = None,
            label: str = "") -> StockMovement:
    """Deduct ``quantity_base`` from the oldest eligible batches (FIFO) and record it."""
    if quantity_base <= 0:
        raise BusinessRuleError("Quantity must be positive")
    lock_items(db, [item.id])
    rows = db.scalars(
        select(StockBatch)
        .where(StockBatch.stock_item_id == item.id, StockBatch.branch_id == p.branch.id,
               StockBatch.remaining_quantity > 0, StockBatch.is_quarantined.is_(False))
        .order_by(StockBatch.received_at, StockBatch.sequence)
        .with_for_update()).all()
    domain = [DomainBatch(
        batch_id=str(b.id), ingredient_id=str(item.id), branch_id=str(p.branch.id),
        received_at=b.received_at, sequence=b.sequence,
        original_quantity=Decimal(b.original_quantity), remaining_quantity=Decimal(b.remaining_quantity),
        unit_cost=Decimal(b.unit_cost), currency=b.currency) for b in rows]
    policy = InsufficientStockPolicy.ALLOW_SHORTFALL if allow_shortfall else InsufficientStockPolicy.BLOCK
    try:
        result = allocate_fifo(str(item.id), str(p.branch.id), quantity_base, domain, policy)
    except InsufficientStockError as e:
        raise InsufficientStock(
            f"{label or 'Item'}: requested {qty(e.requested).normalize():f}, "
            f"available {qty(e.available).normalize():f} (base units)",
            stock_item_id=str(item.id), requested=str(e.requested), available=str(e.available))

    by_id = {str(b.id): b for b in rows}
    for ub in result.updated_batches:
        by_id[ub.batch_id].remaining_quantity = ub.remaining_quantity
    fifo_cost = result.total_cost
    shortfall_cost = ZERO
    if result.shortfall > 0:
        cur = db.get(IngredientCurrentCost, (item.ingredient_id, p.branch.id)) if item.ingredient_id else None
        shortfall_cost = result.shortfall * Decimal(cur.cost_per_base_unit if cur else 0)
    mv = StockMovement(
        branch_id=p.branch.id, stock_item_id=item.id, type=movement_type,
        quantity=-quantity_base, total_cost=-money(fifo_cost + shortfall_cost),
        shortfall_quantity=result.shortfall, currency=p.branch.currency, reason=reason, note=note,
        source_type=source_type, source_id=source_id, created_by=p.user.id,
        occurred_at=occurred_at or now())
    db.add(mv)
    db.flush()
    for a in result.allocations:
        db.add(StockAllocation(movement_id=mv.id, batch_id=uuid.UUID(a.batch_id),
                               quantity=a.quantity, unit_cost=a.unit_cost))
    db.flush()
    return mv


def item_label(db: Session, item: StockItem) -> str:
    if item.ingredient_id:
        ing = db.get(Ingredient, item.ingredient_id)
        return ing.name if ing else "Ingredient"
    return "Finished good"
