"""Prometheus metrics (text format). Protected by APP_METRICS_TOKEN; disabled when unset.

Gauges are computed from durable state on each scrape, so they are correct with any number of
worker processes. Request latency/error rates come from the structured access log."""

import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Header, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from ..errors import NotFound, Unauthorized

router = APIRouter(tags=["ops"])
STARTED = time.time()


def _last_backup_age(backup_dir: str | None) -> float:
    if not backup_dir or not Path(backup_dir).is_dir():
        return -1
    manifests = list(Path(backup_dir).glob("*/MANIFEST"))
    return time.time() - max(m.stat().st_mtime for m in manifests) if manifests else -1


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request, authorization: str | None = Header(default=None)):
    s = request.app.state.settings
    if not s.metrics_token:
        raise NotFound("Not found")
    if authorization != f"Bearer {s.metrics_token}":
        raise Unauthorized("Bad metrics token")
    lines = [f'kl_build_info{{version="{s.app_version}",profile="{request.app.state.profile}"}} 1',
             f"kl_process_uptime_seconds {time.time() - STARTED:.0f}"]
    try:
        with request.app.state.engine.connect() as c:
            c.execute(text("SELECT 1"))
            lines.append("kl_database_up 1")
            if request.app.state.profile == "branch":
                q = dict(c.execute(text("SELECT status, count(*) FROM outbox_event GROUP BY status")).all())
                for st in ("pending", "retry", "failed", "acked"):
                    lines.append(f'kl_sync_events{{status="{st}"}} {q.get(st, 0)}')
                oldest = c.execute(text("SELECT EXTRACT(EPOCH FROM now() - min(created_at)) FROM outbox_event "
                                        "WHERE status IN ('pending','retry','failed')")).scalar()
                lines.append(f"kl_sync_oldest_unsent_seconds {float(oldest or 0):.0f}")
                st = c.execute(text("SELECT registered_at IS NOT NULL, halted_reason IS NOT NULL, "
                                    "EXTRACT(EPOCH FROM now() - last_success_at) FROM sync_state WHERE id = 1")).one()
                lines.append(f"kl_sync_registered {int(st[0])}")
                lines.append(f"kl_sync_halted {int(st[1])}")
                lines.append(f"kl_sync_last_success_age_seconds {float(st[2]) if st[2] is not None else -1:.0f}")
                lines.append(f"kl_files_not_in_cloud {c.execute(text('SELECT count(*) FROM stored_file WHERE cloud_verified_at IS NULL')).scalar()}")
                lines.append(f"kl_low_stock_items {c.execute(text('''SELECT count(*) FROM ingredient_branch_setting s JOIN stock_item i ON i.ingredient_id = s.ingredient_id
                    WHERE s.min_stock_base > 0 AND s.min_stock_base > COALESCE((SELECT sum(remaining_quantity) FROM stock_batch b WHERE b.stock_item_id = i.id), 0)''')).scalar()}")
            else:
                for code, state, seen, err in c.execute(text(
                        "SELECT code, status, EXTRACT(EPOCH FROM now() - last_seen_at), last_error IS NOT NULL FROM cloud_branch")):
                    lines.append(f'kl_branch_halted{{branch="{code}"}} {int(state == "halted")}')
                    lines.append(f'kl_branch_last_seen_seconds{{branch="{code}"}} {float(seen) if seen is not None else -1:.0f}')
                    lines.append(f'kl_branch_apply_error{{branch="{code}"}} {int(err)}')
    except Exception:  # noqa: BLE001
        lines.append("kl_database_up 0")
    du = shutil.disk_usage(Path(s.files_dir).resolve())
    lines.append(f"kl_disk_free_bytes {du.free}")
    lines.append(f"kl_disk_free_ratio {du.free / du.total:.4f}")
    lines.append(f"kl_last_backup_age_seconds {_last_backup_age(s.backup_dir):.0f}")
    # set by the backup service when the daily restore test fails; cleared by the next passing test
    failed = bool(s.backup_dir) and (Path(s.backup_dir) / "RESTORE_TEST_FAILED").exists()
    lines.append(f"kl_restore_test_failed {int(failed)}")
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
