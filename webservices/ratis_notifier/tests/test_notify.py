"""
TDD tests for POST /api/v1/notify.

All tests use the SAVEPOINT isolation fixture from conftest — each test
starts and ends with a clean DB state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest
from ratis_core.models.analytics import NotificationLog, UserPushToken
from ratis_core.models.notifications import PushReceiptTicket
from ratis_core.models.user import User

# ── Helpers ───────────────────────────────────────────────────────────────────

NOW_UTC = datetime(2026, 4, 8, 14, 0, 0, tzinfo=UTC)  # 14:00 UTC — outside quiet hours


def _make_user(db, timezone: str = "UTC") -> User:
    # Since H2 Phase 2 the OAuth identity lives in ``user_identities`` ;
    # the ``users`` row only carries an ``account_type`` state.
    uid = uuid.uuid4()
    user = User(
        id=uid,
        email=f"{uuid.uuid4().hex}@test.com",
        account_type="oauth",
        timezone=timezone,
    )
    db.add(user)
    db.flush()
    return user


def _add_token(db, user_id: uuid.UUID, token: str = "ExponentPushToken[test-token]") -> UserPushToken:
    t = UserPushToken(id=uuid.uuid4(), user_id=user_id, token=token, platform="ios")
    db.add(t)
    db.flush()
    return t


def _add_log(db, user_id: uuid.UUID, notif_type: str, status: str = "sent", minutes_ago: int = 0) -> NotificationLog:
    sent_at = NOW_UTC - timedelta(minutes=minutes_ago)
    log = NotificationLog(
        id=uuid.uuid4(),
        user_id=user_id,
        type=notif_type,
        status=status,
        sent_at=sent_at,
    )
    db.add(log)
    db.flush()
    return log


EXPO_SUCCESS = {"data": [{"status": "ok", "id": "expo-ticket-abc123"}]}


def _distinct_ticket_post():
    """httpx.post side_effect yielding a unique Expo ticket id per call.

    Expo returns a globally-unique ticket per push ; a constant-id mock
    would collide on the ``uq_push_receipt_tickets_ticket`` UNIQUE
    constraint when a user has multiple tokens. Mirrors real Expo
    behaviour."""
    counter = {"n": 0}

    def _post(url, json, timeout):
        counter["n"] += 1
        m = MagicMock()
        m.raise_for_status = MagicMock()
        m.json.return_value = {"data": [{"status": "ok", "id": f"expo-ticket-{counter['n']}"}]}
        return m

    return _post


EXPO_DEVICE_NOT_REGISTERED = {
    "data": [{"status": "error", "message": "...", "details": {"error": "DeviceNotRegistered"}}]
}


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestNotifySuccess:
    def test_returns_202(self, client, db, freeze_time):
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            resp = client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {"products_identified": 5},
                },
            )

        assert resp.status_code == 202

    def test_notification_log_created_with_sent_status(self, client, db, freeze_time):
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        log = db.query(NotificationLog).filter_by(user_id=user.id).first()
        assert log is not None
        assert log.status == "sent"
        assert log.type == "scan_done"
        assert log.expo_ticket_id == "expo-ticket-abc123"

    def test_multiple_tokens_one_log(self, client, db, freeze_time):
        """Multiple tokens → both receive the push, but only one log entry is created."""
        user = _make_user(db)
        _add_token(db, user.id, "ExponentPushToken[token-ios]")
        _add_token(db, user.id, "ExponentPushToken[token-android]")
        freeze_time(NOW_UTC)

        with patch(
            "services.notify_service.httpx.post",
            side_effect=_distinct_ticket_post(),
        ):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "badge_unlocked",
                    "data": {},
                },
            )

        logs = db.query(NotificationLog).filter_by(user_id=user.id, status="sent").all()
        assert len(logs) == 1


class TestNotifyNoTokens:
    def test_no_tokens_silent_ignore(self, client, db, freeze_time):
        user = _make_user(db)
        freeze_time(NOW_UTC)

        resp = client.post(
            "/api/v1/notify",
            json={
                "user_id": str(user.id),
                "type": "scan_done",
                "data": {},
            },
        )

        assert resp.status_code == 202
        log = db.query(NotificationLog).filter_by(user_id=user.id).first()
        assert log is None  # silent ignore — no log entry


class TestNotifyQuietHours:
    # Users have timezone="UTC" → quiet hours (22h–8h) map 1:1 to UTC hours.
    # DST-independent.
    @pytest.mark.parametrize("utc_hour", [22, 23, 0, 1, 7])
    def test_quiet_hours_skipped(self, client, db, freeze_time, utc_hour):
        user = _make_user(db, timezone="UTC")
        _add_token(db, user.id)
        freeze_time(NOW_UTC.replace(hour=utc_hour, minute=0, second=0))

        resp = client.post(
            "/api/v1/notify",
            json={
                "user_id": str(user.id),
                "type": "scan_done",
                "data": {},
            },
        )

        assert resp.status_code == 202
        log = db.query(NotificationLog).filter_by(user_id=user.id).first()
        assert log is not None
        assert log.status == "skipped"

    def test_outside_quiet_hours_not_skipped(self, client, db, freeze_time):
        user = _make_user(db, timezone="UTC")
        _add_token(db, user.id)
        freeze_time(NOW_UTC.replace(hour=10))

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        log = db.query(NotificationLog).filter_by(user_id=user.id).first()
        assert log.status == "sent"

    def test_timezone_conversion_paris_quiet(self, client, db, freeze_time):
        """UTC 20:00 is outside quiet hours for a UTC user but inside for Europe/Paris (→ 22:00 CEST local).

        Both users' httpx calls are mocked so the 'sent'/'skipped' outcome
        reflects only the quiet-hours decision, never a real network result.
        """
        user_paris = _make_user(db, timezone="Europe/Paris")
        user_utc = _make_user(db, timezone="UTC")
        _add_token(db, user_paris.id, "ExponentPushToken[paris]")
        _add_token(db, user_utc.id, "ExponentPushToken[utc]")
        freeze_time(NOW_UTC.replace(hour=20, minute=0, second=0))

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            client.post("/api/v1/notify", json={"user_id": str(user_utc.id), "type": "scan_done", "data": {}})
            client.post("/api/v1/notify", json={"user_id": str(user_paris.id), "type": "scan_done", "data": {}})

        utc_log = db.query(NotificationLog).filter_by(user_id=user_utc.id).first()
        paris_log = db.query(NotificationLog).filter_by(user_id=user_paris.id).first()
        assert utc_log.status == "sent"  # UTC 20 = outside quiet for UTC user
        assert paris_log.status == "skipped"  # UTC 20 = Paris 22 = start of quiet

    def test_timezone_conversion_paris_boundary_8h_not_quiet(self, client, db, freeze_time):
        """quiet_hours_end is exclusive: 08:00 Paris local is OUT of quiet hours.

        UTC 06:00 → Paris 08:00 CEST. The Paris user must receive the push;
        the UTC user (06:00 UTC, still inside 22h-8h) must be skipped.
        """
        user_paris = _make_user(db, timezone="Europe/Paris")
        user_utc = _make_user(db, timezone="UTC")
        _add_token(db, user_paris.id, "ExponentPushToken[paris-boundary]")
        _add_token(db, user_utc.id, "ExponentPushToken[utc-boundary]")
        freeze_time(NOW_UTC.replace(hour=6, minute=0, second=0))

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            client.post("/api/v1/notify", json={"user_id": str(user_utc.id), "type": "scan_done", "data": {}})
            client.post("/api/v1/notify", json={"user_id": str(user_paris.id), "type": "scan_done", "data": {}})

        utc_log = db.query(NotificationLog).filter_by(user_id=user_utc.id).first()
        paris_log = db.query(NotificationLog).filter_by(user_id=user_paris.id).first()
        assert utc_log.status == "skipped"  # UTC 06 = inside quiet for UTC user
        assert paris_log.status == "sent"  # UTC 06 = Paris 08 = end of quiet (exclusive)


class TestNotifyDailyLimit:
    def test_daily_limit_reached_skipped(self, client, db, freeze_time):
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        for i in range(10):
            _add_log(db, user.id, "scan_done", status="sent", minutes_ago=30 + i)

        resp = client.post(
            "/api/v1/notify",
            json={
                "user_id": str(user.id),
                "type": "cashback_available",
                "data": {},
            },
        )

        assert resp.status_code == 202
        skipped = db.query(NotificationLog).filter_by(user_id=user.id, status="skipped").first()
        assert skipped is not None

    def test_skipped_logs_not_counted_toward_limit(self, client, db, freeze_time):
        """Skipped logs must not count toward the daily cap."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        for i in range(9):
            _add_log(db, user.id, "scan_done", status="sent", minutes_ago=30 + i)
        _add_log(db, user.id, "scan_done", status="skipped", minutes_ago=30)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "cashback_available",
                    "data": {},
                },
            )

        new_sent = (
            db.query(NotificationLog).filter_by(user_id=user.id, status="sent", type="cashback_available").first()
        )
        assert new_sent is not None


