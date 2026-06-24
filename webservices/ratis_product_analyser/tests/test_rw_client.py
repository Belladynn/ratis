"""Unit tests for ``admin_ui.rw_client``.

The mini admin UI lives in PA but admin-settings pages need cross-service
calls to RW (PUT /admin/settings/{section}, GET /admin/settings/audit,
POST /admin/settings/{section}/confirm-2fa, ...). ``rw_client`` mirrors
``au_client`` : Bearer ADMIN_API_KEY + X-Admin-Operator (always) +
X-Admin-TOTP (only on confirm-2fa).

These tests exercise the client in isolation by routing all httpx
traffic through ``httpx.MockTransport`` — no real HTTP, no respx
dependency added (PA test deps stay slim per R33 / no-bloat).

The tests monkeypatch ``ADMIN_API_KEY`` and ``RW_BASE_URL`` per-test so
they don't depend on global conftest values bleeding through.
"""

from __future__ import annotations

import httpx
import pytest
from admin_ui import rw_client


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    """Pin env to known values for every test in this module."""
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key-padded-to-32-chars-min")
    monkeypatch.setenv("RW_BASE_URL", "http://ratis_rewards.test:8004")


def _capture_request_transport():
    """Build a MockTransport that records the inbound request.

    Returns ``(transport, captured)`` where ``captured`` is a list that
    will be appended once with the ``httpx.Request`` object the client
    sends. Tests inspect headers / URL / body afterwards.
    """
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(_handler), captured


def _patch_async_client(monkeypatch, transport: httpx.MockTransport) -> None:
    """Force every ``httpx.AsyncClient(...)`` in rw_client to use ``transport``.

    rw_client builds a fresh ``AsyncClient`` per call (admin = human-paced,
    no pool needed). We wrap the constructor so the test transport is
    injected without rw_client knowing.
    """
    original = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(rw_client.httpx, "AsyncClient", _factory)


# ---------------------------------------------------------------------------
# rw_get
# ---------------------------------------------------------------------------


async def test_rw_get_includes_bearer_and_operator_headers(monkeypatch):
    """GET sends Authorization: Bearer <key> + X-Admin-Operator."""
    transport, captured = _capture_request_transport()
    _patch_async_client(monkeypatch, transport)

    response = await rw_client.rw_get("/admin/settings/audit", operator="alice")

    assert response.status_code == 200
    assert len(captured) == 1
    req = captured[0]
    assert req.method == "GET"
    assert req.url.path == "/admin/settings/audit"
    assert req.headers["Authorization"] == "Bearer test-admin-key-padded-to-32-chars-min"
    assert req.headers["X-Admin-Operator"] == "alice"
    # No TOTP on read calls.
    assert "X-Admin-TOTP" not in req.headers


async def test_rw_get_forwards_query_params(monkeypatch):
    """``params`` argument is forwarded as URL query string."""
    transport, captured = _capture_request_transport()
    _patch_async_client(monkeypatch, transport)

    await rw_client.rw_get(
        "/admin/settings/audit",
        operator="alice",
        params={"section": "rewards", "limit": 10},
    )

    req = captured[0]
    # httpx URL.params is a QueryParams object — compare via dict.
    assert dict(req.url.params) == {"section": "rewards", "limit": "10"}


async def test_rw_get_uses_rw_base_url(monkeypatch):
    """Absolute URL = RW_BASE_URL + path."""
    transport, captured = _capture_request_transport()
    _patch_async_client(monkeypatch, transport)

    await rw_client.rw_get("/admin/settings/foo/editable", operator="bob")

    req = captured[0]
    assert str(req.url) == "http://ratis_rewards.test:8004/admin/settings/foo/editable"


# ---------------------------------------------------------------------------
# rw_put
# ---------------------------------------------------------------------------


