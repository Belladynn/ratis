"""Tests for the admin mini UI Challenges pages (PR3 — Bloc D follow-up).

Covers :

- ``GET  /admin/ui/challenges``                                — list challenges + create form
- ``GET  /admin/ui/challenges/{challenge_id}``                 — detail (challenge + milestones form)
- ``POST /admin/ui/challenges``                                — create challenge
- ``POST /admin/ui/challenges/{challenge_id}``                 — update challenge (PATCH activate/deactivate)
- ``POST /admin/ui/challenges/{challenge_id}/milestones``      — create milestone

Mirrors ``test_admin_ui_battlepass.py`` script-table conventions.
RW endpoints (existing on main) :

- ``GET   /admin/challenges``                          — list with state
- ``POST  /admin/challenges``                          — create
- ``POST  /admin/challenges/{id}/milestones``          — create milestone
- ``PATCH /admin/challenges/{id}/activate``            — activate
- ``PATCH /admin/challenges/{id}/deactivate``          — deactivate
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


class TestChallengesListPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get("/admin/ui/challenges", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_renders_challenges(self, raw_client, stub_rw):
        _login(raw_client)
        cid_a = str(uuid.uuid4())
        cid_b = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            "/admin/challenges",
            json_body=[
                {
                    "id": cid_a,
                    "title": "Scan Sprint",
                    "action_type": "receipt_scan",
                    "objective": 1000,
                    "starts_at": "2026-05-01T00:00:00",
                    "ends_at": "2026-05-31T23:59:59",
                    "is_active": True,
                    "current_count": 250,
                    "milestone_count": 3,
                    "status": "active",
                },
                {
                    "id": cid_b,
                    "title": "Label Marathon",
                    "action_type": "label_scan",
                    "objective": 500,
                    "starts_at": "2026-06-01T00:00:00",
                    "ends_at": "2026-06-30T23:59:59",
                    "is_active": False,
                    "current_count": 0,
                    "milestone_count": 0,
                    "status": "scheduled",
                },
            ],
        )

        r = raw_client.get("/admin/ui/challenges")
        assert r.status_code == 200
        assert "Scan Sprint" in r.text
        assert "Label Marathon" in r.text
        assert f'href="/admin/ui/challenges/{cid_a}"' in r.text

    def test_create_form_visible(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on("GET", "/admin/challenges", json_body=[])
        r = raw_client.get("/admin/ui/challenges")
        assert r.status_code == 200
        assert 'name="title"' in r.text
        assert 'name="action_type"' in r.text
        assert 'name="objective"' in r.text
        assert 'name="starts_at"' in r.text
        assert 'name="ends_at"' in r.text

    def test_rw_error_renders_with_banner(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/challenges",
            status_code=502,
            json_body={"detail": "upstream"},
        )
        r = raw_client.get("/admin/ui/challenges")
        assert r.status_code == 200
        assert "502" in r.text or "Erreur" in r.text


# ============================================================================
# Detail page
# ============================================================================


class TestChallengesDetailPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get(f"/admin/ui/challenges/{uuid.uuid4()}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_renders_challenge_with_milestone_form(self, raw_client, stub_rw):
        _login(raw_client)
        cid = str(uuid.uuid4())
        stub_rw.on(
            "GET",
            "/admin/challenges",
            json_body=[
                {
                    "id": cid,
                    "title": "Scan Sprint",
                    "action_type": "receipt_scan",
                    "objective": 1000,
                    "starts_at": "2026-05-01T00:00:00",
                    "ends_at": "2026-05-31T23:59:59",
                    "is_active": False,
                    "current_count": 0,
                    "milestone_count": 0,
                    "status": "scheduled",
                }
            ],
        )

        r = raw_client.get(f"/admin/ui/challenges/{cid}")
        assert r.status_code == 200
        assert "Scan Sprint" in r.text
        assert 'name="threshold"' in r.text
        assert 'name="reward_type"' in r.text
        assert 'name="reward_value"' in r.text
        # Activate / deactivate button present.
        assert "activate" in r.text.lower() or "désactiver" in r.text.lower()

    def test_unknown_challenge_renders_not_found(self, raw_client, stub_rw):
        _login(raw_client)
        cid = str(uuid.uuid4())
        stub_rw.on("GET", "/admin/challenges", json_body=[])
        r = raw_client.get(f"/admin/ui/challenges/{cid}")
        assert r.status_code == 404
        assert "introuvable" in r.text.lower() or "not found" in r.text.lower()


# ============================================================================
# Create challenge action
# ============================================================================


class TestChallengesCreateAction:
    def test_create_redirects_to_list_with_flash(self, raw_client, stub_rw):
        _login(raw_client)
        cid = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            "/admin/challenges",
            status_code=201,
            json_body={
                "id": cid,
                "title": "New Challenge",
                "action_type": "receipt_scan",
                "objective": 500,
                "starts_at": "2026-07-01T00:00:00",
                "ends_at": "2026-07-31T23:59:59",
                "is_active": False,
            },
        )

        r = raw_client.post(
            "/admin/ui/challenges",
            data={
                "title": "New Challenge",
                "description": "Quick scan sprint",
                "action_type": "receipt_scan",
                "objective": "500",
                "starts_at": "2026-07-01T00:00:00",
                "ends_at": "2026-07-31T23:59:59",
                "grace_period_days": "3",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/challenges" in r.headers["location"]
        post_calls = [c for c in stub_rw.calls if c[0] == "POST"]
        assert len(post_calls) == 1
        body = post_calls[0][3]
        assert body["title"] == "New Challenge"
        assert body["action_type"] == "receipt_scan"
        assert body["objective"] == 500
        assert body["grace_period_days"] == 3

    def test_create_validation_error_redirects_with_flash(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "POST",
            "/admin/challenges",
            status_code=422,
            json_body={"detail": "ends_at_must_be_after_starts_at"},
        )

        r = raw_client.post(
            "/admin/ui/challenges",
            data={
                "title": "Bad",
                "action_type": "receipt_scan",
                "objective": "100",
                "starts_at": "2026-07-31T00:00:00",
                "ends_at": "2026-07-01T00:00:00",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/challenges" in r.headers["location"]


# ============================================================================
# Update challenge action (activate / deactivate)
# ============================================================================


class TestChallengesUpdateAction:
    def test_activate_redirects_with_success_flash(self, raw_client, stub_rw):
        _login(raw_client)
        cid = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/challenges/{cid}/activate",
            json_body={"ok": True},
        )

        r = raw_client.post(
            f"/admin/ui/challenges/{cid}",
            data={"action": "activate"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert f"/admin/ui/challenges/{cid}" in r.headers["location"]
        patch_calls = [c for c in stub_rw.calls if c[0] == "PATCH"]
        assert len(patch_calls) == 1
        assert patch_calls[0][1].endswith("/activate")

    def test_deactivate_routes_to_deactivate_endpoint(self, raw_client, stub_rw):
        _login(raw_client)
        cid = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/challenges/{cid}/deactivate",
            json_body={"ok": True},
        )

        r = raw_client.post(
            f"/admin/ui/challenges/{cid}",
            data={"action": "deactivate"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        patch_calls = [c for c in stub_rw.calls if c[0] == "PATCH"]
        assert len(patch_calls) == 1
        assert patch_calls[0][1].endswith("/deactivate")

    def test_activate_409_active_conflict_flashes(self, raw_client, stub_rw):
        _login(raw_client)
        cid = str(uuid.uuid4())
        stub_rw.on(
            "PATCH",
            f"/admin/challenges/{cid}/activate",
            status_code=409,
            json_body={"detail": "active_challenge_conflict"},
        )
        r = raw_client.post(
            f"/admin/ui/challenges/{cid}",
            data={"action": "activate"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert "conflict" in loc.lower() or "active" in loc.lower()


# ============================================================================
# Create milestone action
# ============================================================================


class TestChallengesMilestoneAction:
    def test_create_milestone_redirects_to_detail(self, raw_client, stub_rw):
        _login(raw_client)
        cid = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            f"/admin/challenges/{cid}/milestones",
            status_code=201,
            json_body={
                "id": str(uuid.uuid4()),
                "challenge_id": cid,
                "threshold": 100,
                "reward_type": "cab",
                "reward_value": {"amount": 50},
            },
        )

        r = raw_client.post(
            f"/admin/ui/challenges/{cid}/milestones",
            data={
                "threshold": "100",
                "reward_type": "cab",
                "reward_value": '{"amount": 50}',
                "label": "First milestone",
                "sort_order": "0",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert f"/admin/ui/challenges/{cid}" in r.headers["location"]
        post_calls = [c for c in stub_rw.calls if c[0] == "POST"]
        assert len(post_calls) == 1
        body = post_calls[0][3]
        assert body["threshold"] == 100
        assert body["reward_type"] == "cab"
        assert body["reward_value"] == {"amount": 50}
        assert body["label"] == "First milestone"

    def test_create_milestone_invalid_json_returns_to_detail_with_error(self, raw_client, stub_rw):
        _login(raw_client)
        cid = str(uuid.uuid4())

        r = raw_client.post(
            f"/admin/ui/challenges/{cid}/milestones",
            data={
                "threshold": "100",
                "reward_type": "cab",
                "reward_value": "not json",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert f"/admin/ui/challenges/{cid}" in loc
        # No POST should have hit RW (fail-fast before HTTP).
        post_calls = [c for c in stub_rw.calls if c[0] == "POST"]
        assert len(post_calls) == 0


# ============================================================================
# Dashboard tile
# ============================================================================


class TestChallengesDashboardTile:
    def test_dashboard_links_to_challenges(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/")
        assert r.status_code == 200
        assert "/admin/ui/challenges" in r.text
        assert "Challenges" in r.text or "challenges" in r.text.lower()
