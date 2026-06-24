"""Tests for the admin mini UI StreakTier pages (PR3 — Bloc D follow-up).

Covers full CRUD with delete-confirm flow :

- ``GET  /admin/ui/streak-tier``                       — list + create form
- ``GET  /admin/ui/streak-tier/{id}``                  — detail (edit form)
- ``POST /admin/ui/streak-tier``                       — create
- ``POST /admin/ui/streak-tier/{id}``                  — partial update
- ``GET  /admin/ui/streak-tier/{id}/delete``           — delete confirmation
- ``POST /admin/ui/streak-tier/{id}/delete``           — execute delete

RW endpoints proxied :
- ``GET    /admin/rewards/streak-tiers``
- ``GET    /admin/rewards/streak-tiers/{id}``
- ``POST   /admin/rewards/streak-tiers``
- ``PATCH  /admin/rewards/streak-tiers/{id}``
- ``DELETE /admin/rewards/streak-tiers/{id}``
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


class TestStreakTierListPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get("/admin/ui/streak-tier", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_renders_tiers(self, raw_client, stub_rw):
        _login(raw_client)
        st_a = str(uuid.uuid4())
        st_b = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            "/admin/rewards/streak-tiers",
            json_body={
                "tiers": [
                    {"id": st_a, "days": 7, "multiplier": "1.50", "label": "Bronze"},
                    {"id": st_b, "days": 30, "multiplier": "2.00", "label": "Silver"},
                ],
                "total": 2,
            },
        )

        r = raw_client.get("/admin/ui/streak-tier")
        assert r.status_code == 200
        assert "Bronze" in r.text
        assert "Silver" in r.text
        assert f'href="/admin/ui/streak-tier/{st_a}"' in r.text

    def test_create_form_visible(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/rewards/streak-tiers",
            json_body={"tiers": [], "total": 0},
        )
        r = raw_client.get("/admin/ui/streak-tier")
        assert r.status_code == 200
        assert 'name="days"' in r.text
        assert 'name="multiplier"' in r.text
        assert 'name="label"' in r.text


# ============================================================================
# Detail page
# ============================================================================


class TestStreakTierDetailPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get(f"/admin/ui/streak-tier/{uuid.uuid4()}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_detail_with_form(self, raw_client, stub_rw):
        _login(raw_client)
        st = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            f"/admin/rewards/streak-tiers/{st}",
            json_body={"id": st, "days": 7, "multiplier": "1.50", "label": "Bronze"},
        )

        r = raw_client.get(f"/admin/ui/streak-tier/{st}")
        assert r.status_code == 200
        assert "Bronze" in r.text
        assert 'name="days"' in r.text
        # Delete link visible.
        assert "/delete" in r.text or "supprimer" in r.text.lower()

    def test_unknown_renders_not_found(self, raw_client, stub_rw):
        _login(raw_client)
        st = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            f"/admin/rewards/streak-tiers/{st}",
            status_code=404,
            json_body={"detail": "streak_tier_not_found"},
        )
        r = raw_client.get(f"/admin/ui/streak-tier/{st}")
        assert r.status_code == 404


# ============================================================================
# Create / update actions
# ============================================================================


class TestStreakTierCreateAction:
    def test_create_redirects(self, raw_client, stub_rw):
        _login(raw_client)
        st = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            "/admin/rewards/streak-tiers",
            status_code=201,
            json_body={"id": st, "days": 14, "multiplier": "1.75", "label": "Iron"},
        )

        r = raw_client.post(
            "/admin/ui/streak-tier",
            data={"days": "14", "multiplier": "1.75", "label": "Iron"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/streak-tier" in r.headers["location"]
        post_calls = [c for c in stub_rw.calls if c[0] == "POST"]
        assert len(post_calls) == 1
        body = post_calls[0][3]
        assert body["days"] == 14
        assert body["label"] == "Iron"
        # Multiplier forwarded as string (preserves Decimal precision on RW).
        assert body["multiplier"] == "1.75"

    def test_create_conflict_flashes_error(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "POST",
            "/admin/rewards/streak-tiers",
            status_code=409,
            json_body={"detail": "streak_tier_uniqueness_conflict"},
        )

        r = raw_client.post(
            "/admin/ui/streak-tier",
            data={"days": "7", "multiplier": "1.5", "label": "Dup"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "conflict" in r.headers["location"].lower()


class TestStreakTierUpdateAction:
    def test_update_handles_errors(self, raw_client, stub_rw):
        _login(raw_client)
        st = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/rewards/streak-tiers/{st}",
            status_code=409,
            json_body={"detail": "streak_tier_uniqueness_conflict"},
        )

        r = raw_client.post(
            f"/admin/ui/streak-tier/{st}",
            data={"days": "7", "multiplier": "1.5", "label": "Bronze"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert f"/admin/ui/streak-tier/{st}" in loc
        assert "conflict" in loc.lower()

    def test_update_success_redirects_to_detail(self, raw_client, stub_rw):
        _login(raw_client)
        st = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/rewards/streak-tiers/{st}",
            status_code=200,
            json_body={"id": st, "days": 30, "multiplier": "2.00", "label": "Gold"},
        )

        r = raw_client.post(
            f"/admin/ui/streak-tier/{st}",
            data={"days": "30", "multiplier": "2.00", "label": "Gold"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert f"/admin/ui/streak-tier/{st}" in r.headers["location"]


# ============================================================================
# Delete flow
# ============================================================================


class TestStreakTierDeleteFlow:
    def test_delete_confirm_page(self, raw_client, stub_rw):
        _login(raw_client)
        st = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            f"/admin/rewards/streak-tiers/{st}",
            json_body={"id": st, "days": 7, "multiplier": "1.5", "label": "Bronze"},
        )
        r = raw_client.get(f"/admin/ui/streak-tier/{st}/delete")
        assert r.status_code == 200
        assert f'action="/admin/ui/streak-tier/{st}/delete"' in r.text

    def test_delete_calls_rw_delete(self, raw_client, stub_rw):
        _login(raw_client)
        st = str(uuid.uuid4())
        stub_rw.on(
            "DELETE",
            f"/admin/rewards/streak-tiers/{st}",
            status_code=204,
        )
        r = raw_client.post(
            f"/admin/ui/streak-tier/{st}/delete",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/streak-tier" in r.headers["location"]
        delete_calls = [c for c in stub_rw.calls if c[0] == "DELETE"]
        assert len(delete_calls) == 1
        assert delete_calls[0][1] == f"/admin/rewards/streak-tiers/{st}"

    def test_delete_404_redirects_to_list(self, raw_client, stub_rw):
        _login(raw_client)
        st = str(uuid.uuid4())
        stub_rw.on(
            "DELETE",
            f"/admin/rewards/streak-tiers/{st}",
            status_code=404,
            json_body={"detail": "streak_tier_not_found"},
        )
        r = raw_client.post(
            f"/admin/ui/streak-tier/{st}/delete",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/streak-tier" in r.headers["location"]


# ============================================================================
# Dashboard tile
# ============================================================================


class TestStreakTierDashboardTile:
    def test_dashboard_links_to_streak_tier(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/")
        assert r.status_code == 200
        assert "/admin/ui/streak-tier" in r.text
