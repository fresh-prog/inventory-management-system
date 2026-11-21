import uuid

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import Conflict, NotFound
from ..jsonenc import enc
from ..models import PurchaseInvoice, Supplier, SupplierLedgerEntry, SupplierPayment
from ..schemas.inputs import SupplierIn
from ..security import Principal, require
from ..services.platform import audit, emit
from ..services.purchasing import invoice_balance, paid_and_credited, supplier_balance
from .serializers import payment_out, supplier_out

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


def _get(db, sid) -> Supplier:
    s = db.get(Supplier, sid)
    if not s:
        raise NotFound("Supplier not found")
    return s


@router.get("")
def list_suppliers(q: str | None = None, p: Principal = Depends(require("supplier.read")),
                   db: Session = Depends(get_db)):
    stmt = select(Supplier).order_by(Supplier.name)
    if q:
        stmt = stmt.where(Supplier.name.ilike(f"%{q}%"))
    balances = dict(db.execute(select(SupplierLedgerEntry.supplier_id, func.sum(SupplierLedgerEntry.amount))
                               .where(SupplierLedgerEntry.branch_id == p.branch.id)
                               .group_by(SupplierLedgerEntry.supplier_id)).all())
    totals = dict(db.execute(select(PurchaseInvoice.supplier_id, func.sum(PurchaseInvoice.total))
                             .where(PurchaseInvoice.branch_id == p.branch.id, PurchaseInvoice.status == "confirmed")
                             .group_by(PurchaseInvoice.supplier_id)).all())
    return enc([{**supplier_out(s, balances.get(s.id, 0)), "total_purchases": totals.get(s.id, 0)}
                for s in db.scalars(stmt)])


@router.post("", status_code=201)
def create(body: SupplierIn, p: Principal = Depends(require("supplier.write")), db: Session = Depends(get_db)):
    s = Supplier(**{**body.model_dump(), "code": body.code.upper()})
    db.add(s)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict(f"Supplier code {body.code} already exists.")
    audit(db, p, "supplier.create", "supplier", s.id, {"name": s.name})
    emit(db, p.branch.id, "supplier.upserted", s.id, {"id": s.id, "version": s.version})
    db.commit()
    return enc(supplier_out(s, 0))


@router.put("/{supplier_id}")
def update(supplier_id: uuid.UUID, body: SupplierIn, if_match: int | None = Header(default=None),
           p: Principal = Depends(require("supplier.write")), db: Session = Depends(get_db)):
    s = _get(db, supplier_id)
    if if_match is not None and if_match != s.version:
        raise Conflict("This supplier was changed by someone else. Reload and try again.")
    for k, v in body.model_dump().items():
        setattr(s, k, v.upper() if k == "code" else v)
    s.version += 1
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict("Another supplier already uses this code.")
    audit(db, p, "supplier.update", "supplier", s.id, body.model_dump())
    emit(db, p.branch.id, "supplier.upserted", s.id, {"id": s.id, "version": s.version})
    db.commit()
    return enc(supplier_out(s, supplier_balance(db, p.branch.id, s.id)))


@router.get("/{supplier_id}")
def get(supplier_id: uuid.UUID, p: Principal = Depends(require("supplier.read")), db: Session = Depends(get_db)):
    s = _get(db, supplier_id)
    invs = db.scalars(select(PurchaseInvoice).where(PurchaseInvoice.supplier_id == s.id,
                                                    PurchaseInvoice.branch_id == p.branch.id)
                      .order_by(PurchaseInvoice.invoice_date.desc(), PurchaseInvoice.created_at.desc())).all()
    bal = paid_and_credited(db, [i.id for i in invs])
    invoices = [{"id": i.id, "invoice_ref": i.invoice_ref, "invoice_date": i.invoice_date, "due_date": i.due_date,
                 "total": i.total, "status": i.status, **invoice_balance(i, *bal[i.id])} for i in invs]
    pays = db.scalars(select(SupplierPayment).where(SupplierPayment.supplier_id == s.id,
                                                    SupplierPayment.branch_id == p.branch.id)
                      .order_by(SupplierPayment.paid_on.desc(), SupplierPayment.created_at.desc())).all()
    ledger = db.scalars(select(SupplierLedgerEntry).where(SupplierLedgerEntry.supplier_id == s.id,
                                                          SupplierLedgerEntry.branch_id == p.branch.id)
                        .order_by(SupplierLedgerEntry.posted_at)).all()
    running, statement = 0, []
    for e in ledger:
        running += e.amount
        statement.append({"posted_at": e.posted_at, "type": e.entry_type, "description": e.description,
                          "amount": e.amount, "balance": running, "source_id": e.source_id})
    confirmed = [i for i in invs if i.status == "confirmed"]
    return enc({**supplier_out(s, supplier_balance(db, p.branch.id, s.id)),
                "total_purchases": sum((i.total for i in confirmed), 0),
                "total_paid": sum((pp.amount for pp in pays if pp.status == "confirmed"), 0),
                "invoices": invoices, "payments": [payment_out(x) for x in pays], "statement": statement})
