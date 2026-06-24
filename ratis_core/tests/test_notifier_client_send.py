"""Tests for ``ratis_core.notifier_client.send`` — extended client.

Covers:

* Wire shape : POST body has ``user_id`` / ``type`` / ``data``, and ``data``
  carries the reserved ``_visible_push`` / ``_push_rate_limit_seconds`` /
  ``_push_title`` / ``_push_body`` keys.
* Defaults : ``visible_push=True``, ``push_rate_limit_seconds=0``, no title /
  body override.
* Reserved-key protection : caller cannot inject ``_visible_push`` etc via
  ``payload`` to bypass the kwargs.
* Fire-and-forget : missing env, network errors, 4xx / 5xx — never raises.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ratis_core import notifier_client


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("NOTIFIER_URL", "http://notifier.test/api/v1/notify")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-internal-key")


def _ok_response() -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 202
    resp.raise_for_status = MagicMock(return_value=None)
    return resp


# ---------------------------------------------------------------------------
# Wire shape — kwargs land in the right places
# ---------------------------------------------------------------------------


class TestWireShape:
    def test_posts_to_notifier_url(self):
        with patch("ratis_core.notifier_client.httpx.post", return_value=_ok_response()) as mock_post:
            notifier_client.send(
                user_id=uuid.uuid4(),
                notif_type="achievement_unlocked",
                payload={"code": "v_first"},
            )
        assert mock_post.called
        url = mock_post.call_args.args[0]
        assert url == "http://notifier.test/api/v1/notify"

    def test_body_carries_user_type_data(self):
        uid = uuid.uuid4()
        with patch("ratis_core.notifier_client.httpx.post", return_value=_ok_response()) as mock_post:
            notifier_client.send(
                user_id=uid,
                notif_type="achievement_unlocked",
                payload={"code": "v_first", "rarity": "bronze"},
            )
        body = mock_post.call_args.kwargs["json"]
        assert body["user_id"] == str(uid)
        assert body["type"] == "achievement_unlocked"
        assert body["data"]["code"] == "v_first"
        assert body["data"]["rarity"] == "bronze"

    def test_authorization_header(self):
        with patch("ratis_core.notifier_client.httpx.post", return_value=_ok_response()) as mock_post:
            notifier_client.send(
                user_id=uuid.uuid4(),
                notif_type="achievement_unlocked",
                payload={},
            )
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer test-internal-key"


# ---------------------------------------------------------------------------
# Reserved kwargs land in payload._<name>
# ---------------------------------------------------------------------------


class TestReservedKeys:
    def test_default_visible_push_true(self):
        with patch("ratis_core.notifier_client.httpx.post", return_value=_ok_response()) as mock_post:
            notifier_client.send(
                user_id=uuid.uuid4(),
                notif_type="achievement_unlocked",
                payload={},
            )
        data = mock_post.call_args.kwargs["json"]["data"]
        assert data["_visible_push"] is True
        assert data["_push_rate_limit_seconds"] == 0
        # Title/body absent when not passed.
        assert "_push_title" not in data
        assert "_push_body" not in data

    def test_visible_push_false(self):
        with patch("ratis_core.notifier_client.httpx.post", return_value=_ok_response()) as mock_post:
            notifier_client.send(
                user_id=uuid.uuid4(),
                notif_type="achievement_unlocked",
                payload={},
                visible_push=False,
            )
        data = mock_post.call_args.kwargs["json"]["data"]
        assert data["_visible_push"] is False

    def test_rate_limit_passed_through(self):
        with patch("ratis_core.notifier_client.httpx.post", return_value=_ok_response()) as mock_post:
            notifier_client.send(
                user_id=uuid.uuid4(),
                notif_type="achievement_unlocked",
                payload={},
                push_rate_limit_seconds=3600,
            )
        data = mock_post.call_args.kwargs["json"]["data"]
        assert data["_push_rate_limit_seconds"] == 3600

    def test_title_body_set_when_provided(self):
        with patch("ratis_core.notifier_client.httpx.post", return_value=_ok_response()) as mock_post:
            notifier_client.send(
                user_id=uuid.uuid4(),
                notif_type="achievement_unlocked",
                payload={},
                push_title="Trophy",
                push_body="You unlocked it",
            )
        data = mock_post.call_args.kwargs["json"]["data"]
        assert data["_push_title"] == "Trophy"
        assert data["_push_body"] == "You unlocked it"

    def test_caller_cannot_poison_reserved_keys(self):
        """Reserved keys passed in ``payload`` are stripped before kwargs win."""
        with patch("ratis_core.notifier_client.httpx.post", return_value=_ok_response()) as mock_post:
            notifier_client.send(
                user_id=uuid.uuid4(),
                notif_type="achievement_unlocked",
                payload={
                    "_visible_push": True,  # caller tries to override
                    "_push_rate_limit_seconds": 999,  # caller tries to override
                    "code": "ok",
                },
                visible_push=False,
                push_rate_limit_seconds=3600,
            )
        data = mock_post.call_args.kwargs["json"]["data"]
        assert data["_visible_push"] is False  # kwarg wins
        assert data["_push_rate_limit_seconds"] == 3600  # kwarg wins
        assert data["code"] == "ok"  # caller key preserved


# ---------------------------------------------------------------------------
# Fire-and-forget — never raises
# ---------------------------------------------------------------------------


class TestFireAndForget:
    def test_missing_env_silently_skips(self, monkeypatch):
        monkeypatch.delenv("NOTIFIER_URL", raising=False)
        monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
        # Must not raise.
        notifier_client.send(
            user_id=uuid.uuid4(),
            notif_type="achievement_unlocked",
            payload={},
        )

    def test_swallows_network_error(self):
        with patch(
            "ratis_core.notifier_client.httpx.post",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            # Must not raise.
            notifier_client.send(
                user_id=uuid.uuid4(),
                notif_type="achievement_unlocked",
                payload={},
            )

    def test_swallows_4xx(self):
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 422
        bad_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "422",
                request=MagicMock(),
                response=bad_resp,
            ),
        )
        with patch("ratis_core.notifier_client.httpx.post", return_value=bad_resp):
            notifier_client.send(
                user_id=uuid.uuid4(),
                notif_type="achievement_unlocked",
                payload={},
            )

    def test_swallows_5xx(self):
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 503
        bad_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "503",
                request=MagicMock(),
                response=bad_resp,
            ),
        )
        with patch("ratis_core.notifier_client.httpx.post", return_value=bad_resp):
            notifier_client.send(
                user_id=uuid.uuid4(),
                notif_type="achievement_unlocked",
                payload={},
            )

    def test_swallows_invalid_url(self, monkeypatch):
        monkeypatch.setenv("NOTIFIER_URL", "not a url")
        with patch(
            "ratis_core.notifier_client.httpx.post",
            side_effect=httpx.InvalidURL("invalid"),
        ):
            notifier_client.send(
                user_id=uuid.uuid4(),
                notif_type="achievement_unlocked",
                payload={},
            )
