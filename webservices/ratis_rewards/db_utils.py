"""
Database transaction utilities for ratis_rewards routes.

Usage in route handlers:

    try:
        with db_transaction(db):
            result = some_repo_call(db, ...)
    except SomeBusinessError:
        raise HTTPException(status_code=409, detail="some_error")
    return result

The context manager commits on success and rolls back on any exception,
then re-raises so the caller can convert business exceptions to HTTPException.
"""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy.orm import Session


@contextmanager
def db_transaction(db: Session):
    """Commit on success, rollback and re-raise on any exception."""
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise
