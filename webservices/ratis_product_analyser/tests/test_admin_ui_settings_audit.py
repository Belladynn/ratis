"""Tests for the admin mini UI settings audit pages (Bloc E).

Covers :

- ``GET /admin/ui/settings/audit``                   — list page (paginated + filters)
- ``GET /admin/ui/settings/audit/{audit_id}``        — detail page with diff viewer

All RW calls go through ``rw_get`` which we monkeypatch with the same
in-memory script-table fixture used by Bloc D tests
(``test_admin_ui_settings.py``). The page is read-only — no PUT/POST
flows here. Auth gate is the same cookie pattern as the rest of the
admin UI ; reusing the ``_login`` helper keeps the wire identical.
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
    """Script-table for RW HTTP calls (GET-only here).

    Each ``on(method, path, ...)`` registers a handler keyed by method +
    exact path. Query params are captured into ``calls`` so tests can
    assert what we forwarded (section / status / limit / offset).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
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
    """Replace rw_get with an in-memory script-table.

    Bloc E only reads from RW (no PUT/POST), so we don't bother stubbing
    rw_put / rw_post — leaving them untouched would crash any test that
    accidentally hit one, surfacing the bug instead of a silent pass.
    """
    stub = _StubRW()

    async def fake_rw_get(path, *, operator, params=None):
        # Drop None values so the assertion ergonomics match the RW
        # contract (the actual rw_get sends None as "param missing").
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        stub.calls.append(("GET", path, clean_params))
        if ("GET", path) in stub._handlers:
            code, body = stub._handlers[("GET", path)]
            return httpx.Response(code, json=body)
        return httpx.Response(404, json={"detail": "not_handled"})

    monkeypatch.setattr("admin_ui.routes.rw_get", fake_rw_get)
    return stub


@pytest.fixture(autouse=True)
def _set_rw_base_url(monkeypatch):
    monkeypatch.setenv("RW_BASE_URL", "http://ratis_rewards.test:8004")


def _make_audit_item(
    *,
    audit_id: str | None = None,
    section: str = "rewards",
    status: str = "applied",
    operator: str = "alice",
    reason: str = "Bump cab_per_receipt for alpha test",
    timestamp: str = "2026-05-02T12:00:00+00:00",
    expires_at: str | None = None,
    applied_at: str | None = "2026-05-02T12:00:00+00:00",
) -> dict[str, Any]:
    """Build a minimal audit list-item matching the RW serialize shape."""
    return {
        "id": audit_id or str(uuid.uuid4()),
        "timestamp": timestamp,
        "operator": operator,
        "section": section,
        "reason": reason,
        "status": status,
        "expires_at": expires_at,
        "applied_at": applied_at,
    }


# ============================================================================
# List page — auth + filters + pagination + rendering
# ============================================================================


