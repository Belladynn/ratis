"""Unit + DB tests for :mod:`worker.pipeline.fraud_lookup` (anti-fraud PR4).

Coverage matrix (cf ARCH § "Politique cross-user" + § fuzzy fallback) :

- :func:`check_cross_user_duplicate` — second_strict, minute, mixed,
  no-match, expired window, same-user filtered out.
- :func:`fuzzy_match_intra_user` — 10/10 exact, 9/10 + Lev≤1 numeric,
  8/10 + Lev=1 (boundary), 8/10 + Lev=2 (must NOT match), 7/10 (under
  threshold), window respected.
- :func:`check_device_pattern` — boundary (3 users, no signal), over
  threshold (4 users, signal), NULL device_fp (none), expired window.

Tests use the shared ``db`` fixture from ``conftest.py`` — savepoint
isolation per test so peer-receipts seeded inline don't leak.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from ratis_core.models.user import User
from sqlalchemy import text
from worker.pipeline.fingerprint import FingerprintComponents
from worker.pipeline.fraud_lookup import (
    check_cross_user_duplicate,
    check_device_pattern,
    fuzzy_match_intra_user,
)

# ── Helpers ───────────────────────────────────────────────────────────────


def _make_user(db, *, suffix: str = "") -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=f"frlk-{suffix or uid.hex[:8]}@ratis.fr",
        display_name="X",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    db.commit()
    return u


def _insert_receipt(
    db,
    *,
    user_id: uuid.UUID | None,
    fp_global: str | None = None,
    fp_user: str | None = None,
    time_precision: str | None = None,
    components: FingerprintComponents | None = None,
    device_fingerprint: str | None = None,
    created_at: datetime | None = None,
) -> uuid.UUID:
    """Insert a minimal receipt row directly via SQL (bypasses ORM
    defaults so we can control ``created_at`` for window tests).

    All anti-fraud columns are optional — pass ``None`` to leave NULL.
    """
    receipt_id = uuid.uuid4()
    import json as _json

    components_json = None
    if components is not None:
        comp_dict = {k: v for k, v in components.__dict__.items() if v is not None}
        components_json = _json.dumps(comp_dict, sort_keys=True)

    if created_at is None:
        db.execute(
            text(
                "INSERT INTO receipts "
                "(id, user_id, purchased_at, store_status, "
                " parse_fingerprint_user, parse_fingerprint_global, "
                " fingerprint_components_jsonb, "
                " device_fingerprint, time_precision) "
                "VALUES (:id, :uid, CURRENT_DATE, 'unknown', "
                "        :fp_user, :fp_global, "
                "        CASE WHEN CAST(:components AS text) IS NOT NULL "
                "             THEN CAST(:components AS jsonb) ELSE NULL END, "
                "        :device, :tp)"
            ),
            {
                "id": receipt_id,
                "uid": user_id,
                "fp_user": fp_user,
                "fp_global": fp_global,
                "components": components_json,
                "device": device_fingerprint,
                "tp": time_precision,
            },
        )
    else:
        db.execute(
            text(
                "INSERT INTO receipts "
                "(id, user_id, purchased_at, store_status, "
                " parse_fingerprint_user, parse_fingerprint_global, "
                " fingerprint_components_jsonb, "
                " device_fingerprint, time_precision, created_at) "
                "VALUES (:id, :uid, CURRENT_DATE, 'unknown', "
                "        :fp_user, :fp_global, "
                "        CASE WHEN CAST(:components AS text) IS NOT NULL "
                "             THEN CAST(:components AS jsonb) ELSE NULL END, "
                "        :device, :tp, :ts)"
            ),
            {
                "id": receipt_id,
                "uid": user_id,
                "fp_user": fp_user,
                "fp_global": fp_global,
                "components": components_json,
                "device": device_fingerprint,
                "tp": time_precision,
                "ts": created_at,
            },
        )
    db.commit()
    return receipt_id


def _full_components(**overrides) -> FingerprintComponents:
    """Build a "10-perfect" FingerprintComponents and apply overrides."""
    defaults = {
        "store_id": "8b9c1f0a-3d4e-4a5f-9b6c-7d8e9f0a1b2c",
        "address_normalized": "1 RUE DE PARIS",
        "brand_normalized": "INTERMARCHE",
        "iso_date": "2026-04-30",
        "iso_time": "14:30:45",
        "time_precision": "second",
        "total_ttc_cents": 1234,
        "item_count_declared": 5,
        "payment_method": "cb",
        "tva_total_cents": 200,
    }
    defaults.update(overrides)
    return FingerprintComponents(**defaults)


# ────────────────────────────────────────────────────────────────────────
# check_cross_user_duplicate
# ────────────────────────────────────────────────────────────────────────


def test_cross_user_second_strict_match(db):
    """Peer at time_precision='second' + we are 'second' → second_strict."""
    me = _make_user(db, suffix="strict-me")
    peer = _make_user(db, suffix="strict-peer")
    peer_id = _insert_receipt(
        db,
        user_id=peer.id,
        fp_global="a" * 64,
        time_precision="second",
    )
    verdict = check_cross_user_duplicate(
        db,
        fp_global="a" * 64,
        time_precision_self="second",
        scanned_at=datetime.now(UTC),
        current_user_id=me.id,
        window_hours=48,
    )
    assert verdict.kind == "second_strict"
    assert verdict.matched_receipt_id == peer_id
    assert verdict.matched_user_id == peer.id


def test_cross_user_minute_when_peer_at_minute(db):
    """Peer at minute precision → flag-only kind 'minute'."""
    me = _make_user(db, suffix="min-me")
    peer = _make_user(db, suffix="min-peer")
    _insert_receipt(
        db,
        user_id=peer.id,
        fp_global="b" * 64,
        time_precision="minute",
    )
    verdict = check_cross_user_duplicate(
        db,
        fp_global="b" * 64,
        time_precision_self="second",
        scanned_at=datetime.now(UTC),
        current_user_id=me.id,
        window_hours=48,
    )
    assert verdict.kind == "minute"
    assert verdict.matched_receipt_id is not None


def test_cross_user_minute_when_self_at_minute(db):
    """Even if peer is at 'second', when self is 'minute' → flag-only."""
    me = _make_user(db, suffix="self-min-me")
    peer = _make_user(db, suffix="self-min-peer")
    _insert_receipt(
        db,
        user_id=peer.id,
        fp_global="c" * 64,
        time_precision="second",
    )
    verdict = check_cross_user_duplicate(
        db,
        fp_global="c" * 64,
        time_precision_self="minute",
        scanned_at=datetime.now(UTC),
        current_user_id=me.id,
        window_hours=48,
    )
    assert verdict.kind == "minute"


def test_cross_user_no_match_empty_db(db):
    me = _make_user(db, suffix="empty-me")
    verdict = check_cross_user_duplicate(
        db,
        fp_global="d" * 64,
        time_precision_self="second",
        scanned_at=datetime.now(UTC),
        current_user_id=me.id,
        window_hours=48,
    )
    assert verdict.kind == "none"
    assert verdict.matched_receipt_id is None


def test_cross_user_same_user_not_returned(db):
    """A previous receipt of the SAME user with same fp must be ignored —
    that's the intra-user UNIQUE-INDEX rescan path, not cross-user fraud."""
    me = _make_user(db, suffix="same-me")
    _insert_receipt(
        db,
        user_id=me.id,
        fp_global="e" * 64,
        time_precision="second",
    )
    verdict = check_cross_user_duplicate(
        db,
        fp_global="e" * 64,
        time_precision_self="second",
        scanned_at=datetime.now(UTC),
        current_user_id=me.id,
        window_hours=48,
    )
    assert verdict.kind == "none"


