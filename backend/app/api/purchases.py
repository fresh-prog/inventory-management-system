import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..jsonenc import J, enc
from ..models import PaymentAllocation, PurchaseInvoice, PurchaseLine, Supplier, SupplierCredit, SupplierPayment
from ..schemas.inputs import CreditIn, PaymentIn, PurchaseIn, ReasonIn
from ..security import Principal, require
from ..services import purchasing as svc
from .deps import IdemKey, idempotent_post
from ..services.sync_status import record_sync_status
from .serializers import payment_out, purchase_out

router = APIRouter(tags=["purchases & payments"])


def _out(db, inv, extra=None):
    bal = svc.paid_and_credited(db, [inv.id])[inv.id]
    return purchase_out(inv, svc.invoice_balance(inv, *bal),
                        {"sync_status": record_sync_status(db, [inv.id])[inv.id], **(extra or {})})


@router.get("/purchases")
def list_purchases(supplier_id: uuid.UUID | None = None, status: str | None = None,
                   date_from: date | None = None, date_to: date | None = None,
                   payment_status: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                   p: Principal = Depends(require("purchase.read")), db: Session = Depends(get_db)):
    """Filtering, payment status and pagination are computed in SQL: only one page is loaded."""
    paid = (select(PaymentAllocation.invoice_id.label("iid"), func.sum(PaymentAllocation.amount).label("v"))
            .join(SupplierPayment, SupplierPayment.id == PaymentAllocation.payment_id)
            .where(SupplierPayment.status == "confirmed").group_by(PaymentAllocation.invoice_id).subquery())
    cred = (select(SupplierCredit.invoice_id.label("iid"), func.sum(SupplierCredit.amount).label("v"))
            .where(SupplierCredit.status == "confirmed").group_by(SupplierCredit.invoice_id).subquery())
    nlines = (select(PurchaseLine.invoice_id.label("iid"), func.count().label("n"))
              .group_by(PurchaseLine.invoice_id).subquery())
    PI = PurchaseInvoice
    paid_v, cred_v = func.coalesce(paid.c.v, 0), func.coalesce(cred.c.v, 0)
    outstanding = case((PI.status == "voided", 0), else_=PI.total - paid_v - cred_v)
    pstatus = case((PI.status == "voided", "voided"), (outstanding <= 0, "paid"), (outstanding < PI.total, "partially_paid"),
                   else_="unpaid")
    q = (select(PI.id, PI.invoice_ref, PI.supplier_id, Supplier.name.label("supplier_name"), PI.invoice_date, PI.due_date,
                PI.total, PI.status, func.coalesce(nlines.c.n, 0).label("line_count"), paid_v.label("amount_paid"),
                cred_v.label("amount_credited"), outstanding.label("outstanding"), pstatus.label("payment_status"),
                PI.created_at)
         .join(Supplier, Supplier.id == PI.supplier_id)
         .outerjoin(paid, paid.c.iid == PI.id).outerjoin(cred, cred.c.iid == PI.id).outerjoin(nlines, nlines.c.iid == PI.id)
         .where(PI.branch_id == p.branch.id))
    if supplier_id:
        q = q.where(PI.supplier_id == supplier_id)
    if status:
        q = q.where(PI.status == status)
    if date_from:
        q = q.where(PI.invoice_date >= date_from)
    if date_to:
        q = q.where(PI.invoice_date <= date_to)
    sq = q.subquery()
    filtered = select(sq)
    if payment_status:
        filtered = filtered.where(sq.c.payment_status == payment_status)
    total = db.scalar(select(func.count()).select_from(filtered.subquery()))
    rows = db.execute(filtered.order_by(sq.c.invoice_date.desc(), sq.c.created_at.desc()).limit(limit).offset(offset)).mappings().all()
    items = [{k: v for k, v in r.items() if k != "created_at"} for r in rows]
    return J({"total": total, "limit": limit, "offset": offset, "items": items})


@router.post("/purchases/validate")
def validate(body: PurchaseIn, p: Principal = Depends(require("purchase.create")), db: Session = Depends(get_db)):
    """Dry run: checks arithmetic, units, duplicates. Writes nothing."""
    v = svc.validate_purchase(db, p, body.model_dump())
    db.rollback()
    return J({"ok": True, "subtotal": v["subtotal"], "total": v["total"],
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
    pay_sync = record_sync_status(db, [x.id for x in pays])
    return J(_out(db, inv, {"payments": [
        {**payment_out(x), "allocated": next(a.amount for a in x.allocations if a.invoice_id == inv.id),
         "sync_status": pay_sync[x.id]} for x in pays]}))


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
