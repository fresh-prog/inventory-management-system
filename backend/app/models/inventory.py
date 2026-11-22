import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, ForeignKey, Index, Sequence, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, Money, Qty, UnitCost, created_at_col
from ..ids import uuid7

batch_seq = Sequence("stock_batch_seq", metadata=Base.metadata)


class StockItem(Base):
    """Anything held in stock. kind='ingredient' (raw) now; 'finished_good' is reserved so
    future POS sales of pre-produced dishes reduce finished goods, never raw ingredients."""

    __tablename__ = "stock_item"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    kind: Mapped[str] = mapped_column(String(16))
    ingredient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingredient.id"), unique=True)
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recipe.id"), unique=True)
    base_dimension: Mapped[str] = mapped_column(String(8))
    __table_args__ = (
        CheckConstraint("kind IN ('ingredient','finished_good')", name="kind"),
        CheckConstraint("(kind = 'ingredient') = (ingredient_id IS NOT NULL)", name="ingredient"),
        CheckConstraint("(kind = 'finished_good') = (recipe_id IS NOT NULL)", name="finished"),
    )


class StockBatch(Base):
    __tablename__ = "stock_batch"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    stock_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stock_item.id"))
    source_type: Mapped[str] = mapped_column(String(16))  # purchase | count_gain
    source_id: Mapped[uuid.UUID]
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("supplier.id"))
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_invoice.id"))
    purchase_line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_line.id"), unique=True)
    purchase_date: Mapped[date | None] = mapped_column(Date)
    received_at: Mapped[datetime]
    sequence: Mapped[int] = mapped_column(
        BigInteger, batch_seq, server_default=batch_seq.next_value(), unique=True)
    original_quantity: Mapped[Decimal] = mapped_column(Qty)
    remaining_quantity: Mapped[Decimal] = mapped_column(Qty)
    unit_cost: Mapped[Decimal] = mapped_column(UnitCost)
    currency: Mapped[str] = mapped_column(String(3))
    expires_on: Mapped[date | None] = mapped_column(Date)
    is_quarantined: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at_col()
    supplier = relationship("Supplier", lazy="select")
    __table_args__ = (
        CheckConstraint("original_quantity > 0", name="original"),
        CheckConstraint("remaining_quantity >= 0 AND remaining_quantity <= original_quantity",
                        name="remaining"),
        CheckConstraint("unit_cost >= 0", name="cost"),
        Index("ix_stock_batch_fifo", "stock_item_id", "branch_id", "received_at", "sequence",
              postgresql_where="remaining_quantity > 0"),
    )


class StockMovement(Base):
    """Immutable. quantity is signed in base units: + into stock, - out of stock."""

    __tablename__ = "stock_movement"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    stock_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stock_item.id"))
    type: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    total_cost: Mapped[Decimal] = mapped_column(Money)  # FIFO cost (signed like quantity)
    shortfall_quantity: Mapped[Decimal] = mapped_column(Qty, default=Decimal(0))
    currency: Mapped[str] = mapped_column(String(3))
    reason: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    occurred_at: Mapped[datetime]
    recorded_at: Mapped[datetime] = created_at_col()
    allocations = relationship("StockAllocation", lazy="selectin")
    __table_args__ = (
        CheckConstraint(
            "type IN ('receipt','consumption','production','adjustment','count_adjustment',"
            "'purchase_void','sale')", name="type"),
        CheckConstraint(
            "reason IS NULL OR reason IN ('spoilage','expired','damaged','prep_waste',"
            "'staff_meal','count_variance','other')", name="reason"),
        CheckConstraint("type <> 'adjustment' OR (reason IS NOT NULL AND quantity < 0)",
                        name="adjustment_reason"),
        CheckConstraint("quantity <> 0", name="non_zero"),
        CheckConstraint("shortfall_quantity >= 0", name="shortfall"),
        CheckConstraint("source_id IS NOT NULL", name="traceable"),
        Index("ix_movement_item_time", "stock_item_id", "branch_id", "occurred_at"),
    )


class StockAllocation(Base):
    """FIFO detail of an outbound movement: which batch, how much, at what cost."""

    __tablename__ = "stock_allocation"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    movement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stock_movement.id"), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stock_batch.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Qty)  # positive = taken from batch
    unit_cost: Mapped[Decimal] = mapped_column(UnitCost)
    __table_args__ = (CheckConstraint("quantity > 0", name="qty"),)


class ProductionRun(Base):
    """Ingredients are deducted HERE, once. Selling the output later must reduce
    finished-goods stock (type 'sale'), never these ingredients again."""

    __tablename__ = "production_run"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    recipe_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recipe_version.id"))
    batches: Mapped[Decimal] = mapped_column(Qty)
    expected_portions: Mapped[Decimal] = mapped_column(Qty)
    actual_portions: Mapped[Decimal | None] = mapped_column(Qty)
    total_cost: Mapped[Decimal] = mapped_column(Money)
    cost_per_portion: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    produced_at: Mapped[datetime]
    created_at: Mapped[datetime] = created_at_col()
    recipe_version = relationship("RecipeVersion", lazy="joined")
    __table_args__ = (CheckConstraint("batches > 0", name="batches"),)


class StockCount(Base):
    __tablename__ = "stock_count"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    status: Mapped[str] = mapped_column(String(12), default="open")
    note: Mapped[str | None] = mapped_column(Text)
    started_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    started_at: Mapped[datetime] = created_at_col()
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    approved_at: Mapped[datetime | None]
    lines = relationship("StockCountLine", lazy="selectin")
    __table_args__ = (CheckConstraint("status IN ('open','approved','cancelled')", name="status"),)


class StockCountLine(Base):
    __tablename__ = "stock_count_line"
    count_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stock_count.id"), primary_key=True)
    stock_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stock_item.id"), primary_key=True)
    expected_at_start: Mapped[Decimal] = mapped_column(Qty)
    counted_quantity: Mapped[Decimal | None] = mapped_column(Qty)
    expected_at_approval: Mapped[Decimal | None] = mapped_column(Qty)
    variance_quantity: Mapped[Decimal | None] = mapped_column(Qty)
    variance_value: Mapped[Decimal | None] = mapped_column(Money)
    adjustment_movement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stock_movement.id"))
    __table_args__ = (
        CheckConstraint("counted_quantity IS NULL OR counted_quantity >= 0", name="counted"),
    )
