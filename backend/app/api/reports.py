from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..jsonenc import enc
from ..models import AuditLog, OutboxEvent, PurchaseInvoice, Supplier, User
from ..security import Principal, require
from ..services import reports as svc
from ..services.common import money, now

router = APIRouter(tags=["dashboard & reports"])


def _range(date_from: date | None, date_to: date | None):
    today = date.today()
    df = date_from or today.replace(day=1)
    dt = date_to or today
    tz = now().tzinfo
    return df, dt, datetime.combine(df, time(), tz), datetime.combine(dt + timedelta(days=1), time(), tz)


@router.get("/dashboard")
def dashboard(p: Principal = Depends(require("report.read")), db: Session = Depends(get_db)):
    return enc(svc.dashboard(db, p.branch.id))


@router.get("/reports/inventory-valuation")
def valuation(p: Principal = Depends(require("report.read")), db: Session = Depends(get_db)):
    rows = svc.stock_levels(db, p.branch.id, include_inactive=True)
    rows = [r for r in rows if r["on_hand_base"] > 0 or r["is_active"]]
    return enc({"cost_basis": "fifo", "as_of": now(), "total_value": svc.inventory_value(db, p.branch.id),
                "items": rows})


@router.get("/reports/purchases")
def purchases(date_from: date | None = None, date_to: date | None = None,
              p: Principal = Depends(require("report.read")), db: Session = Depends(get_db)):
    df, dt, _, _ = _range(date_from, date_to)
    rows = db.execute(select(Supplier.name, func.count(PurchaseInvoice.id), func.sum(PurchaseInvoice.total))
                      .join(Supplier, Supplier.id == PurchaseInvoice.supplier_id)
                      .where(PurchaseInvoice.branch_id == p.branch.id, PurchaseInvoice.status == "confirmed",
                             PurchaseInvoice.invoice_date >= df, PurchaseInvoice.invoice_date <= dt)
                      .group_by(Supplier.name).order_by(func.sum(PurchaseInvoice.total).desc())).all()
    return enc({"from": df, "to": dt, "total": money(sum((r[2] for r in rows), 0)) if rows else 0,
                "by_supplier": [{"supplier": n, "invoices": c, "total": t} for n, c, t in rows]})


@router.get("/reports/consumption")
def consumption(date_from: date | None = None, date_to: date | None = None,
                p: Principal = Depends(require("report.read")), db: Session = Depends(get_db)):
    df, dt, start, end = _range(date_from, date_to)
    return enc({"from": df, "to": dt, **svc.consumption_summary(db, p.branch.id, start, end)})


@router.get("/reports/recipe-costs")
def recipe_costs(p: Principal = Depends(require("report.read")), db: Session = Depends(get_db)):
    return enc({"cost_basis": "current", "items": svc.recipe_food_costs(db, p.branch.id)})


@router.get("/reports/price-changes")
def price_changes(limit: int = Query(50, le=200), p: Principal = Depends(require("report.read")),
                  db: Session = Depends(get_db)):
    return enc(svc.price_changes(db, p.branch.id, limit))


@router.get("/reports/integrity")
def integrity(p: Principal = Depends(require("report.read")), db: Session = Depends(get_db)):
    """Reconciles dashboard figures with the underlying records."""
    return enc(svc.integrity_check(db, p.branch.id))


@router.get("/audit-log")
def audit_log(entity: str | None = None, limit: int = Query(100, le=500), offset: int = 0,
              p: Principal = Depends(require("settings.manage")), db: Session = Depends(get_db)):
    q = (select(AuditLog, User.display_name).outerjoin(User, User.id == AuditLog.user_id)
         .where(AuditLog.branch_id == p.branch.id))
    if entity:
        q = q.where(AuditLog.entity == entity)
    rows = db.execute(q.order_by(AuditLog.at.desc()).limit(limit).offset(offset)).all()
    return enc([{"id": a.id, "at": a.at, "user": u, "action": a.action, "entity": a.entity,
                 "entity_id": a.entity_id, "detail": a.detail} for a, u in rows])


@router.get("/sync/status")
def sync_status(p: Principal = Depends(require("report.read")), db: Session = Depends(get_db)):
    from ..config import get_settings
    pending = db.scalar(select(func.count()).select_from(OutboxEvent).where(
        OutboxEvent.branch_id == p.branch.id, OutboxEvent.acked_at.is_(None)))
    oldest = db.scalar(select(func.min(OutboxEvent.created_at)).where(
        OutboxEvent.branch_id == p.branch.id, OutboxEvent.acked_at.is_(None)))
    s = get_settings()
    return enc({"mode": "local_branch", "cloud_configured": bool(s.cloud_sync_url),
                "pending_events": pending, "oldest_pending_at": oldest,
                "note": "Cloud synchronisation is not enabled in this release. Events are stored durably "
                        "in the outbox and will be sent once sync is enabled."})
