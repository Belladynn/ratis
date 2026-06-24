"""Tests for ``ratis_core.products.claim_first_discovery``.

V1.1 follow-up — KP-75 / DP-achievements-v1-followups item 1.

The helper attributes a product's first-discovery slot atomically :
* succeeds only when the column is NULL (CAS-style)
* skips banned / deleted users
* never overwrites a previously claimed slot
* tolerates NULL inputs silently

DB-backed tests using the ratis_core conftest (SAVEPOINT isolation).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from ratis_core.products import claim_first_discovery
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Fixtures (kept local — ratis_core/tests doesn't share factories)
# ---------------------------------------------------------------------------


def _make_user(
    db: Any,
    *,
    is_deleted: bool = False,
    is_shadow_banned: bool = False,
) -> uuid.UUID:
    from ratis_core.identifiers import generate_support_id

    uid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO users "
            "    (id, email, support_id, account_type, "
            "     is_deleted, is_shadow_banned, created_at, updated_at) "
            "VALUES (:id, :email, :sid, 'oauth', "
            "        :deleted, :banned, now(), now())"
        ),
        {
            "id": uid,
            "email": f"u-{uid.hex[:8]}@example.com",
            "sid": generate_support_id(),
            "deleted": is_deleted,
            "banned": is_shadow_banned,
        },
    )
    return uid


def _make_product(db: Any, *, ean: str | None = None) -> str:
    ean = ean or str(uuid.uuid4().int)[:13]
    db.execute(
        text("INSERT INTO products (ean, name, source) VALUES (:ean, 'p', 'off')"),
        {"ean": ean},
    )
    return ean


def _read_discoverer(db: Any, ean: str) -> uuid.UUID | None:
    row = db.execute(
        text("SELECT first_discovered_by_user_id FROM products WHERE ean = :ean"),
        {"ean": ean},
    ).first()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestClaimSucceeds:
    """First eligible caller wins the slot."""

    def test_first_user_claims_slot(self, db):
        ean = _make_product(db)
        uid = _make_user(db)

        ok = claim_first_discovery(db, ean, uid)

        assert ok is True
        assert _read_discoverer(db, ean) == uid

    def test_returns_true_on_success(self, db):
        ean = _make_product(db)
        uid = _make_user(db)

        assert claim_first_discovery(db, ean, uid) is True


# ---------------------------------------------------------------------------
# Idempotency / non-overwrite
# ---------------------------------------------------------------------------


class TestClaimNoOverwrite:
    """Once a slot is taken, subsequent callers no-op."""

    def test_second_user_does_not_overwrite(self, db):
        ean = _make_product(db)
        first = _make_user(db)
        second = _make_user(db)

        claim_first_discovery(db, ean, first)
        ok = claim_first_discovery(db, ean, second)

        assert ok is False
        assert _read_discoverer(db, ean) == first

    def test_same_user_double_claim_no_op_after_first(self, db):
        ean = _make_product(db)
        uid = _make_user(db)

        assert claim_first_discovery(db, ean, uid) is True
        assert claim_first_discovery(db, ean, uid) is False
        assert _read_discoverer(db, ean) == uid


# ---------------------------------------------------------------------------
# Anti-ban / anti-deleted (mirrors achievement dispatcher)
# ---------------------------------------------------------------------------


class TestClaimRejectsIneligibleUsers:
    """Banned / deleted users cannot grab the first-discovery credit."""

    def test_shadow_banned_user_skipped(self, db):
        ean = _make_product(db)
        uid = _make_user(db, is_shadow_banned=True)

        ok = claim_first_discovery(db, ean, uid)

        assert ok is False
        assert _read_discoverer(db, ean) is None

    def test_deleted_user_skipped(self, db):
        ean = _make_product(db)
        uid = _make_user(db, is_deleted=True)

        ok = claim_first_discovery(db, ean, uid)

        assert ok is False
        assert _read_discoverer(db, ean) is None

    def test_banned_user_does_not_block_legitimate_later_claim(self, db):
        ean = _make_product(db)
        banned = _make_user(db, is_shadow_banned=True)
        legit = _make_user(db)

        # Banned user attempt — silent no-op.
        assert claim_first_discovery(db, ean, banned) is False

        # Legit user claims afterwards — wins.
        assert claim_first_discovery(db, ean, legit) is True
        assert _read_discoverer(db, ean) == legit


# ---------------------------------------------------------------------------
# Defensive / edge cases
# ---------------------------------------------------------------------------


class TestClaimDefensive:
    """NULL inputs and missing rows must not raise."""

    def test_null_ean_returns_false(self, db):
        uid = _make_user(db)
        assert claim_first_discovery(db, None, uid) is False

    def test_empty_ean_returns_false(self, db):
        uid = _make_user(db)
        assert claim_first_discovery(db, "", uid) is False

    def test_null_user_id_returns_false(self, db):
        ean = _make_product(db)
        assert claim_first_discovery(db, ean, None) is False

    def test_unknown_product_no_op(self, db):
        uid = _make_user(db)
        # No such product row ; UPDATE matches nothing.
        assert claim_first_discovery(db, "0000000000000", uid) is False

    def test_unknown_user_no_op(self, db):
        ean = _make_product(db)
        # No such user row ; EXISTS subquery rejects.
        assert claim_first_discovery(db, ean, uuid.uuid4()) is False
        assert _read_discoverer(db, ean) is None


# ---------------------------------------------------------------------------
# FK ON DELETE SET NULL (regression guard for the migration)
# ---------------------------------------------------------------------------


class TestForeignKeyBehaviour:
    """Deleting the discoverer user clears the attribution but keeps the row."""

    @pytest.mark.skip(
        reason="Hard-deleting a user requires cleaning up many other "
        "FK chains (scans, receipts, …). Soft-delete via "
        "``is_deleted=true`` is the production path and is already "
        "covered by the dispatcher's anti-deleted guard. Documented "
        "behaviour : the FK uses ON DELETE SET NULL — verified at the "
        "schema level by the migration smoke test."
    )
    def test_user_hard_delete_clears_attribution(self, db):
        ean = _make_product(db)
        uid = _make_user(db)
        claim_first_discovery(db, ean, uid)

        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": uid})
        db.flush()

        assert _read_discoverer(db, ean) is None