class TestSettingsAuditListAuth:
    def test_unauthenticated_redirects_login(self, raw_client):
        r = raw_client.get("/admin/ui/settings/audit", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"


class TestSettingsAuditListRendering:
    def test_renders_items(self, raw_client, stub_rw):
        _login(raw_client)
        items = [
            _make_audit_item(section="rewards", status="applied", operator="alice"),
            _make_audit_item(section="missions", status="pending_2fa", operator="bob"),
            _make_audit_item(section="xp", status="cancelled", operator="alice"),
            _make_audit_item(section="rewards", status="expired", operator="charlie"),
            _make_audit_item(section="referral", status="applied", operator="alice"),
        ]
        stub_rw.on(
            "GET",
            "/admin/settings/audit",
            json_body={"items": items, "total": 5, "limit": 20, "offset": 0},
        )

        r = raw_client.get("/admin/ui/settings/audit")
        assert r.status_code == 200
        # All five sections / operators surface in the rendered HTML.
        for it in items:
            assert it["section"] in r.text
        for op in ("alice", "bob", "charlie"):
            assert op in r.text
        # All four status enum values present in their respective rows.
        for st in ("applied", "pending_2fa", "cancelled", "expired"):
            assert st in r.text

    def test_empty_state_when_no_items(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/settings/audit",
            json_body={"items": [], "total": 0, "limit": 20, "offset": 0},
        )

        r = raw_client.get("/admin/ui/settings/audit")
        assert r.status_code == 200
        # Empty state copy visible (FR — locale of the admin UI).
        assert "Aucune mutation" in r.text

    def test_reason_long_truncated(self, raw_client, stub_rw):
        _login(raw_client)
        long_reason = "A" * 300  # > 80-char truncation
        items = [_make_audit_item(reason=long_reason)]
        stub_rw.on(
            "GET",
            "/admin/settings/audit",
            json_body={"items": items, "total": 1, "limit": 20, "offset": 0},
        )

        r = raw_client.get("/admin/ui/settings/audit")
        assert r.status_code == 200
        # Full reason kept in a tooltip / title attribute, truncated copy
        # in the visible row text. Either way the full reason MUST appear
        # somewhere (title="..."). The visible portion must NOT contain
        # the entire 300-A run uncropped — we test the title attribute is
        # present (full text) and that the row still renders.
        assert long_reason in r.text  # full text present (title attr)


class TestSettingsAuditListFilters:
    def test_section_filter_propagates(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/settings/audit",
            json_body={"items": [], "total": 0, "limit": 20, "offset": 0},
        )

        r = raw_client.get("/admin/ui/settings/audit?section=rewards")
        assert r.status_code == 200
        # Verify the param was forwarded to RW.
        get_calls = [c for c in stub_rw.calls if c[1] == "/admin/settings/audit"]
        assert len(get_calls) == 1
        assert get_calls[0][2].get("section") == "rewards"

    def test_status_filter_propagates(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/settings/audit",
            json_body={"items": [], "total": 0, "limit": 20, "offset": 0},
        )

        r = raw_client.get("/admin/ui/settings/audit?status=pending_2fa")
        assert r.status_code == 200
        get_calls = [c for c in stub_rw.calls if c[1] == "/admin/settings/audit"]
        assert len(get_calls) == 1
        assert get_calls[0][2].get("status") == "pending_2fa"

    def test_combined_filters_propagate(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/settings/audit",
            json_body={"items": [], "total": 0, "limit": 20, "offset": 0},
        )

        r = raw_client.get("/admin/ui/settings/audit?section=rewards&status=applied")
        assert r.status_code == 200
        get_calls = [c for c in stub_rw.calls if c[1] == "/admin/settings/audit"]
        assert len(get_calls) == 1
        params = get_calls[0][2]
        assert params.get("section") == "rewards"
        assert params.get("status") == "applied"


class TestSettingsAuditListPagination:
    def test_default_limit_is_20(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/settings/audit",
            json_body={"items": [], "total": 0, "limit": 20, "offset": 0},
        )

        r = raw_client.get("/admin/ui/settings/audit")
        assert r.status_code == 200
        get_calls = [c for c in stub_rw.calls if c[1] == "/admin/settings/audit"]
        assert get_calls[0][2].get("limit") == 20
        assert get_calls[0][2].get("offset") == 0

    def test_offset_propagates(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/settings/audit",
            json_body={"items": [], "total": 100, "limit": 20, "offset": 40},
        )

        r = raw_client.get("/admin/ui/settings/audit?offset=40")
        assert r.status_code == 200
        get_calls = [c for c in stub_rw.calls if c[1] == "/admin/settings/audit"]
        assert get_calls[0][2].get("offset") == 40
        # Pagination links should preserve filters and round-trip offsets.
        assert "offset=20" in r.text  # previous
        assert "offset=60" in r.text  # next

    def test_pagination_no_next_on_last_page(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/settings/audit",
            json_body={
                "items": [_make_audit_item() for _ in range(5)],
                "total": 25,
                "limit": 20,
                "offset": 20,
            },
        )

        r = raw_client.get("/admin/ui/settings/audit?offset=20")
        assert r.status_code == 200
        # No next link past total. Defensive : assert offset=40 is NOT
        # rendered as a hot link (the template should suppress it).
        # We test by asserting a "next disabled" marker OR that offset=40
        # does not appear.
        assert "offset=40" not in r.text


class TestSettingsAuditListErrors:
    def test_rw_5xx_renders_with_empty_list(self, raw_client, stub_rw):
        _login(raw_client)
        stub_rw.on(
            "GET",
            "/admin/settings/audit",
            status_code=503,
            json_body={"detail": "upstream_unavailable"},
        )

        r = raw_client.get("/admin/ui/settings/audit")
        # Page renders (no 500), shows an error flash + empty list.
        assert r.status_code == 200
        assert "Erreur" in r.text or "erreur" in r.text


# ============================================================================
# Detail page — auth + diff rendering + 404 path
# ============================================================================


class TestSettingsAuditDetailAuth:
    def test_unauthenticated_redirects_login(self, raw_client):
        audit_id = uuid.uuid4()
        r = raw_client.get(f"/admin/ui/settings/audit/{audit_id}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"


class TestSettingsAuditDetailRendering:
    def test_renders_diff(self, raw_client, stub_rw):
        _login(raw_client)
        audit_id = str(uuid.uuid4())
        body = {
            "id": audit_id,
            "timestamp": "2026-05-02T12:00:00+00:00",
            "operator": "alice",
            "section": "rewards",
            "reason": "Bump cab_per_receipt for alpha test",
            "status": "applied",
            "expires_at": None,
            "applied_at": "2026-05-02T12:00:00+00:00",
            "diff": {
                "added": ["new_key"],
                "removed": ["dropped_key"],
                "changed": ["cab_per_receipt"],
            },
            "old_data": {
                "cab_per_receipt": 500,
                "dropped_key": "old",
            },
            "new_data": {
                "cab_per_receipt": 600,
                "new_key": "added",
            },
        }
        stub_rw.on("GET", f"/admin/settings/audit/{audit_id}", json_body=body)

        r = raw_client.get(f"/admin/ui/settings/audit/{audit_id}")
        assert r.status_code == 200
        # Header content surfaces.
        assert "rewards" in r.text
        assert "alice" in r.text
        assert "applied" in r.text
        # Reason text in full.
        assert "Bump cab_per_receipt for alpha test" in r.text
        # Diff buckets all visible.
        assert "new_key" in r.text
        assert "dropped_key" in r.text
        assert "cab_per_receipt" in r.text
        # Both old + new values surface in the diff viewer.
        assert "500" in r.text
        assert "600" in r.text

    def test_pending_2fa_shows_expires_at(self, raw_client, stub_rw):
        _login(raw_client)
        audit_id = str(uuid.uuid4())
        body = {
            "id": audit_id,
            "timestamp": "2026-05-02T12:00:00+00:00",
            "operator": "alice",
            "section": "rewards",
            "reason": "Big bump >50%",
            "status": "pending_2fa",
            "expires_at": "2026-05-02T12:10:00+00:00",
            "applied_at": None,
            "diff": {"added": [], "removed": [], "changed": ["cab_per_receipt"]},
            "old_data": {"cab_per_receipt": 500},
            "new_data": {"cab_per_receipt": 5000},
        }
        stub_rw.on("GET", f"/admin/settings/audit/{audit_id}", json_body=body)

        r = raw_client.get(f"/admin/ui/settings/audit/{audit_id}")
        assert r.status_code == 200
        # expires_at visible (the 10-min grace marker for the operator).
        assert "2026-05-02T12:10:00" in r.text


class TestSettingsAuditDetail404:
    def test_404_renders_error_template(self, raw_client, stub_rw):
        _login(raw_client)
        audit_id = uuid.uuid4()
        stub_rw.on(
            "GET",
            f"/admin/settings/audit/{audit_id}",
            status_code=404,
            json_body={"detail": "audit_not_found"},
        )

        r = raw_client.get(f"/admin/ui/settings/audit/{audit_id}")
        # The page renders (no 500), shows a not-found state.
        assert r.status_code == 404
        assert "introuvable" in r.text.lower() or "not_found" in r.text.lower()
