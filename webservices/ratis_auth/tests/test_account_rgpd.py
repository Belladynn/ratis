"""RGPD anonymize completeness tests — audit F-AU-3.

Validates that ``delete_account`` :

1. Anonymizes behavioral / event-tracking tables that previously kept the
   real user_id inline (achievements, reward_events, missions, battlepass,
   community_*, mystery, label_sessions, xp_*, name_resolutions, etc.) by
   replacing user_id with a deterministic per-user anon UUID.

2. Anonymizes NEVER-PURGE financial tables (cabecoin_transactions,
   cashback_transactions, cashback_withdrawals, gift_card_orders) by
   replacing user_id with the static anon sentinel
   ``00000000-0000-0000-0000-000000000001`` — preserves the row (legal
   retention) while breaking cross-table per-user correlation.

3. DELETEs purely-individual tables with no analytics value
   (user_savings_snapshot, user_xp_balance, notification_outbox).

4. Is fully idempotent — re-calling ``delete_account(user)`` after the
   first call must not raise and must leave the same end-state.

5. Does not affect rows owned by other users.
"""

from __future__ import annotations

import uuid

import pytest
from _auth_helpers import oauth_signup
from ratis_core.anonymize import ANON_SENTINEL_USER_ID, anonymize_user_id
from sqlalchemy import text


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, email: str, password: str = "password123") -> dict:
    """Mint a user + tokens via OAuth. ``password`` kept for call-site
    compatibility but ignored — Ratis is OAuth-only."""
    return oauth_signup(client, email)


# ============================================================
# Seed helpers — minimal rows in each behavioral table
# ============================================================


def _seed_full_user_activity(db, user_id: uuid.UUID, peer_user_id: uuid.UUID | None = None) -> dict:
    """Insert one row per table touched by the new delete_account flow.

    Returns a dict mapping table_name -> seeded primary key, so tests can
    assert exact-row level behavior. ``peer_user_id`` (optional) seeds an
    identical row owned by a different user — used to verify the deletion
    is scoped to the target user only.
    """
    uid = str(user_id)
    peer = str(peer_user_id) if peer_user_id else None

    # --- achievements catalog (needs an Achievement row to attach to) ---
    code_seed = f"rgpd_seed_{uuid.uuid4().hex[:8]}"
    ach_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO achievements "
            "(id, code, label, description, icon, rarity, category, "
            "trigger_type, target_value, cab_reward) "
            "VALUES (:id, :code, 'lbl', 'desc', 'ic', 'bronze', 'volume', "
            "'scan_count', 1, 5) "
            "ON CONFLICT (code) DO NOTHING"
        ),
        {"id": ach_id, "code": code_seed},
    )

    # Resolve back the id in case ON CONFLICT skipped insertion (re-seed run).
    ach_id = db.execute(
        text("SELECT id FROM achievements WHERE code = :c"),
        {"c": code_seed},
    ).scalar()

    user_ach_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO user_achievements "
            "(id, user_id, achievement_id, unlocked_at, cab_granted, "
            "trigger_event) VALUES "
            "(:id, :uid, :aid, now(), 5, '{}'::jsonb)"
        ),
        {"id": user_ach_id, "uid": uid, "aid": ach_id},
    )

    # --- reward_events ---
    re_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO reward_events "
            "(id, user_id, action_type, quantity, idempotency_key, status) "
            "VALUES (:id, :uid, 'scan_accepted', 1, :key, 'processed')"
        ),
        {"id": re_id, "uid": uid, "key": f"k_{re_id}"},
    )

    # --- cabecoin_transactions (sentinel target) ---
    cab_tx_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO cabecoin_transactions "
            "(id, user_id, direction, amount, reason) VALUES "
            "(:id, :uid, 'credit', 5, 'receipt_scan')"
        ),
        {"id": cab_tx_id, "uid": uid},
    )

    # --- notification_outbox (DELETE target) ---
    nox_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO notification_outbox (id, user_id, type, data) VALUES (:id, :uid, 'test', '{}'::jsonb)"),
        {"id": nox_id, "uid": uid},
    )

    # --- user_savings_snapshot (DELETE target) ---
    db.execute(
        text(
            "INSERT INTO user_savings_snapshot (user_id, lifetime_savings_cents) "
            "VALUES (:uid, 1234) ON CONFLICT (user_id) DO NOTHING"
        ),
        {"uid": uid},
    )

    # --- user_xp_balance (DELETE target) ---
    db.execute(
        text("INSERT INTO user_xp_balance (user_id, balance) VALUES (:uid, 100) ON CONFLICT (user_id) DO NOTHING"),
        {"uid": uid},
    )

    # --- xp_transactions ---
    xp_tx_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO xp_transactions (id, user_id, amount, reason) VALUES (:id, :uid, 10, 'receipt_scan')"),
        {"id": xp_tx_id, "uid": uid},
    )

    # --- peer user activity for IDOR check ---
    if peer:
        db.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "(id, user_id, direction, amount, reason) VALUES "
                "(:id, :uid, 'credit', 9, 'receipt_scan')"
            ),
            {"id": str(uuid.uuid4()), "uid": peer},
        )

    db.commit()

    return {
        "user_achievement_id": user_ach_id,
        "achievement_id": ach_id,
        "reward_event_id": re_id,
        "cabecoin_tx_id": cab_tx_id,
        "notification_outbox_id": nox_id,
        "xp_tx_id": xp_tx_id,
    }


