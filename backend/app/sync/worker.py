"""Background sync worker for the branch server:  python -m app.sync.worker

Runs a cycle every few seconds while there is work, backs off when the cloud is unreachable.
Safe with several workers (an advisory lock elects one runner) and safe to kill at any time."""

import logging
import signal
import time

from ..config import get_settings
from ..db import make_engine, make_sessionmaker
from ..logging_setup import configure_logging
from .client import run_cycle

log = logging.getLogger("app.sync.worker")
_stop = False


def _handle(*_):
    global _stop
    _stop = True


def main() -> None:
    configure_logging()
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    Session = make_sessionmaker(make_engine(get_settings().database_url), capture=True)
    delay = 5
    while not _stop:
        try:
            out = run_cycle(Session)
        except Exception:  # noqa: BLE001
            log.exception("sync cycle crashed; will retry")
            out = {"state": "error"}
        state = out.get("state")
        if state == "ok":
            busy = (out.get("push") or {}).get("sent", 0) or out.get("commands")
            delay = 2 if busy else 10
        elif state in ("not_registered", "halted"):
            delay = 60
        else:
            delay = min(300, delay * 2)
        log.info("sync cycle", extra={"status": state})
        for _ in range(int(delay * 10)):
            if _stop:
                break
            time.sleep(0.1)


if __name__ == "__main__":
    main()
