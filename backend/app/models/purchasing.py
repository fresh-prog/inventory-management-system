import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, Money, Qty, UnitCost, created_at_col
from ..ids import uuid7


class Supplier(Base):
    __tablename__ = "supplier"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    tax_id: Mapped[str | None] = mapped_column(String(40))
    contact_person: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(254))
    address: Mapped[str | None] = mapped_column(Text)
    payment_terms_days: Mapped[int | None]
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = created_at_col()


class PurchaseInvoice(Base):
    __tablename__ = "purchase_invoice"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier.id"))
    invoice_ref: Mapped[str] = mapped_column(String(60))
    supplier_tax_ref: Mapped[str | None] = mapped_column(String(80))  # e.g. EFRIS FDN
    invoice_date: Mapped[date] = mapped_column(Date)
    received_at: Mapped[datetime]
    due_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    subtotal: Mapped[Decimal] = mapped_column(Money)
    discount_total: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    tax_total: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    total: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(12), default="confirmed")
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = created_at_col()
    voided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    voided_at: Mapped[datetime | None]
    void_reason: Mapped[str | None] = mapped_column(String(200))

    supplier = relationship("Supplier", lazy="joined")
    lines = relationship("PurchaseLine", order_by="PurchaseLine.line_no", lazy="selectin")
    __table_args__ = (
        CheckConstraint("status IN ('confirmed','voided')", name="status"),
        CheckConstraint("total >= 0 AND subtotal >= 0 AND discount_total >= 0 AND tax_total >= 0",
                        name="amounts"),
        # same supplier invoice cannot be confirmed twice
        Index("uq_supplier_invoice_confirmed", "supplier_id", "invoice_ref", unique=True,
              postgresql_where="status = 'confirmed'"),
        Index("ix_purchase_branch_date", "branch_id", "invoice_date"),
    )


class PurchaseLine(Base):
    __tablename__ = "purchase_line"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_invoice.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    ingredient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingredient.id"))
    description: Mapped[str | None] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    unit: Mapped[str] = mapped_column(String(8))
    unit_price: Mapped[Decimal] = mapped_column(UnitCost)
    line_discount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    line_total: Mapped[Decimal] = mapped_column(Money)
    base_quantity: Mapped[Decimal] = mapped_column(Qty)
    stock_cost: Mapped[Decimal] = mapped_column(Money)  # after invoice discount / tax share
    cost_per_base_unit: Mapped[Decimal] = mapped_column(UnitCost)
    ingredient = relationship("Ingredient", lazy="joined")
    __table_args__ = (
        UniqueConstraint("invoice_id", "line_no"),
        CheckConstraint("quantity > 0 AND base_quantity > 0", name="qty"),
        CheckConstraint("unit_price >= 0 AND line_discount >= 0 AND line_total >= 0", name="amounts"),
    )


class SupplierPayment(Base):
    __tablename__ = "supplier_payment"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier.id"), index=True)
    paid_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    method: Mapped[str] = mapped_column(String(20))
    reference: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="confirmed")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = created_at_col()
    voided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    voided_at: Mapped[datetime | None]
    void_reason: Mapped[str | None] = mapped_column(String(200))
    allocations = relationship("PaymentAllocation", lazy="selectin")
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount"),
        CheckConstraint("status IN ('confirmed','voided')", name="status"),
        CheckConstraint("method IN ('cash','bank_transfer','mobile_money','cheque','card','other')",
                        name="method"),
        Index("uq_payment_reference", "supplier_id", "method", "reference", unique=True,
              postgresql_where="reference IS NOT NULL AND status = 'confirmed'"),
    )


class PaymentAllocation(Base):
    __tablename__ = "payment_allocation"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier_payment.id"))
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_invoice.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    __table_args__ = (
        UniqueConstraint("payment_id", "invoice_id"),
        CheckConstraint("amount > 0", name="amount"),
    )


class SupplierCredit(Base):
    __tablename__ = "supplier_credit"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier.id"))
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_invoice.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    reason: Mapped[str] = mapped_column(String(200))
    reference: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(12), default="confirmed")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = created_at_col()
    __table_args__ = (CheckConstraint("amount > 0", name="amount"),)


class SupplierLedgerEntry(Base):
    """Traceable payable balance: Σ amount per supplier = balance owed (ADR/BR-PAY-5)."""

    __tablename__ = "supplier_ledger_entry"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier.id"))
    entry_type: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[uuid.UUID]
    amount: Mapped[Decimal] = mapped_column(Money)  # + increases what we owe
    currency: Mapped[str] = mapped_column(String(3))
    description: Mapped[str] = mapped_column(String(200))
    posted_at: Mapped[datetime] = created_at_col()
    __table_args__ = (
        UniqueConstraint("entry_type", "source_id"),
        Index("ix_ledger_supplier", "supplier_id", "branch_id", "posted_at"),
    )
