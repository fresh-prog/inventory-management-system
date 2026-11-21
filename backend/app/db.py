from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal

from fastapi import Request
from sqlalchemy import DateTime, MetaData, Numeric, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import get_settings

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Column types (ADR-0004)
Money = Numeric(18, 4)
Qty = Numeric(18, 6)
UnitCost = Numeric(28, 12)
Rate = Numeric(9, 6)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {Decimal: Money, datetime: DateTime(timezone=True)}


def created_at_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def make_engine(url: str):
    return create_engine(url, pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=1800)


_engine = None


def get_engine():
    """Default engine from settings (used by scripts/tests; apps hold their own)."""
    global _engine
    if _engine is None:
        _engine = make_engine(get_settings().database_url)
    return _engine


def make_sessionmaker(engine, capture: bool) -> sessionmaker[Session]:
    """capture=True turns on change capture into the sync outbox (branch profile)."""
    return sessionmaker(bind=engine, expire_on_commit=False, info={"capture": capture})


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.Session()
    try:
        yield db
    finally:
        db.close()  # rolls back anything not committed
