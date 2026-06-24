"""Tests for the admin mini UI Mystery Product pages (PR3 — Bloc D follow-up).

Covers full CRUD with delete-confirm flow :

- ``GET  /admin/ui/mystery``                         — list challenges + create form
- ``GET  /admin/ui/mystery/{mystery_id}``            — detail (edit form)
- ``POST /admin/ui/mystery``                         — create
- ``POST /admin/ui/mystery/{mystery_id}``            — partial update
- ``GET  /admin/ui/mystery/{mystery_id}/delete``     — delete confirmation page
- ``POST /admin/ui/mystery/{mystery_id}/delete``     — execute delete

RW endpoints proxied :
- ``GET    /admin/mystery``
- ``POST   /admin/mystery``
- ``PATCH  /admin/mystery/{id}``
- ``DELETE /admin/mystery/{id}``
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


class TestMysteryListPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get("/admin/ui/mystery", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_renders_challenges(self, raw_client, stub_rw):
        _login(raw_client)
        mid_a = str(uuid.uuid4())
        mid_b = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            "/admin/mystery",
            json_body=[
                {
                    "id": mid_a,
                    "product_ean": "3017620422003",
                    "starts_at": "2026-05-01T00:00:00",
                    "ends_at": "2026-05-08T00:00:00",
                    "status": "active",
                    "reward_tiers": [{"tier": 1, "cab": 100}],
                    "finds_count": 12,
                },
                {
                    "id": mid_b,
                    "product_ean": "3274080005003",
                    "starts_at": "2026-05-10T00:00:00",
                    "ends_at": "2026-05-17T00:00:00",
                    "status": "scheduled",
                    "reward_tiers": [],
                    "finds_count": 0,
                },
            ],
        )

        r = raw_client.get("/admin/ui/mystery")
        assert r.status_code == 200
        assert "3017620422003" in r.text
        assert "3274080005003" in r.text
        assert f'href="/admin/ui/mystery/{mid_a}"' in r.text

    def test_create_form_visible(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on("GET", "/admin/mystery", json_body=[])
        r = raw_client.get("/admin/ui/mystery")
        assert r.status_code == 200
        assert 'name="starts_at"' in r.text
        assert 'name="reward_tiers"' in r.text
        assert 'name="clues"' in r.text


# ============================================================================
# Detail page (edit form)
# ============================================================================


class TestMysteryDetailPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get(f"/admin/ui/mystery/{uuid.uuid4()}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_detail_with_form(self, raw_client, stub_rw):
        _login(raw_client)
        mid = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            "/admin/mystery",
            json_body=[
                {
                    "id": mid,
                    "product_ean": "3017620422003",
                    "starts_at": "2026-05-01T00:00:00",
                    "ends_at": "2026-05-08T00:00:00",
                    "status": "scheduled",
                    "reward_tiers": [{"tier": 1, "cab": 100}],
                    "finds_count": 0,
                }
            ],
        )

        r = raw_client.get(f"/admin/ui/mystery/{mid}")
        assert r.status_code == 200
        assert "3017620422003" in r.text
        assert 'name="starts_at"' in r.text
        assert 'name="reward_tiers"' in r.text
        # Delete link/button visible.
        assert "/delete" in r.text or "supprimer" in r.text.lower()

    def test_unknown_renders_not_found(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on("GET", "/admin/mystery", json_body=[])
        r = raw_client.get(f"/admin/ui/mystery/{uuid.uuid4()}")
        assert r.status_code == 404


# ============================================================================
# Create / update actions
# ============================================================================


class TestMysteryCreateAction:
    def test_create_redirects(self, raw_client, stub_rw):
        _login(raw_client)
        mid = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            "/admin/mystery",
            status_code=200,
            json_body={"id": mid},
        )

        r = raw_client.post(
            "/admin/ui/mystery",
            data={
                "starts_at": "2026-06-01T00:00:00",
                "product_ean": "3017620422003",
                "reward_tiers": '[{"tier": 1, "cab": 100}]',
                "clues": '[{"reveal_day": 1, "clue_text": "Indice 1"}]',
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/mystery" in r.headers["location"]
        post_calls = [c for c in stub_rw.calls if c[0] == "POST"]
        assert len(post_calls) == 1
        body = post_calls[0][3]
        assert body["product_ean"] == "3017620422003"
        assert body["reward_tiers"] == [{"tier": 1, "cab": 100}]

    def test_create_invalid_json_redirects_with_error(self, raw_client, stub_rw):
        _login(raw_client)
        r = raw_client.post(
            "/admin/ui/mystery",
            data={
                "starts_at": "2026-06-01T00:00:00",
                "reward_tiers": "not json",
                "clues": "[]",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        # No POST hit RW (fail-fast).
        post_calls = [c for c in stub_rw.calls if c[0] == "POST"]
        assert len(post_calls) == 0


class TestMysteryUpdateAction:
    def test_update_handles_errors(self, raw_client, stub_rw):
        _login(raw_client)
        mid = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/mystery/{mid}",
            status_code=409,
            json_body={"detail": "challenge_not_modifiable"},
        )

        r = raw_client.post(
            f"/admin/ui/mystery/{mid}",
            data={
                "starts_at": "2026-06-15T00:00:00",
                "reward_tiers": '[{"tier": 1, "cab": 200}]',
                "clues": "[]",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert f"/admin/ui/mystery/{mid}" in loc
        assert "conflict" in loc.lower() or "not_modifiable" in loc.lower()

    def test_update_success_redirects_to_detail(self, raw_client, stub_rw):
        _login(raw_client)
        mid = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/mystery/{mid}",
            status_code=200,
            json_body={"id": mid},
        )

        r = raw_client.post(
            f"/admin/ui/mystery/{mid}",
            data={
                "starts_at": "2026-06-15T00:00:00",
                "reward_tiers": '[{"tier": 1, "cab": 200}]',
                "clues": '[{"reveal_day": 2, "clue_text": "Hint"}]',
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert f"/admin/ui/mystery/{mid}" in r.headers["location"]


# ============================================================================
# Delete flow (confirm-then-execute)
# ============================================================================


class TestMysteryDeleteFlow:
    def test_delete_confirm_page_shows_confirmation(self, raw_client, stub_rw):
        _login(raw_client)
        mid = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            "/admin/mystery",
            json_body=[
                {
                    "id": mid,
                    "product_ean": "3017620422003",
                    "starts_at": "2026-05-01T00:00:00",
                    "ends_at": "2026-05-08T00:00:00",
                    "status": "scheduled",
                    "reward_tiers": [],
                    "finds_count": 0,
                }
            ],
        )

        r = raw_client.get(f"/admin/ui/mystery/{mid}/delete")
        assert r.status_code == 200
        # Confirm form posts to .../delete
        assert f'action="/admin/ui/mystery/{mid}/delete"' in r.text
        assert "supprimer" in r.text.lower() or "confirmer" in r.text.lower() or "delete" in r.text.lower()

    def test_delete_calls_rw_delete(self, raw_client, stub_rw):
        _login(raw_client)
        mid = str(uuid.uuid4())
        stub_rw.on(
            "DELETE",
            f"/admin/mystery/{mid}",
            status_code=204,
            json_body={"deleted": True},
        )

        r = raw_client.post(
            f"/admin/ui/mystery/{mid}/delete",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/mystery" in r.headers["location"]
        delete_calls = [c for c in stub_rw.calls if c[0] == "DELETE"]
        assert len(delete_calls) == 1
        assert delete_calls[0][1] == f"/admin/mystery/{mid}"

    def test_delete_409_flashes_error(self, raw_client, stub_rw):
        _login(raw_client)
        mid = str(uuid.uuid4())
        stub_rw.on(
            "DELETE",
            f"/admin/mystery/{mid}",
            status_code=409,
            json_body={"detail": "challenge_not_modifiable"},
        )

        r = raw_client.post(
            f"/admin/ui/mystery/{mid}/delete",
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert "conflict" in loc.lower() or "not_modifiable" in loc.lower()


# ============================================================================
# Dashboard tile
# ============================================================================


class TestMysteryDashboardTile:
    def test_dashboard_links_to_mystery(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/")
        assert r.status_code == 200
        assert "/admin/ui/mystery" in r.text
        assert "Mystery" in r.text or "mystery" in r.text.lower()
