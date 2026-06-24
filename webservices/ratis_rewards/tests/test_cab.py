"""
Tests for the CAB bloc: award_cab, debit_cab, GET /rewards/cab/balance,
POST /rewards/events/action, check_missions_progress.

Phase B (PR #325) replaced the legacy ``/rewards/events/scan_accepted``
endpoint with the generic ``/rewards/events/action`` route. The mapping
from V0 ``scan_type`` to V1 ``action_type`` is :

    receipt          → receipt_scan
    electronic_label → label_scan
    manual           → product_identification

The legacy endpoint is gone — the only test that still exercises it
(``test_legacy_scan_accepted_endpoint_404``) lives in
``test_phase_b_trigger_action.py`` and asserts a 404.
"""

from __future__ import annotations

import re
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from main import app
from ratis_core.database import get_db
from repositories.cab_repository import (
    InsufficientBalance,
    award_cab,
    debit_cab,
    get_balance,
    get_next_milestone_delta,
)
from repositories.missions_repository import check_missions_progress
from sqlalchemy import text

from tests.conftest import make_milestone, make_mission, make_season, make_user

# ===========================================================================
# award_cab
# ===========================================================================


class TestAwardCab:
    def test_credits_balance(self, db):
        uid = make_user(db)
        award_cab(db, uid, 100, "receipt_scan")
        db.flush()
        assert get_balance(db, uid) == 100

    def test_accumulates(self, db):
        uid = make_user(db)
        award_cab(db, uid, 50, "receipt_scan")
        award_cab(db, uid, 30, "label_scan")
        db.flush()
        assert get_balance(db, uid) == 80

    def test_rejects_zero_amount(self, db):
        uid = make_user(db)
        with pytest.raises(ValueError, match="amount must be positive"):
            award_cab(db, uid, 0, "receipt_scan")

    def test_rejects_negative_amount(self, db):
        uid = make_user(db)
        with pytest.raises(ValueError, match="amount must be positive"):
            award_cab(db, uid, -10, "receipt_scan")

    def test_rejects_invalid_reason(self, db):
        uid = make_user(db)
        with pytest.raises(ValueError, match="Invalid CAB reason"):
            award_cab(db, uid, 50, "invalid_reason")

    def test_persists_reference_id_and_type(self, db):
        uid = make_user(db)
        ref_id = uuid.uuid4()
        award_cab(db, uid, 50, "receipt_scan", reference_id=ref_id, reference_type="scan")
        db.flush()
        row = db.execute(
            text("SELECT reference_id, reference_type FROM cabecoin_transactions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.reference_id == ref_id
        assert row.reference_type == "scan"

    def test_inserts_transaction_row(self, db):
        uid = make_user(db)
        award_cab(db, uid, 75, "barcode_scan")
        db.flush()
        row = db.execute(
            text("SELECT direction, amount, reason FROM cabecoin_transactions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.direction == "credit"
        assert row.amount == 75
        assert row.reason == "barcode_scan"

    def test_updates_battlepass_progress_when_active_season(self, db):
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        award_cab(db, uid, 200, "receipt_scan")
        db.flush()
        row = db.execute(
            text("SELECT cab_earned_season FROM user_battlepass_progress WHERE user_id = :uid AND season_id = :sid"),
            {"uid": uid, "sid": season_id},
        ).first()
        assert row is not None
        assert row.cab_earned_season == 200

    def test_skips_battlepass_progress_when_no_active_season(self, db):
        uid = make_user(db)
        award_cab(db, uid, 100, "receipt_scan")
        db.flush()
        row = db.execute(
            text("SELECT COUNT(*) AS n FROM user_battlepass_progress WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.n == 0

    def test_accumulates_battlepass_progress(self, db):
        uid = make_user(db)
        make_season(db, is_active=True)
        award_cab(db, uid, 100, "receipt_scan")
        award_cab(db, uid, 50, "label_scan")
        db.flush()
        row = db.execute(
            text("SELECT cab_earned_season FROM user_battlepass_progress WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.cab_earned_season == 150

    def test_uses_provided_season_id_for_bp_progress(self, db):
        """When the caller passes the already-fetched season id, award_cab
        applies the progress UPSERT without re-querying battlepass_seasons
        (RW-10)."""
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        award_cab(db, uid, 200, "receipt_scan", active_season_id=season_id)
        db.flush()
        row = db.execute(
            text("SELECT cab_earned_season FROM user_battlepass_progress WHERE user_id = :uid AND season_id = :sid"),
            {"uid": uid, "sid": season_id},
        ).first()
        assert row is not None
        assert row.cab_earned_season == 200


# ===========================================================================
# debit_cab
# ===========================================================================


class TestDebitCab:
    def test_debits_balance(self, db):
        uid = make_user(db)
        award_cab(db, uid, 200, "receipt_scan")
        debit_cab(db, uid, 80, "shop_purchase")
        db.flush()
        assert get_balance(db, uid) == 120

    def test_raises_on_insufficient(self, db):
        uid = make_user(db)
        award_cab(db, uid, 50, "receipt_scan")
        with pytest.raises(InsufficientBalance):
            debit_cab(db, uid, 100, "shop_purchase")

    def test_inserts_debit_transaction(self, db):
        uid = make_user(db)
        award_cab(db, uid, 200, "receipt_scan")
        debit_cab(db, uid, 60, "cashback_boost_debit")
        db.flush()
        row = db.execute(
            text(
                "SELECT direction, amount, reason FROM cabecoin_transactions "
                "WHERE user_id = :uid AND direction = 'debit'"
            ),
            {"uid": uid},
        ).first()
        assert row.direction == "debit"
        assert row.amount == 60
        assert row.reason == "cashback_boost_debit"

    def test_exact_balance_succeeds(self, db):
        uid = make_user(db)
        award_cab(db, uid, 100, "receipt_scan")
        debit_cab(db, uid, 100, "shop_purchase")
        db.flush()
        assert get_balance(db, uid) == 0

    def test_rejects_zero_amount(self, db):
        uid = make_user(db)
        award_cab(db, uid, 100, "receipt_scan")
        db.flush()
        with pytest.raises(ValueError, match="amount must be positive"):
            debit_cab(db, uid, 0, "shop_purchase")


# ===========================================================================
# get_next_milestone_delta
# ===========================================================================


class TestNextMilestoneDelta:
    def test_returns_delta_to_next_unclaimed_milestone(self, db):
        uid = make_user(db)
        season_id = make_season(db)
        make_milestone(db, season_id=season_id, milestone_number=1, cab_required=200)
        make_milestone(db, season_id=season_id, milestone_number=2, cab_required=500)
        delta = get_next_milestone_delta(db, uid, season_id, cab_earned_season=340)
        assert delta == 160  # 500 - 340

    def test_returns_zero_when_all_milestones_claimed(self, db):
        uid = make_user(db)
        season_id = make_season(db)
        milestone_id = make_milestone(db, season_id=season_id, cab_required=200)
        db.execute(
            text("INSERT INTO user_battlepass_claims (id, user_id, milestone_id) VALUES (:id, :uid, :mid)"),
            {"id": uuid.uuid4(), "uid": uid, "mid": milestone_id},
        )
        db.flush()
        delta = get_next_milestone_delta(db, uid, season_id, cab_earned_season=200)
        assert delta == 0

    def test_skips_already_claimed_milestones(self, db):
        uid = make_user(db)
        season_id = make_season(db)
        milestone1_id = make_milestone(db, season_id=season_id, milestone_number=1, cab_required=200)
        make_milestone(db, season_id=season_id, milestone_number=2, cab_required=500)
        db.execute(
            text("INSERT INTO user_battlepass_claims (id, user_id, milestone_id) VALUES (:id, :uid, :mid)"),
            {"id": uuid.uuid4(), "uid": uid, "mid": milestone1_id},
        )
        db.flush()
        # Milestone 1 (200) is claimed, next unclaimed is milestone 2 (500)
        delta = get_next_milestone_delta(db, uid, season_id, cab_earned_season=300)
        assert delta == 200  # 500 - 300


# ===========================================================================
# check_missions_progress
# ===========================================================================


class TestCheckMissionsProgress:
    def test_creates_user_mission_on_first_action(self, db):
        uid = make_user(db)
        make_mission(db, action_type="receipt_scan", frequency="daily", target_count=3)
        today = date(2026, 4, 10)
        check_missions_progress(db, uid, "receipt_scan", today)
        db.flush()
        row = db.execute(
            text("SELECT current_count, status FROM user_missions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.current_count == 1
        assert row.status == "pending"

    def test_completes_mission_when_target_reached(self, db):
        uid = make_user(db)
        make_mission(db, action_type="receipt_scan", frequency="daily", target_count=1)
        today = date(2026, 4, 10)
        check_missions_progress(db, uid, "receipt_scan", today)
        db.flush()
        row = db.execute(
            text("SELECT status FROM user_missions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.status == "completed"

    def test_increments_existing_user_mission(self, db):
        uid = make_user(db)
        make_mission(db, action_type="receipt_scan", frequency="daily", target_count=5)
        today = date(2026, 4, 10)
        # First action
        check_missions_progress(db, uid, "receipt_scan", today)
        db.flush()
        # Second action
        check_missions_progress(db, uid, "receipt_scan", today)
        db.flush()
        row = db.execute(
            text("SELECT current_count FROM user_missions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.current_count == 2

    def test_does_not_exceed_completed_status(self, db):
        uid = make_user(db)
        make_mission(db, action_type="receipt_scan", frequency="daily", target_count=2)
        today = date(2026, 4, 10)
        check_missions_progress(db, uid, "receipt_scan", today)
        check_missions_progress(db, uid, "receipt_scan", today)  # completes
        check_missions_progress(db, uid, "receipt_scan", today)  # over target
        db.flush()
        row = db.execute(
            text("SELECT current_count, status FROM user_missions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.current_count == 3
        assert row.status == "completed"

    def test_skips_claimed_missions(self, db):
        uid = make_user(db)
        mission_id = make_mission(db, action_type="receipt_scan", frequency="daily", target_count=1)
        today = date(2026, 4, 10)
        # Pre-insert a claimed user_mission (target_count required — no DB default)
        db.execute(
            text(
                "INSERT INTO user_missions "
                "    (id, user_id, mission_id, period_start, current_count, status, target_count) "
                "VALUES (:id, :uid, :mid, :period, 1, 'claimed', 1)"
            ),
            {"id": uuid.uuid4(), "uid": uid, "mid": mission_id, "period": today},
        )
        db.flush()
        check_missions_progress(db, uid, "receipt_scan", today)
        db.flush()
        row = db.execute(
            text("SELECT current_count FROM user_missions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.current_count == 1  # unchanged

    def test_handles_weekly_period_correctly(self, db):
        uid = make_user(db)
        make_mission(db, action_type="receipt_scan", frequency="weekly", target_count=5)
        # Thursday 2026-04-09 → monday is 2026-04-06
        thursday = date(2026, 4, 9)
        check_missions_progress(db, uid, "receipt_scan", thursday)
        db.flush()
        row = db.execute(
            text("SELECT period_start FROM user_missions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.period_start == date(2026, 4, 6)  # monday

    def test_ignores_different_action_type(self, db):
        uid = make_user(db)
        make_mission(db, action_type="label_scan", frequency="daily", target_count=1)
        today = date(2026, 4, 10)
        check_missions_progress(db, uid, "receipt_scan", today)
        db.flush()
        count = db.execute(
            text("SELECT COUNT(*) AS n FROM user_missions WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        assert count == 0

    def test_ignores_inactive_missions(self, db):
        uid = make_user(db)
        make_mission(db, action_type="receipt_scan", frequency="daily", is_active=False)
        today = date(2026, 4, 10)
        check_missions_progress(db, uid, "receipt_scan", today)
        db.flush()
        count = db.execute(
            text("SELECT COUNT(*) AS n FROM user_missions WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        assert count == 0

    def test_variable_increment_accumulates_amount(self, db):
        uid = make_user(db)
        make_mission(db, action_type="price_compared", frequency="daily", target_count=750)
        today = date(2026, 4, 10)
        check_missions_progress(db, uid, "price_compared", today, increment=230)
        db.flush()
        check_missions_progress(db, uid, "price_compared", today, increment=310)
        db.flush()
        row = db.execute(
            text("SELECT current_count, status FROM user_missions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.current_count == 540
        assert row.status == "pending"

    def test_variable_increment_completes_when_target_reached(self, db):
        uid = make_user(db)
        make_mission(db, action_type="price_compared", frequency="daily", target_count=750)
        today = date(2026, 4, 10)
        check_missions_progress(db, uid, "price_compared", today, increment=750)
        db.flush()
        row = db.execute(
            text("SELECT current_count, status FROM user_missions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.current_count == 750
        assert row.status == "completed"

    def test_variable_increment_on_first_action_creates_row(self, db):
        uid = make_user(db)
        make_mission(db, action_type="price_compared", frequency="daily", target_count=750)
        today = date(2026, 4, 10)
        check_missions_progress(db, uid, "price_compared", today, increment=230)
        db.flush()
        row = db.execute(
            text("SELECT current_count, status FROM user_missions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.current_count == 230
        assert row.status == "pending"


# ===========================================================================
# POST /rewards/events/action — generic event endpoint
# ===========================================================================


def _post_action(client, *, user_id, action_type, scan_id=None, idem=None):
    """Helper for the new generic event endpoint."""
    body = {"user_id": str(user_id), "action_type": action_type}
    if idem is None and scan_id is not None:
        idem = str(scan_id)
    if idem is not None:
        body["idempotency_key"] = idem
    if scan_id is not None:
        body["context"] = {"scan_id": str(scan_id)}
    return client.post("/api/v1/rewards/events/action", json=body)


class TestActionEndpoint:
    def test_awards_cab_for_receipt_scan(self, client, db):
        uid = make_user(db)
        resp = _post_action(
            client,
            user_id=uid,
            action_type="receipt_scan",
            scan_id=uuid.uuid4(),
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_returns_422_for_unknown_action_type(self, client):
        # Pydantic Literal whitelist rejects unknown action_types at
        # the route boundary — the service layer never runs.
        resp = client.post(
            "/api/v1/rewards/events/action",
            json={"user_id": str(uuid.uuid4()), "action_type": "unknown_type"},
        )
        assert resp.status_code == 422

    def test_returns_403_without_internal_key(self, raw_client):
        resp = raw_client.post(
            "/api/v1/rewards/events/action",
            json={"user_id": str(uuid.uuid4()), "action_type": "receipt_scan"},
        )
        assert resp.status_code == 403

    def test_cab_persisted_after_scan(self, client, db):
        uid = make_user(db)
        _post_action(
            client,
            user_id=uid,
            action_type="receipt_scan",
            scan_id=uuid.uuid4(),
        )
        balance = get_balance(db, uid)
        assert balance == 20  # cab_per_receipt_scan from settings (V1.x recal)

    def test_all_legacy_scan_action_types_accepted(self, client, db):
        for action_type in ("receipt_scan", "label_scan", "product_identification"):
            uid = make_user(db)
            resp = _post_action(
                client,
                user_id=uid,
                action_type=action_type,
                scan_id=uuid.uuid4(),
            )
            assert resp.status_code == 200, f"Failed for action_type={action_type}"


# ===========================================================================
# GET /rewards/cab/balance
# ===========================================================================


class TestGetCabBalance:
    def test_returns_balance_no_battlepass(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        award_cab(db, uid, 120, "receipt_scan")
        db.commit()
        set_user(uid)
        resp = http.get("/api/v1/rewards/cab/balance")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cab_balance"] == 120
        assert body["battlepass"] is None

    def test_returns_balance_with_battlepass(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True, season_number=1)
        make_milestone(db, season_id=season_id, milestone_number=1, cab_required=500)
        award_cab(db, uid, 200, "receipt_scan")
        db.commit()
        set_user(uid)
        resp = http.get("/api/v1/rewards/cab/balance")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cab_balance"] == 200
        bp = body["battlepass"]
        assert bp is not None
        assert bp["season_number"] == 1
        assert bp["cab_earned_season"] == 200
        assert bp["next_milestone_delta"] == 300  # 500 - 200

    def test_requires_auth(self, db):
        """Without auth override, the endpoint returns 401."""

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as c:
                resp = c.get("/api/v1/rewards/cab/balance")
        finally:
            app.dependency_overrides.pop(get_db, None)
        assert resp.status_code == 401


# ===========================================================================
# Achievements V1 — hook in events_service.handle_action (PR4)
# ===========================================================================


class TestAchievementHookScanAccepted:
    """`events_service.handle_action` must fire `check_achievements` with
    event_type='scan_accepted' for scan-class action_types (receipt_scan /
    label_scan / product_identification). Other action_types must NOT fire
    the hook (no matching trigger in EVENT_TYPE_TO_TRIGGERS).

    Pattern : monkeypatch a wrapper around achievement_service.check_achievements
    so we capture call kwargs without mutating the dispatcher's behaviour.
    """

    def _spy(self, monkeypatch):
        from services import achievement_service

        calls: list[dict] = []
        original = achievement_service.check_achievements

        def wrapper(*args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            return original(*args, **kwargs)

        monkeypatch.setattr(achievement_service, "check_achievements", wrapper)
        return calls

    def test_receipt_scan_triggers_scan_accepted_event(self, client, db, monkeypatch):
        calls = self._spy(monkeypatch)
        uid = make_user(db)
        scan_id = uuid.uuid4()
        resp = _post_action(
            client,
            user_id=uid,
            action_type="receipt_scan",
            scan_id=scan_id,
        )
        assert resp.status_code == 200
        scan_calls = [c for c in calls if c["kwargs"].get("event_type") == "scan_accepted"]
        assert len(scan_calls) == 1
        assert scan_calls[0]["kwargs"].get("user_id") == uid
        assert scan_calls[0]["kwargs"].get("payload") == {"scan_id": str(scan_id)}

    def test_non_scan_action_does_not_trigger_scan_accepted(self, client, db, monkeypatch):
        calls = self._spy(monkeypatch)
        uid = make_user(db)
        resp = _post_action(
            client,
            user_id=uid,
            action_type="fill_product_field",
        )
        assert resp.status_code == 200
        scan_calls = [c for c in calls if c["kwargs"].get("event_type") == "scan_accepted"]
        assert scan_calls == []


class TestAchievementHookBattlepassSeasonParticipated:
    """The first time a user touches the active battlepass season (cab_before
    == 0) `handle_action` must additionally fire the
    `battlepass_season_participated` event. Subsequent events in the same
    season must NOT re-fire it.
    """

    def _spy(self, monkeypatch):
        from services import achievement_service

        calls: list[dict] = []
        original = achievement_service.check_achievements

        def wrapper(*args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            return original(*args, **kwargs)

        monkeypatch.setattr(achievement_service, "check_achievements", wrapper)
        return calls

    def test_first_event_in_active_season_fires(self, client, db, monkeypatch):
        season_id = make_season(db, is_active=True, season_number=1)
        make_milestone(db, season_id=season_id, milestone_number=1, cab_required=500)
        db.commit()

        calls = self._spy(monkeypatch)
        uid = make_user(db)
        resp = _post_action(
            client,
            user_id=uid,
            action_type="receipt_scan",
            scan_id=uuid.uuid4(),
        )
        assert resp.status_code == 200
        bp_calls = [c for c in calls if c["kwargs"].get("event_type") == "battlepass_season_participated"]
        assert len(bp_calls) == 1
        assert bp_calls[0]["kwargs"].get("user_id") == uid
        assert bp_calls[0]["kwargs"]["payload"]["season_id"] == str(season_id)

    def test_second_event_does_not_refire(self, client, db, monkeypatch):
        season_id = make_season(db, is_active=True, season_number=1)
        make_milestone(db, season_id=season_id, milestone_number=1, cab_required=500)
        db.commit()
        uid = make_user(db)
        # First event (uninstrumented) — primes user_battlepass_progress.
        _post_action(
            client,
            user_id=uid,
            action_type="receipt_scan",
            scan_id=uuid.uuid4(),
        )

        calls = self._spy(monkeypatch)
        resp = _post_action(
            client,
            user_id=uid,
            action_type="label_scan",
            scan_id=uuid.uuid4(),
        )
        assert resp.status_code == 200
        bp_calls = [c for c in calls if c["kwargs"].get("event_type") == "battlepass_season_participated"]
        assert bp_calls == []

    def test_no_active_season_does_not_fire(self, client, db, monkeypatch):
        # No make_season → no row with is_active=true.
        calls = self._spy(monkeypatch)
        uid = make_user(db)
        resp = _post_action(
            client,
            user_id=uid,
            action_type="receipt_scan",
            scan_id=uuid.uuid4(),
        )
        assert resp.status_code == 200
        bp_calls = [c for c in calls if c["kwargs"].get("event_type") == "battlepass_season_participated"]
        assert bp_calls == []


# ===========================================================================
# CAB / XP reasons sync (KP-08)
# ===========================================================================
def test_cab_reasons_match_model_enum():
    """KP-08 — full set-equality between the repository runtime guard
    (``VALID_REASONS``) and the model CHECK-constraint source
    (``_CAB_REASONS``).

    A single-reason spot-check (the previous form of this test) cannot
    detect drift on any *other* reason — it missed the ``retro_scan``
    drift that this assertion now catches. Any divergence means either a
    route accepts a reason the DB rejects (opaque 500 at COMMIT) or the
    DB accepts a value the runtime never emits (silent dead code).
    """
    from ratis_core.models.gamification import _CAB_REASONS
    from repositories.cab_repository import VALID_REASONS

    assert frozenset(_CAB_REASONS) == VALID_REASONS


def test_xp_reasons_match_model_enum():
    """KP-08 — full set-equality between the XP repository runtime guard
    (``VALID_XP_REASONS``) and the model CHECK-constraint source
    (``_XP_REASONS``)."""
    from ratis_core.models.gamification import _XP_REASONS
    from repositories.xp_repository import VALID_XP_REASONS

    assert frozenset(_XP_REASONS) == VALID_XP_REASONS


def test_cab_reason_guard_matches_db_check_constraint(db):
    """KP-08 — the runtime guard ``VALID_REASONS`` must equal the literals
    in the live ``cabecoin_transactions_reason_check`` CHECK constraint.

    The test DB schema is built by ``Base.metadata.create_all()`` from the
    model ``__table_args__``, so this asserts the runtime allowlist matches
    the constraint actually enforced by Postgres at COMMIT.
    """
    from repositories.cab_repository import VALID_REASONS

    defn = db.execute(
        text(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conname = 'cabecoin_transactions_reason_check'"
        )
    ).scalar_one()
    db_reasons = frozenset(re.findall(r"'([^']+)'", defn))
    assert db_reasons == VALID_REASONS


def test_cab_reference_types_match_db_check_constraint(db):
    """KP-08 — the runtime guard ``VALID_REFERENCE_TYPES`` must equal the
    literals in the live ``cabecoin_transactions_reference_type_check``
    CHECK constraint. Guards against the RW-03 class of opaque 500s."""
    from repositories.cab_repository import VALID_REFERENCE_TYPES

    defn = db.execute(
        text(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conname = 'cabecoin_transactions_reference_type_check'"
        )
    ).scalar_one()
    db_ref_types = frozenset(re.findall(r"'([^']+)'", defn))
    assert db_ref_types == VALID_REFERENCE_TYPES


# ===========================================================================
# reference_type validation (RW-03)
# ===========================================================================
class TestReferenceTypeValidation:
    def test_award_cab_rejects_invalid_reference_type(self, db):
        uid = make_user(db)
        with pytest.raises(ValueError, match="Invalid CAB reference_type"):
            award_cab(
                db,
                uid,
                100,
                "receipt_scan",
                reference_id=uuid.uuid4(),
                reference_type="not_a_real_type",
            )

    def test_award_cab_rejects_invalid_reference_type_before_db_write(self, db):
        """The guard must raise before any balance mutation — no row written."""
        uid = make_user(db)
        with pytest.raises(ValueError, match="Invalid CAB reference_type"):
            award_cab(
                db,
                uid,
                100,
                "receipt_scan",
                reference_id=uuid.uuid4(),
                reference_type="bogus",
            )
        db.flush()
        assert get_balance(db, uid) == 0

    def test_debit_cab_rejects_invalid_reference_type(self, db):
        uid = make_user(db)
        award_cab(db, uid, 100, "receipt_scan")
        db.flush()
        with pytest.raises(ValueError, match="Invalid CAB reference_type"):
            debit_cab(
                db,
                uid,
                50,
                "shop_purchase",
                reference_id=uuid.uuid4(),
                reference_type="bogus",
            )

    def test_award_cab_accepts_valid_reference_type(self, db):
        uid = make_user(db)
        ref_id = uuid.uuid4()
        award_cab(
            db,
            uid,
            100,
            "receipt_scan",
            reference_id=ref_id,
            reference_type="scan",
        )
        db.flush()
        assert get_balance(db, uid) == 100

    def test_award_cab_accepts_null_reference_type(self, db):
        """reference_type=None stays valid — not every credit has a reference."""
        uid = make_user(db)
        award_cab(db, uid, 100, "receipt_scan")
        db.flush()
        assert get_balance(db, uid) == 100
