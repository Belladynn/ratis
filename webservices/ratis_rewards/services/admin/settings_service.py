"""Editable sections + frozen sub-keys allowlist for admin settings UI.

Sections not in :data:`EDITABLE_SECTIONS` are frozen (read-only via UI /
403 via PUT). Within an editable section, ``frozen_keys`` are top-level
sub-paths that must remain unchanged on PUT — protects sub-objects with
high-risk semantics (e.g. ``gamification.feed_jack`` streak algo).
Modifying a frozen sub-key returns 403 ``frozen_key_modified``.

This module is the **only** place that decides whether a section is
editable or not. The REST layer (``routes/admin/settings.py``) delegates
the policy here ; the helper :func:`update_settings_section_with_2fa_check`
wraps the lower-level :func:`ratis_core.settings.update_settings_section`
so callers do not bypass the allowlist.

See ``ARCH_admin_settings.md`` § Sections éditables vs frozen (V1) and
§ Frozen sub-keys for the rationale behind the constants below.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


#: Allowlist of section → frozen sub-keys (top-level dict keys protected).
#:
#: A section absent from this mapping is **frozen** : the PUT endpoint
#: returns 403 ``section_frozen``. The empty ``frozenset()`` means "fully
#: editable" — only ``gamification`` carries a frozen sub-key in V1
#: (``feed_jack`` streak parameters).
EDITABLE_SECTIONS: dict[str, frozenset[str]] = {
    "rewards": frozenset(),
    "xp": frozenset(),
    "missions": frozenset(),
    "battle_pass": frozenset(),
    "mystery_product": frozenset(),
    "gift_cards": frozenset(),
    "referral": frozenset(),
    "gamification": frozenset({"feed_jack"}),
    "subscription_promotions": frozenset(),
}


#: M3 (audit sécurité 2026-05-03) — sub-keys to mask in audit responses.
#:
#: Per section, the set of top-level keys whose **value** must be replaced
#: by ``"***REDACTED***"`` when the audit row is serialized for the
#: operator (GET /admin/settings/audit, /admin/settings/audit/{id}). The
#: DB row is left untouched — the legal trail keeps the real value
#: (admin_settings_audit is NEVER PURGE), but it never leaks through API
#: or UI surfaces.
#:
#: Currently masks ``subscription_promotions.active_codes`` — the active
#: promo code list. Future patterns (API keys stored in a section, PII
#: leaking into a free-form sub-key) extend the mapping rather than
#: invent ad-hoc redaction at the route layer.
REDACTED_KEYS_PER_SECTION: dict[str, frozenset[str]] = {
    "subscription_promotions": frozenset({"active_codes"}),
}


#: M5 (audit sécurité 2026-05-03) — backend-side cap on PUT body data.
#:
#: The UI already caps at 64 KB ; this guards against a curl bypass / a
#: malicious operator pushing a multi-MB payload to DoS the service.
#: Applied AT the route handler (before the lower-level service does any
#: work) so the JSON parse cost is bounded too.
_MAX_BODY_DATA_BYTES = 64 * 1024  # 64 KB


def is_editable(section: str) -> bool:
    """True if the section is in the editable allowlist."""
    return section in EDITABLE_SECTIONS


def redact_for_audit(section: str, data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a new dict with sensitive top-level keys masked as ``"***REDACTED***"``.

    M3 — applied at audit response serialization only. The DB row keeps
    the real values (legal trail, NEVER PURGE). Sections absent from
    :data:`REDACTED_KEYS_PER_SECTION` are returned unchanged ; ``None``
    input is returned as-is so callers can pass row.old_data on
    first-write rows without a guard.

    The helper does NOT mutate its input — a shallow copy is returned.
    """
    if data is None:
        return None
    keys_to_mask = REDACTED_KEYS_PER_SECTION.get(section, frozenset())
    if not keys_to_mask:
        return data
    masked = dict(data)
    for key in keys_to_mask:
        if key in masked:
            masked[key] = "***REDACTED***"
    return masked


def validate_body_size(data: dict[str, Any]) -> None:
    """Reject PUT payloads above :data:`_MAX_BODY_DATA_BYTES` (64 KB).

    M5 — defense against DoS via huge JSON push from a malicious operator
    (or a curl call bypassing the UI cap). Raises :class:`HTTPException`
    413 with a structured detail so the caller knows the limit and the
    actual size.
    """
    serialized = json.dumps(data, separators=(",", ":")).encode("utf-8")
    if len(serialized) > _MAX_BODY_DATA_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "detail": "payload_too_large",
                "max_bytes": _MAX_BODY_DATA_BYTES,
                "got_bytes": len(serialized),
            },
        )


