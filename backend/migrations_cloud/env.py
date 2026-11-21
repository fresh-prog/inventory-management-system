"""Cloud control-plane migrations (public schema). Per-branch replica schemas use migrations/."""
from alembic import context
from sqlalchemy import create_engine, pool

from app.cloud.models import CloudBase
from app.config import get_settings

config = context.config
url = config.attributes.get("url") or context.get_x_argument(as_dictionary=True).get("url") or get_settings().database_url


def run():
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=CloudBase.metadata, compare_type=True,
                          version_table="alembic_version_cloud", transaction_per_migration=True,
                          include_name=lambda name, type_, parent: type_ != "table" or name in CloudBase.metadata.tables)
        with context.begin_transaction():
            context.run_migrations()


run()
