"""End-to-end tests for ``DELETE /api/v1/account`` — tombstone regression suite.

History
=======
The ``DELETE /account`` endpoint was silently broken in production until
2026-05-11 : the anonymize routine sets the users tombstone state, but the
PG CHECKs (defined only in migrations, not mirrored in the ORM) rejected
that shape. The fix mirrored the CHECKs in the ORM ``__table_args__``
(Pattern A) so test bootstraps via ``create_all`` enforce the same shape
production does.

Since H2 Phase 2 (migration ``20260518_1300_acct_type``) the OAuth
identity lives in ``user_identities`` ; the ``users`` row carries only an
``account_type`` *state* (``oauth | internal | deleted | dev``). The
tombstone now flips ``account_type`` to ``'deleted'`` — there is no
longer a ``provider_id`` column nor an ``auth_coherence`` CHECK.

This module covers the **end-to-end DELETE /account contract** : full
request cycle through the HTTP layer (FastAPI → service → DB), tombstone
state assertions on ``users``, idempotency, downstream auth gate.
"""

from __future__ import annotations

import uuid

from _auth_helpers import oauth_signup
from sqlalchemy import text


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, email: str, password: str = "password123") -> dict:
    """Mint a user + tokens via OAuth. ``password`` kept for call-site
    compatibility but ignored — Ratis is OAuth-only."""
    return oauth_signup(client, email)


# ============================================================
# Happy path — OAuth user
# ============================================================


def test_delete_account_email_user_full_tombstone_state(client, db):
    """Happy path : OAuth user → DELETE /account succeeds end-to-end.

    Asserts EVERY column of the tombstone state on ``users`` and — critically
    — that ``db.commit()`` itself succeeded (no IntegrityError silently
    swallowed). Regression guard : if the ORM mirror of
    ``account_type_check`` regresses, the response would be 500 instead of
    204.
    """
    import repositories.user_repository as user_repo

    tokens = _register(client, "del_e2e_email@example.com")
    user = user_repo.get_by_email(db, "del_e2e_email@example.com")
    user_id = user.id

    # Sanity : pre-delete shape is a normal OAuth account.
    assert user.account_type == "oauth"
    assert user.password_hash is None
    assert user.is_deleted is False

    # --- The actual request (must NOT 500) ---
    response = client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))
    assert response.status_code == 204, (
        "DELETE /account returned non-204 — the PG CHECK regression is back. "
        f"Status={response.status_code} body={response.text!r}"
    )

    # --- Tombstone shape on users (each column independently) ---
    # Re-fetch the row outside the ORM identity map to make sure we see the
    # committed state, not a stale in-session attribute.
    row = db.execute(
        text(
            "SELECT email, display_name, avatar_url, password_hash, account_type, is_deleted FROM users WHERE id = :uid"
        ),
        {"uid": str(user_id)},
    ).first()
    assert row is not None, "users row was hard-deleted — must be a tombstone, not gone"

    email, display_name, avatar_url, password_hash, account_type, is_deleted = row
    assert email == f"deleted_{user_id}@deleted.invalid"
    assert display_name is None
    assert avatar_url is None
    assert password_hash is None
    assert account_type == "deleted"
    assert is_deleted is True


def test_delete_account_email_user_blocks_subsequent_requests(client):
    """After tombstone, the original access token returns 401 ``account_deleted``."""
    tokens = _register(client, "del_e2e_block@example.com")

    r = client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))
    assert r.status_code == 204

    # Same token, profile lookup must now reject — downstream gate is
    # ``get_current_user`` checking ``users.is_deleted``.
    r = client.get("/api/v1/account/profile", headers=_auth(tokens["access_token"]))
    assert r.status_code == 401


# ============================================================
# account_type transition ``oauth`` → ``deleted``
# ============================================================


def test_delete_account_google_user_tombstone_state(client, db):
    """OAuth user → DELETE succeeds, account_type transitions to 'deleted'.

    The ``account_type_check`` CHECK must accept the ``'deleted'`` state.
    """
    import repositories.user_repository as user_repo
    from services.auth_service import create_access_token

    user = user_repo.create_user(
        db,
        email="del_e2e_google@example.com",
        account_type="oauth",
    )
    db.commit()
    token = create_access_token(user.id)
    user_id = user.id

    assert user.account_type == "oauth"
    assert user.password_hash is None

    response = client.delete("/api/v1/account", headers=_auth(token))
    assert response.status_code == 204, response.text

    row = db.execute(
        text("SELECT account_type, password_hash, is_deleted, email FROM users WHERE id = :uid"),
        {"uid": str(user_id)},
    ).first()
    account_type, password_hash, is_deleted, email = row
    assert account_type == "deleted"
    assert password_hash is None
    assert is_deleted is True
    assert email == f"deleted_{user_id}@deleted.invalid"


def test_delete_account_apple_user_tombstone_state(client, db):
    """Second OAuth user → DELETE succeeds, account_type transitions to 'deleted'.

    Symmetric to the case above ; kept separate for grep-discoverability.
    """
    import repositories.user_repository as user_repo
    from services.auth_service import create_access_token

    user = user_repo.create_user(
        db,
        email="del_e2e_apple@example.com",
        account_type="oauth",
    )
    db.commit()
    token = create_access_token(user.id)
    user_id = user.id

    response = client.delete("/api/v1/account", headers=_auth(token))
    assert response.status_code == 204, response.text

    row = db.execute(
        text("SELECT account_type, password_hash, is_deleted FROM users WHERE id = :uid"),
        {"uid": str(user_id)},
    ).first()
    account_type, password_hash, is_deleted = row
    assert account_type == "deleted"
    assert password_hash is None
    assert is_deleted is True


