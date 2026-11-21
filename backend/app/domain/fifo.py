"""Perpetual FIFO allocation — pure, deterministic core.

The database layer (later stage) wraps this in a transaction that locks the
ingredient's open batches (``SELECT ... FOR UPDATE`` in FIFO order) so two
users can never consume the same stock. This module only decides *what* to
allocate; it never mutates its inputs. See ADR-0005 and BR-FIFO-*.

Deterministic order: (received_at, sequence). ``sequence`` is a per-branch,
monotonically increasing batch number, so two batches received at the same
instant still have a stable order.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum

ZERO = Decimal("0")


class InsufficientStockPolicy(str, Enum):
    BLOCK = "block"  # default: reject the whole consumption
    ALLOW_SHORTFALL = "allow_shortfall"  # authorised override; uncosted part recorded


class InsufficientStockError(Exception):
    def __init__(self, ingredient_id: str, requested: Decimal, available: Decimal):
        self.ingredient_id = ingredient_id
        self.requested = requested
        self.available = available
        super().__init__(
            f"Insufficient stock for {ingredient_id}: requested {requested}, "
            f"available {available}"
        )


@dataclass(frozen=True)
class StockBatch:
    batch_id: str
    ingredient_id: str
    branch_id: str
    received_at: datetime
    sequence: int
    original_quantity: Decimal  # base units
    remaining_quantity: Decimal  # base units
    unit_cost: Decimal  # per base unit
    currency: str
    is_quarantined: bool = False  # e.g. expired / under dispute: not eligible

    def __post_init__(self) -> None:
        if self.remaining_quantity < ZERO:
            raise ValueError("remaining_quantity cannot be negative")
        if self.remaining_quantity > self.original_quantity:
            raise ValueError("remaining_quantity cannot exceed original_quantity")


@dataclass(frozen=True)
class Allocation:
    batch_id: str
    quantity: Decimal
    unit_cost: Decimal

    @property
    def cost(self) -> Decimal:
        return self.quantity * self.unit_cost


@dataclass(frozen=True)
class FifoResult:
    ingredient_id: str
    requested: Decimal
    allocations: tuple[Allocation, ...]
    shortfall: Decimal  # quantity not covered by any batch (override only)
    updated_batches: tuple[StockBatch, ...]  # new state of touched batches

    @property
    def allocated_quantity(self) -> Decimal:
        return sum((a.quantity for a in self.allocations), ZERO)

    @property
    def total_cost(self) -> Decimal:
        return sum((a.cost for a in self.allocations), ZERO)


def fifo_order(batches: list[StockBatch]) -> list[StockBatch]:
    return sorted(batches, key=lambda b: (b.received_at, b.sequence))


def allocate_fifo(
    ingredient_id: str,
    branch_id: str,
    quantity: Decimal,
    batches: list[StockBatch],
    policy: InsufficientStockPolicy = InsufficientStockPolicy.BLOCK,
) -> FifoResult:
    if not isinstance(quantity, Decimal):
        raise TypeError("quantity must be Decimal")
    if quantity <= ZERO:
        raise ValueError("Consumption quantity must be positive")

    eligible = fifo_order(
        [
            b
            for b in batches
            if b.ingredient_id == ingredient_id
            and b.branch_id == branch_id
            and not b.is_quarantined
            and b.remaining_quantity > ZERO
        ]
    )
    currencies = {b.currency for b in eligible}
    if len(currencies) > 1:
        raise ValueError(f"Mixed currencies in batches: {sorted(currencies)}")

    available = sum((b.remaining_quantity for b in eligible), ZERO)
    if available < quantity and policy is InsufficientStockPolicy.BLOCK:
        raise InsufficientStockError(ingredient_id, quantity, available)

    outstanding = quantity
    allocations: list[Allocation] = []
    updated: list[StockBatch] = []
    for batch in eligible:
        if outstanding == ZERO:
            break
        take = min(batch.remaining_quantity, outstanding)
        allocations.append(Allocation(batch.batch_id, take, batch.unit_cost))
        updated.append(replace(batch, remaining_quantity=batch.remaining_quantity - take))
        outstanding -= take

    return FifoResult(
        ingredient_id=ingredient_id,
        requested=quantity,
        allocations=tuple(allocations),
        shortfall=outstanding,
        updated_batches=tuple(updated),
    )


def inventory_value(batches: list[StockBatch]) -> Decimal:
    return sum((b.remaining_quantity * b.unit_cost for b in batches), ZERO)
