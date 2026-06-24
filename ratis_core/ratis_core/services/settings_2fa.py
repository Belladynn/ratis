"""Magnitude-breach detection for admin settings 2FA grace period.

Backs the V1 garde-fou described in ``ARCH_admin_settings.md`` § Garde-fous V1
§ 2 : any single numeric value (int / float, never bool) whose relative
variation exceeds ``threshold`` between the previous and the new payload
flips the whole PUT into a pending-2FA state. The detector is the *single*
deterministic entry point — the caller (``update_settings_section``) handles
the side effects (audit row insertion, ``app_settings`` upsert skipping…).

Design choices :

- **Atomic flag** : the helper returns the *first* offending dotted key path
  so the caller can surface a useful 2FA prompt ("you raised
  ``rewards.cab_per_receipt_complete`` from 500 to 5000 — confirm with
  TOTP"). Multiple offenders are not reported — V1 keeps it simple.
- **No baseline = no breach** : a freshly seeded section (``old_data is None``)
  cannot trip the check ; there is nothing to compare against. New keys
  added by a PUT also skip — V2 may revisit if we ship hard limits.
- **Bool excluded** : ``isinstance(True, int) == True`` in Python. We exclude
  booleans explicitly before any numeric arithmetic.
- **Arrays / strings / structural changes** : skipped by design. V1 only
  protects numeric magnitudes — adding type-aware rules belongs to a future
  Pydantic-validated layer.
"""

from __future__ import annotations

from numbers import Real

#: Maximum recursion depth for :func:`_walk` — defensive cap (security audit
#: L1). Real-world settings sections nest 1-3 levels deep ; 32 levels is
#: orders of magnitude above any legitimate payload. A payload deeper than
#: this is treated as malformed input and the walker silently returns
#: ``(False, None)`` rather than raising :exc:`RecursionError` and crashing
#: the route with a 500. The trade-off (skip the magnitude check on
#: pathological inputs) is intentional : a hostile PUT still goes through
#: the section/key allowlist and DB CHECK constraints, so the only thing we
#: lose is the breach detection — never a security barrier.
_MAX_DEPTH = 32


def _is_numeric(value: object) -> bool:
    """True for ``int`` and ``float`` (and other ``numbers.Real``) — never bool.

    Python's ``True`` is an ``int`` subclass ; an explicit ``isinstance(value,
    bool)`` short-circuit is mandatory so admins toggling boolean flags do
    not accidentally trip the magnitude check.
    """
    if isinstance(value, bool):
        return False
    return isinstance(value, Real)


def _check_pair(old: object, new: object, threshold: float) -> bool:
    """Return ``True`` if the numeric pair (``old``, ``new``) is a breach.

    Pre-condition : both values have already been screened by
    :func:`_is_numeric`. The function is **not** safe to call with arbitrary
    types.
    """
    if old == 0:
        # Any non-zero target from a zero baseline is a breach. This guards
        # against typos on settings where the default is intentionally 0
        # (e.g. ``referral.bonus_extra = 0``).
        return new != 0
    return abs(new - old) / abs(old) > threshold


def detect_magnitude_breach(
    old_data: dict | None,
    new_data: dict,
    *,
    threshold: float = 0.5,
) -> tuple[bool, str | None]:
    """Return ``(breach, breach_key)`` for a settings update payload.

    Walks both ``old_data`` and ``new_data`` recursively. For every numeric
    key present in **both** maps, computes the relative variation. Returns
    on the first breach found ; the matching dotted key path lets the caller
    surface it to the operator.

    :param old_data: previous JSONB payload, or ``None`` for the first
        write of a section. ``None`` always returns ``(False, None)``.
    :param new_data: candidate JSONB payload submitted via the admin UI.
    :param threshold: relative variation above which a numeric change is a
        breach. Default ``0.5`` (50 %) per ARCH § Garde-fous V1 § 2.
    :return: ``(breach: bool, breach_key: str | None)``. ``breach_key`` is
        the dotted path of the first offending leaf (e.g.
        ``"rewards.cab_per_receipt_complete"`` or
        ``"gamification.feed_jack.multiplier_per_day"``).
    """
    if old_data is None:
        return (False, None)
    return _walk(old_data, new_data, threshold, prefix="", _depth=0)


def _walk(
    old_data: dict,
    new_data: dict,
    threshold: float,
    *,
    prefix: str,
    _depth: int = 0,
) -> tuple[bool, str | None]:
    """Recursive worker for :func:`detect_magnitude_breach`.

    Iterates ``new_data`` in insertion order — Python dicts since 3.7
    preserve insertion ordering, so the *first breach found* is stable
    given a consistent payload shape. Keys absent from ``old_data`` are
    skipped (no baseline). Keys whose old/new values are both nested dicts
    recurse with an extended dotted prefix.

    ``_depth`` is the recursion counter ; once it exceeds :data:`_MAX_DEPTH`
    the walker bails out with ``(False, None)`` defensively (security audit
    L1). See the constant's docstring for the trade-off rationale.
    """
    if _depth > _MAX_DEPTH:
        # Defensive bail-out — the payload is deeper than any legitimate
        # settings section. The caller treats this as "no breach" so the
        # PUT continues through the section / key allowlist and reason
        # CHECK constraints, all of which are stronger guarantees than the
        # magnitude check anyway.
        return (False, None)

    for key, new_value in new_data.items():
        if key not in old_data:
            continue
        old_value = old_data[key]
        dotted = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"

        # Recurse into nested dicts. A type mismatch (dict vs scalar) is
        # treated as a structural change — skip per spec.
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            breach, breach_key = _walk(old_value, new_value, threshold, prefix=dotted, _depth=_depth + 1)
            if breach:
                return (True, breach_key)
            continue

        # Numeric leaves are the only thing we ever flag.
        if _is_numeric(old_value) and _is_numeric(new_value) and _check_pair(old_value, new_value, threshold):
            return (True, dotted)

        # Anything else (string, bool, list, type mismatch) : skip silently.

    return (False, None)
