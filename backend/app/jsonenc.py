"""JSON encoding that keeps Decimal exact (as strings) — never floats (ADR-0004).

``enc`` produces JSON-compatible Python data (used for API bodies, audit details, idempotency
records). ``J`` renders an HTTP response directly, skipping FastAPI's generic (and, for large
lists, ~5x slower) re-encoding. Measured in Stage 4: stock list of 3,000 items 80 ms → 16 ms."""

import datetime
import enum
import json
import uuid
from decimal import Decimal
from typing import Any

from fastapi.responses import Response


def _default(o: Any):
    if isinstance(o, Decimal):
        return format(o, "f")
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, (datetime.datetime, datetime.date, datetime.time)):
        return o.isoformat()
    if isinstance(o, enum.Enum):
        return o.value
    if isinstance(o, (set, frozenset)):
        return list(o)
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if isinstance(o, bytes):
        return o.decode("utf-8", "replace")
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serialisable")


def dumps(obj: Any) -> bytes:
    return json.dumps(obj, default=_default, separators=(",", ":"), ensure_ascii=False).encode()


def enc(obj: Any) -> Any:
    return json.loads(dumps(obj))


class J(Response):
    """Pre-rendered JSON response."""

    media_type = "application/json"

    def __init__(self, content: Any, status_code: int = 200, headers: dict | None = None):
        super().__init__(dumps(content), status_code=status_code, headers=headers)
