"""DB-integration tests for :mod:`worker.pipeline.phash_lookup`.

Cover :

- empty DB → ``None``
- exact same pHash on a cross-user receipt → match with distance 0
- pHash 1 bit apart on a cross-user receipt → match (under threshold)
- pHash above threshold → no match
- SAME-user receipt with same pHash → not returned (cross-user only)
- outside the ``window_days`` lookback → not returned
- ``user_id=NULL`` peer rows are skipped (anonymized receipts)
- closest match wins when several peers are inside the threshold
- invalid candidate / threshold / window → graceful ``None``
- DB error path → ``None`` + log warning (no raise)

Cf. ``ARCH_receipt_pipeline.md`` § "Réconciliation tickets — V1" step 2.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from ratis_core.models.user import User
from sqlalchemy import text
from worker.pipeline.phash_lookup import lookup_phash_cross_user

# ── helpers ──────────────────────────────────────────────────────────────


def _make_user(db, *, email_suffix: str = "") -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=f"phash-test-{email_suffix or uid.hex[:8]}@ratis.fr",
        display_name="PhashTester",
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
    phash_hex: str | None,
    created_at: datetime | None = None,
) -> uuid.UUID:
    """Insert a minimal receipt row with the supplied pHash. ``created_at``
    is set explicitly when provided (overrides the server_default) so we
    can simulate out-of-window peers."""
    receipt_id = uuid.uuid4()
    if created_at is None:
        db.execute(
            text("INSERT INTO receipts (id, user_id, purchased_at, image_phash) VALUES (:id, :uid, CURRENT_DATE, :ph)"),
            {"id": receipt_id, "uid": user_id, "ph": phash_hex},
        )
    else:
        db.execute(
            text(
                "INSERT INTO receipts "
                "(id, user_id, purchased_at, image_phash, created_at) "
                "VALUES (:id, :uid, CURRENT_DATE, :ph, :ts)"
            ),
            {"id": receipt_id, "uid": user_id, "ph": phash_hex, "ts": created_at},
        )
    db.commit()
    return receipt_id


def _flip_one_bit(hex16: str) -> str:
    """Return a 16-char hex with exactly one bit flipped (Hamming distance 1)."""
    as_int = int(hex16, 16) ^ 0b1
    return f"{as_int:016x}"


# ── tests ────────────────────────────────────────────────────────────────


def test_lookup_returns_none_on_empty_db(db):
    me = _make_user(db, email_suffix="empty-me")
    result = lookup_phash_cross_user(
        db,
        user_id=me.id,
        candidate_phash_hex="0123456789abcdef",
        max_hamming_distance=8,
        window_days=30,
    )
    assert result is None


def test_lookup_returns_match_on_identical_phash_cross_user(db):
    peer = _make_user(db, email_suffix="peer-1")
    me = _make_user(db, email_suffix="me-1")
    target = "fedcba9876543210"
    peer_receipt_id = _insert_receipt(db, user_id=peer.id, phash_hex=target)

    result = lookup_phash_cross_user(
        db,
        user_id=me.id,
        candidate_phash_hex=target,
        max_hamming_distance=8,
        window_days=30,
    )
    assert result is not None
    matched_id, details = result
    assert matched_id == peer_receipt_id
    assert details["peer_receipt_id"] == str(peer_receipt_id)
    assert details["peer_user_id"] == str(peer.id)
    assert details["hamming_distance"] == 0


def test_lookup_returns_match_on_near_phash_cross_user(db):
    peer = _make_user(db, email_suffix="peer-2")
    me = _make_user(db, email_suffix="me-2")
    target = "fedcba9876543210"
    near = _flip_one_bit(target)
    peer_receipt_id = _insert_receipt(db, user_id=peer.id, phash_hex=target)

    result = lookup_phash_cross_user(
        db,
        user_id=me.id,
        candidate_phash_hex=near,
        max_hamming_distance=8,
        window_days=30,
    )
    assert result is not None
    matched_id, details = result
    assert matched_id == peer_receipt_id
    assert details["hamming_distance"] == 1


def test_lookup_returns_none_when_distance_above_threshold(db):
    peer = _make_user(db, email_suffix="peer-3")
    me = _make_user(db, email_suffix="me-3")
    # Distance 64 (full inversion) is above any reasonable threshold.
    target = "0000000000000000"
    candidate = "ffffffffffffffff"
    _insert_receipt(db, user_id=peer.id, phash_hex=target)

    result = lookup_phash_cross_user(
        db,
        user_id=me.id,
        candidate_phash_hex=candidate,
        max_hamming_distance=8,
        window_days=30,
    )
    assert result is None


def test_lookup_excludes_same_user(db):
    me = _make_user(db, email_suffix="self-only")
    target = "1111111111111111"
    _insert_receipt(db, user_id=me.id, phash_hex=target)

    result = lookup_phash_cross_user(
        db,
        user_id=me.id,
        candidate_phash_hex=target,
        max_hamming_distance=8,
        window_days=30,
    )
    assert result is None


def test_lookup_skips_rows_outside_window(db):
    peer = _make_user(db, email_suffix="peer-old")
    me = _make_user(db, email_suffix="me-old")
    target = "2222222222222222"
    long_ago = datetime.now(UTC) - timedelta(days=90)
    _insert_receipt(db, user_id=peer.id, phash_hex=target, created_at=long_ago)

    result = lookup_phash_cross_user(
        db,
        user_id=me.id,
        candidate_phash_hex=target,
        max_hamming_distance=8,
        window_days=30,
    )
    assert result is None


def test_lookup_skips_null_user_peers(db):
    me = _make_user(db, email_suffix="me-anon")
    target = "3333333333333333"
    _insert_receipt(db, user_id=None, phash_hex=target)

    result = lookup_phash_cross_user(
        db,
        user_id=me.id,
        candidate_phash_hex=target,
        max_hamming_distance=8,
        window_days=30,
    )
    assert result is None


def test_lookup_returns_closest_when_multiple_match(db):
    peer_far = _make_user(db, email_suffix="peer-far")
    peer_near = _make_user(db, email_suffix="peer-near")
    me = _make_user(db, email_suffix="me-multi")
    candidate = "0000000000000000"
    # Far : 4 bits flipped.
    far_hex = f"{0b1111:016x}"
    # Near : 1 bit flipped.
    near_hex = f"{0b1:016x}"
    _insert_receipt(db, user_id=peer_far.id, phash_hex=far_hex)
    near_id = _insert_receipt(db, user_id=peer_near.id, phash_hex=near_hex)

    result = lookup_phash_cross_user(
        db,
        user_id=me.id,
        candidate_phash_hex=candidate,
        max_hamming_distance=8,
        window_days=30,
    )
    assert result is not None
    matched_id, details = result
    assert matched_id == near_id
    assert details["hamming_distance"] == 1


# ── input validation : graceful None, no crash ─────────────────────────


def test_lookup_invalid_candidate_returns_none(db):
    me = _make_user(db, email_suffix="me-invalid")
    assert (
        lookup_phash_cross_user(
            db,
            user_id=me.id,
            candidate_phash_hex="",
            max_hamming_distance=8,
            window_days=30,
        )
        is None
    )
    assert (
        lookup_phash_cross_user(
            db,
            user_id=me.id,
            candidate_phash_hex="tooShort",  # 8 chars
            max_hamming_distance=8,
            window_days=30,
        )
        is None
    )


def test_lookup_invalid_threshold_or_window_returns_none(db):
    me = _make_user(db, email_suffix="me-bad-args")
    assert (
        lookup_phash_cross_user(
            db,
            user_id=me.id,
            candidate_phash_hex="0123456789abcdef",
            max_hamming_distance=-1,
            window_days=30,
        )
        is None
    )
    assert (
        lookup_phash_cross_user(
            db,
            user_id=me.id,
            candidate_phash_hex="0123456789abcdef",
            max_hamming_distance=8,
            window_days=0,
        )
        is None
    )


def test_lookup_db_error_returns_none_and_logs(db, caplog, monkeypatch):
    """Simulate a DB blowup mid-execute — helper must not propagate."""
    me = _make_user(db, email_suffix="me-db-err")

    class _ExplodingSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError("simulated DB failure")

    with caplog.at_level("WARNING", logger="worker.pipeline.phash_lookup"):
        result = lookup_phash_cross_user(
            _ExplodingSession(),
            user_id=me.id,
            candidate_phash_hex="0123456789abcdef",
            max_hamming_distance=8,
            window_days=30,
        )
    assert result is None
    assert any("lookup_phash_cross_user failed" in rec.message for rec in caplog.records)