def test_cross_user_outside_window_not_returned(db):
    """A peer receipt older than ``window_hours`` is not considered."""
    me = _make_user(db, suffix="oow-me")
    peer = _make_user(db, suffix="oow-peer")
    _insert_receipt(
        db,
        user_id=peer.id,
        fp_global="f" * 64,
        time_precision="second",
        created_at=datetime.now(UTC) - timedelta(hours=100),
    )
    verdict = check_cross_user_duplicate(
        db,
        fp_global="f" * 64,
        time_precision_self="second",
        scanned_at=datetime.now(UTC),
        current_user_id=me.id,
        window_hours=48,
    )
    assert verdict.kind == "none"


def test_cross_user_returns_none_on_empty_fp(db):
    me = _make_user(db, suffix="emptyfp-me")
    verdict = check_cross_user_duplicate(
        db,
        fp_global="",
        time_precision_self="second",
        scanned_at=datetime.now(UTC),
        current_user_id=me.id,
        window_hours=48,
    )
    assert verdict.kind == "none"


def test_cross_user_strict_priority_when_multiple_candidates(db):
    """If 2 peer rows match (one 'minute' + one 'second') we return strict."""
    me = _make_user(db, suffix="prio-me")
    peer_minute = _make_user(db, suffix="prio-min")
    peer_second = _make_user(db, suffix="prio-sec")
    _insert_receipt(db, user_id=peer_minute.id, fp_global="9" * 64, time_precision="minute")
    strict_id = _insert_receipt(db, user_id=peer_second.id, fp_global="9" * 64, time_precision="second")
    verdict = check_cross_user_duplicate(
        db,
        fp_global="9" * 64,
        time_precision_self="second",
        scanned_at=datetime.now(UTC),
        current_user_id=me.id,
        window_hours=48,
    )
    assert verdict.kind == "second_strict"
    assert verdict.matched_receipt_id == strict_id


