"""Row image <-> JSON with exact types (Decimal as string, never float)."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Table


def to_json(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, (uuid.UUID,)):
        return str(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (dict, list)):
        return v
    return str(v)


def coerce(table: Table, row: dict) -> dict:
    """Turn a JSON row image back into typed values for ``table`` (unknown keys are dropped,
    so a replica can apply images written by an older schema version)."""
    out = {}
    for name, value in row.items():
        col = table.c.get(name)
        if col is None:
            continue
        if value is None:
            out[name] = None
            continue
        try:
            py = col.type.python_type
        except NotImplementedError:
            py = None
        if py is Decimal:
            out[name] = Decimal(value)
        elif py is uuid.UUID:
            out[name] = uuid.UUID(value)
        elif py is datetime:
            out[name] = datetime.fromisoformat(value)
        elif py is date:
            out[name] = date.fromisoformat(value)
        else:
            out[name] = value
    return out
