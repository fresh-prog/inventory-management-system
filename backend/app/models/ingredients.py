import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, Money, Qty, UnitCost, created_at_col
from ..ids import uuid7


class IngredientCategory(Base):
    __tablename__ = "ingredient_category"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class Ingredient(Base):
    __tablename__ = "ingredient"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingredient_category.id"))
    description: Mapped[str | None] = mapped_column(Text)
    base_dimension: Mapped[str] = mapped_column(String(8))
    purchase_unit: Mapped[str] = mapped_column(String(8))
    consumption_unit: Mapped[str] = mapped_column(String(8))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = created_at_col()

    category = relationship("IngredientCategory", lazy="joined")
    conversions = relationship("IngredientConversion", lazy="selectin",
                               cascade="all, delete-orphan", order_by="IngredientConversion.from_unit")
    __table_args__ = (
        CheckConstraint("base_dimension IN ('mass','volume','count')", name="dimension"),
    )


class IngredientConversion(Base):
    """Ingredient-specific cross-dimension rule (e.g. 1 l = 910 g, 1 unit = 25 kg bag)."""

    __tablename__ = "ingredient_conversion"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    ingredient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingredient.id", ondelete="CASCADE"))
    from_unit: Mapped[str] = mapped_column(String(8))
    from_qty: Mapped[Decimal] = mapped_column(Qty)
    to_unit: Mapped[str] = mapped_column(String(8))
    to_qty: Mapped[Decimal] = mapped_column(Qty)
    note: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (
        UniqueConstraint("ingredient_id", "from_unit", "to_unit"),
        CheckConstraint("from_qty > 0 AND to_qty > 0", name="positive"),
    )


class IngredientBranchSetting(Base):
    __tablename__ = "ingredient_branch_setting"
    ingredient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingredient.id"), primary_key=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"), primary_key=True)
    min_stock_base: Mapped[Decimal] = mapped_column(Qty, default=Decimal(0))
    __table_args__ = (CheckConstraint("min_stock_base >= 0", name="min_stock"),)


class IngredientCurrentCost(Base):
    """CURRENT cost basis (ADR-0006): latest purchase cost per base unit, per branch."""

    __tablename__ = "ingredient_current_cost"
    ingredient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingredient.id"), primary_key=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"), primary_key=True)
    cost_per_base_unit: Mapped[Decimal] = mapped_column(UnitCost)
    currency: Mapped[str] = mapped_column(String(3))
    effective_date: Mapped[date] = mapped_column(Date)
    price_history_id: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = created_at_col()
    __table_args__ = (CheckConstraint("cost_per_base_unit >= 0", name="non_negative"),)


class IngredientPriceHistory(Base):
    """Append-only. Voiding a purchase marks its rows voided; nothing is deleted."""

    __tablename__ = "ingredient_price_history"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    ingredient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingredient.id"))
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    source: Mapped[str] = mapped_column(String(16))  # purchase | manual
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("supplier.id"))
    purchase_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_line.id"), index=True)
    effective_date: Mapped[date] = mapped_column(Date)
    purchase_quantity: Mapped[Decimal] = mapped_column(Qty)
    purchase_unit: Mapped[str] = mapped_column(String(8))
    purchase_amount: Mapped[Decimal] = mapped_column(Money)
    cost_per_base_unit: Mapped[Decimal] = mapped_column(UnitCost)
    previous_cost_per_base_unit: Mapped[Decimal | None] = mapped_column(UnitCost)
    currency: Mapped[str] = mapped_column(String(3))
    is_voided: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = created_at_col()
    __table_args__ = (
        Index("ix_price_history_lookup", "ingredient_id", "branch_id", "effective_date"),
    )


class SupplierIngredient(Base):
    __tablename__ = "supplier_ingredient"
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier.id"), primary_key=True)
    ingredient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingredient.id"), primary_key=True, index=True)
    supplier_sku: Mapped[str | None] = mapped_column(String(60))
    is_preferred: Mapped[bool] = mapped_column(Boolean, default=False)
