from decimal import Decimal as D

import pytest

from app.domain.invoice import InvoiceInput, InvoiceLine, check_invoice
from app.domain.payables import (
    Allocation, Credit, PaymentError, PaymentStatus, PostingStatus,
    check_idempotency, outstanding_balance, payment_status, validate_new_allocation,
)

P, DR = PostingStatus.POSTED, PostingStatus.DRAFT


def test_outstanding_counts_only_posted_items():
    allocs = [Allocation("1", "i", D("30"), P), Allocation("2", "i", D("50"), DR)]
    credits = [Credit("c", "i", D("10"), P)]
    out = outstanding_balance(D("100"), allocs, credits)
    assert out == D("60")
    assert payment_status(D("100"), out) is PaymentStatus.PARTIALLY_PAID
    assert payment_status(D("100"), D("100")) is PaymentStatus.UNPAID
    assert payment_status(D("100"), D("0")) is PaymentStatus.PAID


def test_over_allocation_and_duplicates_rejected():
    allocs = [Allocation("1", "i", D("90"), P)]
    validate_new_allocation(D("100"), allocs, [], D("10"))
    with pytest.raises(PaymentError):
        validate_new_allocation(D("100"), allocs, [], D("10.01"))
    with pytest.raises(PaymentError):
        check_idempotency({"k1"}, "k1")


def test_invoice_arithmetic():
    inv = InvoiceInput(
        lines=(InvoiceLine(D("10"), D("9"), entered_line_total=D("90")),
               InvoiceLine(D("2.5"), D("4"), D("1"), entered_line_total=D("9"))),
        invoice_discount=D("5"), tax_total=D("16.92"), entered_total=D("110.92"),
    )
    r = check_invoice(inv)
    assert r.ok, r.errors
    assert r.subtotal == D("99")


def test_invoice_mismatch_reported():
    inv = InvoiceInput(lines=(InvoiceLine(D("3"), D("3.33"), entered_line_total=D("10.50")),),
                       entered_total=D("12"))
    r = check_invoice(inv)
    assert not r.ok and len(r.errors) == 2
