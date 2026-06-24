"""
Utilitaire de seed pour le catalog Achievements V1.

Source-of-truth des 23 entrées initiales. Référence : section "Catalog seed
initial" de docs/superpowers/specs/2026-05-09-achievements-v1-design.md.

NB : ``sea_winter`` (Hiver 25) est intentionnellement omis — sa fenêtre
``available_until`` est déjà fermée à la date de seed (2026-05-10), donc
visible pour personne. À insérer plus tard via une migration de backfill
si besoin (rare).

Usage :

* Migration Alembic ``20260510_1010_seed_achievements_v1`` →
  ``seed_achievements(db)`` (idempotent — UPSERT sur ``code``).
* Tests ``conftest.py`` → autouse session-scope fixture (post
  ``Base.metadata.create_all``).

Le pattern miroir ``ratis_core.seed_settings`` (cf
``ratis_core/seed_settings.py``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _entry(
    code: str,
    label: str,
    description: str,
    icon: str,
    rarity: str,
    category: str,
    trigger_type: str,
    target_value: int,
    cab_reward: int,
    display_order: int,
    *,
    window_days: int | None = None,
    extra_params: dict[str, Any] | None = None,
    is_secret: bool = False,
    is_hidden: bool = False,
    available_from: str | None = None,
    available_until: str | None = None,
) -> dict[str, Any]:
    """Compact constructor for a catalog entry — keeps the data table
    below readable while making each field explicit at the call site."""
    return {
        "code": code,
        "label": label,
        "description": description,
        "icon": icon,
        "rarity": rarity,
        "category": category,
        "trigger_type": trigger_type,
        "target_value": target_value,
        "window_days": window_days,
        "extra_params": extra_params,
        "cab_reward": cab_reward,
        "is_secret": is_secret,
        "is_hidden": is_hidden,
        "available_from": available_from,
        "available_until": available_until,
        "display_order": display_order,
    }


# 23 entries — sea_winter omis (window passée). Mirror of catalog seed
# table in spec § 6.
ACHIEVEMENTS_V1: tuple[dict[str, Any], ...] = (
    # ── VOLUME ──
    _entry(
        "v_first",
        "Premier scan",
        "Scanner ton tout premier ticket",
        "x",
        "terracotta",
        "volume",
        "scan_count",
        1,
        20,
        10,
    ),
    _entry("v_10", "Habitue", "Scanner 10 tickets", "list", "bronze", "volume", "scan_count", 10, 30, 20),
    _entry("v_50", "Cinquantaine", "Scanner 50 tickets", "list2", "copper", "volume", "scan_count", 50, 40, 30),
    _entry("v_500", "Demi-millier", "Scanner 500 tickets", "chart", "gold", "volume", "scan_count", 500, 100, 40),
    _entry("v_1000", "Millier", "Scanner 1000 tickets", "trophy", "crystal", "volume", "scan_count", 1000, 750, 50),
    # ── SAVINGS (target_value en centimes) ──
    _entry(
        "s_1",
        "Premiere eco",
        "Economiser ton premier euro",
        "coin",
        "terracotta",
        "savings",
        "savings_eur_total",
        100,
        20,
        110,
    ),
    _entry("s_10", "10 balles", "Economiser 10 EUR", "bill1", "bronze", "savings", "savings_eur_total", 1000, 30, 120),
    _entry("s_50", "Demi-bil", "Economiser 50 EUR", "bill2", "copper", "savings", "savings_eur_total", 5000, 40, 130),
    _entry(
        "s_500",
        "Demi-millier EUR",
        "Economiser 500 EUR",
        "bill3",
        "sapphire",
        "savings",
        "savings_eur_total",
        50000,
        250,
        140,
    ),
    _entry(
        "s_day_20",
        "Grosse journee",
        "Economiser 20 EUR en une journee",
        "star",
        "emerald",
        "savings",
        "savings_eur_in_window",
        2000,
        150,
        150,
        window_days=1,
    ),
    # ── STREAK ──
    _entry("r_3", "Trio", "Streak de 3 jours", "fire", "bronze", "streak", "streak_days", 3, 30, 210),
    _entry("r_7", "Semaine pleine", "Streak de 7 jours", "fire", "copper", "streak", "streak_days", 7, 40, 220),
    _entry("r_14", "Quinzaine", "Streak de 14 jours", "fire", "silver", "streak", "streak_days", 14, 50, 230),
    _entry("r_30", "Mois sans rater", "Streak de 30 jours", "fire", "sapphire", "streak", "streak_days", 30, 250, 240),
    _entry("r_365", "Une annee", "Streak de 365 jours", "milky", "diamond", "streak", "streak_days", 365, 1200, 250),
    # ── SOCIAL ──
    _entry("soc_invite_1", "Recruteur", "Inviter 1 ami", "hands", "bronze", "social", "referral_count", 1, 30, 310),
    _entry("soc_invite_10", "Reseau", "Inviter 10 amis", "globe", "gold", "social", "referral_count", 10, 100, 320),
    # ── EXPLORATION ──
    _entry(
        "exp_brand_5",
        "Curieux",
        "Scanner dans 5 enseignes differentes",
        "cart",
        "bronze",
        "exploration",
        "unique_brands_count",
        5,
        30,
        410,
    ),
    _entry(
        "exp_cat_15",
        "Encyclopediste",
        "Scanner dans 15 categories differentes",
        "books",
        "gold",
        "exploration",
        "unique_categories_count",
        15,
        100,
        420,
    ),
    _entry(
        "exp_unknown_10",
        "Pionnier",
        "Decouvrir 10 produits jamais vus",
        "rocket",
        "emerald",
        "exploration",
        "unique_products_discovered_count",
        10,
        150,
        430,
    ),
    # ── SEASONAL (sea_winter omis — window deja fermee) ──
    _entry(
        "sea_summer",
        "Ete 26",
        "Participer au Pass Ete 26",
        "sun",
        "gold",
        "seasonal",
        "first_event",
        1,
        100,
        510,
        extra_params={
            "event": "battlepass_season_participated",
            "season_id": "summer_26",
        },
        available_from="2026-06-01T00:00:00+00:00",
        available_until="2026-09-01T00:00:00+00:00",
    ),
    # ── SECRET ──
    _entry(
        "sec_konami",
        "???",
        "Succes secret",
        "qmark",
        "diamond",
        "secret",
        "first_event",
        1,
        1200,
        610,
        extra_params={"event": "konami_code_entered"},
        is_secret=True,
    ),
    _entry(
        "sec_3am",
        "???",
        "Succes secret",
        "qmark",
        "gold",
        "secret",
        "first_event",
        1,
        100,
        620,
        extra_params={"event": "app_opened_at_3am"},
        is_secret=True,
    ),
)


SEED_CODES: tuple[str, ...] = tuple(row["code"] for row in ACHIEVEMENTS_V1)


def seed_achievements(db: Session) -> int:
    """Insert / upsert the V1 catalog. Returns the number of rows touched.

    Idempotent : ``ON CONFLICT (code) DO UPDATE`` — re-running on a
    populated DB simply refreshes label/description/etc. (rarities and
    target values are preserved by the snapshot in ``user_achievements``
    of historical unlocks ; updating the catalog row never rewrites past
    grants).
    """
    sql = text(
        """
        INSERT INTO achievements (
            code, label, description, icon, rarity, category, trigger_type,
            target_value, window_days, extra_params, cab_reward,
            is_secret, is_hidden, available_from, available_until,
            display_order
        )
        VALUES (
            :code, :label, :description, :icon, :rarity, :category,
            :trigger_type, :target_value, :window_days,
            CAST(:extra_params AS jsonb), :cab_reward,
            :is_secret, :is_hidden,
            CAST(:available_from AS timestamptz),
            CAST(:available_until AS timestamptz),
            :display_order
        )
        ON CONFLICT (code) DO UPDATE SET
            label = EXCLUDED.label,
            description = EXCLUDED.description,
            icon = EXCLUDED.icon,
            rarity = EXCLUDED.rarity,
            category = EXCLUDED.category,
            trigger_type = EXCLUDED.trigger_type,
            target_value = EXCLUDED.target_value,
            window_days = EXCLUDED.window_days,
            extra_params = EXCLUDED.extra_params,
            cab_reward = EXCLUDED.cab_reward,
            is_secret = EXCLUDED.is_secret,
            is_hidden = EXCLUDED.is_hidden,
            available_from = EXCLUDED.available_from,
            available_until = EXCLUDED.available_until,
            display_order = EXCLUDED.display_order,
            updated_at = now()
        """
    )
    count = 0
    for row in ACHIEVEMENTS_V1:
        params = dict(row)
        if params["extra_params"] is not None:
            params["extra_params"] = json.dumps(params["extra_params"])
        db.execute(sql, params)
        count += 1
    db.commit()
    logger.info("seed_achievements: %d rows upserted", count)
    return count
