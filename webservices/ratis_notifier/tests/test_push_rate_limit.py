"""Tests for V1.1 visible-push rate limiting + the data-only push branch.

Closes the audit gap surfaced 2026-05-10 : ``ratis_core.notifier_client.send``
has been bundling ``_visible_push`` and ``_push_rate_limit_seconds`` into the
wire payload since V1, but the notifier service was *ignoring* both. This
suite asserts the new behaviour :

* ``_visible_push=False``                               → no Expo POST, log = "skipped" / "data_only_push".
* Rate-limit cooldown free                               → first push fires, log = "sent".
* Rate-limit cooldown still active for the (user, type)  → push downgraded → log = "skipped" / "push_rate_limited".
* Cooldown TTL respected                                 → after the TTL expires, next push fires again.
* Reserved keys stripped from the wire payload sent to Expo (no ``_visible_push`` etc leaks to the device).
* Fail-open : a Redis outage must not silence the notification (over-deliver > silence).

Notes :
* Uses the ``fake_rate_limiter`` fixture from conftest (fakeredis-backed
  ``RedisPushRateLimiter`` injected via FastAPI dependency override).
* The ``client`` fixture transitively pulls ``fake_rate_limiter`` so all
  tests in this module use the same isolated cache per test (cleared by the
  fixture's teardown).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from ratis_core.models.analytics import NotificationLog, UserPushToken
from ratis_core.models.user import User

NOW_UTC = datetime(2026, 5, 10, 14, 0, 0, tzinfo=UTC)  # outside quiet hours

EXPO_SUCCESS = {"data": [{"status": "ok", "id": "expo-ticket-rate-test"}]}


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


def _add_token(db, user_id: uuid.UUID) -> UserPushToken:
    # Token must be unique per user — the underlying table has a UNIQUE
    # constraint on the token string. uuid in the suffix avoids cross-test
    # collisions when two users are created in the same test (cf
    # ``test_different_users_have_independent_cooldowns``).
    t = UserPushToken(
        id=uuid.uuid4(),
        user_id=user_id,
        token=f"ExponentPushToken[rate-limit-{uuid.uuid4().hex[:8]}]",
        platform="ios",
    )
    db.add(t)
    db.flush()
    return t


def _ok_resp() -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = EXPO_SUCCESS
    return resp


# ---------------------------------------------------------------------------
# Visible-push gate (data-only push)
# ---------------------------------------------------------------------------


class TestVisiblePushGate:
    def test_visible_push_false_skips_expo_call(self, client, db, freeze_time):
        """``_visible_push=False`` → no Expo POST, log = data_only_push."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        with patch("services.notify_service.httpx.post", return_value=_ok_resp()) as mock_post:
            resp = client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "achievement_unlocked",
                    "data": {
                        "_visible_push": False,
                        "code": "v_first",
                    },
                },
            )

        assert resp.status_code == 202
        # NO Expo call must have been made.
        assert mock_post.call_count == 0
        # A "skipped" log must have been written with reason context (we
        # store reason in log message, not a column — assert the row exists
        # with status="skipped").
        log = db.query(NotificationLog).filter_by(user_id=user.id).one()
        assert log.status == "skipped"

    def test_visible_push_default_true_still_sends(self, client, db, freeze_time):
        """No ``_visible_push`` key → default True → push fires."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        with patch("services.notify_service.httpx.post", return_value=_ok_resp()) as mock_post:
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {"products_identified": 3},
                },
            )

        assert mock_post.call_count == 1


# ---------------------------------------------------------------------------
# Push rate-limit (Redis SETNX)
# ---------------------------------------------------------------------------


class TestPushRateLimit:
    def test_first_push_fires_then_second_is_rate_limited(
        self,
        client,
        db,
        freeze_time,
        fake_rate_limiter,
    ):
        """Two visible pushes back-to-back with cooldown=3600 → only the
        first reaches Expo ; the second is downgraded → log = skipped."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        payload = {
            "user_id": str(user.id),
            "type": "achievement_unlocked",
            "data": {
                "_push_rate_limit_seconds": 3600,
                "code": "r_30",  # sapphire example
            },
        }

        with patch("services.notify_service.httpx.post", return_value=_ok_resp()) as mock_post:
            r1 = client.post("/api/v1/notify", json=payload)
            r2 = client.post("/api/v1/notify", json=payload)

        assert r1.status_code == 202
        assert r2.status_code == 202
        # Only the first round-trip reached Expo.
        assert mock_post.call_count == 1

        # Two log rows : the first "sent", the second "skipped" (rate-limit).
        logs = db.query(NotificationLog).filter_by(user_id=user.id).order_by(NotificationLog.sent_at).all()
        assert len(logs) == 2
        assert logs[0].status == "sent"
        assert logs[1].status == "skipped"

    def test_zero_cooldown_means_no_rate_limit_check(
        self,
        client,
        db,
        freeze_time,
        fake_rate_limiter,
    ):
        """Crystal/Diamant pass cooldown=0 → both pushes always fire."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        payload = {
            "user_id": str(user.id),
            "type": "achievement_unlocked",
            "data": {
                "_push_rate_limit_seconds": 0,
                "code": "exp_diamond",
            },
        }

        with patch("services.notify_service.httpx.post", return_value=_ok_resp()) as mock_post:
            client.post("/api/v1/notify", json=payload)
            client.post("/api/v1/notify", json=payload)

        assert mock_post.call_count == 2

    def test_different_notif_types_have_independent_cooldowns(
        self,
        client,
        db,
        freeze_time,
        fake_rate_limiter,
    ):
        """Cooldown is keyed by (user_id, notif_type) — a sapphire push must
        not block a `scan_done` push."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        with patch("services.notify_service.httpx.post", return_value=_ok_resp()) as mock_post:
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "achievement_unlocked",
                    "data": {"_push_rate_limit_seconds": 3600, "code": "r_30"},
                },
            )
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "scan_done",
                    "data": {"products_identified": 1},
                },
            )

        # Both should fire — different notif_types.
        assert mock_post.call_count == 2

    def test_different_users_have_independent_cooldowns(
        self,
        client,
        db,
        freeze_time,
        fake_rate_limiter,
    ):
        u1 = _make_user(db)
        u2 = _make_user(db)
        _add_token(db, u1.id)
        _add_token(db, u2.id)
        freeze_time(NOW_UTC)

        with patch("services.notify_service.httpx.post", return_value=_ok_resp()) as mock_post:
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(u1.id),
                    "type": "achievement_unlocked",
                    "data": {"_push_rate_limit_seconds": 3600, "code": "r_30"},
                },
            )
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(u2.id),
                    "type": "achievement_unlocked",
                    "data": {"_push_rate_limit_seconds": 3600, "code": "r_30"},
                },
            )

        # Both fire — keys are namespaced by user.
        assert mock_post.call_count == 2

    def test_cooldown_ttl_expires_then_push_fires_again(
        self,
        client,
        db,
        freeze_time,
        fake_rate_limiter,
    ):
        """Once the SETNX TTL elapses, the next push must fire again."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        payload = {
            "user_id": str(user.id),
            "type": "achievement_unlocked",
            "data": {
                "_push_rate_limit_seconds": 3600,
                "code": "r_30",
            },
        }

        with patch("services.notify_service.httpx.post", return_value=_ok_resp()) as mock_post:
            client.post("/api/v1/notify", json=payload)  # 1st — fires
            client.post("/api/v1/notify", json=payload)  # 2nd — rate-limited

            # Force-expire the cooldown key on the fake redis.
            keys = list(fake_rate_limiter._client.scan_iter(match="notif:push:rate:*"))
            assert keys, "expected SETNX to have written a key"
            for k in keys:
                fake_rate_limiter._client.delete(k)

            client.post("/api/v1/notify", json=payload)  # 3rd — fires again

        assert mock_post.call_count == 2


# ---------------------------------------------------------------------------
# Reserved keys never leak to Expo
# ---------------------------------------------------------------------------


class TestReservedKeysStripped:
    def test_expo_payload_does_not_contain_reserved_keys(
        self,
        client,
        db,
        freeze_time,
    ):
        """The wire payload sent to Expo must never carry the underscore-
        prefixed routing flags — they're internal to ratis_notifier."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        with patch("services.notify_service.httpx.post", return_value=_ok_resp()) as mock_post:
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "achievement_unlocked",
                    "data": {
                        "_visible_push": True,
                        "_push_rate_limit_seconds": 0,
                        "_push_title": "Trophy",
                        "_push_body": "+250 CAB",
                        "code": "r_30",
                        "rarity": "sapphire",
                    },
                },
            )

        # Expo payload (the keyword arg "json" of httpx.post) should not
        # contain underscore-prefixed reserved keys in the data dict.
        sent_payload = mock_post.call_args.kwargs["json"]
        assert "data" in sent_payload
        for reserved in ("_visible_push", "_push_rate_limit_seconds", "_push_title", "_push_body"):
            assert reserved not in sent_payload["data"], f"reserved key {reserved!r} leaked to Expo payload"
        # Caller's own keys are preserved.
        assert sent_payload["data"]["code"] == "r_30"
        assert sent_payload["data"]["rarity"] == "sapphire"

    def test_push_title_body_overrides_used(
        self,
        client,
        db,
        freeze_time,
    ):
        """``_push_title`` and ``_push_body`` override the settings template."""
        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        with patch("services.notify_service.httpx.post", return_value=_ok_resp()) as mock_post:
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "achievement_unlocked",
                    "data": {
                        "_push_title": "🏆 Trophée Saphir !",
                        "_push_body": "Mois sans rater · +250 CAB",
                        "code": "r_30",
                    },
                },
            )

        sent_payload = mock_post.call_args.kwargs["json"]
        assert sent_payload["title"] == "🏆 Trophée Saphir !"
        assert sent_payload["body"] == "Mois sans rater · +250 CAB"


