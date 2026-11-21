"""Branch schema migrations.

Runs against a branch database (default), or — with a schema — against one branch's replica
schema inside the cloud database:  alembic -x schema=br_<id> upgrade head
Programmatic callers may pass config.attributes["url"] / ["schema"].
"""
from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401  (registers tables)
from app.config import get_settings
from app.db import Base

config = context.config
x = context.get_x_argument(as_dictionary=True)
url = config.attributes.get("url") or x.get("url") or get_settings().database_url
schema = config.attributes.get("schema") or x.get("schema")
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, compare_type=True,
                      version_table_schema=schema)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        if schema:
            if not schema.replace("_", "").isalnum():
                raise ValueError("invalid schema name")
            connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            connection.exec_driver_sql(f'SET search_path TO "{schema}"')
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                          transaction_per_migration=True, version_table_schema=schema)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
