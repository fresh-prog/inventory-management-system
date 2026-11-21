from datetime import datetime, timedelta
from decimal import Decimal as D

import pytest

from app.domain.fifo import (
    InsufficientStockError, InsufficientStockPolicy, StockBatch, allocate_fifo, inventory_value,
)

T0 = datetime(2026, 1, 1, 8, 0)


def batch(bid, day, qty, cost, seq=None, **kw):
    return StockBatch(
        batch_id=bid, ingredient_id="beef", branch_id="b1",
        received_at=T0 + timedelta(days=day), sequence=seq if seq is not None else day,
        original_quantity=D(qty), remaining_quantity=D(qty),
        unit_cost=D(cost), currency="USD", **kw,
    )


def abc():
    # deliberately unordered input
    return [batch("C", 2, "10", "12"), batch("A", 0, "10", "9"), batch("B", 1, "10", "11")]


def test_brief_example_15kg():
    r = allocate_fifo("beef", "b1", D("15"), abc())
    assert [(a.batch_id, a.quantity, a.cost) for a in r.allocations] == [
        ("A", D("10"), D("90")), ("B", D("5"), D("55")),
    ]
    assert r.total_cost == D("145")
    assert r.shortfall == D("0")
    remaining = {b.batch_id: b.remaining_quantity for b in r.updated_batches}
    assert remaining == {"A": D("0"), "B": D("5")}
    after = [b for b in abc() if b.batch_id == "C"] + [b for b in r.updated_batches]
    assert inventory_value(after) == D("55") + D("120")


def test_inputs_are_not_mutated():
    batches = abc()
    allocate_fifo("beef", "b1", D("15"), batches)
    assert all(b.remaining_quantity == b.original_quantity for b in batches)


def test_blocks_insufficient_stock_by_default():
    with pytest.raises(InsufficientStockError) as e:
        allocate_fifo("beef", "b1", D("31"), abc())
    assert e.value.available == D("30")


def test_shortfall_policy_records_uncovered_quantity():
    r = allocate_fifo("beef", "b1", D("32"), abc(), InsufficientStockPolicy.ALLOW_SHORTFALL)
    assert r.allocated_quantity == D("30")
    assert r.shortfall == D("2")
    assert r.total_cost == D("320")


def test_same_timestamp_uses_sequence_tiebreak():
    b = [batch("Y", 0, "5", "2", seq=8), batch("X", 0, "5", "1", seq=7)]
    r = allocate_fifo("beef", "b1", D("6"), b)
    assert [a.batch_id for a in r.allocations] == ["X", "Y"]


def test_skips_quarantined_empty_and_other_branch():
    b = [
        batch("Q", 0, "10", "1", is_quarantined=True),
        StockBatch("E", "beef", "b1", T0, 1, D("10"), D("0"), D("1"), "USD"),  # empty
        StockBatch("O", "beef", "b2", T0, 2, D("10"), D("10"), D("1"), "USD"),
        batch("G", 3, "10", "4"),
    ]
    r = allocate_fifo("beef", "b1", D("3"), b)
    assert [a.batch_id for a in r.allocations] == ["G"]


def test_rejects_bad_quantity_and_mixed_currency():
    with pytest.raises(ValueError):
        allocate_fifo("beef", "b1", D("0"), abc())
    mixed = abc() + [StockBatch("Z", "beef", "b1", T0, 9, D("1"), D("1"), D("1"), "UGX")]
    with pytest.raises(ValueError):
        allocate_fifo("beef", "b1", D("1"), mixed)
