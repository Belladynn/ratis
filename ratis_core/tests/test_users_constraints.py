"""Direct PG-level CHECK assertion tests for ``users`` — Pattern A.

Each test seeds a single row that targets a specific CHECK predicate. Tests
that should PASS the constraint use ``db.flush()`` to surface the error
immediately (instead of at commit time, which would taint the SAVEPOINT).
Tests that should FAIL the constraint catch ``IntegrityError`` and assert
the offending constraint name appears in the driver message.

H2 Phase 2 (migration ``20260518_1300_acct_type``) collapsed the OAuth
identity columns ``users.(provider, provider_id)`` into a single
``account_type`` column narrowed to account *states*
(``oauth | internal | deleted | dev``). The real OAuth identity now lives
in ``user_identities``. As a consequence :
  * the ``auth_coherence`` CHECK is gone (its per-provider shape is
    meaningless once ``provider_id`` is externalised),
  * ``provider_check`` is renamed ``account_type_check`` and its whitelist
    no longer includes ``'google'`` / ``'apple'``,
  * the ``users_email_key`` UNIQUE is dropped — with the account key now
    in ``user_identities.(provider, provider_id)``, two accounts may share
    an email (Decision 2). ``email`` stays ``NOT NULL``.

These tests pin that post-Phase-2 contract.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def _support_id() -> str:
    return f"RTS-{uuid.uuid4().hex[:6].upper()}"


def _insert_user(db, **overrides) -> uuid.UUID:
    """Insert + flush a ``users`` row with sensible defaults.

    Returns the new id. Wraps both the INSERT statement and the flush in
    a single call so callers can use the recommended single-statement
    ``with pytest.raises(IntegrityError):`` form (avoids PT012 lint).
    The caller commits / rolls back as appropriate for the test scenario.

    Defaults match the post-Phase-2 shape : ``account_type='oauth'`` (the
    OAuth identity itself lives in ``user_identities``, not on this row).
    """
    uid = uuid.uuid4()
    cols = {
        "id": str(uid),
        "email": f"row_{uid}@example.com",
        "support_id": _support_id(),
        "account_type": "oauth",
        "password_hash": None,
        "display_name": None,
        "is_deleted": False,
        "gift_card_redeemed_ytd_cents": 0,
    }
    cols.update(overrides)
    db.execute(
        text(
            "INSERT INTO users "
            "(id, email, support_id, account_type, password_hash, "
            "display_name, is_deleted, gift_card_redeemed_ytd_cents) "
            "VALUES "
            "(:id, :email, :support_id, :account_type, "
            ":password_hash, :display_name, :is_deleted, "
            ":gift_card_redeemed_ytd_cents)"
        ),
        cols,
    )
    db.flush()
    return uid


def _expect_check_violation(db, constraint_name: str, **overrides) -> None:
    """Assert that ``_insert_user(**overrides)`` raises an IntegrityError
    whose driver message mentions ``constraint_name`` (or generically
    ``check constraint``). Rolls back the SAVEPOINT after capture so the
    outer test fixture stays usable.
    """
    with pytest.raises(IntegrityError) as exc_info:
        _insert_user(db, **overrides)
    msg = str(exc_info.value.orig).lower()
    assert constraint_name in msg or "check constraint" in msg, (
        f"Expected CHECK violation on {constraint_name}, got: {msg!r}"
    )
    db.rollback()


# ============================================================
# account_type_check — whitelist of accepted account states
# ============================================================


def test_account_type_check_accepts_oauth(db):
    """The nominal state — the OAuth identity lives in ``user_identities``."""
    _insert_user(db, account_type="oauth", password_hash=None)


def test_account_type_check_accepts_internal(db):
    """Admin / sentinel users use the 'internal' account_type."""
    _insert_user(db, account_type="internal", password_hash=None)


def test_account_type_check_accepts_dev(db):
    """Seed personas use the 'dev' account_type."""
    _insert_user(db, account_type="dev", password_hash=None)


def test_account_type_check_accepts_deleted_with_full_tombstone_shape(db):
    """The CHECK accepts ``account_type='deleted'`` when (a) password_hash
    NULL, (b) is_deleted=TRUE — the exact shape written by
    ``account_service.delete_account``.
    """
    _insert_user(
        db,
        account_type="deleted",
        password_hash=None,
        is_deleted=True,
    )


def test_account_type_check_rejects_unknown_account_type(db):
    _expect_check_violation(db, "account_type_check", account_type="martian")


def test_account_type_check_rejects_empty_account_type(db):
    _expect_check_violation(db, "account_type_check", account_type="")


def test_account_type_check_rejects_google(db):
    """``'google'`` is no longer a valid ``account_type`` — the OAuth
    provider identity moved to ``user_identities`` (H2 Phase 2). The
    column now holds account *states* only.
    """
    _expect_check_violation(db, "account_type_check", account_type="google")


# ============================================================
# email is NOT unique post-Phase-2 (Decision 2)
# ============================================================


def test_email_no_longer_unique(db):
    """Two ``users`` rows may share the same ``email`` (Decision 2).

    The account key moved to ``user_identities.(provider, provider_id)``
    — ``email`` is now a purely informative contact field. The
    ``users_email_key`` UNIQUE was dropped by migration
    ``20260518_1300_acct_type``. Both inserts must succeed and yield two
    distinct ids.
    """
    shared_email = f"shared_{uuid.uuid4()}@example.com"
    uid1 = _insert_user(db, email=shared_email)
    uid2 = _insert_user(db, email=shared_email)
    assert uid1 != uid2, "the two inserts must produce distinct user ids"

    count = db.execute(
        text("SELECT COUNT(*) FROM users WHERE email = :email"),
        {"email": shared_email},
    ).scalar()
    assert count == 2, f"expected 2 rows sharing the email, got {count}"
    db.rollback()


# ============================================================
# Bug 5 specific — minimal repro of the silent prod break
# ============================================================


def test_bug5_repro_full_anonymize_update_path_succeeds(db):
    """Reproduces the exact UPDATE pattern run by ``delete_account``.

    1. INSERT a fresh ``account_type='oauth'`` user (commits OK).
    2. UPDATE the row to the tombstone shape — the ``account_type_check``
       CHECK must accept ``'deleted'`` and the UPDATE flushes cleanly.

    If this test ever fails, the tombstone contract has regressed.
    """
    uid = _insert_user(db, account_type="oauth", password_hash=None)

    db.execute(
        text(
            "UPDATE users SET "
            "email = :email, display_name = NULL, avatar_url = NULL, "
            "password_hash = NULL, account_type = 'deleted', "
            "is_deleted = true "
            "WHERE id = :uid"
        ),
        {"email": f"deleted_{uid}@deleted.invalid", "uid": str(uid)},
    )
    db.flush()
    db.rollback()


# ============================================================
# user_identities — Phase 2 multi-identity table
# ============================================================


def _insert_identity(db, user_id, **overrides) -> uuid.UUID:
    """Insert + flush a ``user_identities`` row, returning the new id.

    Wraps INSERT + flush in one call so callers can use the single-statement
    ``with pytest.raises(IntegrityError):`` form (avoids PT012 lint), mirroring
    the ``_insert_user`` helper above.
    """
    iid = uuid.uuid4()
    cols = {
        "id": str(iid),
        "user_id": str(user_id),
        "provider": "google",
        "provider_id": f"oauth-{iid}",
        "email": f"ident_{iid}@example.com",
    }
    cols.update(overrides)
    db.execute(
        text(
            "INSERT INTO user_identities "
            "(id, user_id, provider, provider_id, email) "
            "VALUES (:id, :user_id, :provider, :provider_id, :email)"
        ),
        cols,
    )
    db.flush()
    return iid


class TestUserIdentities:
    """Direct PG-level constraint assertions for the ``user_identities``
    table (H2 Phase 2 — one OAuth identity per ``(provider, provider_id)``).
    """

    def test_user_identities_unique_provider_provider_id(self, db):
        """Two identity rows with the same ``(provider, provider_id)`` pair
        violate the ``user_identities_provider_provider_id_key`` UNIQUE
        constraint — the second INSERT must raise ``IntegrityError``.
        """
        uid = _insert_user(db)
        _insert_identity(db, uid, provider="google", provider_id="dup-sub")

        with pytest.raises(IntegrityError) as exc_info:
            _insert_identity(db, uid, provider="google", provider_id="dup-sub")
        msg = str(exc_info.value.orig).lower()
        assert "user_identities_provider_provider_id_key" in msg, (
            f"Expected UNIQUE violation on provider/provider_id, got: {msg!r}"
        )
        db.rollback()

    def test_user_identities_fk_cascade(self, db):
        """Deleting a ``users`` row cascades to its ``user_identities`` rows
        (FK ``ON DELETE CASCADE``).
        """
        uid = _insert_user(db)
        ident_id = _insert_identity(db, uid, provider="apple", provider_id="app-cascade-sub")

        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(uid)})
        db.flush()

        remaining = db.execute(
            text("SELECT COUNT(*) FROM user_identities WHERE id = :id"),
            {"id": str(ident_id)},
        ).scalar()
        assert remaining == 0, "FK ON DELETE CASCADE did not remove the identity row"
        db.rollback()