# ---------------------------------------------------------------------------
# Fail-open behaviour
# ---------------------------------------------------------------------------


class TestFailOpenOnRedisError:
    def test_redis_error_during_setnx_falls_open_and_push_fires(
        self,
        client,
        db,
        freeze_time,
        fake_rate_limiter,
    ):
        """A Redis blip must not silence the notification — fail OPEN."""
        import redis

        user = _make_user(db)
        _add_token(db, user.id)
        freeze_time(NOW_UTC)

        # Monkeypatch the underlying redis client's `set` to throw.
        with (
            patch.object(
                fake_rate_limiter._client,
                "set",
                side_effect=redis.RedisError("connection refused"),
            ),
            patch(
                "services.notify_service.httpx.post",
                return_value=_ok_resp(),
            ) as mock_post,
        ):
            client.post(
                "/api/v1/notify",
                json={
                    "user_id": str(user.id),
                    "type": "achievement_unlocked",
                    "data": {
                        "_push_rate_limit_seconds": 3600,
                        "code": "r_30",
                    },
                },
            )

        # Expo must still have been called — the user gets the push.
        assert mock_post.call_count == 1


# ---------------------------------------------------------------------------
# Direct unit tests for the limiter (no HTTP layer)
# ---------------------------------------------------------------------------


