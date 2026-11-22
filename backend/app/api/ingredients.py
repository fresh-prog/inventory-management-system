import uuid

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain.units import UNITS
from ..jsonenc import J, enc
from ..models import (
    Ingredient, IngredientBranchSetting, IngredientCategory, IngredientCurrentCost, IngredientPriceHistory,
    Supplier, SupplierIngredient,
)
from ..schemas.inputs import IngredientIn, IngredientPatch, PriceIn
from ..security import Principal, require
from ..services import ingredients as svc
from ..services.inventory import on_hand
from .serializers import ingredient_out

router = APIRouter(prefix="/ingredients", tags=["ingredients"])


def _full(db: Session, p: Principal, ing: Ingredient) -> dict:
    sups = db.execute(select(SupplierIngredient, Supplier.name).join(Supplier)
                      .where(SupplierIngredient.ingredient_id == ing.id)).all()
    item = svc.stock_item_for(db, ing.id)
    oh = on_hand(db, p.branch.id, [item.id]).get(item.id)
    return ingredient_out(
        ing, setting=db.get(IngredientBranchSetting, (ing.id, p.branch.id)),
        cost=db.get(IngredientCurrentCost, (ing.id, p.branch.id)),
        suppliers=[{"supplier_id": s.supplier_id, "supplier_name": n, "supplier_sku": s.supplier_sku,
                    "is_preferred": s.is_preferred} for s, n in sups],
        on_hand=oh[0] if oh else 0)


@router.get("/units", tags=["ingredients"])
def units():
    return [{"code": u.code, "dimension": u.dimension.value, "to_base": str(u.to_base)} for u in UNITS.values()]


@router.get("/categories")
def categories(_: Principal = Depends(require("ingredient.read")), db: Session = Depends(get_db)):
    return J([{"id": c.id, "name": c.name} for c in
                db.scalars(select(IngredientCategory).order_by(IngredientCategory.name))])


@router.get("")
def list_ingredients(q: str | None = Query(None, max_length=100), active: bool | None = None, limit: int = Query(200, ge=1, le=5000),
                     offset: int = 0, p: Principal = Depends(require("ingredient.read")),
                     db: Session = Depends(get_db)):
    stmt = select(Ingredient).order_by(Ingredient.name)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Ingredient.name.ilike(like), Ingredient.code.ilike(like)))
    if active is not None:
        stmt = stmt.where(Ingredient.is_active.is_(active))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    ings = db.scalars(stmt.limit(limit).offset(offset)).unique().all()
    ids = [i.id for i in ings]
    costs = {c.ingredient_id: c for c in db.scalars(select(IngredientCurrentCost).where(
        IngredientCurrentCost.branch_id == p.branch.id, IngredientCurrentCost.ingredient_id.in_(ids)))}
    settings = {s.ingredient_id: s for s in db.scalars(select(IngredientBranchSetting).where(
        IngredientBranchSetting.branch_id == p.branch.id, IngredientBranchSetting.ingredient_id.in_(ids)))}
    return J({"total": total, "items": [ingredient_out(i, setting=settings.get(i.id), cost=costs.get(i.id))
                                          for i in ings]})


@router.post("", status_code=201)
def create(body: IngredientIn, p: Principal = Depends(require("ingredient.write")), db: Session = Depends(get_db)):
    ing = svc.create_ingredient(db, p, body.model_dump())
    db.commit()
    return J(_full(db, p, ing), 201)


@router.get("/{ingredient_id}")
def get(ingredient_id: uuid.UUID, p: Principal = Depends(require("ingredient.read")), db: Session = Depends(get_db)):
    return J(_full(db, p, svc.get_ingredient(db, ingredient_id)))


@router.patch("/{ingredient_id}")
def patch(ingredient_id: uuid.UUID, body: IngredientPatch, if_match: int | None = Header(default=None),
          p: Principal = Depends(require("ingredient.write")), db: Session = Depends(get_db)):
    ing = svc.get_ingredient(db, ingredient_id)
    svc.update_ingredient(db, p, ing, body.model_dump(exclude_unset=True), if_match)
    db.commit()
    return J(_full(db, p, ing))


@router.get("/{ingredient_id}/price-history")
def price_history(ingredient_id: uuid.UUID, p: Principal = Depends(require("ingredient.read")),
                  db: Session = Depends(get_db)):
    rows = db.execute(select(IngredientPriceHistory, Supplier.name)
                      .outerjoin(Supplier, Supplier.id == IngredientPriceHistory.supplier_id)
                      .where(IngredientPriceHistory.ingredient_id == ingredient_id,
                             IngredientPriceHistory.branch_id == p.branch.id)
                      .order_by(IngredientPriceHistory.effective_date.desc(),
                                IngredientPriceHistory.created_at.desc())).all()
    return J([{"id": h.id, "source": h.source, "supplier": n, "effective_date": h.effective_date,
                 "purchase_quantity": h.purchase_quantity, "purchase_unit": h.purchase_unit,
                 "purchase_amount": h.purchase_amount, "cost_per_base_unit": h.cost_per_base_unit,
                 "previous_cost_per_base_unit": h.previous_cost_per_base_unit, "currency": h.currency,
                 "is_voided": h.is_voided, "note": h.note, "created_at": h.created_at} for h, n in rows])


@router.post("/{ingredient_id}/prices", status_code=201)
def manual_price(ingredient_id: uuid.UUID, body: PriceIn, p: Principal = Depends(require("ingredient.write")),
                 db: Session = Depends(get_db)):
    """Record a quoted/market price without a purchase (e.g. before the first delivery).
    Updates the CURRENT cost; does not add stock."""
    from datetime import date
    ing = svc.get_ingredient(db, ingredient_id)
    svc.record_manual_price(db, p, ing, body.quantity, body.unit, body.amount,
                            body.effective_date or date.today(), body.note)
    db.commit()
    return J(_full(db, p, ing), 201)
