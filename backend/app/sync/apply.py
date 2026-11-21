"""Apply replicated row images to a replica (cloud per-branch schema, or a branch restoring
from the cloud). Pure upserts/deletes by primary key, in event order; immutable business events
are insert-only. No business logic runs here: the branch already validated everything."""

from sqlalchemy import Connection, delete, text
from sqlalchemy.dialects.postgresql import insert

from ..db import Base
from .rowcodec import coerce
from .spec import EXCLUDED_COLUMNS, IMMUTABLE_TABLES, REPLICATED_TABLES


def apply_rows(conn: Connection, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        table = Base.metadata.tables.get(r["t"])
        if table is None or r["t"] not in REPLICATED_TABLES:
            raise ValueError(f"table {r['t']!r} is not replicated")
        if r["op"] == "delete":
            pk = coerce(table, r["pk"])
            conn.execute(delete(table).where(*[table.c[k] == v for k, v in pk.items()]))
        else:
            values = {**{k: v for k, v in EXCLUDED_COLUMNS.get(r["t"], {}).items()}, **coerce(table, r["row"])}
            stmt = insert(table).values(**values)
            pk_cols = [c.name for c in table.primary_key.columns]
            if r["t"] in IMMUTABLE_TABLES:
                stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
            else:
                excluded = set(EXCLUDED_COLUMNS.get(r["t"], {}))
                upd = {k: stmt.excluded[k] for k in values if k not in pk_cols and k not in excluded}
                stmt = stmt.on_conflict_do_update(index_elements=pk_cols, set_=upd) if upd else \
                    stmt.on_conflict_do_nothing(index_elements=pk_cols)
            conn.execute(stmt)
        n += 1
    return n


def reset_replica(conn: Connection) -> None:
    """Before applying a full snapshot: empty every replicated table (TRUNCATE does not fire the
    append-only row triggers). Runs in the same transaction as the snapshot's first chunk."""
    names = ", ".join(f'"{t}"' for t in REPLICATED_TABLES)
    conn.execute(text(f"TRUNCATE {names} CASCADE"))
