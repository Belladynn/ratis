"""
Tests for X-Request-ID middleware and Sentry initialisation (DP-02).

Uses raw_client (no auth bypass) — 403 responses still carry response headers.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch


class TestRequestIDMiddleware:
    def test_response_contains_request_id(self, raw_client):
        """Every response must include X-Request-ID header."""
        resp = raw_client.post("/api/v1/notify", json={})
        assert "x-request-id" in resp.headers

    def test_generated_request_id_is_valid_uuid(self, raw_client):
        """When no X-Request-ID is sent, the server generates a valid UUID v4."""
        resp = raw_client.post("/api/v1/notify", json={})
        request_id = resp.headers["x-request-id"]
        parsed = uuid.UUID(request_id)
        assert parsed.version == 4

    def test_client_request_id_is_propagated(self, raw_client):
        """When the client sends X-Request-ID, the same value is echoed back."""
        custom_id = str(uuid.uuid4())
        resp = raw_client.post(
            "/api/v1/notify",
            json={},
            headers={"X-Request-ID": custom_id},
        )
        assert resp.headers["x-request-id"] == custom_id

    def test_each_request_gets_unique_id(self, raw_client):
        """Two requests without X-Request-ID get different IDs."""
        id1 = raw_client.post("/api/v1/notify", json={}).headers["x-request-id"]
        id2 = raw_client.post("/api/v1/notify", json={}).headers["x-request-id"]
        assert id1 != id2


class TestSentryInit:
    def test_sentry_not_initialised_when_dsn_empty(self, monkeypatch):
        """init_sentry is a no-op when SENTRY_DSN is empty (default in tests)."""
        monkeypatch.setenv("SENTRY_DSN", "")
        with patch("sentry_sdk.init") as mock_init:
            from ratis_core.observability import init_sentry

            init_sentry("ratis_notifier")
            mock_init.assert_not_called()

    def test_sentry_initialised_when_dsn_set(self, monkeypatch):
        """init_sentry calls sentry_sdk.init when SENTRY_DSN is non-empty."""
        monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/123")
        monkeypatch.setenv("SENTRY_SEND_PII", "false")
        with patch("sentry_sdk.init") as mock_init:
            from ratis_core.observability import init_sentry

            init_sentry("ratis_notifier")
            mock_init.assert_called_once()
            call_kwargs = mock_init.call_args.kwargs
            assert call_kwargs["dsn"] == "https://fake@sentry.io/123"
            assert call_kwargs["send_default_pii"] is False

    def test_sentry_send_pii_false_by_default(self, monkeypatch):
        """send_default_pii is False unless SENTRY_SEND_PII=true (RGPD)."""
        monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/123")
        monkeypatch.delenv("SENTRY_SEND_PII", raising=False)
        with patch("sentry_sdk.init") as mock_init:
            from ratis_core.observability import init_sentry

            init_sentry("ratis_notifier")
            call_kwargs = mock_init.call_args.kwargs
            assert call_kwargs["send_default_pii"] is False