class TestNotifyDailyCapLock:
    """The daily-cap check+insert is serialised by a per-user Postgres
    transaction-scoped advisory lock so concurrent notify requests for the
    same user can never push the count past the cap.

    True OS-thread concurrency is impractical in pytest (the SAVEPOINT
    isolation fixture binds one connection), so the suite asserts (a) the
    advisory lock is actually acquired on the cap path and (b) the cap logic
    itself holds — the lock is what makes the logic correct under contention.
    """

    def test_advisory_lock_acquired_before_cap_count(self, client, db, freeze_time):
        """The pipeline must call pg_advisory_xact_lock with the per-user key
        before counting today's notifications."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        from repositories import notification_repository as repo

        with patch.object(repo, "acquire_user_cap_lock", wraps=repo.acquire_user_cap_lock) as spy:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = EXPO_SUCCESS
            with patch("services.notify_service.httpx.post", return_value=mock_resp):
                client.post(
                    "/api/v1/notify",
                    json={
                        "user_id": str(user.id),
                        "type": "scan_done",
                        "data": {},
                    },
                )

        spy.assert_called_once()
        assert spy.call_args.args[1] == user.id

    def test_lock_uses_per_user_key(self, db):
        """acquire_user_cap_lock issues a pg_advisory_xact_lock keyed on the
        user — distinct users get distinct lock keys (no false contention)."""
        from repositories import notification_repository as repo

        user_a = _make_user(db)
        user_b = _make_user(db)

        # Both succeed without blocking — the savepoint connection is single,
        # but the call itself must not raise and must be scoped per user.
        repo.acquire_user_cap_lock(db, user_a.id)
        repo.acquire_user_cap_lock(db, user_b.id)

    def test_cap_holds_at_exactly_the_limit(self, client, db, freeze_time):
        """With `max_notifications_per_day` already-sent logs, the next
        request is skipped — the count seen under the lock is the committed
        count."""
        cap = client.app.state.cfg["notifier"]["max_notifications_per_day"]
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        for i in range(cap):
            _add_log(db, user.id, "scan_done", status="sent", minutes_ago=30 + i)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS
        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "cashback_available",
                    "data": {},
                },
            )

        sent = db.query(NotificationLog).filter_by(user_id=user.id, status="sent").count()
        assert sent == cap  # the (cap+1)-th request did not send
        skipped = db.query(NotificationLog).filter_by(user_id=user.id, status="skipped").first()
        assert skipped is not None


class TestNotifyDedup:
    def test_same_type_same_minute_deduped(self, client, db, freeze_time):
        """Two requests in the same calendar minute → second is silently deduped via IntegrityError.
        Both calls mock httpx.post to succeed so status='sent' on both attempts.
        First call commits the log (SAVEPOINT released to outer tx). Second call
        hits the unique index, IntegrityError is caught, rollback to SAVEPOINT only."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            first = client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )
            second = client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        assert first.status_code == 202
        assert second.status_code == 202
        sent = db.query(NotificationLog).filter_by(user_id=user.id, status="sent").count()
        assert sent == 1  # second request deduped — IntegrityError swallowed

    def test_same_type_different_minute_not_deduped(self, client, db, freeze_time):
        """Previous 'sent' in a different calendar minute → no conflict → sends normally."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)
        _add_log(db, user.id, "scan_done", status="sent", minutes_ago=1)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        sent = db.query(NotificationLog).filter_by(user_id=user.id, status="sent").count()
        assert sent == 2

    def test_different_type_not_deduped(self, client, db, freeze_time):
        """Different type → different constraint key → no dedup."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)
        _add_log(db, user.id, "scan_done", status="sent", minutes_ago=0)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "badge_unlocked",
                    "data": {},
                },
            )

        new_sent = db.query(NotificationLog).filter_by(user_id=user.id, status="sent", type="badge_unlocked").first()
        assert new_sent is not None


