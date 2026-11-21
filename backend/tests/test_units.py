from decimal import Decimal as D

import pytest

from app.domain.units import CrossDimensionRule, Dimension, UnitConversionError, convert, to_base


def test_same_dimension_conversions():
    assert convert(D("2.5"), "kg", "g") == D("2500")
    assert convert(D("750"), "ml", "l") == D("0.75")
    assert convert(D("12"), "unit", "unit") == D("12")


def test_mass_to_volume_refused_without_rule():
    with pytest.raises(UnitConversionError):
        convert(D("1"), "kg", "l")


def test_mass_volume_with_density_rule_both_directions():
    oil = (CrossDimensionRule("l", D("1"), "g", D("910")),)
    assert convert(D("2"), "l", "kg", oil) == D("1.82")
    assert convert(D("910"), "g", "ml", oil) == D("1000")


def test_count_to_mass_rule():
    eggs = (CrossDimensionRule("unit", D("1"), "g", D("55")),)
    assert to_base(D("1.1"), "kg", Dimension.COUNT, eggs) == D("20")


def test_rejects_float_and_unknown_unit():
    with pytest.raises(TypeError):
        convert(1.0, "kg", "g")  # type: ignore[arg-type]
    with pytest.raises(UnitConversionError):
        convert(D("1"), "lb", "g")


def test_rule_must_cross_dimensions():
    with pytest.raises(UnitConversionError):
        CrossDimensionRule("kg", D("1"), "g", D("1000"))