# ────────────────────────────────────────────────────────────────────────
# fuzzy_match_intra_user
# ────────────────────────────────────────────────────────────────────────


def test_fuzzy_match_all_10_exact(db):
    """All 10 components identical → match with exact_matches=10, lev=0."""
    me = _make_user(db, suffix="fuzzy-10")
    comp = _full_components()
    existing_id = _insert_receipt(
        db,
        user_id=me.id,
        fp_user="a" * 64,
        fp_global="x" * 64,
        components=comp,
    )
    match = fuzzy_match_intra_user(
        db,
        components=comp,
        user_id=me.id,
        window_hours=48,
        threshold=8,
    )
    assert match is not None
    assert match.existing_receipt_id == existing_id
    assert match.exact_matches == 10
    assert match.lev_tolerance_used == 0


def test_fuzzy_match_9_exact_plus_lev_one_on_total(db):
    """9 components match exactly, total_ttc_cents off by 1 → match."""
    me = _make_user(db, suffix="fuzzy-9lev")
    existing_comp = _full_components(total_ttc_cents=1234)
    existing_id = _insert_receipt(
        db,
        user_id=me.id,
        fp_user="b" * 64,
        components=existing_comp,
    )
    current_comp = _full_components(total_ttc_cents=1235)  # +1 cent
    match = fuzzy_match_intra_user(
        db,
        components=current_comp,
        user_id=me.id,
        window_hours=48,
        threshold=8,
    )
    assert match is not None
    assert match.existing_receipt_id == existing_id
    assert match.exact_matches == 9
    assert match.lev_tolerance_used == 1


