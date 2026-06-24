"""Tests for the admin mini UI RewardConfig pages (PR3 — Bloc D follow-up).

Covers full CRUD with delete-confirm flow :

- ``GET  /admin/ui/reward-config``                       — list + create form
- ``GET  /admin/ui/reward-config/{id}``                  — detail (edit form)
- ``POST /admin/ui/reward-config``                       — create
- ``POST /admin/ui/reward-config/{id}``                  — partial update
- ``GET  /admin/ui/reward-config/{id}/delete``           — delete confirmation
- ``POST /admin/ui/reward-config/{id}/delete``           — execute delete

RW endpoints proxied :
- ``GET    /admin/rewards/configs``
- ``GET    /admin/rewards/configs/{id}``
- ``POST   /admin/rewards/configs``
- ``PATCH  /admin/rewards/configs/{id}``
- ``DELETE /admin/rewards/configs/{id}``
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest


def _login(raw_client, api_key: str = "test-admin-key-padded-to-32-chars-min", operator: str = "tester"):
    return raw_client.post(
        "/admin/ui/login",
        data={"api_key": api_key, "operator": operator},
        follow_redirects=False,
    )


class _StubRW:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, str | None]] = []
        self._handlers: dict[tuple[str, str], tuple[int, Any]] = {}

    def on(
        self,
        method: str,
        path: str,
        *,
        status_code: int = 200,
        json_body: Any = None,
    ) -> None:
        self._handlers[(method.upper(), path)] = (status_code, json_body if json_body is not None else {})


@pytest.fixture
def stub_rw(monkeypatch):
    stub = _StubRW()

    async def fake_rw_get(path, *, operator, params=None):
        stub.calls.append(("GET", path, dict(params or {}), None, None))
        if ("GET", path) in stub._handlers:
            code, body = stub._handlers[("GET", path)]
            return httpx.Response(code, json=body)
        return httpx.Response(404, json={"detail": "not_handled"})

    async def fake_rw_post(path, *, operator, json, totp=None):
        stub.calls.append(("POST", path, None, dict(json) if json is not None else None, totp))
        if ("POST", path) in stub._handlers:
            code, body = stub._handlers[("POST", path)]
            return httpx.Response(code, json=body)
        return httpx.Response(404, json={"detail": "not_handled"})

    async def fake_rw_patch(path, *, operator, json=None):
        stub.calls.append(("PATCH", path, None, dict(json) if json is not None else None, None))
        if ("PATCH", path) in stub._handlers:
            code, body = stub._handlers[("PATCH", path)]
            return httpx.Response(code, json=body)
        return httpx.Response(404, json={"detail": "not_handled"})

    async def fake_rw_delete(path, *, operator):
        stub.calls.append(("DELETE", path, None, None, None))
        if ("DELETE", path) in stub._handlers:
            code, body = stub._handlers[("DELETE", path)]
            return httpx.Response(code, json=body)
        return httpx.Response(404, json={"detail": "not_handled"})

    monkeypatch.setattr("admin_ui.routes.rw_get", fake_rw_get)
    monkeypatch.setattr("admin_ui.routes.rw_post", fake_rw_post)
    monkeypatch.setattr("admin_ui.routes.rw_patch", fake_rw_patch, raising=False)
    monkeypatch.setattr("admin_ui.routes.rw_delete", fake_rw_delete, raising=False)
    return stub


@pytest.fixture(autouse=True)
def _set_rw_base_url(monkeypatch):
    monkeypatch.setenv("RW_BASE_URL", "http://ratis_rewards.test:8004")


# ============================================================================
# List page
# ============================================================================


class TestRewardConfigListPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get("/admin/ui/reward-config", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_renders_configs(self, raw_client, stub_rw):
        _login(raw_client)
        rc_a = str(uuid.uuid4())
        rc_b = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            "/admin/rewards/configs",
            json_body={
                "configs": [
                    {"id": rc_a, "action_type": "receipt_scan", "base_amount": 50},
                    {"id": rc_b, "action_type": "label_scan", "base_amount": 20},
                ],
                "total": 2,
            },
        )

        r = raw_client.get("/admin/ui/reward-config")
        assert r.status_code == 200
        assert "receipt_scan" in r.text
        assert "label_scan" in r.text
        assert f'href="/admin/ui/reward-config/{rc_a}"' in r.text

    def test_create_form_visible(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/rewards/configs",
            json_body={"configs": [], "total": 0},
        )
        r = raw_client.get("/admin/ui/reward-config")
        assert r.status_code == 200
        assert 'name="action_type"' in r.text
        assert 'name="base_amount"' in r.text


# ============================================================================
# Detail page
# ============================================================================


class TestRewardConfigDetailPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get(f"/admin/ui/reward-config/{uuid.uuid4()}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_detail_with_form(self, raw_client, stub_rw):
        _login(raw_client)
        rc = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            f"/admin/rewards/configs/{rc}",
            json_body={"id": rc, "action_type": "receipt_scan", "base_amount": 50},
        )

        r = raw_client.get(f"/admin/ui/reward-config/{rc}")
        assert r.status_code == 200
        assert "receipt_scan" in r.text
        assert 'name="action_type"' in r.text
        assert 'name="base_amount"' in r.text
        # Delete link visible.
        assert "/delete" in r.text or "supprimer" in r.text.lower()

    def test_unknown_renders_not_found(self, raw_client, stub_rw):
        _login(raw_client)
        rc = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            f"/admin/rewards/configs/{rc}",
            status_code=404,
            json_body={"detail": "reward_config_not_found"},
        )
        r = raw_client.get(f"/admin/ui/reward-config/{rc}")
        assert r.status_code == 404


# ============================================================================
# Create / update actions
# ============================================================================


class TestRewardConfigCreateAction:
    def test_create_redirects(self, raw_client, stub_rw):
        _login(raw_client)
        rc = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            "/admin/rewards/configs",
            status_code=201,
            json_body={"id": rc, "action_type": "barcode_scan", "base_amount": 10},
        )

        r = raw_client.post(
            "/admin/ui/reward-config",
            data={"action_type": "barcode_scan", "base_amount": "10"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/reward-config" in r.headers["location"]
        post_calls = [c for c in stub_rw.calls if c[0] == "POST"]
        assert len(post_calls) == 1
        body = post_calls[0][3]
        assert body["action_type"] == "barcode_scan"
        assert body["base_amount"] == 10

    def test_create_conflict_flashes_error(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "POST",
            "/admin/rewards/configs",
            status_code=409,
            json_body={"detail": "reward_config_uniqueness_conflict"},
        )

        r = raw_client.post(
            "/admin/ui/reward-config",
            data={"action_type": "receipt_scan", "base_amount": "50"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "conflict" in r.headers["location"].lower()


class TestRewardConfigUpdateAction:
    def test_update_handles_errors(self, raw_client, stub_rw):
        _login(raw_client)
        rc = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/rewards/configs/{rc}",
            status_code=409,
            json_body={"detail": "reward_config_uniqueness_conflict"},
        )

        r = raw_client.post(
            f"/admin/ui/reward-config/{rc}",
            data={"action_type": "receipt_scan", "base_amount": "50"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert f"/admin/ui/reward-config/{rc}" in loc
        assert "conflict" in loc.lower()

    def test_update_success_redirects_to_detail(self, raw_client, stub_rw):
        _login(raw_client)
        rc = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/rewards/configs/{rc}",
            status_code=200,
            json_body={"id": rc, "action_type": "receipt_scan", "base_amount": 75},
        )

        r = raw_client.post(
            f"/admin/ui/reward-config/{rc}",
            data={"action_type": "receipt_scan", "base_amount": "75"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert f"/admin/ui/reward-config/{rc}" in r.headers["location"]


# ============================================================================
# Delete flow
# ============================================================================


class TestRewardConfigDeleteFlow:
    def test_delete_confirm_page(self, raw_client, stub_rw):
        _login(raw_client)
        rc = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            f"/admin/rewards/configs/{rc}",
            json_body={"id": rc, "action_type": "receipt_scan", "base_amount": 50},
        )
        r = raw_client.get(f"/admin/ui/reward-config/{rc}/delete")
        assert r.status_code == 200
        assert f'action="/admin/ui/reward-config/{rc}/delete"' in r.text

    def test_delete_calls_rw_delete(self, raw_client, stub_rw):
        _login(raw_client)
        rc = str(uuid.uuid4())
        stub_rw.on(
            "DELETE",
            f"/admin/rewards/configs/{rc}",
            status_code=204,
        )
        r = raw_client.post(
            f"/admin/ui/reward-config/{rc}/delete",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/reward-config" in r.headers["location"]
        delete_calls = [c for c in stub_rw.calls if c[0] == "DELETE"]
        assert len(delete_calls) == 1
        assert delete_calls[0][1] == f"/admin/rewards/configs/{rc}"

    def test_delete_404_redirects_to_list(self, raw_client, stub_rw):
        _login(raw_client)
        rc = str(uuid.uuid4())
        stub_rw.on(
            "DELETE",
            f"/admin/rewards/configs/{rc}",
            status_code=404,
            json_body={"detail": "reward_config_not_found"},
        )
        r = raw_client.post(
            f"/admin/ui/reward-config/{rc}/delete",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/reward-config" in r.headers["location"]


# ============================================================================
# Dashboard tile
# ============================================================================


class TestRewardConfigDashboardTile:
    def test_dashboard_links_to_reward_config(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/")
        assert r.status_code == 200
        assert "/admin/ui/reward-config" in r.text
