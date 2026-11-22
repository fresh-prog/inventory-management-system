"""Per-record synchronisation state, so the UI can say exactly where a saved record is:

  local     — saved on the restaurant server; cloud sync not set up
  pending   — saved on the restaurant server; not yet confirmed by the cloud
  cloud     — the cloud acknowledged it (signed acknowledgement verified)
  attention — the cloud rejected it; an operator must act (Settings → Cloud sync)
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import OutboxEvent, SyncState


def record_sync_status(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    st = db.get(SyncState, 1)
    if st is None or not st.registered_at:
        return {i: "local" for i in ids}
    unacked_snapshot = db.scalar(select(func.min(OutboxEvent.sequence)).where(
        OutboxEvent.kind == "snapshot", OutboxEvent.status != "acked"))
    latest = {}
    for agg, status, seq in db.execute(select(OutboxEvent.aggregate_id, OutboxEvent.status, OutboxEvent.sequence)
                                       .where(OutboxEvent.aggregate_id.in_(ids)).order_by(OutboxEvent.sequence)):
        latest[agg] = (status, seq)
    out = {}
    for i in ids:
        status, seq = latest.get(i, ("acked", 0))  # acked events are purged after the retention period
        if status == "acked":
            out[i] = "cloud"
        elif status == "superseded":  # contained in a snapshot: confirmed once every later snapshot part is acked
            out[i] = "pending" if (unacked_snapshot is not None and unacked_snapshot > seq) else "cloud"
        elif status == "failed":
            out[i] = "attention"
        else:
            out[i] = "pending"
    if st.needs_snapshot:  # a full copy is about to be re-sent: nothing is confirmed until it is acked
        out = {i: ("pending" if v == "cloud" else v) for i, v in out.items()}
    return out
