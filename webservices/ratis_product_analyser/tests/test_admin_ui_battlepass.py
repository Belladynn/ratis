"""Tests for the admin mini UI Battle Pass pages (PR2 — Bloc D follow-up).

Covers :

- ``GET  /admin/ui/battlepass``                       — list seasons
- ``GET  /admin/ui/battlepass/{season_id}``           — detail (season + milestones form)
- ``POST /admin/ui/battlepass``                       — create season
- ``POST /admin/ui/battlepass/{season_id}``           — create milestone (form POST)
- ``POST /admin/ui/battlepass/{season_id}/validate``  — activate season

All RW calls go through the shared ``rw_get`` / ``rw_post`` helpers
which we monkeypatch with an in-memory script-table fixture (calque on
``test_admin_ui_settings.py``). Auth gate is the same cookie pattern as
the rest of the admin UI.

Note on RW endpoint surface (PR1 #277) :

- ``GET   /admin/battlepass/seasons``                  — read-only listing
- ``POST  /admin/battlepass/seasons``                  — create draft season
- ``PATCH /admin/battlepass/seasons/{id}/activate``    — single-active
- ``POST  /admin/battlepass/seasons/{id}/tiers``       — create milestone

There is currently no ``GET /admin/battlepass/seasons/{id}`` endpoint
that returns milestones — the detail page reuses the listing call and
filters client-side. Existing milestones cannot be listed yet (flagged
in NOTES of the report-back ; follow-up PR3).
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

# ============================================================================
# Helpers
# ============================================================================


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
    """Replace rw_get / rw_post / rw_patch with in-memory script-table."""
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


class TestBattlePassListPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get("/admin/ui/battlepass", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_renders_seasons(self, raw_client, stub_rw):
        _login(raw_client)
        sid_a = str(uuid.uuid4())
        sid_b = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            "/admin/battlepass/seasons",
            json_body={
                "seasons": [
                    {
                        "id": sid_a,
                        "season_number": 2,
                        "name": "Saison 2 - Hiver",
                        "started_at": "2026-01-01T00:00:00",
                        "ends_at": "2026-03-31T23:59:59",
                        "is_active": True,
                    },
                    {
                        "id": sid_b,
                        "season_number": 1,
                        "name": "Saison 1 - Pilote",
                        "started_at": "2025-10-01T00:00:00",
                        "ends_at": "2025-12-31T23:59:59",
                        "is_active": False,
                    },
                ],
                "total": 2,
            },
        )

        r = raw_client.get("/admin/ui/battlepass")
        assert r.status_code == 200
        assert "Saison 2 - Hiver" in r.text
        assert "Saison 1 - Pilote" in r.text
        # Detail link to each season.
        assert f'href="/admin/ui/battlepass/{sid_a}"' in r.text
        # Active marker visible.
        assert "Active" in r.text or "active" in r.text.lower()

    def test_create_form_visible(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/battlepass/seasons",
            json_body={"seasons": [], "total": 0},
        )
        r = raw_client.get("/admin/ui/battlepass")
        assert r.status_code == 200
        # Form artefacts for "create season".
        assert 'name="name"' in r.text
        assert 'name="season_number"' in r.text
        assert 'name="started_at"' in r.text
        assert 'name="ends_at"' in r.text

    def test_rw_error_renders_with_banner(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/battlepass/seasons",
            status_code=502,
            json_body={"detail": "upstream"},
        )
        r = raw_client.get("/admin/ui/battlepass")
        assert r.status_code == 200  # graceful degradation
        assert "502" in r.text or "Erreur" in r.text


# ============================================================================
# Detail page
# ============================================================================


class TestBattlePassDetailPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get(f"/admin/ui/battlepass/{uuid.uuid4()}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_renders_season_with_milestone_form(self, raw_client, stub_rw):
        _login(raw_client)
        sid = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            "/admin/battlepass/seasons",
            json_body={
                "seasons": [
                    {
                        "id": sid,
                        "season_number": 3,
                        "name": "Saison 3",
                        "started_at": "2026-04-01T00:00:00",
                        "ends_at": "2026-06-30T23:59:59",
                        "is_active": False,
                    }
                ],
                "total": 1,
            },
        )

        r = raw_client.get(f"/admin/ui/battlepass/{sid}")
        assert r.status_code == 200
        assert "Saison 3" in r.text
        # Milestone create form artefacts.
        assert 'name="milestone_number"' in r.text
        assert 'name="cab_required"' in r.text
        assert 'name="reward_type"' in r.text
        assert 'name="reward_value"' in r.text
        # Validate / activate button visible (not active).
        assert "/validate" in r.text or "Activer" in r.text or "activer" in r.text.lower()

    def test_unknown_season_renders_not_found(self, raw_client, stub_rw):
        _login(raw_client)
        sid = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            "/admin/battlepass/seasons",
            json_body={"seasons": [], "total": 0},
        )
        r = raw_client.get(f"/admin/ui/battlepass/{sid}")
        assert r.status_code == 404
        assert "introuvable" in r.text.lower() or "not found" in r.text.lower()


# ============================================================================
# Create season action
# ============================================================================


class TestBattlePassCreateAction:
    def test_create_redirects_to_list_with_flash(self, raw_client, stub_rw):
        _login(raw_client)
        sid = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            "/admin/battlepass/seasons",
            status_code=201,
            json_body={
                "id": sid,
                "season_number": 4,
                "name": "Saison 4",
                "started_at": "2026-07-01T00:00:00",
                "ends_at": "2026-09-30T23:59:59",
                "is_active": False,
            },
        )

        r = raw_client.post(
            "/admin/ui/battlepass",
            data={
                "name": "Saison 4",
                "season_number": "4",
                "started_at": "2026-07-01T00:00:00",
                "ends_at": "2026-09-30T23:59:59",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/battlepass" in r.headers["location"]
        # POST body forwarded with parsed types.
        post_calls = [c for c in stub_rw.calls if c[0] == "POST"]
        assert len(post_calls) == 1
        body = post_calls[0][3]
        assert body["name"] == "Saison 4"
        assert body["season_number"] == 4
        assert body["started_at"] == "2026-07-01T00:00:00"
        assert body["ends_at"] == "2026-09-30T23:59:59"

    def test_create_conflict_redirects_with_error_flash(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "POST",
            "/admin/battlepass/seasons",
            status_code=409,
            json_body={"detail": "season_number_conflict"},
        )

        r = raw_client.post(
            "/admin/ui/battlepass",
            data={
                "name": "Saison Bis",
                "season_number": "1",
                "started_at": "2026-07-01T00:00:00",
                "ends_at": "2026-09-30T23:59:59",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert "/admin/ui/battlepass" in loc
        assert "conflict" in loc.lower() or "409" in loc

    def test_create_validation_error_redirects_with_flash(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "POST",
            "/admin/battlepass/seasons",
            status_code=422,
            json_body={"detail": "ends_at_must_be_after_started_at"},
        )

        r = raw_client.post(
            "/admin/ui/battlepass",
            data={
                "name": "Saison Inversée",
                "season_number": "5",
                "started_at": "2026-09-30T23:59:59",
                "ends_at": "2026-07-01T00:00:00",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert "/admin/ui/battlepass" in loc


# ============================================================================
# Create milestone action (POST /battlepass/{id})
# ============================================================================


class TestBattlePassMilestoneAction:
    def test_create_milestone_redirects_to_detail(self, raw_client, stub_rw):
        _login(raw_client)
        sid = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            f"/admin/battlepass/seasons/{sid}/tiers",
            status_code=201,
            json_body={
                "id": str(uuid.uuid4()),
                "season_id": sid,
                "milestone_number": 1,
                "cab_required": 100,
                "reward_type": "cab",
                "reward_value": 50,
                "subscriber_only": False,
            },
        )

        r = raw_client.post(
            f"/admin/ui/battlepass/{sid}",
            data={
                "milestone_number": "1",
                "cab_required": "100",
                "reward_type": "cab",
                "reward_value": "50",
                "subscriber_only": "false",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert f"/admin/ui/battlepass/{sid}" in r.headers["location"]
        post_calls = [c for c in stub_rw.calls if c[0] == "POST"]
        assert len(post_calls) == 1
        body = post_calls[0][3]
        assert body["milestone_number"] == 1
        assert body["cab_required"] == 100
        assert body["reward_type"] == "cab"
        assert body["reward_value"] == 50
        assert body["subscriber_only"] is False

    def test_create_milestone_subscriber_only_true_when_checked(self, raw_client, stub_rw):
        _login(raw_client)
        sid = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            f"/admin/battlepass/seasons/{sid}/tiers",
            status_code=201,
            json_body={"id": str(uuid.uuid4()), "season_id": sid},
        )

        r = raw_client.post(
            f"/admin/ui/battlepass/{sid}",
            data={
                "milestone_number": "5",
                "cab_required": "1000",
                "reward_type": "skin",
                "reward_value": "42",
                "subscriber_only": "on",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        body = next(c for c in stub_rw.calls if c[0] == "POST")[3]
        assert body["subscriber_only"] is True

    def test_create_milestone_conflict_flashes_error(self, raw_client, stub_rw):
        _login(raw_client)
        sid = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            f"/admin/battlepass/seasons/{sid}/tiers",
            status_code=409,
            json_body={"detail": "milestone_number_conflict"},
        )

        r = raw_client.post(
            f"/admin/ui/battlepass/{sid}",
            data={
                "milestone_number": "1",
                "cab_required": "100",
                "reward_type": "cab",
                "reward_value": "50",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert f"/admin/ui/battlepass/{sid}" in loc
        assert "conflict" in loc.lower()


# ============================================================================
# Validate / activate season action
# ============================================================================


class TestBattlePassValidateAction:
    def test_validate_redirects_with_success_flash(self, raw_client, stub_rw):
        _login(raw_client)
        sid = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/battlepass/seasons/{sid}/activate",
            json_body={"id": sid, "is_active": True},
        )

        r = raw_client.post(
            f"/admin/ui/battlepass/{sid}/validate",
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert f"/admin/ui/battlepass/{sid}" in loc
        # PATCH was issued.
        patch_calls = [c for c in stub_rw.calls if c[0] == "PATCH"]
        assert len(patch_calls) == 1

    def test_validate_409_active_conflict_flashes(self, raw_client, stub_rw):
        _login(raw_client)
        sid = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/battlepass/seasons/{sid}/activate",
            status_code=409,
            json_body={"detail": "active_season_conflict"},
        )
        r = raw_client.post(
            f"/admin/ui/battlepass/{sid}/validate",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "conflict" in r.headers["location"].lower() or "active" in r.headers["location"].lower()

    def test_validate_404_redirects_to_list(self, raw_client, stub_rw):
        _login(raw_client)
        sid = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/battlepass/seasons/{sid}/activate",
            status_code=404,
            json_body={"detail": "season_not_found"},
        )
        r = raw_client.post(
            f"/admin/ui/battlepass/{sid}/validate",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/battlepass" in r.headers["location"]


# ============================================================================
# Dashboard tile
# ============================================================================


class TestBattlePassDashboardTile:
    def test_dashboard_links_to_battlepass(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/")
        assert r.status_code == 200
        assert "/admin/ui/battlepass" in r.text
        assert "Battle Pass" in r.text or "battle pass" in r.text.lower()
