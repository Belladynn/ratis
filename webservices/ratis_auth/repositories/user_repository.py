import uuid

from ratis_core.identifiers import generate_support_id
from ratis_core.models import User
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# Max retries when a freshly-generated ``support_id`` collides with an
# existing row. The keyspace is 32^6 ≈ 1.07B — the collision rate is
# vanishingly small even at 1M users. 5 attempts is comfortable defense.
_SUPPORT_ID_MAX_RETRIES = 5


def get_by_email(db: Session, email: str) -> User | None:
    """Return the first ``users`` row matching ``email``.

    Email is NOT unique since Phase 2 (``user_identities`` is the account
    key) — this returns the FIRST match by insertion order. Do not use it
    as an identity lookup.
    """
    return db.query(User).filter(User.email == email).first()


def get_by_id(db: Session, user_id: uuid.UUID) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def get_by_support_id(db: Session, support_id: str) -> User | None:
    """Look up a user by their public ``RTS-XXXXXX`` support identifier."""
    return db.query(User).filter(User.support_id == support_id).first()


def create_user(
    db: Session,
    *,
    email: str,
    account_type: str = "oauth",
    display_name: str | None = None,
    avatar_url: str | None = None,
    timezone: str = "Europe/Paris",
) -> User:
    """Insert a new ``users`` row with a freshly-generated ``support_id``.

    The ``support_id`` is centralised here — every user creation path
    (register, OAuth Google, OAuth Apple) goes through this function, so
    no caller needs to know the format or the retry contract. On the
    extremely unlikely event of a UNIQUE collision we retry up to
    :data:`_SUPPORT_ID_MAX_RETRIES` times with a SAVEPOINT to keep the
    outer transaction usable. Beyond that we propagate the IntegrityError
    — if it ever fires it signals either a bug in the generator or the
    keyspace is too small (in which case the alphabet/length must grow).
    """
    last_exc: IntegrityError | None = None
    for _ in range(_SUPPORT_ID_MAX_RETRIES):
        sp = db.begin_nested()
        try:
            user = User(
                email=email,
                account_type=account_type,
                display_name=display_name,
                avatar_url=avatar_url,
                timezone=timezone,
                support_id=generate_support_id(),
            )
            db.add(user)
            db.flush()  # assigns id + checks UNIQUE constraints inside the SAVEPOINT
            sp.commit()
            db.refresh(user)
            return user
        except IntegrityError as exc:
            sp.rollback()
            # Only retry on a duplicate ``support_id``. Since H2 Phase 2 the
            # ``support_id`` unique index is the only UNIQUE on a ``users``
            # insert (``email`` is no longer unique, ``provider_id`` is gone).
            # Any other IntegrityError — a NOT NULL / CHECK violation — must
            # surface to the caller.
            if "support_id" not in str(exc.orig):
                raise
            last_exc = exc
            continue
    # Exhausted retries — propagate the last collision so callers know it
    # was a support_id keyspace exhaustion (signal to widen the alphabet).
    assert last_exc is not None
    raise last_exc
