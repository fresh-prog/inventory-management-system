import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..jsonenc import J, enc
from ..models import Ingredient, StockBatch, StockCount, StockItem, StockMovement, User
from ..schemas.inputs import AdjustmentIn, ConsumptionIn, CountLinesIn, CountStartIn, ProductionIn
from ..security import Principal, require
from ..services import stock_ops
from ..services.ingredients import base_unit, from_base_qty, get_ingredient, stock_item_for
from ..services.recipes import published_version
from ..services.reports import stock_levels
from .deps import IdemKey, idempotent_post

router = APIRouter(prefix="/stock", tags=["stock & inventory"])


@router.get("")
def levels(include_inactive: bool = False, p: Principal = Depends(require("stock.read")),
           db: Session = Depends(get_db)):
    rows = stock_levels(db, p.branch.id, include_inactive)
    from ..services.reports import inventory_value
    total = inventory_value(db, p.branch.id)  # same definition as the dashboard (exact sum, rounded once)
    return J({"cost_basis": "fifo", "total_value": total, "items": rows})


@router.get("/ingredients/{ingredient_id}/batches")
def batches(ingredient_id: uuid.UUID, include_empty: bool = False,
            p: Principal = Depends(require("stock.read")), db: Session = Depends(get_db)):
    ing = get_ingredient(db, ingredient_id)
    item = stock_item_for(db, ing.id)
    q = select(StockBatch).where(StockBatch.stock_item_id == item.id, StockBatch.branch_id == p.branch.id)
    if not include_empty:
        q = q.where(StockBatch.remaining_quantity > 0)
    rows = db.scalars(q.order_by(StockBatch.received_at, StockBatch.sequence)).all()
    return J({"ingredient_id": ing.id, "name": ing.name, "base_unit": base_unit(ing), "cost_basis": "fifo",
                "batches": [{"id": b.id, "sequence": b.sequence, "source_type": b.source_type,
                             "purchase_id": b.purchase_id, "supplier": b.supplier.name if b.supplier else None,
                             "purchase_date": b.purchase_date, "received_at": b.received_at,
                             "original_quantity": b.original_quantity, "remaining_quantity": b.remaining_quantity,
                             "unit_cost": b.unit_cost, "remaining_value": b.remaining_quantity * b.unit_cost,
                             "currency": b.currency, "fifo_position": i + 1}
                            for i, b in enumerate(rows)]})


@router.get("/movements")
def movements(ingredient_id: uuid.UUID | None = None, type: str | None = None,
              date_from: datetime | None = None, date_to: datetime | None = None,
              limit: int = Query(100, le=500), offset: int = 0,
              p: Principal = Depends(require("stock.read")), db: Session = Depends(get_db)):
    q = (select(StockMovement, Ingredient, User.display_name)
         .join(StockItem, StockItem.id == StockMovement.stock_item_id)
         .join(Ingredient, Ingredient.id == StockItem.ingredient_id)
         .outerjoin(User, User.id == StockMovement.created_by)
         .where(StockMovement.branch_id == p.branch.id))
    if ingredient_id:
        q = q.where(Ingredient.id == ingredient_id)
    if type:
        q = q.where(StockMovement.type == type)
    if date_from:
        q = q.where(StockMovement.occurred_at >= date_from)
    if date_to:
        q = q.where(StockMovement.occurred_at <= date_to)
    rows = db.execute(q.order_by(StockMovement.recorded_at.desc()).limit(limit).offset(offset)).all()
    return J({"cost_basis": "fifo", "items": [{
        "id": m.id, "ingredient_id": ing.id, "ingredient_name": ing.name, "type": m.type,
        "quantity_base": m.quantity, "base_unit": base_unit(ing), "total_cost": m.total_cost,
        "shortfall_quantity": m.shortfall_quantity, "reason": m.reason, "note": m.note,
        "source_type": m.source_type, "source_id": m.source_id, "occurred_at": m.occurred_at,
        "recorded_at": m.recorded_at, "created_by": who,
        "allocations": [{"batch_id": a.batch_id, "quantity": a.quantity, "unit_cost": a.unit_cost}
                        for a in m.allocations]} for m, ing, who in rows]})


@router.post("/consumptions", status_code=201)
def consumption(body: ConsumptionIn, response: Response, idempotency_key: str | None = IdemKey,
                p: Principal = Depends(require("stock.consume")), db: Session = Depends(get_db)):
    return idempotent_post(db, p, response, idempotency_key, "stock.consume", body.model_dump(mode="json"),
                           lambda: stock_ops.record_consumption(db, p, body.model_dump()))


@router.post("/adjustments", status_code=201)
def adjustment(body: AdjustmentIn, response: Response, idempotency_key: str | None = IdemKey,
               p: Principal = Depends(require("stock.adjust")), db: Session = Depends(get_db)):
    return idempotent_post(db, p, response, idempotency_key, "stock.adjust", body.model_dump(mode="json"),
                           lambda: stock_ops.record_adjustment(db, p, body.model_dump()))


