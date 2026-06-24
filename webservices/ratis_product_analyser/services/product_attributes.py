"""Pure helpers that inspect ``products`` columns and return derived
attributes for downstream consumers (notably the missions/rewards
emit layer).

This module is intentionally **side-effect-free** : no DB IO, no logging,
no network. Inputs are plain Python values fetched by the caller.

Phase C-1 (missions sprint) — first attribute supported : ``is_organic``.
Phase C-3 adds ``derive_scan_distinct_qualifiers`` : builds the
``category:<slug>`` and ``store:<uuid>`` qualifier strings consumed by
the ``scan_distinct`` mission family (8 active templates in V1 catalog).
Phase C-2 adds ``is_french_product`` : matches OFF ``origins_tags`` so
the PA worker can decorate ``trigger_action`` events with
``qualifier='attribute:french'`` (3 ``product_identification`` templates
gated until the prod backfill batch + manual mission flip).

Future attributes (``is_fair_trade``…) will land in this module as
similar pure functions.
"""

from __future__ import annotations

import uuid

# Canonical set of OFF ``labels_tags`` entries that signal **organic
# certification**. Sourced from Open Food Facts taxonomy :
#
#   * ``en:organic``               — generic English flag
#   * ``fr:bio``                   — French short-hand
#   * ``en:eu-organic``            — EU regulation 2018/848 certification
#   * ``fr:agriculture-biologique`` — French AB label
#
# Match is exact (no prefix slicing) and case-insensitive — OFF normally
# lowercases tags but defensive in case the upstream sync ever changes.
# Adding a new signal here is the single point of truth ; do NOT scatter
# the literal across the codebase.
_ORGANIC_SIGNALS = frozenset(
    {
        "en:organic",
        "fr:bio",
        "en:eu-organic",
        "fr:agriculture-biologique",
    }
)


# Canonical set of OFF ``origins_tags`` entries that signal **French
# origin**. Sourced from Open Food Facts taxonomy ; the live API
# returns shapes such as ``['en:france']`` (most common),
# ``['en:france', 'en:european-union']`` (frequent), or
# ``['en:france', 'fr:france']`` (rare bilingual). ``en:made-in-france``
# is a less frequent but documented variant — included for forward-compat.
#
# Match is exact (no prefix slicing) and case-insensitive — OFF normally
# lowercases tags but defensive in case the upstream sync ever changes.
# Adding a new signal here is the single point of truth ; do NOT scatter
# the literal across the codebase.
_FRENCH_SIGNALS = frozenset(
    {
        "en:france",
        "fr:france",
        "en:made-in-france",
    }
)


def is_organic_product(labels_tags: list[str] | None) -> bool:
    """Return ``True`` iff the product's OFF ``labels_tags`` array
    contains at least one canonical organic-certification signal.

    Args :
        labels_tags : value read from ``products.labels_tags`` (ARRAY of
            TEXT). May be ``None`` (column nullable) or an empty list.

    Returns :
        bool — ``True`` if any tag (case-insensitive) matches one of the
        canonical organic signals (see ``_ORGANIC_SIGNALS``).
        ``False`` for ``None`` / empty / non-matching arrays.

    Notes :
        Case-insensitive : ``["EN:Organic"]`` matches. Partial-string
        matches do NOT count — ``"en:organic-farming-something"`` is
        rejected by design (avoids false positives on derived sub-tags).
    """
    if not labels_tags:
        return False
    return any(tag.lower() in _ORGANIC_SIGNALS for tag in labels_tags)


def is_french_product(origins_tags: list[str] | None) -> bool:
    """Return ``True`` iff the product's OFF ``origins_tags`` array
    contains at least one canonical French-origin signal.

    Args :
        origins_tags : value read from ``products.origins_tags`` (ARRAY of
            TEXT, added by migration ``20260511_2400_phase_c2_origins_tags``).
            May be ``None`` (column nullable, pre-backfill rows) or an
            empty list (OFF row with no origin metadata).

    Returns :
        bool — ``True`` if any tag (case-insensitive) matches one of the
        canonical French signals (see ``_FRENCH_SIGNALS``).
        ``False`` for ``None`` / empty / non-matching arrays.

    Notes :
        Case-insensitive : ``["EN:France"]`` matches. Partial-string
        matches do NOT count — ``"en:france-metropolitaine"`` is rejected
        by design (avoids false positives on derived sub-tags ; if such a
        variant becomes prevalent, add it to ``_FRENCH_SIGNALS`` rather
        than loosening the matcher).

        Live OFF data confirmed (2026-05-11) : the dominant shape is the
        plain ``en:france`` literal, sometimes alongside broader origin
        tags like ``en:european-union``. The simple ``in`` check on the
        full array is sufficient — no hierarchy parsing needed.
    """
    if not origins_tags:
        return False
    return any(tag.lower() in _FRENCH_SIGNALS for tag in origins_tags)


# ── Phase C-3 — scan_distinct qualifier derivation ─────────────────────


def derive_scan_distinct_qualifiers(
    *,
    categories_tags: list[str] | None,
    store_id: uuid.UUID | None,
) -> list[str]:
    """Build the qualifier strings consumed by ``scan_distinct`` missions.

    The V1 catalogue carries 8 active ``scan_distinct`` missions :

      * 6 ``scan_distinct + category`` (daily/weekly × easy/medium/hard)
      * 2 ``scan_distinct + store``   (weekly easy + weekly medium)

    Each fires a separate ``trigger_action`` from the caller with the
    qualifier shape this helper produces. The mission runtime
    (``missions_repository.apply_action_event_to_user_missions`` branch B)
    splits on the FIRST colon to extract the type tag (``category`` /
    ``store``) and treats the remainder as the tracked value appended
    (deduped) to ``user_missions.tracked_values``.

    Args :
        categories_tags : the OFF ``products.categories_tags`` array, or
            ``None`` for unmatched scans. When present and non-empty,
            ``categories_tags[0]`` is taken — by OFF convention the FIRST
            entry is the most-specific tag for the product (e.g.
            ``en:apples`` rather than ``en:fruits``). V1 picks the
            most-specific tag because :
              * it gives the user a tangible per-product progress signal
                ("I scanned an apple") rather than a broad bucket;
              * it maps 1:1 with the OFF data without ranking heuristics;
              * the broader-tag rollout is a future C-3.1 evolution.
            The full tag (including ``<lang>:`` prefix) is preserved
            verbatim — splitting on the first colon at consumer side
            yields ``en:apples`` as the tracked value.
        store_id : the resolved ``scans.store_id`` after reconciliation
            (NOT the pre-reconciliation NULL). ``None`` skips the
            ``store:`` qualifier emit.

    Returns :
        list[str] — 0 to 2 qualifier strings. Order is :
            1. ``category:<tag>`` (if categories_tags non-empty)
            2. ``store:<uuid>``    (if store_id not None)

    Notes :
        Side-effect-free pure function. The caller (typically
        ``reconciliation_service._default_reward_trigger``) iterates the
        returned list and emits one ``trigger_action`` per qualifier with
        a distinct ``idempotency_key`` suffix to avoid the
        ``reward_events UNIQUE(user_id, reference_type, reference_id)``
        collision.
    """
    qualifiers: list[str] = []
    if categories_tags:
        # ``categories_tags[0]`` is the most-specific OFF tag by convention.
        qualifiers.append(f"category:{categories_tags[0]}")
    if store_id is not None:
        qualifiers.append(f"store:{store_id}")
    return qualifiers
