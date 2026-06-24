import uuid

from ratis_core.models import UserPreferences
from sqlalchemy.orm import Session


def get(db: Session, user_id: uuid.UUID) -> UserPreferences | None:
    return db.query(UserPreferences).filter(UserPreferences.user_id == user_id).first()


def get_or_create(db: Session, user_id: uuid.UUID) -> UserPreferences:
    prefs = get(db, user_id)
    if prefs is None:
        prefs = UserPreferences(user_id=user_id)
        db.add(prefs)
        db.flush()
        db.refresh(prefs)
    return prefs


def upsert(
    db: Session,
    user_id: uuid.UUID,
    *,
    search_radius_km: int | None,
    transport_mode: str | None,
) -> UserPreferences:
    prefs = get_or_create(db, user_id)
    if search_radius_km is not None:
        prefs.search_radius_km = search_radius_km
    if transport_mode is not None:
        prefs.transport_mode = transport_mode
    return prefs