def test_fuzzy_match_8_exact_plus_lev_one_date(db):
    """8 exact + Lev=1 on iso_date → match at threshold boundary."""
    me = _make_user(db, suffix="fuzzy-8lev-date")
    existing_comp = _full_components(iso_date="2026-04-30", item_count_declared=5)
    _insert_receipt(
        db,
        user_id=me.id,
        fp_user="c" * 64,
        components=existing_comp,
    )
    # iso_date "2026-04-31" — single-char Lev from "2026-04-30" → 1 edit
    # item_count_declared diff by 2 (≠ Lev1, ≠ match) → drops to 8 exact?
    # Better : keep item_count_declared identical, just drift iso_date.
    # Then we have 9 exact + Lev 1, which is the previous test. To pin
    # the 8/10 boundary : drift iso_date AND drift payment_method
    # (non-numeric, no lev tolerance → mismatch).
    current_comp = _full_components(
        iso_date="2026-04-31",
        payment_method="cash",  # mismatch, no lev fallback
    )
    match = fuzzy_match_intra_user(
        db,
        components=current_comp,
        user_id=me.id,
        window_hours=48,
        threshold=8,
    )
    assert match is not None
    assert match.exact_matches == 8
    assert match.lev_tolerance_used == 1


def test_fuzzy_match_8_exact_plus_lev_two_rejected(db):
    """8 exact, but the numeric drift is > Lev1 → no match (lev_used budget)."""
    me = _make_user(db, suffix="fuzzy-8lev2")
    existing_comp = _full_components(total_ttc_cents=1234, tva_total_cents=200)
    _insert_receipt(
        db,
        user_id=me.id,
        fp_user="d" * 64,
        components=existing_comp,
    )
    # Two numeric drifts, each by +1 → lev_used = 2 (budget = 1 → reject).
    current_comp = _full_components(total_ttc_cents=1235, tva_total_cents=201)
    match = fuzzy_match_intra_user(
        db,
        components=current_comp,
        user_id=me.id,
        window_hours=48,
        threshold=8,
    )
    assert match is None


def test_fuzzy_match_seven_exact_under_threshold(db):
    """7 exact + 0 lev tolerance → 7 < 8 → no match."""
    me = _make_user(db, suffix="fuzzy-7")
    existing_comp = _full_components()
    _insert_receipt(
        db,
        user_id=me.id,
        fp_user="e" * 64,
        components=existing_comp,
    )
    # Drift 3 non-numeric components so no lev tolerance applies.
    current_comp = _full_components(
        brand_normalized="CARREFOUR",
        address_normalized="2 RUE DE LYON",
        payment_method="cash",
    )
    match = fuzzy_match_intra_user(
        db,
        components=current_comp,
        user_id=me.id,
        window_hours=48,
        threshold=8,
    )
    assert match is None


def test_fuzzy_match_outside_window_skipped(db):
    """Candidate older than ``window_hours`` is not considered."""
    me = _make_user(db, suffix="fuzzy-oow")
    comp = _full_components()
    _insert_receipt(
        db,
        user_id=me.id,
        fp_user="f" * 64,
        components=comp,
        created_at=datetime.now(UTC) - timedelta(hours=100),
    )
    match = fuzzy_match_intra_user(
        db,
        components=comp,
        user_id=me.id,
        window_hours=48,
        threshold=8,
    )
    assert match is None


def test_fuzzy_match_different_user_skipped(db):
    """Other-user candidates are not considered — intra-user only."""
    me = _make_user(db, suffix="fuzzy-other-me")
    other = _make_user(db, suffix="fuzzy-other-other")
    comp = _full_components()
    _insert_receipt(
        db,
        user_id=other.id,
        fp_user="01" * 32,
        components=comp,
    )
    match = fuzzy_match_intra_user(
        db,
        components=comp,
        user_id=me.id,
        window_hours=48,
        threshold=8,
    )
    assert match is None