# ============================================================
# Anonymize completeness — per-table policy assertions
# ============================================================


def test_delete_account_anonymizes_user_achievements(client, db):
    """user_achievements.user_id → per-user anon UUID (preserves analytics)."""
    import repositories.user_repository as user_repo

    tokens = _register(client, "rgpd_ua@example.com")
    user = user_repo.get_by_email(db, "rgpd_ua@example.com")
    user_real_id = user.id

    seeded = _seed_full_user_activity(db, user_real_id)

    r = client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))
    assert r.status_code == 204

    # Row preserved (NOT deleted — analytics value).
    row = db.execute(
        text("SELECT user_id FROM user_achievements WHERE id = :id"),
        {"id": seeded["user_achievement_id"]},
    ).first()
    assert row is not None

    stored_user_id = row[0]
    # user_id no longer the real one
    assert stored_user_id != user_real_id
    # user_id IS the deterministic anon UUID
    salt = "test-rgpd-salt-fixture-fixed-value"  # mirrors conftest setting
    expected = anonymize_user_id(user_real_id, salt)
    assert stored_user_id == expected


def test_delete_account_anonymizes_reward_events(client, db):
    """reward_events.user_id → per-user anon UUID."""
    import repositories.user_repository as user_repo

    tokens = _register(client, "rgpd_re@example.com")
    user = user_repo.get_by_email(db, "rgpd_re@example.com")
    user_real_id = user.id
    seeded = _seed_full_user_activity(db, user_real_id)

    client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))

    row = db.execute(
        text("SELECT user_id FROM reward_events WHERE id = :id"),
        {"id": seeded["reward_event_id"]},
    ).first()
    assert row is not None
    salt = "test-rgpd-salt-fixture-fixed-value"
    assert row[0] == anonymize_user_id(user_real_id, salt)


def test_delete_account_sentinels_cabecoin_transactions(client, db):
    """cabecoin_transactions (NEVER-PURGE financial) → static anon sentinel."""
    import repositories.user_repository as user_repo

    tokens = _register(client, "rgpd_cab@example.com")
    user = user_repo.get_by_email(db, "rgpd_cab@example.com")
    user_real_id = user.id
    seeded = _seed_full_user_activity(db, user_real_id)

    client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))

    row = db.execute(
        text("SELECT user_id FROM cabecoin_transactions WHERE id = :id"),
        {"id": seeded["cabecoin_tx_id"]},
    ).first()
    assert row is not None
    assert row[0] == ANON_SENTINEL_USER_ID


def test_delete_account_deletes_notification_outbox(client, db):
    """notification_outbox rows are DELETEd (PII content, no analytics value)."""
    import repositories.user_repository as user_repo

    tokens = _register(client, "rgpd_nox@example.com")
    user = user_repo.get_by_email(db, "rgpd_nox@example.com")
    user_real_id = user.id
    seeded = _seed_full_user_activity(db, user_real_id)

    client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))

    count = db.execute(
        text("SELECT COUNT(*) FROM notification_outbox WHERE id = :id"),
        {"id": seeded["notification_outbox_id"]},
    ).scalar()
    assert count == 0