def get_frozen_keys(section: str) -> frozenset[str]:
    """Return the frozen sub-key set for an editable section.

    Returns an empty frozenset for unknown sections — callers that need
    to distinguish "frozen section" from "editable but no sub-keys
    frozen" must call :func:`is_editable` first.
    """
    return EDITABLE_SECTIONS.get(section, frozenset())


def detect_frozen_key_modification(
    section: str,
    old_data: dict[str, Any] | None,
    new_data: dict[str, Any],
) -> str | None:
    """Return the name of the first frozen sub-key that was modified.

    Compares ``old_data`` and ``new_data`` on each frozen sub-key declared
    for the section. A key is considered modified when :

    - It was present in ``old_data`` but is missing in ``new_data`` (drop).
    - It was missing in ``old_data`` and present in ``new_data`` (add).
    - It was present in both and the deep-equality check fails.

    Returns the first offending key (deterministic order — sorted) or
    ``None`` if all frozen sub-keys are untouched. Used by the PUT route
    to refuse modifications targeting protected sub-trees.

    The check is a no-op when the section has no frozen sub-keys (the
    common case — only ``gamification.feed_jack`` is frozen V1).
    """
    frozen_keys = get_frozen_keys(section)
    if not frozen_keys:
        return None
    # First-write : old_data is None. If new_data ships a frozen sub-key,
    # we still flag it — the seed is supposed to provide the protected
    # value, and an admin should not bootstrap a frozen sub-key via UI.
    old_data = old_data or {}
    for key in sorted(frozen_keys):
        old_present = key in old_data
        new_present = key in new_data
        if old_present != new_present:
            return key
        if old_present and old_data[key] != new_data[key]:
            return key
    return None


def update_settings_section_with_2fa_check(
    db: "Session",
    section: str,
    new_data: dict[str, Any],
    *,
    operator: str,
    reason: str,
) -> tuple[uuid.UUID, str]:
    """Allowlist-aware wrapper around ``update_settings_section``.

    Enforces the editable / frozen segmentation **before** delegating to
    the ratis_core helper. Three failure modes raise :class:`HTTPException`
    so the FastAPI layer can surface them directly without re-mapping :

    - 403 ``section_frozen`` — section not in :data:`EDITABLE_SECTIONS`.
    - 403 ``frozen_key_modified`` — section editable but a protected
      sub-key was changed (response body : ``{"detail": "frozen_key_modified",
      "key": "<name>"}``).
    - 422 ``reason_too_short`` — reason < 8 chars (caught from the
      ``ValueError`` raised by the lower-level helper).

    On success returns ``(audit_id, status)`` where ``status`` is either
    ``"applied"`` (variation under threshold, value persisted) or
    ``"pending_2fa"`` (variation > 50 %, value held for TOTP confirmation).

    The caller is responsible for ``db.commit()`` (R02 — services do not
    commit) and for surfacing the audit_id to the operator.
    """
    from ratis_core.settings import update_settings_section
    from sqlalchemy import text

    if not is_editable(section):
        raise HTTPException(status_code=403, detail="section_frozen")

    # Snapshot current value to compare frozen sub-keys against. Reads
    # outside any FOR UPDATE — magnitude check inside update_settings_section
    # locks the row again with FOR UPDATE, which is fine (we only need
    # a read-only baseline here).
    row = db.execute(
        text("SELECT data FROM app_settings WHERE section = :section"),
        {"section": section},
    ).first()
    old_data = row.data if row is not None else None

    frozen_modified = detect_frozen_key_modification(section, old_data, new_data)
    if frozen_modified is not None:
        raise HTTPException(
            status_code=403,
            detail={"detail": "frozen_key_modified", "key": frozen_modified},
        )

    try:
        return update_settings_section(
            db,
            section,
            new_data,
            operator=operator,
            reason=reason,
            bypass_2fa=False,
        )
    except ValueError:
        # Raised when reason fails the MIN_REASON_LENGTH check. Translate
        # to a 422 — the DB CHECK constraint would fire 500 otherwise.
        raise HTTPException(status_code=422, detail="reason_too_short")
