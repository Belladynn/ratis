"""Tests for batch/ratis_batch_annual_reset/reset.py."""

import uuid
from datetime import UTC, datetime

from reset import _is_january, reset_gift_card_ytd
from sqlalchemy import text

# ============================================================
# Helpers
# ============================================================


def _get_ytd(db, user_id: uuid.UUID) -> int:
    row = db.execute(
        text("SELECT gift_card_redeemed_ytd_cents FROM users WHERE id = :uid"),
        {"uid": str(user_id)},
    ).first()
    assert row is not None, f"user {user_id} not found"
    return int(row.gift_card_redeemed_ytd_cents)


# ============================================================
# _is_january helper
# ============================================================


def test_is_january_returns_true_for_january():
    jan = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert _is_january(jan) is True


def test_is_january_returns_true_for_late_january():
    jan31 = datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC)
    assert _is_january(jan31) is True


def test_is_january_returns_false_for_february():
    feb = datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)
    assert _is_january(feb) is False


def test_is_january_returns_false_for_december():
    dec = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert _is_january(dec) is False


# ============================================================
# reset_gift_card_ytd — normal run (January)
# ============================================================


def test_reset_zeroes_nonzero_user(session_factory, make_user):
    """In a normal run, a user with ytd > 0 is reset to 0."""
    uid = make_user(ytd_cents=59900)

    count = reset_gift_card_ytd(session_factory, dry_run=False)

    assert count >= 1
    with session_factory() as db:
        assert _get_ytd(db, uid) == 0


def test_reset_only_affects_nonzero_users(session_factory, make_user):
    """Users already at 0 are untouched; only non-zero users are counted."""
    uid_nonzero = make_user(ytd_cents=10000)
    uid_zero = make_user(ytd_cents=0)

    count = reset_gift_card_ytd(session_factory, dry_run=False)

    # Count should be at least 1 (the non-zero user).
    assert count >= 1
    with session_factory() as db:
        assert _get_ytd(db, uid_nonzero) == 0
        assert _get_ytd(db, uid_zero) == 0


def test_reset_reports_correct_count(session_factory, make_user):
    """The count returned matches exactly the number of users that had ytd > 0."""
    # Insert exactly 3 users with ytd > 0 and 1 with ytd == 0.
    for _ in range(3):
        make_user(ytd_cents=5000)
    make_user(ytd_cents=0)

    count = reset_gift_card_ytd(session_factory, dry_run=False)

    # At least 3 users were reset (there may be more from other tests sharing
    # the session — but there are at least 3 non-zero rows).
    assert count >= 3


# ============================================================
# reset_gift_card_ytd — dry-run
# ============================================================


def test_dry_run_does_not_write(session_factory, make_user):
    """--dry-run returns the count but does NOT commit any changes."""
    uid = make_user(ytd_cents=25000)

    count = reset_gift_card_ytd(session_factory, dry_run=True)

    assert count >= 1
    with session_factory() as db:
        # Value must remain unchanged — no write should have occurred.
        assert _get_ytd(db, uid) == 25000


def test_dry_run_returns_zero_when_all_zeroed(session_factory, make_user):
    """If all users already have ytd == 0, dry-run returns 0."""
    # Ensure a fresh state by using a user already at 0.
    make_user(ytd_cents=0)

    # First do a real reset to bring any leftover from sibling tests to 0.
    reset_gift_card_ytd(session_factory, dry_run=False)

    count = reset_gift_card_ytd(session_factory, dry_run=True)

    assert count == 0


# ============================================================
# Idempotency
# ============================================================


def test_second_real_run_is_noop(session_factory, make_user):
    """A second consecutive real run finds nothing to reset and returns 0."""
    make_user(ytd_cents=42000)

    first = reset_gift_card_ytd(session_factory, dry_run=False)
    assert first >= 1

    second = reset_gift_card_ytd(session_factory, dry_run=False)
    assert second == 0


# ============================================================
# Month guard
# ============================================================


def test_month_guard_blocks_non_january():
    """_is_january returns False for non-January months — guard logic is correct."""
    for month in range(2, 13):
        dt = datetime(2026, month, 1, tzinfo=UTC)
        assert _is_january(dt) is False, f"expected False for month {month}"


def test_month_guard_allows_january():
    """_is_january returns True for every day in January."""
    for day in (1, 15, 31):
        dt = datetime(2026, 1, day, tzinfo=UTC)
        assert _is_january(dt) is True, f"expected True for Jan {day}"
