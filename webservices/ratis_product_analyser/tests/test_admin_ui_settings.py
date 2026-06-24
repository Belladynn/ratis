"""Tests for the admin mini UI settings pages (Bloc D).

Covers :

- ``GET /admin/ui/settings``                              — list page (editable + frozen tiles)
- ``GET /admin/ui/settings/{section}``                    — detail page (editable form / frozen preview)
- ``POST /admin/ui/settings/{section}``                   — save form (applied / pending_2fa / errors)
- ``POST /admin/ui/settings/{section}/confirm-2fa``       — TOTP confirmation flow
- ``POST /admin/ui/settings/{section}/cancel-pending``    — abort grace period

All RW calls go through ``rw_get`` / ``rw_put`` / ``rw_post`` which we
monkeypatch with an in-memory script-table fixture. The local DB is used
only for the listing page (``app_settings`` rows produce updated_at +
key count metadata) — RW is the authority for read/write of section data.

Auth gate is the same cookie pattern as the rest of the admin UI.
Reusing the ``_login`` helper from the users page keeps the wire
identical (no double-test of the auth flow).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from admin_ui.settings_sections import EDITABLE_SECTIONS_MIRROR

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
    """Script-table for RW HTTP calls.

    Each ``on(method, path, ...)`` registers a handler keyed by method +
    exact path. JSON bodies on PUT/POST are captured into ``calls`` so
    tests can assert what we forwarded (data + reason + audit_id).
    """

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
    """Replace rw_get / rw_put / rw_post with in-memory script-table."""
    stub = _StubRW()

    async def fake_rw_get(path, *, operator, params=None):
        stub.calls.append(("GET", path, dict(params or {}), None, None))
        if ("GET", path) in stub._handlers:
            code, body = stub._handlers[("GET", path)]
            return httpx.Response(code, json=body)
        return httpx.Response(404, json={"detail": "not_handled"})

    async def fake_rw_put(path, *, operator, json):
        stub.calls.append(("PUT", path, None, dict(json), None))
        if ("PUT", path) in stub._handlers:
            code, body = stub._handlers[("PUT", path)]
            return httpx.Response(code, json=body)
        return httpx.Response(404, json={"detail": "not_handled"})

    async def fake_rw_post(path, *, operator, json, totp=None):
        stub.calls.append(("POST", path, None, dict(json), totp))
        if ("POST", path) in stub._handlers:
            code, body = stub._handlers[("POST", path)]
            return httpx.Response(code, json=body)
        return httpx.Response(404, json={"detail": "not_handled"})

    monkeypatch.setattr("admin_ui.routes.rw_get", fake_rw_get)
    monkeypatch.setattr("admin_ui.routes.rw_put", fake_rw_put)
    monkeypatch.setattr("admin_ui.routes.rw_post", fake_rw_post)
    return stub


@pytest.fixture(autouse=True)
def _set_rw_base_url(monkeypatch):
    monkeypatch.setenv("RW_BASE_URL", "http://ratis_rewards.test:8004")


def _seed_app_settings(db, section: str, data: dict[str, Any]) -> None:
    """Insert one ``app_settings`` row directly so the list page sees it."""
    from sqlalchemy import text

    db.execute(
        text(
            "INSERT INTO app_settings (section, data, updated_at) "
            "VALUES (:s, CAST(:d AS jsonb), now()) "
            "ON CONFLICT (section) DO UPDATE SET data = EXCLUDED.data"
        ),
        {"s": section, "d": json.dumps(data)},
    )
    db.commit()


# ============================================================================
# Mirror contract — local editable list must match RW allowlist
# ============================================================================


class TestMirrorContract:
    def test_editable_sections_mirror_matches_rw(self):
        """The local UI mirror MUST equal the RW source-of-truth allowlist.

        If RW adds an editable section without updating the mirror, the
        list page would silently render it as frozen. CI catches the drift.

        We parse the RW source file via ``ast`` rather than importing
        ``services.admin.settings_service`` because the PA test process
        does not have the ratis_rewards package on its sys.path — both
        services are workspace siblings, not deps. AST-parsing a sibling
        source file is the de-facto cross-service contract pattern.
        """
        import ast
        from pathlib import Path

        # tests/ → ratis_product_analyser/ → webservices/ → ratis_rewards/...
        rw_file = Path(__file__).resolve().parents[2] / "ratis_rewards" / "services" / "admin" / "settings_service.py"
        assert rw_file.exists(), f"RW source not found at {rw_file}"
        tree = ast.parse(rw_file.read_text(encoding="utf-8"))
        rw_keys: set[str] | None = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "EDITABLE_SECTIONS"
            ):
                value = node.value
                if isinstance(value, ast.Dict):
                    rw_keys = {k.value for k in value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                break
        assert rw_keys is not None, "EDITABLE_SECTIONS not found in RW source"
        assert set(EDITABLE_SECTIONS_MIRROR) == rw_keys


# ============================================================================
# List page
# ============================================================================


class TestSettingsListPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get("/admin/ui/settings", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_renders_editable_and_frozen_tiles(self, raw_client, db):
        _login(raw_client)
        # Seed two rows so the page has metadata to display.
        _seed_app_settings(db, "rewards", {"cab_per_receipt": 500})
        _seed_app_settings(db, "consensus", {"min_validators": 3})

        r = raw_client.get("/admin/ui/settings")
        assert r.status_code == 200
        # All editable sections appear in the page (tiles).
        for s in EDITABLE_SECTIONS_MIRROR:
            assert s in r.text, f"editable section {s} missing from list page"
        # Some frozen names also appear (sample check — the full list is in
        # the UI module and matches ``ratis_settings.json`` minus editables).
        for s in ("consensus", "ocr", "subscription"):
            assert s in r.text
        # Each editable tile links to the detail page.
        assert 'href="/admin/ui/settings/rewards"' in r.text
        # Frozen tiles also link to detail (for read-only preview).
        assert 'href="/admin/ui/settings/consensus"' in r.text


# ============================================================================
# Detail page — editable section
# ============================================================================


class TestSettingsDetailEditable:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get("/admin/ui/settings/rewards", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_editable_section_renders_form(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/settings/rewards",
            json_body={"cab_per_receipt": 500},
        )
        stub_rw.on(
            "GET",
            "/admin/settings/rewards/editable",
            json_body={"editable": True, "frozen_keys": []},
        )

        r = raw_client.get("/admin/ui/settings/rewards")
        assert r.status_code == 200
        # Form artefacts.
        assert 'name="data"' in r.text
        assert 'name="reason"' in r.text
        assert "Confirm save" in r.text
        # Current data surfaces in the textarea.
        assert "cab_per_receipt" in r.text
        assert "500" in r.text

    def test_editable_section_with_frozen_subkey_highlights_it(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/settings/gamification",
            json_body={
                "freeze_cost_cab": 100,
                "feed_jack": {"multiplier_per_day": 0.05},
            },
        )
        stub_rw.on(
            "GET",
            "/admin/settings/gamification/editable",
            json_body={"editable": True, "frozen_keys": ["feed_jack"]},
        )

        r = raw_client.get("/admin/ui/settings/gamification")
        assert r.status_code == 200
        # The frozen sub-key surfaces with a warning marker.
        assert "feed_jack" in r.text
        # Marker keyword used in the template — distinct from the JSON
        # body so we know the warning UI fires, not a false-positive
        # match on the data dump.
        assert "frozen sub-key" in r.text.lower() or "sous-clé frozen" in r.text.lower()


# ============================================================================
# Detail page — frozen section
# ============================================================================


class TestSettingsDetailFrozen:
    def test_frozen_section_renders_readonly(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/settings/consensus",
            json_body={"min_validators": 3},
        )
        stub_rw.on(
            "GET",
            "/admin/settings/consensus/editable",
            json_body={"editable": False, "frozen_keys": []},
        )

        r = raw_client.get("/admin/ui/settings/consensus")
        assert r.status_code == 200
        # No save button on a frozen section.
        assert "Confirm save" not in r.text
        assert 'name="reason"' not in r.text
        # The data still surfaces (read-only preview).
        assert "min_validators" in r.text
        # Frozen marker visible.
        assert "PR git" in r.text or "modifiable uniquement" in r.text.lower()


# ============================================================================
# Save flow
# ============================================================================


class TestSettingsSave:
    def test_applied_redirects_with_flash(self, raw_client, stub_rw):
        _login(raw_client)
        audit_id = str(uuid.uuid4())
        stub_rw.on(
            "PUT",
            "/admin/settings/rewards",
            json_body={"audit_id": audit_id, "status": "applied"},
        )

        r = raw_client.post(
            "/admin/ui/settings/rewards",
            data={
                "data": json.dumps({"cab_per_receipt": 600}),
                "reason": "Bump alpha test data",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/settings/rewards" in r.headers["location"]
        # PUT body forwarded with parsed dict + reason.
        put_calls = [c for c in stub_rw.calls if c[0] == "PUT"]
        assert len(put_calls) == 1
        body = put_calls[0][3]
        assert body == {
            "data": {"cab_per_receipt": 600},
            "reason": "Bump alpha test data",
        }

    def test_pending_2fa_renders_2fa_page(self, raw_client, stub_rw):
        _login(raw_client)
        audit_id = str(uuid.uuid4())
        stub_rw.on(
            "PUT",
            "/admin/settings/rewards",
            json_body={"audit_id": audit_id, "status": "pending_2fa"},
        )

        r = raw_client.post(
            "/admin/ui/settings/rewards",
            data={
                "data": json.dumps({"cab_per_receipt": 5000}),
                "reason": "Bump alpha test data",
            },
            follow_redirects=False,
        )
        assert r.status_code == 200
        # 2FA confirmation form artefacts.
        assert audit_id in r.text
        assert 'name="totp"' in r.text
        # Route paths for confirm + cancel buttons.
        assert "/admin/ui/settings/rewards/confirm-2fa" in r.text
        assert "/admin/ui/settings/rewards/cancel-pending" in r.text

    def test_reason_too_short_re_renders_with_error(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/settings/rewards",
            json_body={"cab_per_receipt": 500},
        )
        stub_rw.on(
            "GET",
            "/admin/settings/rewards/editable",
            json_body={"editable": True, "frozen_keys": []},
        )

        r = raw_client.post(
            "/admin/ui/settings/rewards",
            data={
                "data": json.dumps({"cab_per_receipt": 600}),
                "reason": "short",
            },
            follow_redirects=False,
        )
        assert r.status_code == 200
        # Error surface.
        lowered = r.text.lower()
        assert "8 caract" in lowered or "trop court" in lowered or "minimum" in lowered
        # No PUT was issued — local validation short-circuited.
        put_calls = [c for c in stub_rw.calls if c[0] == "PUT"]
        assert put_calls == []

    def test_invalid_json_re_renders_with_error(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/settings/rewards",
            json_body={"cab_per_receipt": 500},
        )
        stub_rw.on(
            "GET",
            "/admin/settings/rewards/editable",
            json_body={"editable": True, "frozen_keys": []},
        )

        r = raw_client.post(
            "/admin/ui/settings/rewards",
            data={
                "data": "{not valid json",
                "reason": "Bump alpha test data",
            },
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "JSON" in r.text or "json" in r.text
        put_calls = [c for c in stub_rw.calls if c[0] == "PUT"]
        assert put_calls == []

    def test_frozen_section_403_renders_flash_error(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "PUT",
            "/admin/settings/consensus",
            status_code=403,
            json_body={"detail": "section_frozen"},
        )

        r = raw_client.post(
            "/admin/ui/settings/consensus",
            data={
                "data": json.dumps({"min_validators": 4}),
                "reason": "Bump consensus floor",
            },
            follow_redirects=False,
        )
        # 303 redirect back to the detail page with a flash.
        assert r.status_code == 303
        loc = r.headers["location"]
        assert "/admin/ui/settings/consensus" in loc
        assert "section_frozen" in loc or "frozen" in loc.lower()

    def test_frozen_subkey_403_renders_flash_with_key_name(self, raw_client, stub_rw):
        _login(raw_client)
        # FastAPI nests the dict ``detail`` under the response ``detail``
        # key, so the body is ``{"detail": {"detail": ..., "key": ...}}``.
        stub_rw.on(
            "PUT",
            "/admin/settings/gamification",
            status_code=403,
            json_body={
                "detail": {"detail": "frozen_key_modified", "key": "feed_jack"},
            },
        )

        r = raw_client.post(
            "/admin/ui/settings/gamification",
            data={
                "data": json.dumps({"freeze_cost_cab": 100, "feed_jack": {"multiplier_per_day": 5.0}}),
                "reason": "Try to bump feed_jack",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert "/admin/ui/settings/gamification" in loc
        # Key name surfaces in the flash so the operator sees what was rejected.
        assert "feed_jack" in loc


# ============================================================================
# 2FA confirmation flow
# ============================================================================


class TestSettingsConfirm2FA:
    def test_happy_path_redirects_with_applied_flash(self, raw_client, stub_rw):
        _login(raw_client)
        audit_id = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            "/admin/settings/rewards/confirm-2fa",
            json_body={"audit_id": audit_id, "status": "applied"},
        )

        r = raw_client.post(
            "/admin/ui/settings/rewards/confirm-2fa",
            data={"audit_id": audit_id, "totp": "123456"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/settings/rewards" in r.headers["location"]
        # TOTP forwarded as keyword arg.
        post_calls = [c for c in stub_rw.calls if c[0] == "POST"]
        assert len(post_calls) == 1
        assert post_calls[0][4] == "123456"
        # Body shape : {"audit_id": ...}
        assert post_calls[0][3] == {"audit_id": audit_id}

    def test_invalid_totp_re_renders_2fa_page_with_error(self, raw_client, stub_rw):
        _login(raw_client)
        audit_id = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            "/admin/settings/rewards/confirm-2fa",
            status_code=401,
            json_body={"detail": "totp_invalid"},
        )

        r = raw_client.post(
            "/admin/ui/settings/rewards/confirm-2fa",
            data={"audit_id": audit_id, "totp": "000000"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        # 2FA page re-rendered with the audit_id and an error.
        assert audit_id in r.text
        assert "TOTP" in r.text or "totp" in r.text
        assert "invalide" in r.text.lower() or "invalid" in r.text.lower()

    def test_expired_redirects_with_flash(self, raw_client, stub_rw):
        _login(raw_client)
        audit_id = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            "/admin/settings/rewards/confirm-2fa",
            status_code=410,
            json_body={"detail": "audit_expired"},
        )

        r = raw_client.post(
            "/admin/ui/settings/rewards/confirm-2fa",
            data={"audit_id": audit_id, "totp": "123456"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert "/admin/ui/settings/rewards" in loc
        assert "expired" in loc.lower() or "expir" in loc.lower()

    def test_already_resolved_redirects_with_flash(self, raw_client, stub_rw):
        _login(raw_client)
        audit_id = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            "/admin/settings/rewards/confirm-2fa",
            status_code=409,
            json_body={"detail": "audit_not_pending"},
        )

        r = raw_client.post(
            "/admin/ui/settings/rewards/confirm-2fa",
            data={"audit_id": audit_id, "totp": "123456"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert "/admin/ui/settings/rewards" in loc


# ============================================================================
# Cancel pending flow
# ============================================================================


class TestSettingsCancelPending:
    def test_cancel_redirects_with_flash(self, raw_client, stub_rw):
        _login(raw_client)
        audit_id = str(uuid.uuid4())
        stub_rw.on(
            "POST",
            "/admin/settings/rewards/cancel-pending",
            json_body={"audit_id": audit_id, "status": "cancelled"},
        )

        r = raw_client.post(
            "/admin/ui/settings/rewards/cancel-pending",
            data={"audit_id": audit_id},
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert "/admin/ui/settings/rewards" in loc
        # No TOTP forwarded.
        post_calls = [c for c in stub_rw.calls if c[0] == "POST"]
        assert len(post_calls) == 1
        assert post_calls[0][4] is None
        assert post_calls[0][3] == {"audit_id": audit_id}
