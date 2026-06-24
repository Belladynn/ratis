import uuid
from datetime import UTC, datetime

from ratis_core.models import RefreshToken
from sqlalchemy.orm import Session


def get_by_jti(db: Session, jti: str) -> RefreshToken | None:
    return db.query(RefreshToken).filter(RefreshToken.jti == jti).first()


def create(db: Session, *, jti: str, user_id: uuid.UUID, expires_at: datetime) -> RefreshToken:
    token = RefreshToken(jti=jti, user_id=user_id, expires_at=expires_at)
    db.add(token)
    db.flush()
    return token


def revoke(db: Session, db_token: RefreshToken) -> None:
    db_token.revoked_at = datetime.now(UTC)


def revoke_all_for_user(db: Session, user_id: uuid.UUID) -> None:
    """Revoke all active refresh tokens for a user (e.g. on password change)."""
    now = datetime.now(UTC)
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id,
        RefreshToken.revoked_at.is_(None),
    ).update({"revoked_at": now}, synchronize_session=False)