# ============================================================
# Idempotency — double-DELETE
# ============================================================


def test_delete_account_double_delete_does_not_crash(client, db):
    """Calling DELETE /account a second time with the same token does NOT
    surface a 5xx and does NOT mutate the tombstone state.

    Two observable behaviors are acceptable from the client's perspective :
    - ``429`` if the in-memory slowapi limiter (``1/hour`` on the route)
      catches the second call before dependency resolution.
    - ``401 account_deleted`` if the rate-limit window has cleared (e.g.
      after a process restart) and the access-token check runs — the
      ``get_current_user`` dependency then rejects on ``users.is_deleted``.

    Both are correct ; what is NOT acceptable is a 500 (which would mean
    the service re-entered and tripped a CHECK or other invariant). The
    inner ``delete_account`` is independently idempotent (covered by
    ``test_delete_account_service_layer_is_idempotent`` below and by
    ``test_account_rgpd.py``).
    """
    import repositories.user_repository as user_repo

    tokens = _register(client, "del_e2e_double@example.com")
    user = user_repo.get_by_email(db, "del_e2e_double@example.com")
    user_id = user.id

    r1 = client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))
    assert r1.status_code == 204

    # Second DELETE with the SAME token — must be 4xx, never 5xx.
    r2 = client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))
    assert r2.status_code in (401, 429), (
        f"Second DELETE returned {r2.status_code} — expected 401 or 429, never 5xx. Body={r2.text!r}"
    )

    # Tombstone state remains unchanged (no flip-flop, no half-rolled-back row).
    row = db.execute(
        text("SELECT account_type, is_deleted, email FROM users WHERE id = :uid"),
        {"uid": str(user_id)},
    ).first()
    account_type, is_deleted, email = row
    assert account_type == "deleted"
    assert is_deleted is True
    assert email == f"deleted_{user_id}@deleted.invalid"


def test_delete_account_service_layer_is_idempotent(client, db):
    """Direct service-layer re-entry on an already-tombstoned user is safe.

    The HTTP layer cannot exercise the second call (token revoked) but the
    admin override path (``POST /admin/users/{id}/anonymize``) and any
    internal cleanup job CAN re-invoke the service. The routine must
    handle that without raising — DELETEs hit empty sets, UPDATEs land on
    the same deterministic anon UUID, the tombstone re-asserts itself.
    """
    import repositories.user_repository as user_repo
    from services.account_service import delete_account

    _register(client, "del_e2e_idempotent@example.com")
    user = user_repo.get_by_email(db, "del_e2e_idempotent@example.com")
    user_id = user.id

    delete_account(db, user)
    db.refresh(user)
    assert user.is_deleted is True
    assert user.account_type == "deleted"

    # Second invocation must NOT raise. CHECKs must accept the unchanged
    # tombstone shape ; UPDATEs are no-ops on already-cleared columns.
    delete_account(db, user)
    db.refresh(user)
    assert user.is_deleted is True
    assert user.account_type == "deleted"
    assert user.email == f"deleted_{user_id}@deleted.invalid"


# ============================================================
# Cross-user isolation (defense-in-depth — already covered in test_account
# but worth a focused E2E here too for grep-discoverability)
# ============================================================


def test_delete_account_only_touches_target_user(client, db):
    """User A's DELETE does NOT tombstone user B's row."""
    import repositories.user_repository as user_repo

    tokens_a = _register(client, "del_e2e_iso_a@example.com")
    _register(client, "del_e2e_iso_b@example.com")
    user_b = user_repo.get_by_email(db, "del_e2e_iso_b@example.com")
    b_id = user_b.id

    r = client.delete("/api/v1/account", headers=_auth(tokens_a["access_token"]))
    assert r.status_code == 204

    row = db.execute(
        text("SELECT account_type, is_deleted, email FROM users WHERE id = :uid"),
        {"uid": str(b_id)},
    ).first()
    account_type, is_deleted, email = row
    assert account_type == "oauth"  # NOT 'deleted' — user B was untouched
    assert is_deleted is False
    assert email == "del_e2e_iso_b@example.com"


# ============================================================
# Regression guard — the bug itself
# ============================================================


def test_db_actually_enforces_users_account_type_check_constraint(db):
    """Direct guard : the test DB schema MUST enforce ``account_type_check``.

    Without this assertion, a future refactor that drops the ORM mirror or
    changes the create_all path could silently let an invalid
    ``account_type`` through — the DELETE /account E2E tests would pass on
    a permissive test schema while production rejects the same write. We
    assert the constraint fires here to make that drift loud.
    """
    import pytest
    from sqlalchemy.exc import IntegrityError

    bogus_id = str(uuid.uuid4())

    def _try_insert_bogus() -> None:
        db.execute(
            text(
                "INSERT INTO users "
                "(id, email, support_id, account_type, is_deleted, "
                "gift_card_redeemed_ytd_cents) "
                "VALUES (:id, :email, :sid, 'martian', false, 0)"
            ),
            {
                "id": bogus_id,
                "email": f"bogus_{bogus_id}@example.com",
                "sid": f"RTS-{uuid.uuid4().hex[:6].upper()}",
            },
        )
        db.flush()

    with pytest.raises(IntegrityError) as exc_info:
        _try_insert_bogus()
    msg = str(exc_info.value.orig).lower()
    assert "account_type_check" in msg or "check constraint" in msg, (
        "INSERT with account_type='martian' raised IntegrityError but not "
        "on account_type_check / generic check — investigate. The ORM "
        f"mirror is expected to surface this CHECK. Got: {msg!r}"
    )
    db.rollback()
