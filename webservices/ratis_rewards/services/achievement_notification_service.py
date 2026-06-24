"""Achievement notification — fan-out to ratis_notifier with rarity-gradated UX.

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md § Notification & UI flow.

Maps catalog ``Achievement.rarity`` to a 4-tier UX gradient :

* Tier 0 — terracotta / bronze / copper / silver : toast only.
* Tier 1 — gold / emerald                       : toast + in-app modal.
* Tier 2 — sapphire / ruby                      : toast + modal + visible push (rate-limited 1h).
* Tier 3 — crystal / diamond                    : toast + modal + visible push (NOT rate-limited).
*           diamond                             : ``has_bespoke=True`` (FE swaps in custom anim).

Sound intensity is a 0..3 scalar that the FE uses to pick haptic + sound profile.

Public surface :

* :func:`notify_achievement_unlocked` — fire-and-forget. Never raises.
"""

from __future__ import annotations

import logging
from uuid import UUID

from ratis_core.models.achievement import Achievement

from ratis_core import notifier_client

logger = logging.getLogger(__name__)


# Rarities that trigger an in-app celebration modal (post-toast).
_MODAL_RARITIES: frozenset[str] = frozenset({"gold", "emerald", "sapphire", "ruby", "crystal", "diamond"})

# Rarities that trigger a visible OS-level push notification.
_PUSH_RARITIES: frozenset[str] = frozenset({"sapphire", "ruby", "crystal", "diamond"})

# Rarities whose visible push is rate-limited (1 / hour / user). Crystal &
# Diamant are extreme rarity → never throttled.
_RATE_LIMITED_RARITIES: frozenset[str] = frozenset({"sapphire", "ruby"})

_PUSH_RATE_LIMIT_SECONDS = 3600  # 1h cooldown for sapphire / ruby

# 0..3 scalar — FE uses it to select haptic + sound preset.
_INTENSITY_BY_RARITY: dict[str, int] = {
    "terracotta": 0,
    "bronze": 0,
    "copper": 0,
    "silver": 1,
    "gold": 1,
    "emerald": 2,
    "sapphire": 2,
    "ruby": 3,
    "crystal": 3,
    "diamond": 3,
}

# French rarity labels — used in the push title (RGPD-safe, no PII).
RARITY_LABELS: dict[str, str] = {
    "terracotta": "Terre cuite",
    "bronze": "Bronze",
    "copper": "Cuivre",
    "silver": "Argent",
    "gold": "Or",
    "emerald": "Émeraude",
    "sapphire": "Saphir",
    "ruby": "Rubis",
    "crystal": "Cristal",
    "diamond": "Diamant",
}

NOTIF_TYPE = "achievement_unlocked"


def notify_achievement_unlocked(user_id: UUID, ach: Achievement) -> None:
    """Send the unlock notification to the user — fire-and-forget.

    Calls :func:`ratis_core.notifier_client.send` with rarity-gradated UX
    knobs (modal yes/no, push yes/no, push cooldown, sound intensity).

    Never raises. The unlock path is the source-of-truth (the row in
    ``user_achievements`` and the CAB grant are pending on the caller's
    session, about to be committed by the caller — since PR #388 ``_unlock``
    no longer commits) ; a notification failure must never reverberate to
    the caller.
    """
    rarity = ach.rarity
    intensity = _INTENSITY_BY_RARITY.get(rarity, 0)

    payload = {
        "achievement_id": str(ach.id),
        "code": ach.code,
        "label": ach.label,
        "description": ach.description,
        "icon": ach.icon,
        "rarity": rarity,
        "category": ach.category,
        "cab_granted": int(ach.cab_reward),
        "show_modal": rarity in _MODAL_RARITIES,
        "has_bespoke": rarity == "diamond",
        "sound_intensity": intensity,
    }

    push_label = RARITY_LABELS.get(rarity, rarity.capitalize() if rarity else "")
    push_title = f"🏆 Trophée {push_label} !"
    push_body = f"{ach.label} · +{int(ach.cab_reward)} CAB"

    try:
        notifier_client.send(
            user_id=user_id,
            notif_type=NOTIF_TYPE,
            payload=payload,
            visible_push=rarity in _PUSH_RARITIES,
            push_rate_limit_seconds=(_PUSH_RATE_LIMIT_SECONDS if rarity in _RATE_LIMITED_RARITIES else 0),
            push_title=push_title,
            push_body=push_body,
        )
    except Exception:
        logger.exception(
            "achievement_notify_failed",
            extra={
                "achievement_code": ach.code,
                "achievement_rarity": rarity,
                "user_id": str(user_id),
            },
        )
