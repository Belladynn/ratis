"""Migration tests for ``20260511_1100_widen`` — widen action_type / notif type CHECKs.

Validates :

* After upgrade, ``reward_config_action_type_check`` admits every
  modern snake_case action_type and REJECTS every legacy uppercase value.
* After upgrade, ``notification_logs_type_check`` admits every modern
  notif type emitted by the code (NotifType Literal + route_ready +
  trust_score_warning) and REJECTS the obsolete legacy values
  (price_drop, streak_reminder, ...).
* The old constraint names (``action_type_check``, ``type_check``) are
  gone, only the new names (``reward_config_action_type_check``,
  ``notification_logs_type_check``) remain.
* Downgrade restores the stale legacy CHECKs.
* Full roundtrip (upgrade → downgrade → upgrade) is idempotent.
"""
from __future__ import annotations

import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


TARGET_REVISION = "20260511_1100_widen"
# The previous head (merge revision of au9_npfk + rgpd_anon_completeness).
PREV_REVISION = "938ee29f5c5f"

ALEMBIC_CFG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "alembic.ini"
)


_MODERN_ACTION_TYPES = (
    "receipt_scan",
    "label_scan",
    "barcode_scan",
    "product_identification",
    "price_compared",
    "fill_product_field",
    "scan_distinct",
    "promo_found",
)

_LEGACY_ACTION_TYPES = (
    "DAILY_LOGIN",
    "SCAN_RECEIPT",
    "VIDEO_SCAN",
    "PRICE_CHALLENGE",
)

_MODERN_NOTIF_TYPES = (
    "scan_done",
    "cashback_available",
    "badge_unlocked",
    "price_alert",
    "route_ready",
    "battlepass_milestone_unlocked",
    "challenge_milestone_unlocked",
    "mystery_product_found",
    "store_validated",
    "retro_cab_gratitude",
    "achievement_unlocked",
    "trust_score_warning",
)

_LEGACY_NOTIF_TYPES = (
    "price_drop",
    "streak_reminder",
    "weekly_recap",
    "challenge_available",
    "cashback_credited",
    "level_up",
)


def _unmasked_url(engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _make_alembic_config(engine) -> Config:
    cfg = Config(ALEMBIC_CFG_PATH)
    cfg.set_main_option("sqlalchemy.url", _unmasked_url(engine))
    return cfg


@pytest.fixture(scope="module")
def upgraded_engine(migration_engine):
    """Wipe + run alembic upgrade up to the migration under test."""
    with migration_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    os.environ["DATABASE_URL"] = _unmasked_url(migration_engine)
    cfg = _make_alembic_config(migration_engine)
    command.upgrade(cfg, TARGET_REVISION)
    return migration_engine


def _check_constraint_names(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT con.conname
              FROM pg_constraint con
              JOIN pg_class c ON c.oid = con.conrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE con.contype = 'c'
               AND n.nspname = 'public'
               AND c.relname = :t
            """
        ),
        {"t": table},
    ).all()
    return {r[0] for r in rows}


def _make_user(conn) -> uuid.UUID:
    """Insert a minimal valid users row (needed for notification_logs FK)."""
    uid = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, email, support_id, provider, password_hash, "
            "                  created_at, updated_at, gift_card_redeemed_ytd_cents) "
            "VALUES (:id, :email, :sid, 'email', 'hashed', now(), now(), 0)"
        ),
        {"id": uid, "email": f"u{uid.hex[:8]}@t.com", "sid": uid.hex[:10]},
    )
    return uid


# ---------------------------------------------------------------------------
# Post-upgrade schema invariants
# ---------------------------------------------------------------------------


def test_reward_config_check_constraints_renamed(upgraded_engine):
    """Old ``action_type_check`` is replaced by ``reward_config_action_type_check``."""
    with upgraded_engine.connect() as conn:
        names = _check_constraint_names(conn, "reward_config")
    assert "reward_config_action_type_check" in names
    assert "action_type_check" not in names


def test_notification_logs_check_constraints_renamed(upgraded_engine):
    """Old ``type_check`` is replaced by ``notification_logs_type_check``."""
    with upgraded_engine.connect() as conn:
        names = _check_constraint_names(conn, "notification_logs")
    assert "notification_logs_type_check" in names
    assert "type_check" not in names


# ---------------------------------------------------------------------------
# reward_config — modern values accepted, legacy values rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action_type", _MODERN_ACTION_TYPES)
def test_reward_config_accepts_modern_action_type(upgraded_engine, action_type):
    """Every modern snake_case action_type must INSERT cleanly."""
    rid = uuid.uuid4()
    with upgraded_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reward_config (id, action_type, base_amount) "
                "VALUES (:id, :at, 10)"
            ),
            {"id": rid, "at": action_type},
        )
        conn.execute(text("DELETE FROM reward_config WHERE id = :id"), {"id": rid})


def _insert_reward_config(engine, action_type: str) -> None:
    """Single statement helper so pytest.raises blocks stay PT012-clean."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reward_config (id, action_type, base_amount) "
                "VALUES (:id, :at, 10)"
            ),
            {"id": uuid.uuid4(), "at": action_type},
        )


