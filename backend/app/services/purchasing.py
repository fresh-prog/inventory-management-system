"""Purchase confirmation (atomic, cross-module), voids, payments, credits, balances."""

import uuid
from collections import defaultdict
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.invoice import InvoiceInput, InvoiceLine, check_invoice
from ..domain.payables import payment_status
from ..errors import BusinessRuleError, Conflict, NotFound
from ..models import (
    IngredientPriceHistory, PaymentAllocation, PurchaseInvoice, PurchaseLine, StockAllocation,
    StockBatch, StockMovement, Supplier, SupplierCredit, SupplierLedgerEntry, SupplierPayment,
)
from ..security import Principal
from .common import ZERO, money, now, qty, unit_cost
from .ingredients import apply_cost, get_ingredient, recompute_current_cost, stock_item_for, to_base_qty
from .inventory import lock_items, receive
from .platform import audit, emit


# ───────────────────────────── balances ─────────────────────────────
def paid_and_credited(db: Session, invoice_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    if not invoice_ids:
        return {}
    paid = dict(db.execute(
        select(PaymentAllocation.invoice_id, func.sum(PaymentAllocation.amount))
        .join(SupplierPayment, SupplierPayment.id == PaymentAllocation.payment_id)
        .where(PaymentAllocation.invoice_id.in_(invoice_ids), SupplierPayment.status == "confirmed")
        .group_by(PaymentAllocation.invoice_id)).all())
    credited = dict(db.execute(
        select(SupplierCredit.invoice_id, func.sum(SupplierCredit.amount))
        .where(SupplierCredit.invoice_id.in_(invoice_ids), SupplierCredit.status == "confirmed")
        .group_by(SupplierCredit.invoice_id)).all())
    return {i: (Decimal(paid.get(i, 0)), Decimal(credited.get(i, 0))) for i in invoice_ids}


def invoice_balance(inv: PurchaseInvoice, paid: Decimal, credited: Decimal) -> dict:
    if inv.status == "voided":
        return {"amount_paid": paid, "amount_credited": credited, "outstanding": ZERO,
                "payment_status": "voided"}
    outstanding = Decimal(inv.total) - paid - credited
    return {"amount_paid": paid, "amount_credited": credited, "outstanding": outstanding,
            "payment_status": payment_status(Decimal(inv.total), outstanding).value}


def supplier_balance(db: Session, branch_id: uuid.UUID, supplier_id: uuid.UUID) -> Decimal:
    return Decimal(db.scalar(select(func.coalesce(func.sum(SupplierLedgerEntry.amount), 0)).where(
        SupplierLedgerEntry.branch_id == branch_id, SupplierLedgerEntry.supplier_id == supplier_id)))


def _ledger(db, p: Principal, supplier_id, entry_type, source_id, amount, description):
    db.add(SupplierLedgerEntry(branch_id=p.branch.id, supplier_id=supplier_id, entry_type=entry_type,
                               source_id=source_id, amount=money(amount), currency=p.branch.currency,
                               description=description[:200]))


# ───────────────────────────── purchases ─────────────────────────────
def _split(amount: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split ``amount`` proportionally; the last share takes the rounding remainder."""
    total = sum(weights, ZERO)
    if amount == 0 or total == 0:
        return [ZERO] * len(weights)
    shares = [money(amount * w / total) for w in weights[:-1]]
    return shares + [amount - sum(shares, ZERO)]


def validate_purchase(db: Session, p: Principal, data: dict) -> dict:
    """Dry run used by the UI and by confirm(). Never writes."""
    supplier = db.get(Supplier, data["supplier_id"])
    if not supplier:
        raise BusinessRuleError("Please select a supplier", fields={"supplier_id": "required"})
    if not supplier.is_active:
        raise BusinessRuleError(f"Supplier {supplier.name} is inactive")
    if not data["lines"]:
        raise BusinessRuleError("Add at least one item")
    lines = []
    for i, ln in enumerate(data["lines"]):
        ing = get_ingredient(db, ln["ingredient_id"])
        if not ing.is_active:
            raise BusinessRuleError(f"Line {i + 1}: {ing.name} is inactive")
        base = to_base_qty(ing, ln["quantity"], ln["unit"])
        if base <= 0:
            raise BusinessRuleError(f"Line {i + 1}: quantity must be positive")
        lines.append((ing, base))
    check = check_invoice(InvoiceInput(
        lines=tuple(InvoiceLine(ln["quantity"], ln["unit_price"], ln.get("line_discount", ZERO),
                                ln.get("line_total")) for ln in data["lines"]),
        invoice_discount=data.get("discount_total", ZERO), tax_total=data.get("tax_total", ZERO),
        entered_total=data.get("total")))
    if not check.ok:
        raise BusinessRuleError("The invoice does not add up: " + "; ".join(check.errors),
                                errors=list(check.errors))
    dup = db.scalar(select(PurchaseInvoice.id).where(
        PurchaseInvoice.supplier_id == supplier.id,
        func.lower(PurchaseInvoice.invoice_ref) == data["invoice_ref"].strip().lower(),
        PurchaseInvoice.status == "confirmed"))
    if dup:
        raise Conflict(f"Invoice {data['invoice_ref']} from {supplier.name} is already recorded.",
                       existing_purchase_id=str(dup))
    discount_shares = _split(Decimal(data.get("discount_total", ZERO)), list(check.computed_line_totals))
    tax_shares = (_split(Decimal(data.get("tax_total", ZERO)), list(check.computed_line_totals))
                  if p.branch.include_tax_in_cost else [ZERO] * len(lines))
    out = []
    for (ing, base), lt, ds, ts in zip(lines, check.computed_line_totals, discount_shares, tax_shares):
        stock_cost = money(lt - ds + ts)
        out.append({"ingredient": ing, "base_quantity": qty(base), "line_total": money(lt),
                    "stock_cost": stock_cost, "cost_per_base_unit": unit_cost(stock_cost / base)})
    return {"supplier": supplier, "lines": out, "subtotal": money(check.subtotal),
            "total": money(check.computed_total)}


def confirm_purchase(db: Session, p: Principal, data: dict) -> PurchaseInvoice:
    """ONE transaction: invoice + lines + batches + receipt movements + price history +
    current cost + recipe re-costing + supplier ledger + audit + outbox (BR-PUR-3)."""
    v = validate_purchase(db, p, data)
    supplier = v["supplier"]
    received_at = data.get("received_at") or datetime.combine(data["invoice_date"], time(12, 0),
                                                              tzinfo=now().tzinfo)
    inv = PurchaseInvoice(
        branch_id=p.branch.id, supplier_id=supplier.id, invoice_ref=data["invoice_ref"].strip(),
        supplier_tax_ref=data.get("supplier_tax_ref"), invoice_date=data["invoice_date"],
        received_at=received_at, due_date=data.get("due_date"), currency=p.branch.currency,
        subtotal=v["subtotal"], discount_total=money(Decimal(data.get("discount_total", ZERO))),
        tax_total=money(Decimal(data.get("tax_total", ZERO))), total=v["total"], status="confirmed",
        notes=data.get("notes"), created_by=p.user.id)
    db.add(inv)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict(f"Invoice {data['invoice_ref']} from {supplier.name} is already recorded.")

    per_ingredient: dict[uuid.UUID, list] = defaultdict(list)
    for no, (src, calc) in enumerate(zip(data["lines"], v["lines"]), start=1):
        ing = calc["ingredient"]
        line = PurchaseLine(
            invoice_id=inv.id, line_no=no, ingredient_id=ing.id, description=src.get("description"),
            quantity=src["quantity"], unit=src["unit"], unit_price=src["unit_price"],
            line_discount=money(Decimal(src.get("line_discount", ZERO))), line_total=calc["line_total"],
            base_quantity=calc["base_quantity"], stock_cost=calc["stock_cost"],
            cost_per_base_unit=calc["cost_per_base_unit"])
        db.add(line)
        db.flush()
        receive(db, p, stock_item_for(db, ing.id), calc["base_quantity"], calc["cost_per_base_unit"],
                "purchase", inv.id, received_at, supplier_id=supplier.id, purchase_id=inv.id,
                purchase_line_id=line.id, purchase_date=inv.invoice_date,
                note=f"{supplier.name} invoice {inv.invoice_ref}")
        per_ingredient[ing.id].append((ing, line, src))

    cost_changes = []
    for ing_id, items in per_ingredient.items():
        ing = items[0][0]
        base = sum((Decimal(l.base_quantity) for _, l, _ in items), ZERO)
        amount = sum((Decimal(l.stock_cost) for _, l, _ in items), ZERO)
        cpb = unit_cost(amount / base)
        first_line, src = items[0][1], items[0][2]
        h = IngredientPriceHistory(
            ingredient_id=ing_id, branch_id=p.branch.id, source="purchase", supplier_id=supplier.id,
            purchase_line_id=first_line.id, effective_date=inv.invoice_date,
            purchase_quantity=sum((Decimal(l.quantity) for _, l, _ in items), ZERO)
            if len({l.unit for _, l, _ in items}) == 1 else base,
            purchase_unit=first_line.unit if len({l.unit for _, l, _ in items}) == 1 else "base",
            purchase_amount=money(amount), cost_per_base_unit=cpb, currency=p.branch.currency,
            created_by=p.user.id)
        db.add(h)
        db.flush()
        changed = apply_cost(db, p, ing, cpb, inv.invoice_date, h,
                             f"purchase {inv.invoice_ref}: {ing.name}")
        if changed:
            cost_changes.append({"ingredient_id": ing_id, "ingredient": ing.name,
                                 "old": h.previous_cost_per_base_unit, "new": cpb})

    _ledger(db, p, supplier.id, "invoice", inv.id, inv.total, f"Invoice {inv.invoice_ref}")
    audit(db, p, "purchase.confirm", "purchase_invoice", inv.id,
          {"supplier": supplier.name, "invoice_ref": inv.invoice_ref, "total": inv.total,
           "lines": len(data["lines"])})
    emit(db, p.branch.id, "purchase.confirmed", inv.id,
         {"id": inv.id, "supplier_id": supplier.id, "total": inv.total, "invoice_ref": inv.invoice_ref})
    db.flush()
    inv._cost_changes = cost_changes  # transient, for the response
    return inv


def get_purchase(db: Session, p: Principal, purchase_id: uuid.UUID, lock=False) -> PurchaseInvoice:
    q = select(PurchaseInvoice).where(PurchaseInvoice.id == purchase_id,
                                      PurchaseInvoice.branch_id == p.branch.id)
    if lock:
        q = q.with_for_update(of=PurchaseInvoice)
    inv = db.scalar(q)
    if not inv:
        raise NotFound("Purchase not found")
    return inv


def void_purchase(db: Session, p: Principal, purchase_id: uuid.UUID, reason: str) -> PurchaseInvoice:
    inv = get_purchase(db, p, purchase_id, lock=True)
    if inv.status != "confirmed":
        raise BusinessRuleError("This purchase is already voided")
    paid, credited = paid_and_credited(db, [inv.id])[inv.id]
    if paid > 0 or credited > 0:
        raise BusinessRuleError("Void the payments and credits on this invoice first")
    batches = db.scalars(select(StockBatch).where(StockBatch.purchase_id == inv.id)).all()
    lock_items(db, [b.stock_item_id for b in batches])
    batches = db.scalars(select(StockBatch).where(StockBatch.purchase_id == inv.id)
                         .order_by(StockBatch.id).with_for_update()).all()
    for b in batches:
        if b.remaining_quantity != b.original_quantity:
            raise BusinessRuleError(
                "Some of this stock has already been used, so the purchase cannot be voided. "
                "Record a supplier credit instead.")
    for b in batches:
        mv = StockMovement(
            branch_id=p.branch.id, stock_item_id=b.stock_item_id, type="purchase_void",
            quantity=-b.original_quantity, total_cost=-money(Decimal(b.original_quantity) * Decimal(b.unit_cost)),
            currency=b.currency, note=f"Void: {reason}", source_type="purchase", source_id=inv.id,
            created_by=p.user.id, occurred_at=now())
        db.add(mv)
        db.flush()
        db.add(StockAllocation(movement_id=mv.id, batch_id=b.id, quantity=b.original_quantity,
                               unit_cost=b.unit_cost))
        b.remaining_quantity = 0
    line_ids = [ln.id for ln in inv.lines]
    hist = db.scalars(select(IngredientPriceHistory).where(
        IngredientPriceHistory.purchase_line_id.in_(line_ids))).all()
    for h in hist:
        h.is_voided = True
    inv.status, inv.voided_by, inv.voided_at, inv.void_reason = "voided", p.user.id, now(), reason
    db.flush()
    for ing_id in {ln.ingredient_id for ln in inv.lines}:
        recompute_current_cost(db, p, ing_id, f"purchase {inv.invoice_ref} voided")
    _ledger(db, p, inv.supplier_id, "invoice_void", inv.id, -Decimal(inv.total),
            f"Void invoice {inv.invoice_ref}: {reason}")
    audit(db, p, "purchase.void", "purchase_invoice", inv.id, {"reason": reason})
    emit(db, p.branch.id, "purchase.voided", inv.id, {"id": inv.id, "reason": reason})
    return inv


# ───────────────────────────── payments ─────────────────────────────
def _lock_invoices(db, p: Principal, supplier_id, invoice_ids) -> dict[uuid.UUID, PurchaseInvoice]:
    invs = db.scalars(select(PurchaseInvoice).where(PurchaseInvoice.id.in_(invoice_ids))
                      .order_by(PurchaseInvoice.id).with_for_update(of=PurchaseInvoice)).all()
    found = {i.id: i for i in invs}
    for iid in invoice_ids:
        inv = found.get(iid)
        if not inv or inv.branch_id != p.branch.id or inv.supplier_id != supplier_id:
            raise BusinessRuleError("An invoice in this payment does not belong to this supplier")
        if inv.status != "confirmed":
            raise BusinessRuleError(f"Invoice {inv.invoice_ref} is voided")
    return found


def post_payment(db: Session, p: Principal, data: dict) -> SupplierPayment:
    supplier = db.get(Supplier, data["supplier_id"])
    if not supplier:
        raise NotFound("Supplier not found")
    allocs = data["allocations"]
    if not allocs:
        raise BusinessRuleError("Choose at least one invoice to pay")
    ids = [a["invoice_id"] for a in allocs]
    if len(set(ids)) != len(ids):
        raise BusinessRuleError("Each invoice can appear only once in a payment")
    amount = Decimal(data["amount"])
    total_alloc = sum((Decimal(a["amount"]) for a in allocs), ZERO)
    if amount <= 0 or any(Decimal(a["amount"]) <= 0 for a in allocs):
        raise BusinessRuleError("Amounts must be positive")
    if total_alloc != amount:
        raise BusinessRuleError(f"Allocated {total_alloc} but the payment is {amount}. They must match.")
    invs = _lock_invoices(db, p, supplier.id, ids)
    balances = paid_and_credited(db, ids)
    for a in allocs:
        inv = invs[a["invoice_id"]]
        outstanding = Decimal(inv.total) - sum(balances[inv.id], ZERO)
        if Decimal(a["amount"]) > outstanding:
            raise BusinessRuleError(
                f"Invoice {inv.invoice_ref}: payment {a['amount']} exceeds the outstanding {money(outstanding)}")
    pay = SupplierPayment(
        branch_id=p.branch.id, supplier_id=supplier.id, paid_on=data["paid_on"], amount=money(amount),
        currency=p.branch.currency, method=data["method"], reference=(data.get("reference") or None),
        notes=data.get("notes"), status="confirmed", created_by=p.user.id)
    db.add(pay)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict(f"A {data['method']} payment with reference '{data.get('reference')}' "
                       f"to {supplier.name} is already recorded.")
    for a in allocs:
        db.add(PaymentAllocation(payment_id=pay.id, invoice_id=a["invoice_id"], amount=money(Decimal(a["amount"]))))
    _ledger(db, p, supplier.id, "payment", pay.id, -amount,
            f"Payment ({pay.method}{' ' + pay.reference if pay.reference else ''})")
    audit(db, p, "payment.post", "supplier_payment", pay.id,
          {"supplier": supplier.name, "amount": amount, "allocations": allocs})
    emit(db, p.branch.id, "payment.posted", pay.id, {"id": pay.id, "amount": amount})
    db.flush()
    return pay


def void_payment(db: Session, p: Principal, payment_id: uuid.UUID, reason: str) -> SupplierPayment:
    pay = db.scalar(select(SupplierPayment).where(SupplierPayment.id == payment_id,
                                                  SupplierPayment.branch_id == p.branch.id).with_for_update())
    if not pay:
        raise NotFound("Payment not found")
    if pay.status != "confirmed":
        raise BusinessRuleError("This payment is already voided")
    _lock_invoices(db, p, pay.supplier_id, [a.invoice_id for a in pay.allocations])
    pay.status, pay.voided_by, pay.voided_at, pay.void_reason = "voided", p.user.id, now(), reason
    _ledger(db, p, pay.supplier_id, "payment_void", pay.id, Decimal(pay.amount), f"Void payment: {reason}")
    audit(db, p, "payment.void", "supplier_payment", pay.id, {"reason": reason})
    emit(db, p.branch.id, "payment.voided", pay.id, {"id": pay.id})
    return pay


def post_credit(db: Session, p: Principal, data: dict) -> SupplierCredit:
    inv = get_purchase(db, p, data["invoice_id"], lock=True)
    if inv.status != "confirmed":
        raise BusinessRuleError("Cannot credit a voided invoice")
    paid, credited = paid_and_credited(db, [inv.id])[inv.id]
    outstanding = Decimal(inv.total) - paid - credited
    amount = Decimal(data["amount"])
    if amount <= 0 or amount > outstanding:
        raise BusinessRuleError(f"Credit must be between 0 and the outstanding {money(outstanding)}")
    c = SupplierCredit(branch_id=p.branch.id, supplier_id=inv.supplier_id, invoice_id=inv.id,
                       amount=money(amount), reason=data["reason"], reference=data.get("reference"),
                       created_by=p.user.id)
    db.add(c)
    db.flush()
    _ledger(db, p, inv.supplier_id, "credit", c.id, -amount, f"Credit on {inv.invoice_ref}: {data['reason']}")
    audit(db, p, "credit.post", "supplier_credit", c.id, {"invoice": inv.invoice_ref, "amount": amount})
    emit(db, p.branch.id, "credit.posted", c.id, {"id": c.id, "amount": amount})
    return c


