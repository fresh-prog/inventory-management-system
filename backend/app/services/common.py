from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

MONEY_Q = Decimal("0.0001")
QTY_Q = Decimal("0.000001")
COST_Q = Decimal("0.000000000001")
ZERO = Decimal(0)


def money(v: Decimal) -> Decimal:
    return v.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def qty(v: Decimal) -> Decimal:
    return v.quantize(QTY_Q, rounding=ROUND_HALF_UP)


def unit_cost(v: Decimal) -> Decimal:
    return v.quantize(COST_Q, rounding=ROUND_HALF_UP)


def now() -> datetime:
    return datetime.now(UTC)