def test_fuzzy_match_picks_highest_exact_score(db):
    """When several candidates match, the one with the highest
    ``exact_matches`` wins."""
    me = _make_user(db, suffix="fuzzy-best")
    weak_comp = _full_components(total_ttc_cents=1235)  # 9 exact + Lev1
    strong_comp = _full_components()  # 10 exact + Lev0
    _insert_receipt(db, user_id=me.id, fp_user="11" * 32, components=weak_comp)
    strong_id = _insert_receipt(db, user_id=me.id, fp_user="22" * 32, components=strong_comp)
    current = _full_components()
    match = fuzzy_match_intra_user(
        db,
        components=current,
        user_id=me.id,
        window_hours=48,
        threshold=8,
    )
    assert match is not None
    assert match.existing_receipt_id == strong_id
    assert match.exact_matches == 10


# ────────────────────────────────────────────────────────────────────────
# check_device_pattern
# ────────────────────────────────────────────────────────────────────────


def test_device_pattern_boundary_three_users_not_flagged(db):
    """3 distinct users on the same device → at boundary (3 == threshold)
    → NOT flagged (strict ``>``)."""
    me = _make_user(db, suffix="dev-3-me")
    others = [_make_user(db, suffix=f"dev-3-o{i}") for i in range(2)]
    df = "deadbeefcafe1234"
    _insert_receipt(db, user_id=me.id, device_fingerprint=df)
    for o in others:
        _insert_receipt(db, user_id=o.id, device_fingerprint=df)
    verdict = check_device_pattern(
        db,
        device_fingerprint=df,
        current_user_id=me.id,
        window_days=30,
        distinct_users_threshold=3,
    )
    assert verdict.kind == "none"
    assert verdict.distinct_user_count == 3


def test_device_pattern_four_users_flagged(db):
    """4 distinct users → strictly > 3 → 'shared'."""
    me = _make_user(db, suffix="dev-4-me")
    others = [_make_user(db, suffix=f"dev-4-o{i}") for i in range(3)]
    df = "feedface11112222"
    _insert_receipt(db, user_id=me.id, device_fingerprint=df)
    for o in others:
        _insert_receipt(db, user_id=o.id, device_fingerprint=df)
    verdict = check_device_pattern(
        db,
        device_fingerprint=df,
        current_user_id=me.id,
        window_days=30,
        distinct_users_threshold=3,
    )
    assert verdict.kind == "shared"
    assert verdict.distinct_user_count == 4


def test_device_pattern_none_when_device_fp_null(db):
    me = _make_user(db, suffix="dev-null")
    verdict = check_device_pattern(
        db,
        device_fingerprint=None,
        current_user_id=me.id,
        window_days=30,
        distinct_users_threshold=3,
    )
    assert verdict.kind == "none"
    assert verdict.distinct_user_count == 0


def test_device_pattern_outside_window_not_counted(db):
    """Old peers don't contribute to the count."""
    me = _make_user(db, suffix="dev-oow-me")
    old_others = [_make_user(db, suffix=f"dev-oow-o{i}") for i in range(3)]
    df = "0123456789abcdef"
    _insert_receipt(db, user_id=me.id, device_fingerprint=df)
    for o in old_others:
        _insert_receipt(
            db,
            user_id=o.id,
            device_fingerprint=df,
            created_at=datetime.now(UTC) - timedelta(days=40),
        )
    verdict = check_device_pattern(
        db,
        device_fingerprint=df,
        current_user_id=me.id,
        window_days=30,
        distinct_users_threshold=3,
    )
    # 3 peer rows are out of window — only the current user counts.
    assert verdict.kind == "none"
    assert verdict.distinct_user_count == 1


def test_device_pattern_counts_current_user_only_once(db):
    """The same user uploading 5 receipts on a device → count = 1."""
    me = _make_user(db, suffix="dev-self-x5")
    df = "abc1230000000000"
    for _ in range(5):
        _insert_receipt(db, user_id=me.id, device_fingerprint=df)
    verdict = check_device_pattern(
        db,
        device_fingerprint=df,
        current_user_id=me.id,
        window_days=30,
        distinct_users_threshold=3,
    )
    assert verdict.kind == "none"
    assert verdict.distinct_user_count == 1
