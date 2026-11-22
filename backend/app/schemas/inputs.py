"""Validated request bodies. Decimals should be sent as strings."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

Unit = Literal["g", "kg", "ml", "l", "unit"]
Dim = Literal["mass", "volume", "count"]
# Bounds keep every value inside the database column types (NUMERIC(18,6) / (18,4)): an absurd
# number is a 422 validation error, never an overflow 500. NaN/Infinity are rejected.
PosDec = Field(gt=0, max_digits=18, decimal_places=6, allow_inf_nan=False)
Money = Field(ge=0, max_digits=18, decimal_places=4, allow_inf_nan=False)


def money(gt: bool = False, default=...):
    return Field(default, gt=0, max_digits=18, decimal_places=4, allow_inf_nan=False) if gt else \
        Field(default, ge=0, max_digits=18, decimal_places=4, allow_inf_nan=False)


def _no_nul(v):
    if isinstance(v, str) and "\x00" in v:
        raise ValueError("text contains an invalid character")
    if isinstance(v, dict):
        for x in v.values():
            _no_nul(x)
    if isinstance(v, list):
        for x in v:
            _no_nul(x)
    return v


class In(BaseModel):
    # every free-text field is bounded (4000 chars unless a tighter limit is declared)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, str_max_length=4000)

    @model_validator(mode="before")
    @classmethod
    def reject_nul(cls, data):
        return _no_nul(data)


class LoginIn(In):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)
    branch_code: str | None = None


class SetupIn(In):
    branch_code: str = Field(min_length=2, max_length=20)
    branch_name: str = Field(min_length=2, max_length=120)
    country: str = Field(min_length=2, max_length=2)
    currency: str = Field(min_length=3, max_length=3)
    currency_decimals: int = Field(default=2, ge=0, le=4)
    timezone: str = "Africa/Kampala"
    owner_name: str = Field(min_length=2, max_length=120)
    owner_email: EmailStr
    owner_password: str = Field(min_length=10, max_length=200)


class UserIn(In):
    email: EmailStr
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=10, max_length=200)
    role: Literal["owner", "manager", "purchaser", "kitchen", "viewer"]


class UserPatch(In):
    display_name: str | None = None
    role: Literal["owner", "manager", "purchaser", "kitchen", "viewer"] | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=200)


class ConversionIn(In):
    from_unit: Unit
    from_qty: Decimal = PosDec
    to_unit: Unit
    to_qty: Decimal = PosDec
    note: str | None = None


class SupplierLinkIn(In):
    supplier_id: uuid.UUID
    supplier_sku: str | None = None
    is_preferred: bool = False


class PriceIn(In):
    quantity: Decimal = PosDec
    unit: Unit
    amount: Decimal = Money
    effective_date: date | None = None
    note: str | None = Field(default=None, max_length=200)


class IngredientIn(In):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=80)
    description: str | None = None
    base_dimension: Dim
    purchase_unit: Unit
    consumption_unit: Unit
    is_active: bool = True
    min_stock: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False)
    min_stock_unit: Unit | None = None
    conversions: list[ConversionIn] = Field(default=[], max_length=20)
    suppliers: list[SupplierLinkIn] = Field(default=[], max_length=100)
    reference_price: PriceIn | None = None


class IngredientPatch(In):
    code: str | None = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = None
    description: str | None = None
    base_dimension: Dim | None = None
    purchase_unit: Unit | None = None
    consumption_unit: Unit | None = None
    is_active: bool | None = None
    min_stock: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False)
    min_stock_unit: Unit | None = None
    conversions: list[ConversionIn] | None = Field(default=None, max_length=20)
    suppliers: list[SupplierLinkIn] | None = Field(default=None, max_length=100)


class RecipeLineIn(In):
    ingredient_id: uuid.UUID
    quantity: Decimal = PosDec
    unit: Unit
    basis: Literal["gross", "net"] = "gross"
    prep_loss_rate: Decimal = Field(default=Decimal(0), ge=0, lt=1)


class RecipeVersionIn(In):
    portions_per_batch: Decimal = PosDec
    portion_size_qty: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6, allow_inf_nan=False)
    portion_size_unit: Unit | None = None
    cooking_shrinkage_rate: Decimal = Field(default=Decimal(0), ge=0, lt=1)
    target_food_cost_rate: Decimal | None = Field(default=None, gt=0, lt=1)
    selling_price: Decimal | None = money(True, None)
    prep_instructions: str | None = None
    cooking_instructions: str | None = None
    change_note: str | None = Field(default=None, max_length=200)
    lines: list[RecipeLineIn] = Field(min_length=1, max_length=200)


class RecipeIn(In):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=120)
    category: str | None = None
    version: RecipeVersionIn


class RecipeDraftIn(RecipeVersionIn):
    name: str | None = None
    category: str | None = None


class SupplierIn(In):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=160)
    tax_id: str | None = None
    contact_person: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool = True

    @field_validator("email", mode="before")
    @classmethod
    def empty_email(cls, v):
        return v or None


class PurchaseLineIn(In):
    ingredient_id: uuid.UUID
    description: str | None = Field(default=None, max_length=200)
    quantity: Decimal = PosDec
    unit: Unit
    unit_price: Decimal = Field(ge=0, max_digits=20, decimal_places=8, allow_inf_nan=False)
    line_discount: Decimal = money(False, Decimal(0))
    line_total: Decimal | None = money(False, None)


class PurchaseIn(In):
    supplier_id: uuid.UUID
    invoice_ref: str = Field(min_length=1, max_length=60)
    supplier_tax_ref: str | None = Field(default=None, max_length=80)
    invoice_date: date
    received_at: datetime | None = None
    due_date: date | None = None
    discount_total: Decimal = money(False, Decimal(0))
    tax_total: Decimal = money(False, Decimal(0))
    total: Decimal | None = money(False, None)
    notes: str | None = None
    lines: list[PurchaseLineIn] = Field(min_length=1, max_length=200)


class ReasonIn(In):
    reason: str = Field(min_length=3, max_length=200)


class AllocationIn(In):
    invoice_id: uuid.UUID
    amount: Decimal = money(True)


class PaymentIn(In):
    supplier_id: uuid.UUID
    paid_on: date
    amount: Decimal = money(True)
    method: Literal["cash", "bank_transfer", "mobile_money", "cheque", "card", "other"]
    reference: str | None = Field(default=None, max_length=80)
    notes: str | None = None
    allocations: list[AllocationIn] = Field(min_length=1, max_length=200)


class CreditIn(In):
    invoice_id: uuid.UUID
    amount: Decimal = money(True)
    reason: str = Field(min_length=3, max_length=200)
    reference: str | None = None


class QtyLineIn(In):
    ingredient_id: uuid.UUID
    quantity: Decimal = PosDec
    unit: Unit


class ConsumptionIn(In):
    lines: list[QtyLineIn] = Field(min_length=1, max_length=200)
    note: str | None = None
    occurred_at: datetime | None = None
    allow_shortfall: bool = False
    override_reason: str | None = None


class AdjustmentIn(In):
    ingredient_id: uuid.UUID
    quantity: Decimal = PosDec
    unit: Unit
    reason: Literal["spoilage", "expired", "damaged", "prep_waste", "staff_meal", "other"]
    note: str | None = Field(default=None, max_length=500)


class ProductionIn(In):
    recipe_id: uuid.UUID
    batches: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6, allow_inf_nan=False)
    portions: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6, allow_inf_nan=False)
    actual_portions: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False)
    note: str | None = None
    produced_at: datetime | None = None
    allow_shortfall: bool = False
    override_reason: str | None = None


class CountStartIn(In):
    note: str | None = None
    ingredient_ids: list[uuid.UUID] | None = Field(default=None, max_length=5000)


class CountEntryIn(In):
    ingredient_id: uuid.UUID
    quantity: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False)
    unit: Unit


class CountLinesIn(In):
    lines: list[CountEntryIn] = Field(max_length=5000)


class BranchSettingsIn(In):
    default_target_food_cost_rate: Decimal | None = Field(default=None, gt=0, lt=1)
    include_tax_in_cost: bool | None = None
    name: str | None = None
