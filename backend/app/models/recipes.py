import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, Money, Qty, Rate, created_at_col
from ..ids import uuid7


class Recipe(Base):
    __tablename__ = "recipe"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = created_at_col()
    versions = relationship("RecipeVersion", back_populates="recipe",
                            order_by="RecipeVersion.version_no", lazy="selectin")


class RecipeVersion(Base):
    """draft -> published -> retired. Published/retired versions are immutable."""

    __tablename__ = "recipe_version"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    recipe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recipe.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="draft")
    portions_per_batch: Mapped[Decimal] = mapped_column(Qty)
    portion_size_qty: Mapped[Decimal | None] = mapped_column(Qty)
    portion_size_unit: Mapped[str | None] = mapped_column(String(8))
    cooking_shrinkage_rate: Mapped[Decimal] = mapped_column(Rate, default=Decimal(0))
    target_food_cost_rate: Mapped[Decimal] = mapped_column(Rate)
    selling_price: Mapped[Decimal | None] = mapped_column(Money)
    prep_instructions: Mapped[str | None] = mapped_column(Text)
    cooking_instructions: Mapped[str | None] = mapped_column(Text)
    change_note: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = created_at_col()
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    published_at: Mapped[datetime | None]

    recipe = relationship("Recipe", back_populates="versions")
    lines = relationship("RecipeLine", order_by="RecipeLine.sort_order", lazy="selectin",
                         cascade="all, delete-orphan")
    __table_args__ = (
        UniqueConstraint("recipe_id", "version_no"),
        CheckConstraint("status IN ('draft','published','retired')", name="status"),
        CheckConstraint("portions_per_batch > 0", name="portions"),
        CheckConstraint("cooking_shrinkage_rate >= 0 AND cooking_shrinkage_rate < 1", name="shrink"),
        CheckConstraint("target_food_cost_rate > 0 AND target_food_cost_rate < 1", name="target"),
        CheckConstraint("selling_price IS NULL OR selling_price > 0", name="price"),
        Index("uq_recipe_one_published", "recipe_id", unique=True,
              postgresql_where="status = 'published'"),
    )


class RecipeLine(Base):
    __tablename__ = "recipe_line"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    recipe_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recipe_version.id", ondelete="CASCADE"))
    ingredient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingredient.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    unit: Mapped[str] = mapped_column(String(8))
    basis: Mapped[str] = mapped_column(String(5), default="gross")
    prep_loss_rate: Mapped[Decimal] = mapped_column(Rate, default=Decimal(0))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    ingredient = relationship("Ingredient", lazy="joined")
    __table_args__ = (
        CheckConstraint("quantity > 0", name="qty"),
        CheckConstraint("basis IN ('gross','net')", name="basis"),
        CheckConstraint("prep_loss_rate >= 0 AND prep_loss_rate < 1", name="loss"),
    )


class RecipeCostSnapshot(Base):
    """CURRENT-basis cost projection history. Never updated."""

    __tablename__ = "recipe_cost_snapshot"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    recipe_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recipe_version.id"))
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branch.id"))
    computed_at: Mapped[datetime] = created_at_col()
    trigger: Mapped[str] = mapped_column(String(32))  # publish | cost_change | manual
    trigger_ref: Mapped[str | None] = mapped_column(String(200))
    batch_cost: Mapped[Decimal] = mapped_column(Money)
    cost_per_portion: Mapped[Decimal] = mapped_column(Money)
    recommended_price: Mapped[Decimal] = mapped_column(Money)
    food_cost_rate: Mapped[Decimal | None] = mapped_column(Rate)
    exceeds_target: Mapped[bool]
    currency: Mapped[str] = mapped_column(String(3))
    detail: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (Index("ix_snapshot_lookup", "recipe_version_id", "branch_id", "computed_at"),)
