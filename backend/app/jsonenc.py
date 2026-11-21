"""JSON encoding that keeps Decimal exact (as strings) — never floats (ADR-0004)."""

from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder


def _dec(d: Decimal) -> str:
    return format(d, "f")


def enc(obj: Any) -> Any:
    return jsonable_encoder(obj, custom_encoder={Decimal: _dec})
