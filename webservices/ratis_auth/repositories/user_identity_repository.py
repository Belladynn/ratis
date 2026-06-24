"""Data layer for the ``user_identities`` table.

H2 Phase 2 moved the OAuth identity off the ``users`` row into a dedicated
``user_identities`` table keyed by the unique ``(provider, provider_id)``
pair. R03 — no SQL outside this module.
"""

import uuid

from ratis_core.models import UserIdentity
from sqlalchemy.orm import Session


def get_by_provider(db: Session, provider: str, provider_id: str) -> UserIdentity | None:
    """Look up an identity by its unique (provider, provider_id) pair."""
    return (
        db.query(UserIdentity)
        .filter(UserIdentity.provider == provider, UserIdentity.provider_id == provider_id)
        .first()
    )


def get_by_provider_for_user(db: Session, user_id: uuid.UUID, provider: str) -> UserIdentity | None:
    """Look up the identity for ``provider`` belonging to ``user_id``."""
    return db.query(UserIdentity).filter(UserIdentity.user_id == user_id, UserIdentity.provider == provider).first()


def list_for_user(db: Session, user_id: uuid.UUID) -> list[UserIdentity]:
    """Return every identity linked to ``user_id``, oldest first."""
    return db.query(UserIdentity).filter(UserIdentity.user_id == user_id).order_by(UserIdentity.created_at.asc()).all()


def count_for_user(db: Session, user_id: uuid.UUID) -> int:
    """Number of identities linked to ``user_id``."""
    return db.query(UserIdentity).filter(UserIdentity.user_id == user_id).count()


def create(
    db: Session,
    *,
    user_id: uuid.UUID,
    provider: str,
    provider_id: str,
    email: str | None = None,
) -> UserIdentity:
    """Insert a new identity row (does not commit)."""
    identity = UserIdentity(
        user_id=user_id,
        provider=provider,
        provider_id=provider_id,
        email=email,
    )
    db.add(identity)
    db.flush()
    return identity


def delete_for_user(db: Session, user_id: uuid.UUID, provider: str) -> int:
    """Delete the identity for ``provider`` on ``user_id``. Returns rows deleted."""
    return (
        db.query(UserIdentity)
        .filter(UserIdentity.user_id == user_id, UserIdentity.provider == provider)
        .delete(synchronize_session=False)
    )
