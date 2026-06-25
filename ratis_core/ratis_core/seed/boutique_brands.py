"""Idempotent seed for the Boutique V1 gift_card_brands catalogue.

Inserts the five Saison 1 brands (Amazon.fr · Carrefour · Decathlon ·
Sephora · Spotify) used by the user-facing boutique
(POST /api/v1/rewards/gift-cards/order).

Function is purely SQL-driven (no ORM dependency on
``GiftCardBrand``) so it can run inside an Alembic migration where the
mapped models may not be fully registered.

⚠️  ``provider_brand_id`` placeholders — the values shipped here are
operations placeholders. Real Runa product IDs land later (phase 3 ops
validation, cf ARCH_boutique.md § Catalogue Saison 1). Substitution is
done via direct SQL UPDATE in production by the ops team — the schema is
``provider_brand_id = :runa_real_id WHERE name = :brand_name``.

Called from :

- Alembic data migration ``20260508_2200_boutique_v1`` — runs the seed
  once on every fresh DB the first time the migration chain is applied.
- Tests — ``test_boutique_v1.test_brand_seed_count`` exercises the seed
  against the ``create_all`` test schema.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from ratis_core.database import affected_rows

_log = logging.getLogger(__name__)


# Placeholder Runa product IDs — substituted by ops at provisioning time.
# Format kept stable so admin queries can detect the placeholders by prefix.
_PLACEHOLDER_PREFIX = "placeholder-runa-"


# Saison 1 catalogue (five distinct categories, anti-doublon).
# Logo URLs left empty — CDN path resolved later via admin upload.
BOUTIQUE_BRANDS_SEASON_1: list[dict[str, str]] = [
    {
        "name": "Amazon.fr",
        "provider_brand_id": f"{_PLACEHOLDER_PREFIX}amazon",
        "logo_url": "",
    },
    {
        "name": "Carrefour",
        "provider_brand_id": f"{_PLACEHOLDER_PREFIX}carrefour",
        "logo_url": "",
    },
    {
        "name": "Decathlon",
        "provider_brand_id": f"{_PLACEHOLDER_PREFIX}decathlon",
        "logo_url": "",
    },
    {
        "name": "Sephora",
        "provider_brand_id": f"{_PLACEHOLDER_PREFIX}sephora",
        "logo_url": "",
    },
    {
        "name": "Spotify",
        "provider_brand_id": f"{_PLACEHOLDER_PREFIX}spotify",
        "logo_url": "",
    },
]


def seed_boutique_brands(db: Session) -> int:
    """Insert the Saison 1 brands. Idempotent — uses
    ``ON CONFLICT (name) DO NOTHING`` so a re-run is a no-op.

    Returns the number of rows actually inserted (0 on a re-run).

    The conflict target is ``name`` because the canonical brand identity
    in the boutique catalogue is the human-readable name (Amazon.fr,
    Carrefour, ...), not the placeholder ``provider_brand_id``.
    Operations may overwrite the placeholder with the real Runa id and
    we don't want a second seed run to insert a duplicate row.
    """
    inserted = 0
    for brand in BOUTIQUE_BRANDS_SEASON_1:
        result = db.execute(
            text(
                "INSERT INTO gift_card_brands "
                "  (id, name, provider_brand_id, logo_url, is_active, created_at) "
                "VALUES (:id, :name, :pbid, :logo, true, now()) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "name": brand["name"],
                "pbid": brand["provider_brand_id"],
                "logo": brand["logo_url"] or None,
            },
        )
        inserted += affected_rows(result)
    _log.info("seed_boutique_brands: %d new brand rows inserted", inserted)
    return inserted