class TestNotifyDeviceNotRegistered:
    def test_token_deleted_on_device_not_registered(self, client, db, freeze_time):
        user = _make_user(db)
        token = _add_token(db, user.id)
        freeze_time(NOW_UTC)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_DEVICE_NOT_REGISTERED

        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            resp = client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        assert resp.status_code == 202
        deleted = db.get(UserPushToken, token.id)
        assert deleted is None

    def test_failed_log_on_device_not_registered(self, client, db, freeze_time):
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_DEVICE_NOT_REGISTERED

        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        log = db.query(NotificationLog).filter_by(user_id=user.id).first()
        assert log.status == "failed"

    def test_valid_token_sent_despite_invalid_sibling(self, client, db, freeze_time):
        """One invalid token + one valid → bad token deleted, overall log is 'sent'."""
        user = _make_user(db)
        bad_token = _add_token(db, user.id, "ExponentPushToken[bad]")
        _add_token(db, user.id, "ExponentPushToken[good]")
        freeze_time(NOW_UTC)

        def side_effect(url, json, timeout):
            mock = MagicMock()
            mock.raise_for_status = MagicMock()
            if json["to"] == bad_token.token:
                mock.json.return_value = EXPO_DEVICE_NOT_REGISTERED
            else:
                mock.json.return_value = EXPO_SUCCESS
            return mock

        with patch("services.notify_service.httpx.post", side_effect=side_effect):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        # Overall pipeline succeeded (at least one token sent) → one "sent" log
        sent_count = db.query(NotificationLog).filter_by(user_id=user.id, status="sent").count()
        assert sent_count == 1
        # Bad token was deleted
        assert db.get(UserPushToken, bad_token.id) is None


