"""
Utilitaire de seed pour app_settings.

Lit ratis_settings.json et insère chaque section dans app_settings.
Idempotent — utilise INSERT … ON CONFLICT DO UPDATE.

Usage CLI (production) :
    DATABASE_URL=postgresql+psycopg://... python -m ratis_core.seed_settings

Usage Python (tests, scripts) :
    from ratis_core.seed_settings import seed_settings
    seed_settings(db)  # db : sqlalchemy Session
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from ratis_core.settings import _load_from_json

logger = logging.getLogger(__name__)


def seed_settings(db: Session) -> int:
    """
    Insert all sections from ratis_settings.json into app_settings.
    Uses UPSERT — safe to call multiple times.
    Returns the number of sections upserted.
    """
    import json

    cfg = _load_from_json()
    count = 0
    for section, data in cfg.items():
        db.execute(
            text(
                "INSERT INTO app_settings (section, data) "
                "VALUES (:section, CAST(:data AS jsonb)) "
                "ON CONFLICT (section) DO UPDATE "
                "  SET data = EXCLUDED.data, updated_at = now()"
            ),
            {"section": section, "data": json.dumps(data)},
        )
        count += 1
    db.commit()
    logger.info("seed_settings: %d sections upserted", count)
    return count


if __name__ == "__main__":
    import os

    from sqlalchemy.orm import sessionmaker

    from ratis_core.database import make_engine

    url = os.environ["DATABASE_URL"]
    engine = make_engine(url)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        n = seed_settings(db)
        print(f"Seeded {n} sections into app_settings.")
    finally:
        db.close()
