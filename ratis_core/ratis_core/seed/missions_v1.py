"""Idempotent seed for the V1 missions catalogue (phase B).

Exposes the canonical list of 41 mission templates that compose the V1
catalogue and a ``seed_missions_catalog_v1(db)`` helper that UPSERTs
them into the ``missions`` table.

Called from :

- Alembic data migrations :
    * ``20260508_1000_missions_catalog_v1`` — phase A initial seed
      once the structural migration has added ``missions.qualifier``
      and extended the unique constraint to include it.
    * ``20260508_1800_missions_phase_b`` — phase B re-seed that
      renames ``barcode_scan→product_identification``, prefixes the
      qualifiers (``attribute:organic``, ``category``, ``store``…)
      and flips every template to ``is_active=true`` after the new
      service code is live.
- Tests — ``test_missions_catalog_v1.py`` and
  ``test_phase_b_trigger_action.py`` exercise the seed against the
  ``create_all`` test schema (no Alembic).

Function is purely SQL-driven (no ORM dependency on Mission) so it can
run inside an Alembic migration where the mapped models may not be
fully registered.

Phase B made every template active : the runtime now honours the
``qualifier`` filter (prefixed values like ``attribute:organic``,
plus ``category`` / ``store`` "type-only" qualifiers used by
``scan_distinct``), the ``fill_product_field`` event and the
``promo_found`` event.

Post-phase-B follow-up (2026-05-08) : the 9 templates with
``qualifier IN ('attribute:organic', 'attribute:french')`` are
deactivated until phase C ships the PA worker qualifier enrichment
(events emitted with the ``attribute:`` prefix on the payload).
Without that upstream signal, those rows would be visible to users
via lazy-gen but their ``current_count`` would never increment —
broken missions. ``is_active`` is therefore False for those 9 rows
and True for the 32 others. Migration
``20260509_0100_disable_qualifier_attribute_missions`` flips the
9 rows on already-seeded prod DBs.
"""

from __future__ import annotations

import logging
from typing import TypedDict

from sqlalchemy import text
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)


class _MissionTemplate(TypedDict):
    action_type: str
    qualifier: str | None
    frequency: str
    difficulty: str
    target_count: int
    cab_reward: int


def _tpl(
    action_type: str,
    qualifier: str | None,
    frequency: str,
    difficulty: str,
    target_count: int,
    cab_reward: int,
) -> _MissionTemplate:
    return {
        "action_type": action_type,
        "qualifier": qualifier,
        "frequency": frequency,
        "difficulty": difficulty,
        "target_count": target_count,
        "cab_reward": cab_reward,
    }


# Canonical catalogue — ordering matches the brainstorm doc so reviewers
# can diff visually. Phase B applied :
#   * rename ``barcode_scan`` → ``product_identification``
#   * prefix qualifiers : ``organic`` → ``attribute:organic``,
#     ``french`` → ``attribute:french``. ``category`` and ``store`` stay
#     as the unprefixed type tag — events are emitted with the resolved
#     value (e.g. ``category:dairy``, ``store:<uuid>``) and the runtime
#     matches them against the type prefix.
MISSION_TEMPLATES_V1: list[_MissionTemplate] = [
    # Mission 1 — receipt_scan (anti-push-buy capped).
    _tpl("receipt_scan", None, "daily", "easy", 1, 5),
    _tpl("receipt_scan", None, "weekly", "easy", 3, 20),
    # Mission 2 — label_scan.
    _tpl("label_scan", None, "daily", "easy", 1, 5),
    _tpl("label_scan", None, "daily", "medium", 3, 15),
    _tpl("label_scan", None, "daily", "hard", 5, 30),
    _tpl("label_scan", None, "weekly", "easy", 10, 20),
    _tpl("label_scan", None, "weekly", "medium", 15, 50),
    _tpl("label_scan", None, "weekly", "hard", 20, 100),
    # Mission 3 — product_identification (manual EAN scans, ex-barcode_scan).
    _tpl("product_identification", None, "daily", "easy", 1, 5),
    _tpl("product_identification", None, "daily", "medium", 3, 15),
    _tpl("product_identification", None, "daily", "hard", 5, 30),
    _tpl("product_identification", None, "weekly", "easy", 5, 20),
    _tpl("product_identification", None, "weekly", "medium", 10, 50),
    _tpl("product_identification", None, "weekly", "hard", 15, 100),
    # Mission 4 — product_identification qualifier=attribute:organic.
    _tpl("product_identification", "attribute:organic", "daily", "easy", 1, 5),
    _tpl("product_identification", "attribute:organic", "weekly", "easy", 3, 20),
    _tpl("product_identification", "attribute:organic", "weekly", "medium", 5, 50),
    # Mission 5 — product_identification qualifier=attribute:french.
    _tpl("product_identification", "attribute:french", "daily", "easy", 1, 5),
    _tpl("product_identification", "attribute:french", "weekly", "easy", 3, 20),
    _tpl("product_identification", "attribute:french", "weekly", "medium", 5, 50),
    # Mission 6 — fill_product_field.
    _tpl("fill_product_field", None, "daily", "easy", 2, 5),
    _tpl("fill_product_field", None, "daily", "medium", 4, 15),
    _tpl("fill_product_field", None, "daily", "hard", 6, 30),
    _tpl("fill_product_field", None, "weekly", "easy", 10, 20),
    _tpl("fill_product_field", None, "weekly", "medium", 12, 50),
    _tpl("fill_product_field", None, "weekly", "hard", 15, 100),
    # Mission 7 — fill_product_field qualifier=attribute:organic.
    _tpl("fill_product_field", "attribute:organic", "daily", "easy", 1, 5),
    _tpl("fill_product_field", "attribute:organic", "weekly", "easy", 2, 20),
    _tpl("fill_product_field", "attribute:organic", "weekly", "medium", 4, 50),
    # Mission 8 — scan_distinct qualifier=category (type tag, values appended
    # to user_missions.tracked_values as 'category:<slug>').
    _tpl("scan_distinct", "category", "daily", "easy", 2, 5),
    _tpl("scan_distinct", "category", "daily", "medium", 3, 15),
    _tpl("scan_distinct", "category", "daily", "hard", 5, 30),
    _tpl("scan_distinct", "category", "weekly", "easy", 5, 20),
    _tpl("scan_distinct", "category", "weekly", "medium", 8, 50),
    _tpl("scan_distinct", "category", "weekly", "hard", 12, 100),
    # Mission 9 — scan_distinct qualifier=store (weekly only, no hard).
    _tpl("scan_distinct", "store", "weekly", "easy", 2, 20),
    _tpl("scan_distinct", "store", "weekly", "medium", 3, 50),
    # Mission 10 — promo_found.
    _tpl("promo_found", None, "daily", "easy", 1, 5),
    _tpl("promo_found", None, "weekly", "easy", 1, 20),
    _tpl("promo_found", None, "weekly", "medium", 2, 50),
    _tpl("promo_found", None, "weekly", "hard", 3, 100),
]