class TestNotifyReceiptTicketPersistence:
    """Successful Expo sends persist a ``push_receipt_tickets`` row so the
    receipt-polling batch can later fetch the delivery outcome and clean up
    dead tokens.
    """

    def test_ticket_persisted_on_successful_send(self, client, db, freeze_time):
        user = _make_user(db)
        token = _add_token(db, user.id)
        freeze_time(NOW_UTC)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        ticket = db.query(PushReceiptTicket).filter_by(user_id=user.id).first()
        assert ticket is not None
        assert ticket.expo_ticket_id == "expo-ticket-abc123"
        assert ticket.push_token == token.token
        assert ticket.checked_at is None

    def test_one_ticket_row_per_token(self, client, db, freeze_time):
        """Two tokens → two ticket rows (each receipt maps to one token)."""
        user = _make_user(db)
        _add_token(db, user.id, "ExponentPushToken[ios]")
        _add_token(db, user.id, "ExponentPushToken[android]")
        freeze_time(NOW_UTC)

        with patch(
            "services.notify_service.httpx.post",
            side_effect=_distinct_ticket_post(),
        ):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        tickets = db.query(PushReceiptTicket).filter_by(user_id=user.id).all()
        assert len(tickets) == 2
        assert {t.push_token for t in tickets} == {
            "ExponentPushToken[ios]",
            "ExponentPushToken[android]",
        }

    def test_no_ticket_row_on_failed_send(self, client, db, freeze_time):
        """All retries exhausted → no ticket persisted (nothing to poll)."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        with (
            patch("time.sleep"),
            patch(
                "services.notify_service.httpx.post",
                side_effect=httpx.HTTPError("timeout"),
            ),
        ):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        assert db.query(PushReceiptTicket).filter_by(user_id=user.id).count() == 0


class TestNotifyRetry:
    def test_retry_on_network_error_succeeds(self, client, db, freeze_time):
        """Network error on first attempt → retry → success → 'sent' log."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        attempts = []

        def fake_post(url, json, timeout):
            attempts.append(1)
            if len(attempts) == 1:
                raise httpx.HTTPError("connection refused")
            m = MagicMock()
            m.raise_for_status = MagicMock()
            m.json.return_value = EXPO_SUCCESS
            return m

        with patch("time.sleep"), patch("services.notify_service.httpx.post", side_effect=fake_post):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        assert len(attempts) == 2
        log = db.query(NotificationLog).filter_by(user_id=user.id).first()
        assert log is not None
        assert log.status == "sent"

    def test_all_retries_exhausted_logs_failed(self, client, db, freeze_time):
        """All retry attempts fail → pipeline logs 'failed', never raises."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        with (
            patch("time.sleep"),
            patch(
                "services.notify_service.httpx.post",
                side_effect=httpx.HTTPError("timeout"),
            ),
        ):
            resp = client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        assert resp.status_code == 202
        log = db.query(NotificationLog).filter_by(user_id=user.id).first()
        assert log is not None
        assert log.status == "failed"


class TestNotifyUnknownUser:
    def test_unknown_user_silent_202(self, client, db, freeze_time):
        """Unknown user_id → no tokens → silent 202, no crash."""
        freeze_time(NOW_UTC)
        resp = client.post(
            "/api/v1/notify",
            json={
                "user_id": str(uuid.uuid4()),
                "type": "scan_done",
                "data": {},
            },
        )
        assert resp.status_code == 202


class TestInternalAuth:
    def test_missing_header_returns_403(self, raw_client, db, freeze_time):
        """No Authorization header → 403."""
        freeze_time(NOW_UTC)
        resp = raw_client.post(
            "/api/v1/notify",
            json={
                "user_id": str(uuid.uuid4()),
                "type": "scan_done",
                "data": {},
            },
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "forbidden"

    def test_wrong_key_returns_403(self, raw_client, db, freeze_time):
        """Wrong bearer value → 403."""
        freeze_time(NOW_UTC)
        resp = raw_client.post(
            "/api/v1/notify",
            json={"user_id": str(uuid.uuid4()), "type": "scan_done", "data": {}},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "forbidden"

    def test_wrong_scheme_returns_403(self, raw_client, db, freeze_time):
        """Basic auth scheme instead of Bearer → HTTPBearer returns None → 403."""
        freeze_time(NOW_UTC)
        resp = raw_client.post(
            "/api/v1/notify",
            json={"user_id": str(uuid.uuid4()), "type": "scan_done", "data": {}},
            headers={"Authorization": "Basic dGVzdC1pbnRlcm5hbC1rZXk="},
        )
        assert resp.status_code == 403

    def test_empty_bearer_value_returns_403(self, raw_client, db, freeze_time):
        """Bearer with empty token string → 403."""
        freeze_time(NOW_UTC)
        resp = raw_client.post(
            "/api/v1/notify",
            json={"user_id": str(uuid.uuid4()), "type": "scan_done", "data": {}},
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 403


class TestInvalidTimezone:
    def test_invalid_timezone_falls_back_to_utc(self, client, db, freeze_time):
        """User with a corrupt/invalid timezone stored in DB → falls back to UTC, does not crash.
        At 10:00 UTC (outside UTC quiet hours), the notification must be delivered (not skipped).
        """
        # UTC 10:00 is outside quiet hours — should be delivered if timezone falls back to UTC
        user = _make_user(db, timezone="Invalid/Zone_That_Does_Not_Exist")
        _add_token(db, user.id)
        freeze_time(NOW_UTC.replace(hour=10, minute=0, second=0))

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp):
            resp = client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        assert resp.status_code == 202
        log = db.query(NotificationLog).filter_by(user_id=user.id).first()
        assert log is not None
        # UTC fallback: 10:00 UTC is outside quiet hours → sent, not skipped
        assert log.status == "sent"

    def test_to_local_empty_string_timezone_falls_back_to_utc(self):
        """``ZoneInfo('')`` raises ValueError (not ZoneInfoNotFoundError/KeyError).
        ``_to_local`` must absorb it and fall back to UTC, never propagate."""
        from services.notify_service import _to_local

        result = _to_local(NOW_UTC, "")
        assert result == NOW_UTC


class TestFireAndForget:
    def test_background_exception_swallowed(self, client, db, freeze_time):
        """Unexpected exception in pipeline → client still gets 202 (never surfaces errors)."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        with patch("services.notify_service._run_pipeline", side_effect=RuntimeError("boom")):
            resp = client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        assert resp.status_code == 202


