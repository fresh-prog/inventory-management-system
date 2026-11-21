"""Invoice arithmetic validation (BR-PUR-*).

line_total     = quantity * unit_price - line_discount
subtotal       = sum(line_total)
invoice_total  = subtotal - invoice_discount + tax_total

Suppliers round line totals, so entered totals are accepted within a
configurable tolerance (default: 0.01 per line, 0.05 on the invoice) and the
difference is reported rather than silently absorbed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")


@dataclass(frozen=True)
class InvoiceLine:
    quantity: Decimal
    unit_price: Decimal
    line_discount: Decimal = ZERO
    entered_line_total: Decimal | None = None


@dataclass(frozen=True)
class InvoiceInput:
    lines: tuple[InvoiceLine, ...]
    invoice_discount: Decimal = ZERO
    tax_total: Decimal = ZERO
    entered_total: Decimal | None = None


@dataclass(frozen=True)
class InvoiceCheck:
    computed_line_totals: tuple[Decimal, ...]
    subtotal: Decimal
    computed_total: Decimal
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def check_invoice(
    inv: InvoiceInput,
    line_tolerance: Decimal = Decimal("0.01"),
    total_tolerance: Decimal = Decimal("0.05"),
) -> InvoiceCheck:
    errors: list[str] = []
    if not inv.lines:
        errors.append("Invoice has no lines")
    totals: list[Decimal] = []
    for i, line in enumerate(inv.lines, start=1):
        if line.quantity <= ZERO:
            errors.append(f"Line {i}: quantity must be positive")
        if line.unit_price < ZERO:
            errors.append(f"Line {i}: unit price cannot be negative")
        if line.line_discount < ZERO:
            errors.append(f"Line {i}: discount cannot be negative")
        computed = line.quantity * line.unit_price - line.line_discount
        if computed < ZERO:
            errors.append(f"Line {i}: discount exceeds line value")
        if (
            line.entered_line_total is not None
            and abs(line.entered_line_total - computed) > line_tolerance
        ):
            errors.append(
                f"Line {i}: entered total {line.entered_line_total} != computed {computed}"
            )
        totals.append(computed)

    subtotal = sum(totals, ZERO)
    if inv.invoice_discount < ZERO or inv.invoice_discount > subtotal:
        errors.append("Invoice discount must be between 0 and the subtotal")
    if inv.tax_total < ZERO:
        errors.append("Tax cannot be negative")
    computed_total = subtotal - inv.invoice_discount + inv.tax_total
    if inv.entered_total is not None and abs(inv.entered_total - computed_total) > total_tolerance:
        errors.append(
            f"Invoice total {inv.entered_total} != computed {computed_total}"
        )
    return InvoiceCheck(tuple(totals), subtotal, computed_total, tuple(errors))