def test_delete_account_deletes_user_savings_snapshot(client, db):
    """user_savings_snapshot is DELETEd (per-user materialized, no analytics)."""
    import repositories.user_repository as user_repo

    tokens = _register(client, "rgpd_uss@example.com")
    user = user_repo.get_by_email(db, "rgpd_uss@example.com")
    user_real_id = user.id
    _seed_full_user_activity(db, user_real_id)

    client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))

    count = db.execute(
        text("SELECT COUNT(*) FROM user_savings_snapshot WHERE user_id = :uid"),
        {"uid": str(user_real_id)},
    ).scalar()
    assert count == 0


def test_delete_account_deletes_user_xp_balance(client, db):
    """user_xp_balance is DELETEd (per-user materialized, no analytics)."""
    import repositories.user_repository as user_repo

    tokens = _register(client, "rgpd_uxb@example.com")
    user = user_repo.get_by_email(db, "rgpd_uxb@example.com")
    user_real_id = user.id
    _seed_full_user_activity(db, user_real_id)

    client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))

    count = db.execute(
        text("SELECT COUNT(*) FROM user_xp_balance WHERE user_id = :uid"),
        {"uid": str(user_real_id)},
    ).scalar()
    assert count == 0


def test_delete_account_anonymizes_xp_transactions(client, db):
    """xp_transactions.user_id → per-user anon UUID (history retention)."""
    import repositories.user_repository as user_repo

    tokens = _register(client, "rgpd_xpt@example.com")
    user = user_repo.get_by_email(db, "rgpd_xpt@example.com")
    user_real_id = user.id
    seeded = _seed_full_user_activity(db, user_real_id)

    client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))

    row = db.execute(
        text("SELECT user_id FROM xp_transactions WHERE id = :id"),
        {"id": seeded["xp_tx_id"]},
    ).first()
    assert row is not None
    salt = "test-rgpd-salt-fixture-fixed-value"
    assert row[0] == anonymize_user_id(user_real_id, salt)


def test_delete_account_clears_user_identities(client, db):
    """user_identities rows are hard-DELETEd (PII: OAuth subject + email).

    ``delete_account`` anonymizes the ``users`` row in place, so the
    ``user_identities.user_id`` FK ``ON DELETE CASCADE`` never fires —
    the identity row (real OAuth provider_id + email) must be explicitly
    purged in the Tier-1 hard-DELETE block.
    """
    import repositories.user_repository as user_repo
    from ratis_core.models import UserIdentity

    tokens = _register(client, "rgpd_identity@example.com")
    user = user_repo.get_by_email(db, "rgpd_identity@example.com")
    user_real_id = user.id

    client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))

    assert db.query(UserIdentity).filter_by(user_id=user_real_id).count() == 0


# ============================================================
# Cross-cutting properties — analytics preserved, idempotent, scoped
# ============================================================


def test_delete_account_anon_uuid_groups_per_user(client, db):
    """Two rows for the SAME user across two tables must map to the SAME
    anon UUID — preserving per-user analytics grouping after anonymization.
    """
    import repositories.user_repository as user_repo

    tokens = _register(client, "rgpd_group@example.com")
    user = user_repo.get_by_email(db, "rgpd_group@example.com")
    user_real_id = user.id
    seeded = _seed_full_user_activity(db, user_real_id)

    client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))

    ua_uid = db.execute(
        text("SELECT user_id FROM user_achievements WHERE id = :id"),
        {"id": seeded["user_achievement_id"]},
    ).scalar()
    re_uid = db.execute(
        text("SELECT user_id FROM reward_events WHERE id = :id"),
        {"id": seeded["reward_event_id"]},
    ).scalar()
    xp_uid = db.execute(
        text("SELECT user_id FROM xp_transactions WHERE id = :id"),
        {"id": seeded["xp_tx_id"]},
    ).scalar()

    # All three rows (different analytics tables) must share the same anon UUID.
    assert ua_uid == re_uid == xp_uid


def test_delete_account_idempotent(client, db):
    """Second call to delete_account on the same user must not raise."""
    import repositories.user_repository as user_repo
    from services.account_service import delete_account

    tokens = _register(client, "rgpd_idem@example.com")
    user = user_repo.get_by_email(db, "rgpd_idem@example.com")
    user_real_id = user.id
    _seed_full_user_activity(db, user_real_id)

    # First call — via HTTP route.
    client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))

    # Second call — directly on the service layer (route would 401 on the
    # tombstone-user via auth dep). Must complete cleanly.
    db.refresh(user)
    delete_account(db, user)

    # Tombstone state still consistent.
    db.refresh(user)
    assert user.is_deleted is True
    assert user.email == f"deleted_{user_real_id}@deleted.invalid"


