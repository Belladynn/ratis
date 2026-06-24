"""Tests for the admin mini UI Missions catalogue pages (PR2 — Bloc D follow-up).

Covers :

- ``GET  /admin/ui/missions``                — list catalogue + create form
- ``GET  /admin/ui/missions/{mission_id}``   — detail (edit form)
- ``POST /admin/ui/missions``                — create catalogue row
- ``POST /admin/ui/missions/{mission_id}``   — partial update

Mirrors ``test_admin_ui_battlepass.py`` script-table conventions.
RW endpoints (PR1 #277) :

- ``GET   /admin/missions/templates``
- ``POST  /admin/missions/templates``
- ``PATCH /admin/missions/templates/{id}``
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
        self._handlers: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}

    def on(
        self,
        method: str,
        path: str,
        *,
        status_code: int = 200,
        json_body: dict[str, Any] | None = None,
    ) -> None:
        self._handlers[(method.upper(), path)] = (status_code, json_body or {})


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

    monkeypatch.setattr("admin_ui.routes.rw_get", fake_rw_get)
    monkeypatch.setattr("admin_ui.routes.rw_post", fake_rw_post)
    monkeypatch.setattr("admin_ui.routes.rw_patch", fake_rw_patch, raising=False)
    return stub


@pytest.fixture(autouse=True)
def _set_rw_base_url(monkeypatch):
    monkeypatch.setenv("RW_BASE_URL", "http://ratis_rewards.test:8004")


# ============================================================================
# List page
# ============================================================================


class TestMissionsListPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get("/admin/ui/missions", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_renders_templates(self, raw_client, stub_rw):
        _login(raw_client)
        mid_a = str(uuid.uuid4())
        mid_b = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            "/admin/missions/templates",
            json_body={
                "templates": [
                    {
                        "id": mid_a,
                        "action_type": "receipt_scan",
                        "frequency": "daily",
                        "difficulty": "easy",
                        "target_count": 1,
                        "cab_reward": 50,
                        "is_active": True,
                        "is_boostable": True,
                    },
                    {
                        "id": mid_b,
                        "action_type": "barcode_scan",
                        "frequency": "weekly",
                        "difficulty": "hard",
                        "target_count": 20,
                        "cab_reward": 500,
                        "is_active": False,
                        "is_boostable": True,
                    },
                ],
                "total": 2,
            },
        )

        r = raw_client.get("/admin/ui/missions")
        assert r.status_code == 200
        assert "receipt_scan" in r.text
        assert "barcode_scan" in r.text
        assert f'href="/admin/ui/missions/{mid_a}"' in r.text

    def test_create_form_visible(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/missions/templates",
            json_body={"templates": [], "total": 0},
        )
        r = raw_client.get("/admin/ui/missions")
        assert r.status_code == 200
        assert 'name="action_type"' in r.text
        assert 'name="frequency"' in r.text
        assert 'name="difficulty"' in r.text
        assert 'name="target_count"' in r.text
        assert 'name="cab_reward"' in r.text

    def test_rw_error_renders_with_banner(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/missions/templates",
            status_code=502,
            json_body={"detail": "upstream"},
        )
        r = raw_client.get("/admin/ui/missions")
        assert r.status_code == 200
        assert "502" in r.text or "Erreur" in r.text


# ============================================================================
# Detail page
# ============================================================================


class TestMissionsDetailPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get(f"/admin/ui/missions/{uuid.uuid4()}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_renders_form_with_current_values(self, raw_client, stub_rw):
        _login(raw_client)
        mid = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            "/admin/missions/templates",
            json_body={
                "templates": [
                    {
                        "id": mid,
                        "action_type": "label_scan",
                        "frequency": "daily",
                        "difficulty": "medium",
                        "target_count": 3,
                        "cab_reward": 150,
                        "is_active": True,
                        "is_boostable": False,
                    }
                ],
                "total": 1,
            },
        )

        r = raw_client.get(f"/admin/ui/missions/{mid}")
        assert r.status_code == 200
        # Fields surface for edit.
        assert "label_scan" in r.text
        assert "150" in r.text
        # Form artefacts.
        assert 'name="target_count"' in r.text
        assert 'name="cab_reward"' in r.text

    def test_unknown_mission_renders_404(self, raw_client, stub_rw):
        _login(raw_client)
        mid = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            "/admin/missions/templates",
            json_body={"templates": [], "total": 0},
        )
        r = raw_client.get(f"/admin/ui/missions/{mid}")
        assert r.status_code == 404
        assert "introuvable" in r.text.lower() or "not found" in r.text.lower()


# ============================================================================
# Create catalogue action
# ============================================================================


class TestMissionsCreateAction:
    def test_create_redirects_with_flash(self, raw_client, stub_rw):
        _login(raw_client)
        mid = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            "/admin/missions/templates",
            status_code=201,
            json_body={
                "id": mid,
                "action_type": "price_compared",
                "frequency": "weekly",
                "difficulty": "hard",
                "target_count": 10,
                "cab_reward": 300,
                "is_active": True,
                "is_boostable": True,
            },
        )
        r = raw_client.post(
            "/admin/ui/missions",
            data={
                "action_type": "price_compared",
                "frequency": "weekly",
                "difficulty": "hard",
                "target_count": "10",
                "cab_reward": "300",
                "is_active": "on",
                "is_boostable": "on",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/missions" in r.headers["location"]
        post_calls = [c for c in stub_rw.calls if c[0] == "POST"]
        assert len(post_calls) == 1
        body = post_calls[0][3]
        assert body["action_type"] == "price_compared"
        assert body["frequency"] == "weekly"
        assert body["difficulty"] == "hard"
        assert body["target_count"] == 10
        assert body["cab_reward"] == 300
        assert body["is_active"] is True
        assert body["is_boostable"] is True

    def test_create_unchecked_flags_default_false(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "POST",
            "/admin/missions/templates",
            status_code=201,
            json_body={"id": str(uuid.uuid4())},
        )
        r = raw_client.post(
            "/admin/ui/missions",
            data={
                "action_type": "receipt_scan",
                "frequency": "daily",
                "difficulty": "easy",
                "target_count": "1",
                "cab_reward": "10",
                # is_active + is_boostable absent (unchecked)
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        body = next(c for c in stub_rw.calls if c[0] == "POST")[3]
        assert body["is_active"] is False
        assert body["is_boostable"] is False

    def test_create_uniqueness_conflict_flashes(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "POST",
            "/admin/missions/templates",
            status_code=409,
            json_body={"detail": "mission_uniqueness_conflict"},
        )
        r = raw_client.post(
            "/admin/ui/missions",
            data={
                "action_type": "receipt_scan",
                "frequency": "daily",
                "difficulty": "easy",
                "target_count": "1",
                "cab_reward": "10",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert "/admin/ui/missions" in loc
        assert "conflict" in loc.lower() or "uniqueness" in loc.lower()


# ============================================================================
# Update (PATCH proxy) action
# ============================================================================


class TestMissionsUpdateAction:
    def test_update_redirects_with_flash(self, raw_client, stub_rw):
        _login(raw_client)
        mid = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/missions/templates/{mid}",
            status_code=200,
            json_body={
                "id": mid,
                "action_type": "receipt_scan",
                "frequency": "daily",
                "difficulty": "easy",
                "target_count": 5,
                "cab_reward": 200,
                "is_active": True,
                "is_boostable": False,
            },
        )

        r = raw_client.post(
            f"/admin/ui/missions/{mid}",
            data={
                "action_type": "receipt_scan",
                "frequency": "daily",
                "difficulty": "easy",
                "target_count": "5",
                "cab_reward": "200",
                "is_active": "on",
                # is_boostable absent (unchecked)
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert f"/admin/ui/missions/{mid}" in r.headers["location"]
        patch_calls = [c for c in stub_rw.calls if c[0] == "PATCH"]
        assert len(patch_calls) == 1
        body = patch_calls[0][3]
        # All form fields forwarded — RW PATCH uses model_fields_set so
        # explicit absence on the wire = "no change". We send everything
        # the form surfaced (full-state replace from the operator's POV).
        assert body["target_count"] == 5
        assert body["cab_reward"] == 200
        assert body["is_active"] is True
        assert body["is_boostable"] is False

    def test_update_404_redirects_to_list(self, raw_client, stub_rw):
        _login(raw_client)
        mid = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/missions/templates/{mid}",
            status_code=404,
            json_body={"detail": "mission_not_found"},
        )
        r = raw_client.post(
            f"/admin/ui/missions/{mid}",
            data={
                "action_type": "receipt_scan",
                "frequency": "daily",
                "difficulty": "easy",
                "target_count": "1",
                "cab_reward": "10",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/missions" in r.headers["location"]

    def test_update_409_redirects_with_error(self, raw_client, stub_rw):
        _login(raw_client)
        mid = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/missions/templates/{mid}",
            status_code=409,
            json_body={"detail": "mission_uniqueness_conflict"},
        )
        r = raw_client.post(
            f"/admin/ui/missions/{mid}",
            data={
                "action_type": "receipt_scan",
                "frequency": "daily",
                "difficulty": "easy",
                "target_count": "1",
                "cab_reward": "10",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert f"/admin/ui/missions/{mid}" in loc
        assert "conflict" in loc.lower() or "uniqueness" in loc.lower()


# ============================================================================
# Dashboard tile
# ============================================================================


class TestMissionsDashboardTile:
    def test_dashboard_links_to_missions(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/")
        assert r.status_code == 200
        assert "/admin/ui/missions" in r.text
        assert "Missions" in r.text or "missions" in r.text.lower()