@router.get("/production/requirements")
def requirements(recipe_id: uuid.UUID, batches: float | None = None, portions: float | None = None,
                 p: Principal = Depends(require("stock.read")), db: Session = Depends(get_db)):
    """Preview what a production run would deduct (no posting)."""
    from decimal import Decimal
    from ..errors import BusinessRuleError
    v = published_version(db, recipe_id)
    if not v:
        raise BusinessRuleError("This recipe has no published version")
    b = Decimal(str(batches)) if batches else Decimal(str(portions or 0)) / v.portions_per_batch
    if b <= 0:
        raise BusinessRuleError("Enter batches or portions")
    from ..services.inventory import on_hand
    reqs = stock_ops.production_requirements(db, v, b)
    oh = on_hand(db, p.branch.id, [stock_item_for(db, ing.id).id for ing, _ in reqs])
    out = []
    for ing, base in reqs:
        have = oh.get(stock_item_for(db, ing.id).id, (Decimal(0), 0))[0]
        out.append({"ingredient_id": ing.id, "name": ing.name, "required_base": base, "base_unit": base_unit(ing),
                    "required": from_base_qty(ing, base, ing.consumption_unit), "unit": ing.consumption_unit,
                    "on_hand_base": have, "sufficient": have >= base})
    return J({"recipe_version": v.version_no, "batches": b, "portions": b * v.portions_per_batch, "lines": out})


@router.post("/production-runs", status_code=201)
def production(body: ProductionIn, response: Response, idempotency_key: str | None = IdemKey,
               p: Principal = Depends(require("production.record")), db: Session = Depends(get_db)):
    def run():
        r = stock_ops.record_production(db, p, body.model_dump())
        return {"id": r.id, "recipe_version_no": r.recipe_version.version_no, "batches": r.batches,
                "expected_portions": r.expected_portions, "actual_portions": r.actual_portions,
                "total_cost": r.total_cost, "cost_per_portion": r.cost_per_portion, "cost_basis": "fifo"}
    return idempotent_post(db, p, response, idempotency_key, "production.record", body.model_dump(mode="json"), run)


def _count_out(db, c: StockCount) -> dict:
    lines = []
    from ..services.inventory import on_hand
    oh = on_hand(db, c.branch_id, [l.stock_item_id for l in c.lines]) if c.status == "open" else {}
    for l in c.lines:
        item = db.get(StockItem, l.stock_item_id)
        ing = db.get(Ingredient, item.ingredient_id)
        system = l.expected_at_approval if c.status != "open" else oh.get(item.id, (Decimal(0), 0))[0]
        unit = ing.consumption_unit
        def disp(x):
            return None if x is None else from_base_qty(ing, x, unit)
        variance = (l.counted_quantity - system) if l.counted_quantity is not None else None
        lines.append({"ingredient_id": ing.id, "name": ing.name, "unit": unit, "base_unit": base_unit(ing),
                      "expected_at_start": disp(l.expected_at_start), "system_quantity": disp(system),
                      "counted": disp(l.counted_quantity), "variance": disp(variance),
                      "variance_value": l.variance_value})
    lines.sort(key=lambda x: x["name"])
    return {"id": c.id, "status": c.status, "note": c.note, "started_at": c.started_at,
            "approved_at": c.approved_at, "lines": lines}


@router.get("/counts")
def counts(p: Principal = Depends(require("stock.read")), db: Session = Depends(get_db)):
    rows = db.scalars(select(StockCount).where(StockCount.branch_id == p.branch.id)
                      .order_by(StockCount.started_at.desc()).limit(50)).all()
    return J([{"id": c.id, "status": c.status, "note": c.note, "started_at": c.started_at,
                 "approved_at": c.approved_at, "lines": len(c.lines),
                 "counted": sum(1 for l in c.lines if l.counted_quantity is not None)} for c in rows])


@router.post("/counts", status_code=201)
def start_count(body: CountStartIn, p: Principal = Depends(require("stock.count")), db: Session = Depends(get_db)):
    c = stock_ops.start_count(db, p, body.note, body.ingredient_ids)
    db.commit()
    return J(_count_out(db, c), 201)


@router.get("/counts/{count_id}")
def get_count(count_id: uuid.UUID, p: Principal = Depends(require("stock.read")), db: Session = Depends(get_db)):
    return J(_count_out(db, stock_ops.get_count(db, p, count_id)))


@router.put("/counts/{count_id}/lines")
def enter(count_id: uuid.UUID, body: CountLinesIn, p: Principal = Depends(require("stock.count")),
          db: Session = Depends(get_db)):
    c = stock_ops.enter_counts(db, p, count_id, body.model_dump()["lines"])
    db.commit()
    return J(_count_out(db, c))


@router.post("/counts/{count_id}/approve")
def approve(count_id: uuid.UUID, response: Response, idempotency_key: str | None = IdemKey,
            p: Principal = Depends(require("stock.count.approve")), db: Session = Depends(get_db)):
    return idempotent_post(db, p, response, idempotency_key, f"count.approve:{count_id}", {},
                           lambda: _count_out(db, stock_ops.approve_count(db, p, count_id)), 200)
