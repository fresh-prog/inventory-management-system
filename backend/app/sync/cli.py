"""Branch sync CLI:  python -m app.sync.cli status | run | retry-failed | resnapshot | restore-from-cloud"""

import json
import sys

from ..config import get_settings
from ..db import make_engine, make_sessionmaker
from . import client


def main() -> None:
    Session = make_sessionmaker(make_engine(get_settings().database_url), capture=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        with Session() as db:
            print(json.dumps(client.queue_status(db), indent=2, default=str))
    elif cmd == "run":
        print(json.dumps(client.run_cycle(Session), indent=2, default=str))
    elif cmd == "retry-failed":
        print({"requeued": client.retry_failed(Session)})
    elif cmd == "resnapshot":
        client.request_resnapshot(Session)
        print("Snapshot will be sent on the next cycle.")
    elif cmd == "restore-from-cloud":
        print(json.dumps(client.restore_from_cloud(Session), indent=2, default=str))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
