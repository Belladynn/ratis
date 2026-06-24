"""Public settings whitelist + projection.

Exposes a **strict** subset of ``app_settings`` over the public read
endpoint ``GET /api/v1/rewards/settings/public``. The whitelist is the
single source of truth for what the mobile client may observe — adding
a key requires :

1. Adding the dotted path to :data:`PUBLIC_SETTINGS_WHITELIST`.
2. Updating ``test_settings_public.py`` to assert the key.

Keeping the whitelist here (services layer) keeps the route handler
thin and prevents accidental leakage of admin-only settings (e.g.
``subscription_promotions.active_codes``, frozen sub-keys, etc.).

Cf F-10 in the V1.1 usage-stats sprint and CLAUDE.md R19 (config
discipline — no hardcoded business values in the client).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Dotted-path whitelist : ``"<section>.<key>[.<sub-key>...]"``.
#:
#: Each entry is resolved against the corresponding ``app_settings.section``
#: row's JSONB ``data`` payload. Missing sections / keys are silently
#: omitted from the response so a partial seed doesn't crash the client.
#:
#: Extension policy : add only values the **mobile client legitimately
#: needs at runtime**. Settings consumed only by the backend (caps,
#: thresholds, internal ratios used in services) stay private.
PUBLIC_SETTINGS_WHITELIST: tuple[str, ...] = (
    # JarPrestige derivation — frontend computes fill % from monthly price.
    "pipeline.jar.monthly_subscription_price_cents",
    # Boutique caps — needed for client-side display ("X€ used / Y€ cap").
    # The actual enforcement remains server-side (POST /rewards/gift-cards/order).
    "boutique.cap_annual_cents",
    "boutique.cap_per_card_cents",
    "boutique.cap_daily_cents",
    "boutique.cap_weekly_cents",
    "boutique.ratio_cab_per_eur",
    "boutique.allowed_denominations_cents",
    # Gift-cards fiscal warning threshold (305 € BNC) — UI surfaces a
    # one-time-per-year modal when the user crosses it. The threshold
    # value lives backend-side so we can adjust without app rebuild.
    "gift_cards.annual_warning_threshold_cents",
)


def get_public_settings(db: Session) -> dict[str, Any]:
    """Project ``app_settings`` through :data:`PUBLIC_SETTINGS_WHITELIST`.

    Returns a flat dict keyed by the dotted path. Sections missing from
    the table are silently skipped — the mobile client uses
    ``settings?.['key'] ?? <fallback>`` so absent keys degrade gracefully.

    Implementation note : we hit ``app_settings`` directly rather than
    going through :func:`ratis_core.settings.load_settings` so the public
    endpoint is decoupled from the in-process cache and always reflects
    the latest DB value (admin pushes a setting → next public read sees
    it within Cache-Control window).
    """
    rows = db.execute(text("SELECT section, data FROM app_settings")).fetchall()
    sections: dict[str, dict[str, Any]] = {row.section: row.data for row in rows}

    out: dict[str, Any] = {}
    for dotted in PUBLIC_SETTINGS_WHITELIST:
        section, *path = dotted.split(".")
        cursor: Any = sections.get(section)
        if cursor is None:
            continue
        # Walk the nested path. Any missing intermediate key → skip silently.
        miss = False
        for part in path:
            if not isinstance(cursor, dict) or part not in cursor:
                miss = True
                break
            cursor = cursor[part]
        if miss:
            continue
        out[dotted] = cursor
    return out
