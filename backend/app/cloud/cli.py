"""Cloud administration CLI.

  python -m app.cloud.cli migrate                      # control plane + every branch replica schema
  python -m app.cloud.cli create-admin EMAIL NAME      # prompts for a password; MFA enrolment at first login
"""

import getpass
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..security import hash_password
from .models import CloudBranch, CloudUser
from .service import provision_schema

BACKEND = Path(__file__).resolve().parents[2]


def migrate(url: str) -> None:
    cfg = Config(str(BACKEND / "alembic_cloud.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations_cloud"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with Session(engine) as db:
        for b in db.scalars(select(CloudBranch).where(CloudBranch.schema_name.is_not(None))):
            provision_schema(engine, b.schema_name)  # upgrade replicas BEFORE branches upgrade


def create_admin(url: str, email: str, name: str, password: str) -> None:
    if len(password) < 12:
        raise SystemExit("Password must be at least 12 characters")
    with Session(create_engine(url)) as db:
        db.add(CloudUser(email=email.lower(), display_name=name, password_hash=hash_password(password), role="admin"))
        db.commit()


if __name__ == "__main__":
    url = get_settings().database_url
    if sys.argv[1:2] == ["migrate"]:
        migrate(url)
    elif sys.argv[1:2] == ["create-admin"] and len(sys.argv) == 4:
        create_admin(url, sys.argv[2], sys.argv[3], getpass.getpass("Password: "))
    else:
        print(__doc__)
