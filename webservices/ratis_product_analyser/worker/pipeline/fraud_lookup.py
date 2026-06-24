"""Anti-fraud PR4 — cross-user, fuzzy intra-user and device-pattern lookups.

Pure DB-aware helpers wired into ``worker/pipeline/persist.py`` around
the fingerprint INSERT (cf ``ARCH_receipt_pipeline.md`` § "Réconciliation
tickets — V1 (dual fingerprint + pHash + admin queue)", décisions actées
2026-05-11). Each helper performs a single ``SELECT`` against the
``receipts`` table — none of them mutate state. The caller decides what
to do with the verdict (REJECT a scan, INSERT a fraud_suspicion,
consolidate two receipts into one).

The 3 lookups (matching the 3 ARCH steps that land in PR4) :

1. :func:`check_cross_user_duplicate` (étape 7 in the flowchart) — look
   up ``parse_fingerprint_global`` collisions inside the rolling
   ``fp_window_hours`` window across other users. The verdict carries
   ``kind="second_strict"`` (both receipts at ``time_precision='second'``
   → caller REJECTs the scan, INSERTs ``fp_global_strict``),
   ``kind="minute"`` (either receipt at ``time_precision='minute'`` or
   mixed → caller FLAGs via ``fp_global_minute`` but does not block) or
   ``kind="none"``.

2. :func:`fuzzy_match_intra_user` (étape 8) — same-user fallback when
   the strict UNIQUE INDEX ``idx_receipts_fp_user`` didn't fire. Iterates
   up to ``_MAX_INTRA_USER_CANDIDATES`` recent receipts with non-null
   fingerprint components, counts exact-match components (out of 10),
   and tolerates a single-unit Levenshtein difference on the numeric
   components. Returns the canonical receipt to consolidate into when
   the match score crosses ``fp_fuzzy_match_threshold`` AND the
   numeric-Lev budget is respected.

3. :func:`check_device_pattern` (étape 11) — count distinct ``user_id``
   values for the same ``device_fingerprint`` over the rolling
   ``device_fp_window_days`` window. Verdict ``kind="shared"`` when the
   count strictly exceeds ``distinct_users_threshold`` (validated PO
   "> 3 users distincts" — so 3 is allowed, 4 is the first flag),
   ``kind="none"`` otherwise.

Anti-pattern guard — these helpers never raise on a "soft" miss : a
malformed JSONB blob in ``fingerprint_components_jsonb`` is logged and
the candidate is skipped rather than crashing the whole pipeline. A
true infrastructure failure (DB unreachable) bubbles up because the
caller's transaction is already poisoned anyway. Cf ``phash_lookup.py``
for the equivalent fail-safe pattern in PR2.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from worker.pipeline.fingerprint import FingerprintComponents

logger = logging.getLogger(__name__)


# Maximum number of candidate receipts the fuzzy lookup considers per call.
# The intra-user window is bounded (``fp_window_hours = 48`` by default)
# and a single user rarely has >50 receipts in 48h — the soft cap protects
# the pipeline against a pathological flood while keeping the common path
# branchless.
_MAX_INTRA_USER_CANDIDATES = 50

# Numeric components participate in Levenshtein-≤-1 tolerance.
_NUMERIC_COMPONENTS: frozenset[str] = frozenset(
    {
        "iso_date",
        "iso_time",
        "total_ttc_cents",
        "item_count_declared",
        "tva_total_cents",
    }
)

# All 10 components participate in exact-match counting.
_ALL_COMPONENTS: tuple[str, ...] = (
    "store_id",
    "address_normalized",
    "brand_normalized",
    "iso_date",
    "iso_time",
    "time_precision",
    "total_ttc_cents",
    "item_count_declared",
    "payment_method",
    "tva_total_cents",
)


# ── Verdict dataclasses ───────────────────────────────────────────────────


CrossUserVerdictKind = Literal["second_strict", "minute", "none"]
DeviceVerdictKind = Literal["shared", "none"]


@dataclass(frozen=True)
class CrossUserVerdict:
    """Outcome of :func:`check_cross_user_duplicate`.

    ``kind`` :
      - ``"second_strict"`` — both receipts at ``time_precision='second'``
        (the matched peer AND the current receipt). Caller MUST reject
        the scan and INSERT a fraud_suspicion with
        ``detection_signal='fp_global_strict'``.
      - ``"minute"`` — either receipt at ``time_precision='minute'`` OR
        the precision values differ (one second / one minute). Caller
        ACCEPTS the scan but INSERTs a fraud_suspicion with
        ``detection_signal='fp_global_minute'`` for admin review.
      - ``"none"`` — no match in window. Caller proceeds normally.

    ``matched_receipt_id`` is populated for ``second_strict`` / ``minute``
    and ``None`` for ``"none"``. The caller threads it through to
    ``fraud_suspicions.evidence_receipt_ids[]``.

    ``matched_user_id`` is informational (admin queue display) — never
    used as a routing key.
    """

    kind: CrossUserVerdictKind
    matched_receipt_id: UUID | None = None
    matched_user_id: UUID | None = None


@dataclass(frozen=True)
class FuzzyMatch:
    """Outcome of :func:`fuzzy_match_intra_user`.

    ``existing_receipt_id`` is the canonical receipt to consolidate
    into (the caller invokes ``_consolidate_rescan_into_existing``).
    ``exact_matches`` and ``lev_tolerance_used`` are diagnostic — surfaced
    in audit events so the admin queue can show why two receipts were
    folded.
    """

    existing_receipt_id: UUID
    exact_matches: int
    lev_tolerance_used: int


@dataclass(frozen=True)
class DeviceVerdict:
    """Outcome of :func:`check_device_pattern`.

    ``distinct_user_count`` is the actual count (incl. the current user)
    so the caller can persist it in the suspicion payload. The verdict
    fires only when the count *strictly exceeds* the threshold (validated
    PO 2026-05-11 — "> 3 users distincts").
    """

    kind: DeviceVerdictKind
    distinct_user_count: int = 0


# ── Helpers ───────────────────────────────────────────────────────────────


def _decode_components_jsonb(payload: object) -> FingerprintComponents | None:
    """Return a :class:`FingerprintComponents` rebuilt from the stored
    JSONB, or ``None`` if the payload is unusable.

    The ``receipts.fingerprint_components_jsonb`` column stores only the
    non-null components (cf ``persist.py::_upsert_receipt``) so missing
    keys map to ``None``. A non-dict payload (legacy / corrupted row)
    logs a warning and yields ``None`` — the fuzzy lookup then skips
    that candidate rather than crashing the whole compare loop.
    """
    if payload is None:
        return None
    # psycopg returns JSONB as a Python dict already ; defensive parse
    # when the row was written via raw SQL with json.dumps text.
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            logger.warning("fraud_lookup: malformed fingerprint_components_jsonb (not JSON) — skipping")
            return None
    if not isinstance(payload, dict):
        logger.warning(
            "fraud_lookup: fingerprint_components_jsonb is %s, expected dict — skipping",
            type(payload).__name__,
        )
        return None
    try:
        return FingerprintComponents(
            store_id=payload.get("store_id"),
            address_normalized=payload.get("address_normalized"),
            brand_normalized=payload.get("brand_normalized"),
            iso_date=payload.get("iso_date"),
            iso_time=payload.get("iso_time"),
            time_precision=payload.get("time_precision"),
            total_ttc_cents=payload.get("total_ttc_cents"),
            item_count_declared=payload.get("item_count_declared"),
            payment_method=payload.get("payment_method"),
            tva_total_cents=payload.get("tva_total_cents"),
        )
    except Exception:
        logger.warning(
            "fraud_lookup: failed to build FingerprintComponents from JSONB — skipping",
            exc_info=True,
        )
        return None


def _numeric_lev_distance(a: object, b: object) -> int:
    """Compute a "single-position" Levenshtein-equivalent distance for
    numeric fingerprint components.

    The rule (cf ARCH § étape 8 — fuzzy intra-user) is "Levenshtein ≤ 1
    on numeric components". For two integers we use the cheaper natural
    metric : differ by exactly one unit → distance 1, equal → 0,
    otherwise infinity (returned as ``999``). For ``iso_date`` /
    ``iso_time`` strings we compute classic Levenshtein bounded at 2 :
    distance 0 = identical, 1 = one digit swap, ≥2 = unrelated.

    Returns a large sentinel (``999``) for unrelated values so the
    caller's ``budget -= dist`` check naturally rejects them.
    """
    if a is None or b is None:
        # NULL-vs-not-NULL is treated as exact mismatch in component
        # counting — the caller already filtered those out.
        return 999
    if a == b:
        return 0
    if isinstance(a, int) and isinstance(b, int):
        return 1 if abs(a - b) == 1 else 999
    if isinstance(a, str) and isinstance(b, str):
        # Bounded Levenshtein for short numeric-formatted strings
        # ("2026-04-30", "14:30:45"). The full DP is overkill here ;
        # a length-difference short-circuit + single-edit scan suffices
        # for the ≤ 1 cutoff.
        if abs(len(a) - len(b)) > 1:
            return 999
        return _levenshtein_le_one(a, b)
    return 999


def _levenshtein_le_one(a: str, b: str) -> int:
    """Return 0 if a == b, 1 if exactly one edit (substitution / insertion
    / deletion) reconciles them, else ``999``.

    Optimised for the short numeric-string components — full DP would
    be ~30 cycles wasted per compare ; the early-exit version below
    runs in O(max(len(a), len(b))).
    """
    if a == b:
        return 0
    la, lb = len(a), len(b)
    # Substitution case — same length.
    if la == lb:
        diffs = sum(1 for ca, cb in zip(a, b, strict=False) if ca != cb)
        return 1 if diffs == 1 else 999
    # Insertion / deletion case — lengths differ by 1.
    if abs(la - lb) == 1:
        # Walk the shorter string against the longer ; allow one skip.
        shorter, longer = (a, b) if la < lb else (b, a)
        i = j = 0
        skipped = False
        while i < len(shorter) and j < len(longer):
            if shorter[i] == longer[j]:
                i += 1
                j += 1
                continue
            if skipped:
                return 999
            skipped = True
            j += 1
        return 1
    return 999


def _count_matches(
    candidate: FingerprintComponents,
    current: FingerprintComponents,
) -> tuple[int, int]:
    """Return ``(exact_matches, lev_tolerance_used)`` between two
    component sets.

    ``exact_matches`` counts components where ``cur == cand`` (NULL == NULL
    counts as a match because both receipts genuinely lack that signal —
    the ARCH treats absence as a discriminator). ``lev_tolerance_used``
    counts components where the value differs but the bounded
    Levenshtein distance is exactly 1 (numeric components only).

    Mismatches with Lev > 1 don't contribute to either counter.
    """
    exact = 0
    lev_used = 0
    for field in _ALL_COMPONENTS:
        cur_val = getattr(current, field)
        cand_val = getattr(candidate, field)
        if cur_val == cand_val:
            exact += 1
            continue
        if field in _NUMERIC_COMPONENTS:
            dist = _numeric_lev_distance(cur_val, cand_val)
            if dist == 1:
                lev_used += 1
    return exact, lev_used


# ── Public lookups ────────────────────────────────────────────────────────


def check_cross_user_duplicate(
    db: Session,
    *,
    fp_global: str,
    time_precision_self: Literal["second", "minute"] | None,
    scanned_at: datetime,
    current_user_id: UUID,
    window_hours: int,
) -> CrossUserVerdict:
    """Lookup ``parse_fingerprint_global`` matches across other users.

    SQL : selects up to 5 candidate receipts with the same fp_global,
    owned by a different user, within ``window_hours``. The Python
    side picks the most-relevant verdict from the candidate set
    (``second_strict`` wins over ``minute`` — strict reject beats flag).

    Args :
        db: SQLAlchemy session — read-only inside this helper.
        fp_global: the 64-hex ``parse_fingerprint_global`` of the
            current receipt. Caller computed it via
            :func:`worker.pipeline.fingerprint.compute_fp_global`.
        time_precision_self: the current receipt's ``time_precision``
            (``"second"`` / ``"minute"`` / ``None``). Used to decide
            ``second_strict`` vs ``minute`` per the cross-user policy
            table.
        scanned_at: the current receipt's scan timestamp (used to
            anchor the rolling window).
        current_user_id: the user uploading the current receipt — must
            be excluded from the lookup so an intra-user rescan never
            surfaces as cross-user duplicate.
        window_hours: ``pipeline.anti_fraud.fp_window_hours`` (48 by
            default). Lookup considers receipts created within the
            last ``window_hours`` hours.

    Returns :
        :class:`CrossUserVerdict`. ``kind="none"`` when no candidate is
        in window OR when ``fp_global`` is empty/None.

    Notes :
        - The SQL filters on ``created_at`` (wall-clock anchor) rather
          than ``purchased_at`` — fraud detection is about upload
          velocity, not retro-active tickets.
        - The lookup is intentionally bounded at 5 rows — multiple peer
          collisions are extremely rare (the same fingerprint across
          >5 users in 48h means we're already in the device-shared
          territory which fires separately). Limiting the scan keeps
          the helper O(1) on a hot path.
    """
    if not fp_global:
        return CrossUserVerdict(kind="none")

    rows = db.execute(
        text(
            "SELECT id, user_id, time_precision "
            "FROM receipts "
            "WHERE parse_fingerprint_global = :fp "
            "  AND user_id IS NOT NULL "
            "  AND user_id != :uid "
            "  AND created_at > :since "
            "ORDER BY created_at DESC "
            "LIMIT 5"
        ),
        {
            "fp": fp_global,
            "uid": current_user_id,
            "since": _window_floor(scanned_at, hours=window_hours),
        },
    ).all()
    if not rows:
        return CrossUserVerdict(kind="none")

    # Strict-reject path dominates : if any candidate is at
    # time_precision='second' AND the current receipt is also 'second',
    # we return that verdict. Otherwise fall back to 'minute' (the rest
    # of the policy table — minute on either side, or mixed precision).
    if time_precision_self == "second":
        for row in rows:
            if row.time_precision == "second":
                return CrossUserVerdict(
                    kind="second_strict",
                    matched_receipt_id=row.id,
                    matched_user_id=row.user_id,
                )
    # All other cases (we are 'minute', or peer is 'minute', or mixed,
    # or either is NULL) → flag-only.
    first = rows[0]
    return CrossUserVerdict(
        kind="minute",
        matched_receipt_id=first.id,
        matched_user_id=first.user_id,
    )


def fuzzy_match_intra_user(
    db: Session,
    *,
    components: FingerprintComponents,
    user_id: UUID,
    window_hours: int,
    threshold: int,
) -> FuzzyMatch | None:
    """Find a same-user receipt the current upload should consolidate into.

    The strict UNIQUE INDEX ``idx_receipts_fp_user`` already catches
    exact-fingerprint rescans (cf PR3). This helper is the *fuzzy*
    fallback for the case where OCR digit-swap leaves the new upload
    one cent off / one minute off / one digit off on a single numeric
    component — both receipts represent the same real-world ticket
    but their ``fp_user`` hashes differ.

    Match rule (cf ARCH décision 2026-05-11) :
        - count exact-match components (``exact``) and
          Lev≤1-numeric-tolerated components (``lev_used``)
        - if ``exact + lev_used >= threshold`` AND ``lev_used <= 1`` →
          MATCH

    The ``lev_used <= 1`` cap is the safety net against false positives :
    a receipt with 7 exact components + 3 numeric components all 1-off
    is *not* a rescan, it's a different ticket. Per PO validation the
    Levenshtein budget is 1, not unlimited.

    Args :
        db: read-only Session.
        components: the current receipt's 10 components.
        user_id: lookup is scoped to this user.
        window_hours: ``pipeline.anti_fraud.fp_window_hours`` (48).
        threshold: ``pipeline.anti_fraud.fp_fuzzy_match_threshold`` (8 in
            the rescaled 10-component scheme — 8/10 exact + Lev≤1).

    Returns :
        :class:`FuzzyMatch` for the best candidate, or ``None`` when
        no candidate clears the threshold.
    """
    rows = db.execute(
        text(
            "SELECT id, fingerprint_components_jsonb "
            "FROM receipts "
            "WHERE user_id = :uid "
            "  AND parse_fingerprint_user IS NOT NULL "
            "  AND fingerprint_components_jsonb IS NOT NULL "
            "  AND receipt_barcode IS NULL "
            "  AND created_at > :since "
            "ORDER BY created_at DESC "
            "LIMIT :lim"
        ),
        {
            "uid": user_id,
            "since": _window_floor(datetime.now(tz=None).astimezone(), hours=window_hours),
            "lim": _MAX_INTRA_USER_CANDIDATES,
        },
    ).all()
    if not rows:
        return None

    best: FuzzyMatch | None = None
    for row in rows:
        candidate = _decode_components_jsonb(row.fingerprint_components_jsonb)
        if candidate is None:
            continue
        exact, lev_used = _count_matches(candidate, components)
        if lev_used > 1:
            # Levenshtein budget exceeded — too many numeric drifts to
            # be the same ticket.
            continue
        if exact + lev_used < threshold:
            continue
        # Prefer the candidate with the highest exact count (most stable
        # match) — when tied, prefer the most recently created (rows are
        # ORDER BY created_at DESC so first-seen wins naturally).
        if best is None or exact > best.exact_matches:
            best = FuzzyMatch(
                existing_receipt_id=row.id,
                exact_matches=exact,
                lev_tolerance_used=lev_used,
            )
    return best


def check_device_pattern(
    db: Session,
    *,
    device_fingerprint: str | None,
    current_user_id: UUID,
    window_days: int,
    distinct_users_threshold: int,
) -> DeviceVerdict:
    """Count distinct users observed for a ``device_fingerprint`` in window.

    The verdict fires when the count *strictly exceeds* the threshold
    (PO validation 2026-05-11 : "> 3 users distincts" → 4 is the first
    flag, 3 is allowed). The current user is included in the count so a
    fresh device on a fresh user yields ``distinct_user_count = 1``
    (well under threshold).

    Args :
        db: read-only Session.
        device_fingerprint: ``receipts.device_fingerprint`` HMAC string.
            When ``None`` (the upload context didn't yield a device
            signal), the helper returns ``kind="none"`` immediately —
            no fingerprint, no signal.
        current_user_id: the user uploading the current receipt — used
            so the helper counts ``1`` instead of ``0`` when the device
            is brand new.
        window_days: ``pipeline.anti_fraud.device_fp_window_days`` (30).
        distinct_users_threshold:
            ``pipeline.anti_fraud.device_fp_distinct_users_threshold``
            (3). Strict ``>`` ; equal-to-threshold is NOT flagged.

    Returns :
        :class:`DeviceVerdict`.
    """
    if not device_fingerprint:
        return DeviceVerdict(kind="none", distinct_user_count=0)

    row = db.execute(
        text(
            "SELECT COUNT(DISTINCT user_id) AS n "
            "FROM receipts "
            "WHERE device_fingerprint = :df "
            "  AND device_fingerprint IS NOT NULL "
            "  AND user_id IS NOT NULL "
            "  AND created_at > now() - make_interval(days => :days)"
        ),
        {"df": device_fingerprint, "days": window_days},
    ).first()
    # Ensure the current user is counted even if its receipt hasn't been
    # INSERTed yet (caller ordering : we may be invoked pre-INSERT).
    n_observed = int(row.n if row is not None else 0)
    # SELECT DISTINCT user_id already includes the current user when their
    # current upload landed before this call ; when called pre-INSERT the
    # count is one short. Use a "floor" of 1 for the current user.
    current_seen = db.execute(
        text("SELECT 1 FROM receipts WHERE device_fingerprint = :df AND user_id = :uid LIMIT 1"),
        {"df": device_fingerprint, "uid": current_user_id},
    ).first()
    if current_seen is None:
        n_observed += 1

    if n_observed > distinct_users_threshold:
        return DeviceVerdict(kind="shared", distinct_user_count=n_observed)
    return DeviceVerdict(kind="none", distinct_user_count=n_observed)


# ── Internal helpers ──────────────────────────────────────────────────────


def _window_floor(anchor: datetime, *, hours: int) -> datetime:
    """Return ``anchor - hours``, preserving tzinfo (or aware-UTC if naive).

    The receipts table stores ``created_at`` as ``timestamptz`` — passing
    a naive datetime would crash psycopg comparison. We coerce to UTC
    when needed so the helper is safe regardless of caller hygiene.
    """
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    from datetime import timedelta

    return anchor - timedelta(hours=hours)


__all__ = [
    "CrossUserVerdict",
    "CrossUserVerdictKind",
    "DeviceVerdict",
    "DeviceVerdictKind",
    "FuzzyMatch",
    "check_cross_user_duplicate",
    "check_device_pattern",
    "fuzzy_match_intra_user",
]
