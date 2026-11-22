"""Apply replicated row images to a replica (cloud per-branch schema, or a branch restoring
from the cloud). Pure upserts/deletes by primary key, in event order; immutable business events
are insert-only. No business logic runs here: the branch already validated everything."""

from sqlalchemy import Connection, delete, text
from sqlalchemy.dialects.postgresql import insert

from ..db import Base
from .rowcodec import coerce
from .spec import EXCLUDED_COLUMNS, IMMUTABLE_TABLES, REPLICATED_TABLES


def apply_rows(conn: Connection, rows: list[dict]) -> int:
    """Apply in order. Consecutive upserts of the same table are sent as multi-row INSERTs (much
    faster for snapshots and large backlogs); order between different tables/operations is kept."""
    group: list[dict] = []
    gtable = None

    def flush():
        nonlocal group, gtable
        if group:
            _upsert(conn, gtable, group)
        group, gtable = [], None

    for r in rows:
        if r["t"] not in REPLICATED_TABLES or Base.metadata.tables.get(r["t"]) is None:
            raise ValueError(f"table {r['t']!r} is not replicated")
        table = Base.metadata.tables[r["t"]]
        if r["op"] == "delete":
            flush()
            pk = coerce(table, r["pk"])
            conn.execute(delete(table).where(*[table.c[k] == v for k, v in pk.items()]))
            continue
        values = {**dict(EXCLUDED_COLUMNS.get(r["t"], {})), **coerce(table, r["row"])}
        keyset = tuple(sorted(values))
        if gtable is not table or (group and tuple(sorted(group[0])) != keyset) or len(group) >= 500:
            flush()
            gtable = table
        group.append(values)
    flush()
    return len(rows)


def _upsert(conn: Connection, table, rows: list[dict]) -> None:
    pk_cols = [c.name for c in table.primary_key.columns]
    seen, unique = set(), True
    for v in rows:
        k = tuple(v.get(c) for c in pk_cols)
        if k in seen:
            unique = False
            break
        seen.add(k)
    if not unique:  # the same row twice in one statement is not allowed by ON CONFLICT: go one by one
        for v in rows:
            _upsert(conn, table, [v])
        return
    stmt = insert(table).values(rows)
    if table.name in IMMUTABLE_TABLES:
        stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
    else:
        excluded = set(EXCLUDED_COLUMNS.get(table.name, {}))
        upd = {k: stmt.excluded[k] for k in rows[0] if k not in pk_cols and k not in excluded}
        stmt = stmt.on_conflict_do_update(index_elements=pk_cols, set_=upd) if upd else \
            stmt.on_conflict_do_nothing(index_elements=pk_cols)
    conn.execute(stmt)


def reset_replica(conn: Connection) -> None:
    """Before applying a full snapshot: empty every replicated table (TRUNCATE does not fire the
    append-only row triggers). Runs in the same transaction as the snapshot's first chunk."""
    names = ", ".join(f'"{t}"' for t in REPLICATED_TABLES)
    conn.execute(text(f"TRUNCATE {names} CASCADE"))
