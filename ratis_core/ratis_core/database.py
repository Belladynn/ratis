import os
import threading
from collections.abc import Generator
from typing import Any

from sqlalchemy import Engine, Result, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str, **kwargs: Any) -> Engine:
    """Create a SQLAlchemy engine. URL must use postgresql+psycopg:// scheme.

    Defaults ``pool_pre_ping=True`` (drops stale connections after a network
    blip / PG restart) and ``pool_recycle=1800`` (prunes connections older
    than 30 min). An explicit kwarg from the caller always wins.
    """
    kwargs.setdefault("pool_pre_ping", True)
    kwargs.setdefault("pool_recycle", 1800)
    return create_engine(url, **kwargs)


def affected_rows(result: Result[Any]) -> int:
    """Number of rows touched by a DML statement (INSERT/UPDATE/DELETE).

    SQLAlchemy 2.0 types ``Session.execute`` as returning ``Result``, whose
    stub omits ``rowcount`` — that attribute lives on the runtime
    ``CursorResult``. This is the single, centralised place that accepts the
    stub gap, so DML call sites stay fully typed instead of scattering
    ``# type: ignore`` across every repository.
    """
    return result.rowcount  # type: ignore[attr-defined]  # reason: rowcount absent from SQLAlchemy Result stub


# Lazy — engine is created on first get_db() call, not at import time.
# This allows importing Base and models without DATABASE_URL being set
# (e.g. pure unit tests, Alembic env.py before env vars are loaded).
DATABASE_URL: str | None = None
engine: Engine | None = None
SessionLocal: sessionmaker | None = None
_init_lock = threading.Lock()


def _init() -> None:
    """Initialize engine from DATABASE_URL. No-op if already done."""
    global DATABASE_URL, engine, SessionLocal
    with _init_lock:
        if engine is not None:
            return
        raw = os.environ.get("DATABASE_URL")
        if not raw:
            raise RuntimeError("DATABASE_URL not set")
        DATABASE_URL = raw
        engine = make_engine(raw)
        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    _init()
    assert SessionLocal is not None  # _init() guarantees it is set (or raised)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