class TestRedisPushRateLimiterUnit:
    """White-box unit tests on ``RedisPushRateLimiter`` itself."""

    @pytest.fixture
    def limiter(self):
        import fakeredis
        from services.push_rate_limiter import RedisPushRateLimiter

        return RedisPushRateLimiter(fakeredis.FakeStrictRedis())

    def test_first_call_allows(self, limiter):
        assert limiter.allow_push(uuid.uuid4(), "achievement_unlocked", 3600) is True

    def test_second_call_within_window_blocks(self, limiter):
        uid = uuid.uuid4()
        assert limiter.allow_push(uid, "achievement_unlocked", 3600) is True
        assert limiter.allow_push(uid, "achievement_unlocked", 3600) is False

    def test_zero_cooldown_short_circuits_to_allow(self, limiter):
        uid = uuid.uuid4()
        # Should not write any key — and never block subsequent calls.
        assert limiter.allow_push(uid, "achievement_unlocked", 0) is True
        assert limiter.allow_push(uid, "achievement_unlocked", 0) is True

    def test_negative_cooldown_short_circuits_to_allow(self, limiter):
        uid = uuid.uuid4()
        # Defensive : a malformed payload with negative seconds shouldn't
        # crash, just allow.
        assert limiter.allow_push(uid, "achievement_unlocked", -10) is True
