"""Supplier invoice balance and payment allocation rules (BR-PAY-*).

Outstanding = invoice total - posted payment allocations - posted credits.

Only POSTED allocations count. A payment that is merely drafted does not
reduce the balance. Over-allocation is rejected, and duplicate payments are
detected by idempotency key before anything is posted.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

ZERO = Decimal("0")


class PostingStatus(str, Enum):
    DRAFT = "draft"
    POSTED = "posted"
    VOIDED = "voided"


class PaymentStatus(str, Enum):
    UNPAID = "unpaid"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"


class PaymentError(ValueError):
    pass


@dataclass(frozen=True)
class Allocation:
    allocation_id: str
    invoice_id: str
    amount: Decimal
    status: PostingStatus


@dataclass(frozen=True)
class Credit:
    credit_id: str
    invoice_id: str
    amount: Decimal
    status: PostingStatus


def outstanding_balance(
    invoice_total: Decimal, allocations: list[Allocation], credits: list[Credit]
) -> Decimal:
    paid = sum((a.amount for a in allocations if a.status is PostingStatus.POSTED), ZERO)
    credited = sum((c.amount for c in credits if c.status is PostingStatus.POSTED), ZERO)
    return invoice_total - paid - credited


def payment_status(invoice_total: Decimal, outstanding: Decimal) -> PaymentStatus:
    if outstanding <= ZERO:
        return PaymentStatus.PAID
    if outstanding < invoice_total:
        return PaymentStatus.PARTIALLY_PAID
    return PaymentStatus.UNPAID


def validate_new_allocation(
    invoice_total: Decimal,
    allocations: list[Allocation],
    credits: list[Credit],
    amount: Decimal,
) -> None:
    if amount <= ZERO:
        raise PaymentError("Allocation amount must be positive")
    remaining = outstanding_balance(invoice_total, allocations, credits)
    if amount > remaining:
        raise PaymentError(
            f"Allocation {amount} exceeds outstanding balance {remaining}"
        )


def check_idempotency(seen_keys: set[str], key: str) -> None:
    if not key:
        raise PaymentError("An idempotency key is required")
    if key in seen_keys:
        raise PaymentError(f"Duplicate submission (idempotency key {key})")
