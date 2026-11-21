import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..jsonenc import enc
from ..models import PurchaseInvoice, SupplierPayment
from ..schemas.inputs import CreditIn, PaymentIn, PurchaseIn, ReasonIn
from ..security import Principal, require
from ..services import purchasing as svc
from .deps import IdemKey, idempotent_post
from .serializers import payment_out, purchase_out

router = APIRouter(tags=["purchases & payments"])


def _out(db, inv, extra=None):
    bal = svc.paid_and_credited(db, [inv.id])[inv.id]
    return purchase_out(inv, svc.invoice_balance(inv, *bal), extra)


@router.get("/purchases")
def list_purchases(supplier_id: uuid.UUID | None = None, status: str | None = None,
                   date_from: date | None = None, date_to: date | None = None,
                   payment_status: str | None = None, limit: int = Query(50, le=200), offset: int = 0,
                   p: Principal = Depends(require("purchase.read")), db: Session = Depends(get_db)):
    q = select(PurchaseInvoice).where(PurchaseInvoice.branch_id == p.branch.id)
    if supplier_id:
        q = q.where(PurchaseInvoice.supplier_id == supplier_id)
    if status:
        q = q.where(PurchaseInvoice.status == status)
    if date_from:
        q = q.where(PurchaseInvoice.invoice_date >= date_from)
    if date_to:
        q = q.where(PurchaseInvoice.invoice_date <= date_to)
    q = q.order_by(PurchaseInvoice.invoice_date.desc(), PurchaseInvoice.created_at.desc())
    invs = db.scalars(q).all()
    bal = svc.paid_and_credited(db, [i.id for i in invs])
    rows = [{"id": i.id, "invoice_ref": i.invoice_ref, "supplier_id": i.supplier_id, "supplier_name": i.supplier.name,
             "invoice_date": i.invoice_date, "due_date": i.due_date, "total": i.total, "status": i.status,
             "line_count": len(i.lines), **svc.invoice_balance(i, *bal[i.id])} for i in invs]
    if payment_status:
        rows = [r for r in rows if r["payment_status"] == payment_status]
    return enc({"total": len(rows), "items": rows[offset:offset + limit]})


@router.post("/purchases/validate")
def validate(body: PurchaseIn, p: Principal = Depends(require("purchase.create")), db: Session = Depends(get_db)):
    """Dry run: checks arithmetic, units, duplicates. Writes nothing."""
    v = svc.validate_purchase(db, p, body.model_dump())
    db.rollback()
    return enc({"ok": True, "subtotal": v["subtotal"], "total": v["total"],
                "lines": [{"ingredient_id": l["ingredient"].id, "base_quantity": l["base_quantity"],
                           "line_total": l["line_total"], "stock_cost": l["stock_cost"],
                           "cost_per_base_unit": l["cost_per_base_unit"]} for l in v["lines"]]})


@router.post("/purchases", status_code=201)
def confirm(body: PurchaseIn, response: Response, idempotency_key: str | None = IdemKey,
            p: Principal = Depends(require("purchase.create")), db: Session = Depends(get_db)):
    """Confirm a purchase. Atomically records it, adds stock batches, updates prices,
    re-costs recipes and the supplier balance. Requires an Idempotency-Key header."""
    def run():
        inv = svc.confirm_purchase(db, p, body.model_dump())
        return _out(db, inv, {"cost_changes": inv._cost_changes,
                              "supplier_balance": svc.supplier_balance(db, p.branch.id, inv.supplier_id)})
    return idempotent_post(db, p, response, idempotency_key, "purchase.confirm", body.model_dump(mode="json"), run)


@router.get("/purchases/{purchase_id}")
def get(purchase_id: uuid.UUID, p: Principal = Depends(require("purchase.read")), db: Session = Depends(get_db)):
    inv = svc.get_purchase(db, p, purchase_id)
    pays = db.scalars(select(SupplierPayment).join(SupplierPayment.allocations)
                      .where(SupplierPayment.allocations.any(invoice_id=inv.id))).unique().all()
    return enc(_out(db, inv, {"payments": [
        {**payment_out(x), "allocated": next(a.amount for a in x.allocations if a.invoice_id == inv.id)}
        for x in pays]}))


@router.post("/purchases/{purchase_id}/void")
def void(purchase_id: uuid.UUID, body: ReasonIn, response: Response, idempotency_key: str | None = IdemKey,
         p: Principal = Depends(require("purchase.void")), db: Session = Depends(get_db)):
    return idempotent_post(db, p, response, idempotency_key, f"purchase.void:{purchase_id}",
                           body.model_dump(), lambda: _out(db, svc.void_purchase(db, p, purchase_id, body.reason)),
                           200)


@router.post("/payments", status_code=201)
def post_payment(body: PaymentIn, response: Response, idempotency_key: str | None = IdemKey,
                 p: Principal = Depends(require("payment.post")), db: Session = Depends(get_db)):
    def run():
        pay = svc.post_payment(db, p, body.model_dump())
        return {**payment_out(pay), "supplier_balance": svc.supplier_balance(db, p.branch.id, pay.supplier_id)}
    return idempotent_post(db, p, response, idempotency_key, "payment.post", body.model_dump(mode="json"), run)


@router.post("/payments/{payment_id}/void")
def void_payment(payment_id: uuid.UUID, body: ReasonIn, response: Response, idempotency_key: str | None = IdemKey,
                 p: Principal = Depends(require("payment.void")), db: Session = Depends(get_db)):
    return idempotent_post(db, p, response, idempotency_key, f"payment.void:{payment_id}", body.model_dump(),
                           lambda: payment_out(svc.void_payment(db, p, payment_id, body.reason)), 200)


@router.post("/supplier-credits", status_code=201)
def post_credit(body: CreditIn, response: Response, idempotency_key: str | None = IdemKey,
                p: Principal = Depends(require("payment.post")), db: Session = Depends(get_db)):
    def run():
        c = svc.post_credit(db, p, body.model_dump())
        return {"id": c.id, "invoice_id": c.invoice_id, "amount": c.amount, "reason": c.reason}
    return idempotent_post(db, p, response, idempotency_key, "credit.post", body.model_dump(mode="json"), run)