async def test_rw_put_sends_json_body_and_headers(monkeypatch):
    """PUT serializes the json kwarg as request body + sends auth headers."""
    import json as _json

    transport, captured = _capture_request_transport()
    _patch_async_client(monkeypatch, transport)

    payload = {"data": {"key": "value"}, "reason": "raising welcome bonus"}
    response = await rw_client.rw_put("/admin/settings/rewards", operator="alice", json=payload)

    assert response.status_code == 200
    req = captured[0]
    assert req.method == "PUT"
    assert req.url.path == "/admin/settings/rewards"
    assert req.headers["Authorization"] == "Bearer test-admin-key-padded-to-32-chars-min"
    assert req.headers["X-Admin-Operator"] == "alice"
    # PUT never carries TOTP — only confirm-2fa POST does.
    assert "X-Admin-TOTP" not in req.headers
    assert _json.loads(req.content.decode("utf-8")) == payload


# ---------------------------------------------------------------------------
# rw_post — TOTP propagation
# ---------------------------------------------------------------------------


async def test_rw_post_includes_totp_header_when_provided(monkeypatch):
    """POST with totp= sets X-Admin-TOTP. Used for confirm-2fa endpoint."""
    transport, captured = _capture_request_transport()
    _patch_async_client(monkeypatch, transport)

    await rw_client.rw_post(
        "/admin/settings/rewards/confirm-2fa",
        operator="alice",
        json={"audit_id": "abc"},
        totp="123456",
    )

    req = captured[0]
    assert req.method == "POST"
    assert req.headers["X-Admin-TOTP"] == "123456"
    assert req.headers["Authorization"] == "Bearer test-admin-key-padded-to-32-chars-min"
    assert req.headers["X-Admin-Operator"] == "alice"


async def test_rw_post_omits_totp_header_when_none(monkeypatch):
    """POST without totp (e.g. cancel-pending) sends no X-Admin-TOTP."""
    transport, captured = _capture_request_transport()
    _patch_async_client(monkeypatch, transport)

    await rw_client.rw_post(
        "/admin/settings/rewards/cancel-pending",
        operator="alice",
        json={"audit_id": "abc"},
    )

    req = captured[0]
    assert "X-Admin-TOTP" not in req.headers


async def test_rw_post_sends_json_body(monkeypatch):
    """POST body is JSON-serialized from the json kwarg."""
    import json as _json

    transport, captured = _capture_request_transport()
    _patch_async_client(monkeypatch, transport)

    payload = {"audit_id": "11111111-1111-1111-1111-111111111111"}
    await rw_client.rw_post(
        "/admin/settings/rewards/cancel-pending",
        operator="alice",
        json=payload,
    )

    req = captured[0]
    assert _json.loads(req.content.decode("utf-8")) == payload


# ---------------------------------------------------------------------------
# Header construction unit tests (no HTTP)
# ---------------------------------------------------------------------------


def test_build_headers_omits_totp_by_default():
    """``_build_headers`` without totp returns 2 keys only."""
    headers = rw_client._build_headers("operator-1")
    assert headers == {
        "Authorization": "Bearer test-admin-key-padded-to-32-chars-min",
        "X-Admin-Operator": "operator-1",
    }


def test_build_headers_includes_totp_when_truthy():
    """totp='123456' adds X-Admin-TOTP."""
    headers = rw_client._build_headers("operator-1", totp="123456")
    assert headers["X-Admin-TOTP"] == "123456"


def test_build_headers_omits_totp_when_empty_string():
    """totp='' is falsy → no X-Admin-TOTP. Avoids leaking an empty header."""
    headers = rw_client._build_headers("operator-1", totp="")
    assert "X-Admin-TOTP" not in headers


def test_base_url_resolved_at_call_time(monkeypatch):
    """``_base_url`` reads env at call time (not import) so tests can patch."""
    monkeypatch.setenv("RW_BASE_URL", "https://rewards.example.com")
    assert rw_client._base_url() == "https://rewards.example.com"
    monkeypatch.setenv("RW_BASE_URL", "http://other.test:9999")
    assert rw_client._base_url() == "http://other.test:9999"


def test_timeout_constant_is_ten_seconds():
    """10 s mirrors au_client — admin actions are slow but not infinite."""
    assert rw_client._TIMEOUT_SECONDS == 10.0