assert len(MISSION_TEMPLATES_V1) == 41, (
    f"MISSION_TEMPLATES_V1 must hold exactly 41 templates — got {len(MISSION_TEMPLATES_V1)}"
)


def _is_active_for(template: _MissionTemplate) -> bool:
    """Active-flag rule for the canonical seed.

    Phase B unlocked every action_type and qualifier shape, so by
    default every template ships active. Exception : the 9 templates
    with ``qualifier IN ('attribute:organic', 'attribute:french')``
    stay inactive until phase C ships PA worker qualifier enrichment
    — the runtime would otherwise surface a mission whose
    ``current_count`` never increments (events not yet tagged with
    the matching ``attribute:`` prefix on the payload).
    """
    return template["qualifier"] not in ("attribute:organic", "attribute:french")


def seed_missions_catalog_v1(db: Session) -> int:
    """Insert (or refresh) the 41 V1 mission templates.

    Idempotent — uses ``ON CONFLICT (action_type, qualifier, frequency,
    difficulty) DO UPDATE`` so a re-run keeps a single row per natural
    key and refreshes the reward / target / is_active columns to match
    the canonical catalogue.

    Returns the number of templates touched (always 41).
    """
    inserted = 0
    for tpl in MISSION_TEMPLATES_V1:
        is_active = _is_active_for(tpl)
        # is_boostable mirrors the V0 rule : receipt_scan is non-boostable
        # (anti-push-buy philosophy), everything else stays boostable.
        is_boostable = tpl["action_type"] != "receipt_scan"
        # ``id`` is filled with ``gen_random_uuid()`` rather than relying on
        # the column's server_default — Alembic-built prod DBs do declare
        # one, but the SQLAlchemy model's create_all path used by tests
        # does not, so an explicit DEFAULT-driven INSERT keeps both lineages
        # working.
        db.execute(
            text(
                "INSERT INTO missions "
                "  (id, action_type, qualifier, frequency, difficulty, "
                "   target_count, cab_reward, is_active, is_boostable) "
                "VALUES (gen_random_uuid(), :action_type, :qualifier, "
                "        :frequency, :difficulty, "
                "        :target_count, :cab_reward, :is_active, "
                "        :is_boostable) "
                "ON CONFLICT (action_type, qualifier, frequency, difficulty) "
                "DO UPDATE SET "
                "  target_count = EXCLUDED.target_count, "
                "  cab_reward = EXCLUDED.cab_reward, "
                "  is_active = EXCLUDED.is_active, "
                "  is_boostable = EXCLUDED.is_boostable"
            ),
            {
                "action_type": tpl["action_type"],
                "qualifier": tpl["qualifier"],
                "frequency": tpl["frequency"],
                "difficulty": tpl["difficulty"],
                "target_count": tpl["target_count"],
                "cab_reward": tpl["cab_reward"],
                "is_active": is_active,
                "is_boostable": is_boostable,
            },
        )
        inserted += 1
    _log.info("seeded missions catalog v1 : %d templates", inserted)
    return inserted
