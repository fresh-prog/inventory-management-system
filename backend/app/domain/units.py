"""Dimension-aware unit conversion.

Rules (see docs/03-business-rules.md, BR-UNIT-*):

* Every unit belongs to exactly one dimension: MASS, VOLUME or COUNT.
* Conversions inside a dimension use fixed, exact factors to a base unit
  (gram, millilitre, unit/piece).
* Conversions across dimensions (e.g. kg -> L, or "1 bunch" -> g) are only
  allowed through an ingredient-specific ``CrossDimensionRule``. The system
  never assumes a density.
* All arithmetic uses ``Decimal``; floats are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Dimension(str, Enum):
    MASS = "mass"
    VOLUME = "volume"
    COUNT = "count"


class UnitConversionError(ValueError):
    """Raised when a conversion is not defined or not allowed."""


@dataclass(frozen=True)
class Unit:
    code: str
    dimension: Dimension
    to_base: Decimal  # how many base units one of this unit equals


BASE_UNIT = {
    Dimension.MASS: "g",
    Dimension.VOLUME: "ml",
    Dimension.COUNT: "unit",
}

UNITS: dict[str, Unit] = {
    u.code: u
    for u in (
        Unit("g", Dimension.MASS, Decimal("1")),
        Unit("kg", Dimension.MASS, Decimal("1000")),
        Unit("ml", Dimension.VOLUME, Decimal("1")),
        Unit("l", Dimension.VOLUME, Decimal("1000")),
        Unit("unit", Dimension.COUNT, Decimal("1")),
    )
}


def get_unit(code: str) -> Unit:
    try:
        return UNITS[code.strip().lower()]
    except KeyError as exc:
        raise UnitConversionError(f"Unknown unit '{code}'") from exc


@dataclass(frozen=True)
class CrossDimensionRule:
    """Ingredient-specific bridge between two dimensions.

    ``from_qty`` of ``from_unit`` equals ``to_qty`` of ``to_unit``.
    Example: olive oil, 1 l = 910 g  -> CrossDimensionRule("l", 1, "g", 910)
    Example: eggs, 1 unit = 55 g      -> CrossDimensionRule("unit", 1, "g", 55)
    """

    from_unit: str
    from_qty: Decimal
    to_unit: str
    to_qty: Decimal

    def __post_init__(self) -> None:
        _require_decimal(self.from_qty, "from_qty")
        _require_decimal(self.to_qty, "to_qty")
        if self.from_qty <= 0 or self.to_qty <= 0:
            raise UnitConversionError("Conversion quantities must be positive")
        if get_unit(self.from_unit).dimension == get_unit(self.to_unit).dimension:
            raise UnitConversionError(
                "A cross-dimension rule must connect two different dimensions"
            )


def _require_decimal(value: object, name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal, got {type(value).__name__}")


def convert(
    quantity: Decimal,
    from_unit: str,
    to_unit: str,
    rules: tuple[CrossDimensionRule, ...] = (),
) -> Decimal:
    """Convert ``quantity`` between units, using ingredient rules if needed."""
    _require_decimal(quantity, "quantity")
    src, dst = get_unit(from_unit), get_unit(to_unit)

    if src.dimension == dst.dimension:
        return quantity * src.to_base / dst.to_base

    for rule in rules:
        r_from, r_to = get_unit(rule.from_unit), get_unit(rule.to_unit)
        if r_from.dimension == src.dimension and r_to.dimension == dst.dimension:
            factor = (rule.to_qty * r_to.to_base) / (rule.from_qty * r_from.to_base)
            return quantity * src.to_base * factor / dst.to_base
        if r_from.dimension == dst.dimension and r_to.dimension == src.dimension:
            factor = (rule.from_qty * r_from.to_base) / (rule.to_qty * r_to.to_base)
            return quantity * src.to_base * factor / dst.to_base

    raise UnitConversionError(
        f"Cannot convert {src.dimension.value} ({from_unit}) to "
        f"{dst.dimension.value} ({to_unit}) without an ingredient-specific rule"
    )


def to_base(
    quantity: Decimal,
    unit: str,
    base_dimension: Dimension,
    rules: tuple[CrossDimensionRule, ...] = (),
) -> Decimal:
    """Convert into the base unit of an ingredient's stock dimension."""
    return convert(quantity, unit, BASE_UNIT[base_dimension], rules)