def test_delete_account_does_not_affect_other_users(client, db):
    """user A's deletion must not anonymize / delete any of user B's rows."""
    import repositories.user_repository as user_repo

    tokens_a = _register(client, "rgpd_scope_a@example.com")
    _register(client, "rgpd_scope_b@example.com")
    user_a = user_repo.get_by_email(db, "rgpd_scope_a@example.com")
    user_b = user_repo.get_by_email(db, "rgpd_scope_b@example.com")

    _seed_full_user_activity(db, user_a.id, peer_user_id=user_b.id)

    # Capture B's row count BEFORE deletion.
    b_cab_before = db.execute(
        text("SELECT COUNT(*) FROM cabecoin_transactions WHERE user_id = :uid"),
        {"uid": str(user_b.id)},
    ).scalar()
    assert b_cab_before == 1

    client.delete("/api/v1/account", headers=_auth(tokens_a["access_token"]))

    # B's row unchanged.
    b_cab_after = db.execute(
        text("SELECT COUNT(*) FROM cabecoin_transactions WHERE user_id = :uid"),
        {"uid": str(user_b.id)},
    ).scalar()
    assert b_cab_after == 1


def test_delete_account_user_tombstone_account_type_deleted_accepted(client, db):
    """After delete_account, users.account_type = 'deleted' must be accepted
    by the (production) ``account_type_check`` constraint. Regression guard
    against the tombstone state being dropped from the CHECK whitelist —
    delete_account would otherwise crash on commit in any environment
    running the actual migrations.
    """
    import repositories.user_repository as user_repo

    tokens = _register(client, "rgpd_account_type_deleted@example.com")
    user = user_repo.get_by_email(db, "rgpd_account_type_deleted@example.com")
    _seed_full_user_activity(db, user.id)

    r = client.delete("/api/v1/account", headers=_auth(tokens["access_token"]))
    assert r.status_code == 204
    db.refresh(user)
    assert user.account_type == "deleted"
    # Sanity : the ORM mirror of ``account_type_check`` is applied to the
    # test DB schema (Pattern A) — the assignment must NOT have raised,
    # which the 204 above already verifies.


def test_delete_account_requires_salt(client, db, monkeypatch):
    """If RGPD_ANONYMIZE_SALT is missing/blank, delete_account must fail
    fast rather than silently producing weak (un-salted) anon UUIDs.
    """
    import repositories.user_repository as user_repo
    from services.account_service import delete_account

    _register(client, "rgpd_no_salt@example.com")
    user = user_repo.get_by_email(db, "rgpd_no_salt@example.com")
    _seed_full_user_activity(db, user.id)

    monkeypatch.delenv("RGPD_ANONYMIZE_SALT", raising=False)

    with pytest.raises(RuntimeError, match="RGPD_ANONYMIZE_SALT is not set"):
        delete_account(db, user)


def test_delete_account_anon_uuid_differs_per_user(client, db):
    """Two distinct users → two distinct anon UUIDs. Sanity check on
    the salt being mixed correctly per-user (no global mask).
    """
    import repositories.user_repository as user_repo
    from services.account_service import delete_account

    _register(client, "rgpd_distinct_a@example.com")
    _register(client, "rgpd_distinct_b@example.com")
    user_a = user_repo.get_by_email(db, "rgpd_distinct_a@example.com")
    user_b = user_repo.get_by_email(db, "rgpd_distinct_b@example.com")

    # Seed identical activity for both users.
    s_a = _seed_full_user_activity(db, user_a.id)
    s_b = _seed_full_user_activity(db, user_b.id)

    delete_account(db, user_a)
    delete_account(db, user_b)

    a_uid = db.execute(
        text("SELECT user_id FROM user_achievements WHERE id = :id"),
        {"id": s_a["user_achievement_id"]},
    ).scalar()
    b_uid = db.execute(
        text("SELECT user_id FROM user_achievements WHERE id = :id"),
        {"id": s_b["user_achievement_id"]},
    ).scalar()
    assert a_uid != b_uid
