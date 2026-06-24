"""Local mirror of editable / frozen sections for the admin settings UI.

The source-of-truth allowlist lives in
``webservices/ratis_rewards/services/admin/settings_service.py``
(``EDITABLE_SECTIONS``). The mini UI in PA needs the same data to render
the list page (25 tiles split editable / frozen) without firing 25
``GET /admin/settings/{section}/editable`` round-trips at every page load
— the listing is a read-mostly catalog, not a hot path, but a 25× HTTP
fan-out per click is still wasteful.

This module duplicates the allowlist intentionally and is kept in sync
with RW by a single contract test (``test_admin_ui_settings.py``
``test_editable_sections_mirror_matches_rw``) that diffs the two
constants on every CI run. If RW adds a new editable section without
updating this mirror, the test fails — the duplication is enforced at
build time, not trusted to memory.

Frozen sections come from ``ratis_settings.json`` minus the editable
allowlist. Computing the union at import time would couple PA to the
JSON layout ; the explicit list below keeps the UI deterministic even
when the JSON adds an experimental section that hasn't been graded
editable / frozen yet.
"""

from __future__ import annotations

#: Editable section names — mirror of the RW allowlist. Order matters
#: for the UI : sorted alphabetically so the operator scans a stable
#: catalog regardless of JSON insertion order.
EDITABLE_SECTIONS_MIRROR: tuple[str, ...] = (
    "battle_pass",
    "gamification",
    "gift_cards",
    "missions",
    "mystery_product",
    "referral",
    "rewards",
    "subscription_promotions",
    "xp",
)


#: Frozen section names — algo / infra / templates / prices that must
#: only change via PR git for traceability. Mirrors the segmentation in
#: ``ARCH_admin_settings.md`` § Sections éditables vs frozen (V1).
FROZEN_SECTIONS: tuple[str, ...] = (
    "cashback",
    "consensus",
    "fuzzy",
    "knowledge",
    "label",
    "list_optimiser",
    "llm",
    "name_resolution_consensus",
    "notifier",
    "ocr",
    "off_sync",
    "osm_sync",
    "pipeline",
    "savings",
    "store_matching",
    "store_validation",
    "subscription",
    "type_detector",
)


#: Frozen sub-keys per editable section. Mirrors RW's
#: ``EDITABLE_SECTIONS[<section>]`` value (the per-section frozenset).
#: The UI surfaces these in red on the detail page so the operator sees
#: at a glance which sub-trees would 403 on submit.
FROZEN_SUB_KEYS: dict[str, tuple[str, ...]] = {
    "gamification": ("feed_jack",),
}


def is_editable(section: str) -> bool:
    """True if the section is in the local editable mirror."""
    return section in EDITABLE_SECTIONS_MIRROR


def get_frozen_sub_keys(section: str) -> tuple[str, ...]:
    """Return the frozen sub-key tuple for an editable section (or empty)."""
    return FROZEN_SUB_KEYS.get(section, ())