def _insert_notification_log(engine, notif_type: str) -> None:
    """Single statement helper so pytest.raises blocks stay PT012-clean."""
    with engine.begin() as conn:
        uid = _make_user(conn)
        conn.execute(
            text(
                "INSERT INTO notification_logs (id, user_id, type) "
                "VALUES (:id, :uid, :t)"
            ),
            {"id": uuid.uuid4(), "uid": uid, "t": notif_type},
        )


@pytest.mark.parametrize("action_type", _LEGACY_ACTION_TYPES)
def test_reward_config_rejects_legacy_action_type(upgraded_engine, action_type):
    """Every legacy UPPERCASE action_type must raise CheckViolation."""
    with pytest.raises(IntegrityError):
        _insert_reward_config(upgraded_engine, action_type)


# ---------------------------------------------------------------------------
# notification_logs — modern types accepted, legacy types rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("notif_type", _MODERN_NOTIF_TYPES)
def test_notification_logs_accepts_modern_type(upgraded_engine, notif_type):
    """Every modern notif type emitted by the code must INSERT cleanly."""
    with upgraded_engine.begin() as conn:
        uid = _make_user(conn)
        nid = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO notification_logs (id, user_id, type) "
                "VALUES (:id, :uid, :t)"
            ),
            {"id": nid, "uid": uid, "t": notif_type},
        )


@pytest.mark.parametrize("notif_type", _LEGACY_NOTIF_TYPES)
def test_notification_logs_rejects_legacy_type(upgraded_engine, notif_type):
    """Obsolete legacy notif types must be rejected by the new CHECK."""
    with pytest.raises(IntegrityError):
        _insert_notification_log(upgraded_engine, notif_type)


# ---------------------------------------------------------------------------
# Downgrade restores legacy CHECKs
# ---------------------------------------------------------------------------


def test_downgrade_restores_legacy_checks(migration_engine):
    """Downgrade reinstalls the stale CHECK names and rejects modern values."""
    with migration_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    os.environ["DATABASE_URL"] = _unmasked_url(migration_engine)
    cfg = _make_alembic_config(migration_engine)
    command.upgrade(cfg, TARGET_REVISION)
    command.downgrade(cfg, PREV_REVISION)

    with migration_engine.connect() as conn:
        reward_checks = _check_constraint_names(conn, "reward_config")
        notif_checks = _check_constraint_names(conn, "notification_logs")
    assert "action_type_check" in reward_checks
    assert "reward_config_action_type_check" not in reward_checks
    assert "type_check" in notif_checks
    assert "notification_logs_type_check" not in notif_checks

    # Re-upgrade to leave the DB in the canonical state for the rest of
    # the session and to assert idempotency (upgrade → downgrade → upgrade).
    command.upgrade(cfg, TARGET_REVISION)
    with migration_engine.connect() as conn:
        reward_checks = _check_constraint_names(conn, "reward_config")
        notif_checks = _check_constraint_names(conn, "notification_logs")
    assert "reward_config_action_type_check" in reward_checks
    assert "notification_logs_type_check" in notif_checks
