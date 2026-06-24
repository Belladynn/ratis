"""HSP3 — tests des 5 anomaly flags M4.

Tests via la fixture ``spin_up_migrated_db`` HSP2 (DB jetable migrée).
Chaque test seed un état précis dans ``db_write_approvals`` + ``db_change_log``,
puis vérifie le verdict du flag.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

from ._alembic_fixture import spin_up_migrated_db


@pytest.fixture(scope="module")
def flags_db_url():
    yield from spin_up_migrated_db(prefix="ratis_hsp3_flags")


@pytest.fixture
def conn(flags_db_url):
    """Fresh connection that wipes pipeline + CAB state between tests."""
    eng = create_engine(flags_db_url)
    try:
        with eng.begin() as c:
            c.execute(text("DELETE FROM db_write_approvals"))
            c.execute(text("DELETE FROM cabecoin_transactions"))
        with eng.begin() as c:
            yield c
    finally:
        eng.dispose()


def _insert_approval(
    conn, *, status, procedure, amount_cents=100, user_id=None, money_tier="cab", created_offset_days=0
):
    sid = uuid.uuid4()
    user_id = user_id or str(uuid.uuid4())
    created_at = datetime.now(UTC) - timedelta(days=created_offset_days)
    payload_str = json.dumps(
        {
            "procedure": procedure,
            "mode": "execute",
            "money_tier": money_tier,
            "args": {"amount_cents": amount_cents, "user_id": user_id},
        }
    )
    conn.execute(
        text(
            "INSERT INTO db_write_approvals "
            "(submission_id, mode, payload, status, resume_url, created_at, decided_at) "
            "VALUES (:sid, 'execute', CAST(:p AS jsonb), :s, 'http://x.invalid', :c, :c)"
        ),
        {
            "sid": sid,
            "p": payload_str,
            "s": status,
            "c": created_at,
        },
    )
    return sid, user_id


def test_first_use_of_procedure_true_when_never_approved(conn):
    from ratis_core.human_anomaly_flags import first_use_of_procedure

    assert first_use_of_procedure(conn, "support_credit_cab") is True


def test_first_use_of_procedure_false_when_approved_once(conn):
    from ratis_core.human_anomaly_flags import first_use_of_procedure

    _insert_approval(conn, status="approved", procedure="support_credit_cab")
    assert first_use_of_procedure(conn, "support_credit_cab") is False


def test_amount_above_p95_true_when_amount_exceeds_p95(conn):
    from ratis_core.human_anomaly_flags import amount_above_p95

    # Seed 20 approvals amount=100, 1 outlier amount=10000.
    for _ in range(20):
        _insert_approval(conn, status="approved", procedure="x", amount_cents=100)
    # Le p95 sera environ 100 ; un nouveau à 10000 dépasse largement.
    assert amount_above_p95(conn, "x", current_amount_cents=10000) is True


def test_amount_above_p95_false_when_amount_at_median(conn):
    from ratis_core.human_anomaly_flags import amount_above_p95

    for _ in range(20):
        _insert_approval(conn, status="approved", procedure="x", amount_cents=100)
    assert amount_above_p95(conn, "x", current_amount_cents=100) is False


def test_amount_above_p95_false_when_no_history(conn):
    from ratis_core.human_anomaly_flags import amount_above_p95

    assert amount_above_p95(conn, "y", current_amount_cents=99999) is False


def test_user_repeat_in_24h_true_when_more_than_3(conn):
    from ratis_core.human_anomaly_flags import user_repeat_in_24h

    uid = str(uuid.uuid4())
    for _ in range(4):
        _insert_approval(conn, status="approved", procedure="x", user_id=uid)
    assert user_repeat_in_24h(conn, uid) is True


def test_user_repeat_in_24h_false_below_threshold(conn):
    from ratis_core.human_anomaly_flags import user_repeat_in_24h

    uid = str(uuid.uuid4())
    for _ in range(2):
        _insert_approval(conn, status="approved", procedure="x", user_id=uid)
    assert user_repeat_in_24h(conn, uid) is False


def test_approaching_daily_cap_true_when_sum_close_to_warn(conn):
    """Seuil warn=20000 (HSP2 cap). Avec 18k déjà approvés today + 5k pending,
    le total 23k > 20k → flag true."""
    from ratis_core.human_anomaly_flags import approaching_daily_cap

    for _ in range(18):
        _insert_approval(
            conn,
            status="approved",
            procedure="x",
            amount_cents=1000,
            money_tier="cab",
        )
    assert approaching_daily_cap(conn, money_tier="cab", current_amount_cents=5000) is True


def test_approaching_daily_cap_false_when_below(conn):
    from ratis_core.human_anomaly_flags import approaching_daily_cap

    for _ in range(5):
        _insert_approval(
            conn,
            status="approved",
            procedure="x",
            amount_cents=1000,
            money_tier="cab",
        )
    assert approaching_daily_cap(conn, money_tier="cab", current_amount_cents=1000) is False


def test_proposed_outside_business_hours_true_at_3am(monkeypatch):
    from datetime import datetime as _dt

    import ratis_core.human_anomaly_flags as mod
    from ratis_core.human_anomaly_flags import proposed_outside_business_hours

    class FakeDT:
        @classmethod
        def now(cls, tz=None):
            return _dt(2026, 5, 21, 3, 0, 0, tzinfo=tz)

    monkeypatch.setattr(mod, "_dt_now", lambda tz: _dt(2026, 5, 21, 3, 0, 0, tzinfo=tz))
    assert proposed_outside_business_hours() is True


def test_proposed_outside_business_hours_false_at_14(monkeypatch):
    from datetime import datetime as _dt

    import ratis_core.human_anomaly_flags as mod
    from ratis_core.human_anomaly_flags import proposed_outside_business_hours

    monkeypatch.setattr(mod, "_dt_now", lambda tz: _dt(2026, 5, 21, 14, 0, 0, tzinfo=tz))
    assert proposed_outside_business_hours() is False


def _set_caps_enforced(conn, enforced: bool) -> None:
    """Flip ``app_settings.db_pipeline_caps.caps_enforced``."""
    conn.execute(
        text(
            "UPDATE app_settings "
            "SET data = jsonb_set(data, '{caps_enforced}', CAST(:v AS jsonb)) "
            "WHERE section = 'db_pipeline_caps'"
        ),
        {"v": "true" if enforced else "false"},
    )


def _insert_cab_credit(conn, amount: int) -> None:
    """Insert a credit row in the 24h window. ``user_id`` NULL (FK SET NULL)
    avoids needing a real user row ; the global warn sum is user-agnostic."""
    conn.execute(
        text(
            "INSERT INTO cabecoin_transactions "
            "(direction, amount, reason, created_at) "
            "VALUES ('credit', :a, 'admin_adjustment', now())"
        ),
        {"a": amount},
    )


def test_caps_already_warning_false_when_caps_dormant(conn):
    """Caps not enforced → False even with a 24h cumul above the warn seuil."""
    from ratis_core.human_anomaly_flags import caps_already_warning

    _set_caps_enforced(conn, False)
    _insert_cab_credit(conn, 25000)  # > 20k warn, but caps dormant
    assert caps_already_warning(conn) is False


def test_caps_already_warning_false_when_cumul_below_warn(conn):
    from ratis_core.human_anomaly_flags import caps_already_warning

    _set_caps_enforced(conn, True)
    _insert_cab_credit(conn, 5000)  # well below 20k warn
    assert caps_already_warning(conn) is False


def test_caps_already_warning_true_when_cumul_above_warn(conn):
    from ratis_core.human_anomaly_flags import caps_already_warning

    _set_caps_enforced(conn, True)
    _insert_cab_credit(conn, 21000)  # > 20k warn
    assert caps_already_warning(conn) is True


def test_compute_flags_returns_dict_of_6_keys(conn):
    from ratis_core.human_anomaly_flags import compute_flags

    flags = compute_flags(
        conn,
        procedure="x",
        money_tier="cab",
        user_id=str(uuid.uuid4()),
        current_amount_cents=100,
    )
    expected_keys = {
        "first_use_of_procedure",
        "amount_above_p95",
        "user_repeat_in_24h",
        "approaching_daily_cap",
        "proposed_outside_business_hours",
        "caps_already_warning",
    }
    assert set(flags.keys()) == expected_keys
    for v in flags.values():
        assert isinstance(v, bool)
