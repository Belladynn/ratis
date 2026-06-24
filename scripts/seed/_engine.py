"""SQLAlchemy engine/session helper for seed scripts.

Built on top of ``ratis_core.database.make_engine`` — the seed code never
talks SQL directly, it goes through SA-2.0 ORM models from ``ratis_core``.

Engine is created lazily (first ``get_session()`` call) so importing this
module does not require ``DATABASE_URL`` to be set (e.g. for unit tests
that only exercise safety guards).
"""

from __future__ import annotations

import os
import threading

from ratis_core.database import make_engine
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_init_lock = threading.Lock()


def _init() -> None:
    """Initialize engine from ``DATABASE_URL``. No-op if already done."""
    global _engine, _SessionLocal
    with _init_lock:
        if _engine is not None:
            return
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL not set — seed cannot connect.")
        _engine = make_engine(url)
        _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


def get_engine() -> Engine:
    """Return the lazily-initialized SQLAlchemy engine."""
    _init()
    assert _engine is not None
    return _engine


def get_session() -> Session:
    """Return a new SQLAlchemy session. Caller owns commit/rollback/close."""
    _init()
    assert _SessionLocal is not None
    return _SessionLocal()