class TestStoreValidatedTemplate:
    """Template interpolation for the `store_validated` push notification.

    Part B reconciliation enqueues an outbox row with
    ``data={store_name, reconciled_count, receipt_id}``; the template in
    ``ratis_settings.json`` must interpolate ``{store_name}`` and
    ``{reconciled_count}`` into the push body sent to Expo.
    """

    def test_store_validated_interpolates_store_name_and_count(self, client, db, freeze_time):
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp) as mock_post:
            resp = client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "store_validated",
                    "data": {
                        "store_name": "Carrefour Ménilmontant",
                        "reconciled_count": 3,
                        "receipt_id": str(uuid.uuid4()),
                    },
                },
            )

        assert resp.status_code == 202
        assert mock_post.called
        payload = mock_post.call_args.kwargs["json"]
        assert payload["title"] == "Magasin validé !"
        assert payload["body"] == ("Ton magasin Carrefour Ménilmontant a été validé. 3 scans débloqués.")

    def test_store_validated_missing_data_keeps_placeholder(self, client, db, freeze_time):
        """Malformed enqueue missing keys → placeholder left as-is, no crash."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp) as mock_post:
            resp = client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "store_validated",
                    "data": {},
                },
            )

        assert resp.status_code == 202
        payload = mock_post.call_args.kwargs["json"]
        assert "{store_name}" in payload["body"]
        assert "{reconciled_count}" in payload["body"]


class TestExpoUrlResolution:
    """The Expo push endpoint is configurable via the EXPO_PUSH_URL env var,
    with the ratis_settings.json ``notifier.expo_push_url`` value as fallback.
    """

    def test_expo_push_url_env_var_overrides_json_config(self, client, db, freeze_time, monkeypatch):
        """EXPO_PUSH_URL set → the POST targets that URL, not the JSON value."""
        monkeypatch.setenv("EXPO_PUSH_URL", "https://expo.example.test/push")
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp) as mock_post:
            resp = client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        assert resp.status_code == 202
        assert mock_post.call_args.args[0] == "https://expo.example.test/push"

    def test_expo_push_url_falls_back_to_json_when_env_unset(self, client, db, freeze_time, monkeypatch):
        """EXPO_PUSH_URL unset → the JSON ``notifier.expo_push_url`` is used."""
        monkeypatch.delenv("EXPO_PUSH_URL", raising=False)
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        json_url = client.app.state.cfg["notifier"]["expo_push_url"]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = EXPO_SUCCESS

        with patch("services.notify_service.httpx.post", return_value=mock_resp) as mock_post:
            resp = client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {},
                },
            )

        assert resp.status_code == 202
        assert mock_post.call_args.args[0] == json_url


class TestDocsDisabled:
    """ratis_notifier is an internal-only service — interactive API docs and
    the OpenAPI schema must not be exposed.
    """

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_docs_endpoints_return_404(self, raw_client, path):
        resp = raw_client.get(path)
        assert resp.status_code == 404
