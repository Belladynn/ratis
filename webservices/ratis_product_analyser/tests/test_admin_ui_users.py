"""Tests for the admin mini UI user search + detail pages (UI-1.5).

Covers :

- Format detection on ``/admin/ui/users/search`` :
    UUID  → AU ``/admin/users/{id}``    → 303 redirect to detail page
    RTS-XXXXXX → AU ``/admin/users?support_id=...`` → 303 redirect on hit
    other → AU ``/admin/users?email_contains=...`` → table render
- ``/admin/ui/users/{user_id}`` renders identity + scans blocs.
- Scans table pre-fills filter form + propagates GET params to repo.
- Audit-log query-param enrichment : ``?scan_id=`` pre-fills + auto-runs.
- Auth gate : unauth'd → 302 to /admin/ui/login.

AU calls are mocked by patching the route module's ``au_get`` symbol
with an in-memory script-table fixture, so no real HTTP is performed
and no extra dep beyond the already-vendored ``httpx`` is required.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.store import Store
from sqlalchemy import text

# ============================================================================
# Helpers
# ============================================================================


def _login(raw_client, api_key: str = "test-admin-key-padded-to-32-chars-min", operator: str = "tester"):
    return raw_client.post(
        "/admin/ui/login",
        data={"api_key": api_key, "operator": operator},
        follow_redirects=False,
    )


def _user_summary(
    *,
    uid: uuid.UUID | None = None,
    email: str = "alice@example.com",
    support_id: str = "RTS-AB23CD",
    account_type: str = "oauth",
    is_deleted: bool = False,
) -> dict[str, Any]:
    return {
        "id": str(uid or uuid.uuid4()),
        "email": email,
        "support_id": support_id,
        "account_type": account_type,
        "is_deleted": is_deleted,
        "created_at": "2026-04-01T00:00:00+00:00",
    }


def _user_detail(uid: uuid.UUID) -> dict[str, Any]:
    return {
        "id": str(uid),
        "email": "alice@example.com",
        "support_id": "RTS-AB23CD",
        "account_type": "oauth",
        "display_name": "Alice",
        "avatar_url": None,
        "is_deleted": False,
        "timezone": "Europe/Paris",
        "password_changed_at": None,
        "created_at": "2026-04-01T00:00:00+00:00",
        "updated_at": "2026-04-15T00:00:00+00:00",
        "refresh_tokens_active": 2,
        "subscription_status": "active",
        "cashback_withdrawal_count": 1,
    }


class _StubAU:
    """In-memory script-table for AU calls.

    Each ``on(path, params, ...)`` registers a handler. The fixture's
    fake ``au_get`` walks the handlers in registration order and picks
    the first match (subset on params — the route may add incidental
    fields like ``limit`` that the test doesn't pin).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._handlers: list[tuple[str, dict[str, Any], int, dict[str, Any]]] = []

    def on(
        self,
        path: str,
        params: dict[str, Any] | None,
        *,
        status_code: int = 200,
        json_body: dict[str, Any] | None = None,
    ) -> None:
        self._handlers.append((path, params or {}, status_code, json_body or {}))


@pytest.fixture
def stub_au(monkeypatch):
    """Replace ``au_get`` so the route never touches the network.

    We patch the function rather than spinning a real httpx.AsyncClient
    with MockTransport because the route imports ``au_get`` by name and
    a local ``async def`` replacement is the most-direct seam.
    """
    stub = _StubAU()

    async def fake_au_get(path, *, operator, params=None):
        stub.calls.append((path, dict(params or {})))
        for handler_path, handler_params, code, body in stub._handlers:
            if path != handler_path:
                continue
            # Subset match : the handler asserts the keys it cares about,
            # the route is free to add others (e.g. limit=50 on the email
            # search). Strict-equality matching would make the test
            # brittle to incidental param additions.
            got = {k: str(v) for k, v in (params or {}).items()}
            want = {k: str(v) for k, v in handler_params.items()}
            if any(got.get(k) != v for k, v in want.items()):
                continue
            return httpx.Response(code, json=body)
        return httpx.Response(404, json={"detail": "not_handled"})

    monkeypatch.setattr("admin_ui.routes.au_get", fake_au_get)
    return stub


@pytest.fixture(autouse=True)
def _set_au_base_url(monkeypatch):
    monkeypatch.setenv("AU_BASE_URL", "http://ratis_auth:8001")


# ============================================================================
# Format detection — search routing
# ============================================================================


class TestSearchFormatDetection:
    def test_get_renders_search_form(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/users/search")
        assert r.status_code == 200
        assert 'name="query"' in r.text

    def test_uuid_query_redirects_to_detail_on_hit(self, raw_client, stub_au):
        _login(raw_client)
        uid = uuid.uuid4()
        stub_au.on(f"/admin/users/{uid}", None, json_body=_user_detail(uid))

        r = raw_client.post(
            "/admin/ui/users/search",
            data={"query": str(uid)},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == f"/admin/ui/users/{uid}"
        assert any(call[0] == f"/admin/users/{uid}" for call in stub_au.calls)

    def test_uuid_query_404_re_renders_with_error(self, raw_client, stub_au):
        _login(raw_client)
        uid = uuid.uuid4()
        stub_au.on(f"/admin/users/{uid}", None, status_code=404, json_body={"detail": "user_not_found"})

        r = raw_client.post(
            "/admin/ui/users/search",
            data={"query": str(uid)},
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "introuvable" in r.text.lower()

    def test_support_id_query_redirects_on_unique_hit(self, raw_client, stub_au):
        _login(raw_client)
        uid = uuid.uuid4()
        stub_au.on(
            "/admin/users",
            {"support_id": "RTS-AB23CD"},
            json_body={
                "users": [_user_summary(uid=uid, support_id="RTS-AB23CD")],
                "total": 1,
                "limit": 50,
                "offset": 0,
            },
        )

        r = raw_client.post(
            "/admin/ui/users/search",
            data={"query": "RTS-AB23CD"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == f"/admin/ui/users/{uid}"

    def test_support_id_query_no_hit_renders_error(self, raw_client, stub_au):
        _login(raw_client)
        stub_au.on(
            "/admin/users",
            {"support_id": "RTS-AB23CD"},
            json_body={"users": [], "total": 0, "limit": 50, "offset": 0},
        )

        r = raw_client.post(
            "/admin/ui/users/search",
            data={"query": "RTS-AB23CD"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "introuvable" in r.text.lower()

    def test_email_partial_query_renders_table(self, raw_client, stub_au):
        _login(raw_client)
        u1 = _user_summary(email="alice@example.com")
        u2 = _user_summary(email="alice2@example.com", support_id="RTS-EF45GH")
        stub_au.on(
            "/admin/users",
            {"email_contains": "alice"},
            json_body={"users": [u1, u2], "total": 2, "limit": 50, "offset": 0},
        )

        r = raw_client.post(
            "/admin/ui/users/search",
            data={"query": "alice"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "alice@example.com" in r.text
        assert "alice2@example.com" in r.text
        # Each row links to the detail page
        assert f"/admin/ui/users/{u1['id']}" in r.text

    def test_email_query_no_results_renders_error(self, raw_client, stub_au):
        _login(raw_client)
        stub_au.on(
            "/admin/users",
            {"email_contains": "ghost"},
            json_body={"users": [], "total": 0, "limit": 50, "offset": 0},
        )

        r = raw_client.post(
            "/admin/ui/users/search",
            data={"query": "ghost"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "aucun utilisateur trouv" in r.text.lower()

    def test_search_unauthenticated_redirects_to_login(self, raw_client):
        r = raw_client.get("/admin/ui/users/search", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"


# ============================================================================
# User detail page
# ============================================================================


def _make_user(db, *, email: str | None = None) -> uuid.UUID:
    """Insert a real users row via the model — picks up ``support_id`` default."""
    from ratis_core.models.user import User

    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=email or f"u-{uid.hex[:8]}@example.com",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    db.commit()
    return uid


def _make_store(db, *, name: str = "Lidl Detail") -> Store:
    s = Store(
        id=uuid.uuid4(),
        name=name,
        retailer="lidl",
        address="x",
        city="Paris",
        postal_code="75001",
        lat=Decimal("48.85"),
        lng=Decimal("2.35"),
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _make_scan(db, user_id: uuid.UUID, store: Store, *, scan_type: str = "receipt") -> Scan:
    # CHECK ``receipt_required`` : receipt-typed scans MUST have
    # ``receipt_id NOT NULL`` and non-receipt scans MUST have
    # ``receipt_id IS NULL``. Mirror that here.
    if scan_type == "receipt":
        receipt = Receipt(
            id=uuid.uuid4(),
            store_id=store.id,
            purchased_at=date.today(),
            image_r2_key="fake.jpg",
        )
        db.add(receipt)
        db.flush()
        receipt_id = receipt.id
    else:
        receipt_id = None
    # No product_ean : the FK to ``products`` would force us to insert a
    # product per test, which buys nothing for these UI assertions. Real
    # scan rows can have product_ean=NULL for unmatched scans, so this
    # is a representative shape. ``status='accepted'`` (legacy v2) is
    # the only label-free terminal status — v3 ``matched`` requires
    # product_ean+match_method by CHECK constraint.
    s = Scan(
        id=uuid.uuid4(),
        user_id=user_id,
        store_id=store.id,
        scanned_name="DETAIL_TEST",
        price=199,
        quantity=Decimal("1"),
        scan_type=scan_type,
        receipt_id=receipt_id,
        status="accepted",
        match_method="manual",
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


class TestUserDetailPage:
    def test_unauthenticated_redirects(self, raw_client):
        r = raw_client.get(f"/admin/ui/users/{uuid.uuid4()}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_renders_identity_block(self, raw_client, stub_au, db):
        _login(raw_client)
        uid = _make_user(db)
        stub_au.on(f"/admin/users/{uid}", None, json_body=_user_detail(uid))

        r = raw_client.get(f"/admin/ui/users/{uid}")
        assert r.status_code == 200
        assert "alice@example.com" in r.text
        assert "RTS-AB23CD" in r.text
        assert "oauth" in r.text  # account_type
        # Aggregate fields surface
        assert "active" in r.text  # subscription_status

    def test_renders_scans_block(self, raw_client, stub_au, db):
        _login(raw_client)
        uid = _make_user(db)
        store = _make_store(db)
        scan = _make_scan(db, uid, store)
        stub_au.on(f"/admin/users/{uid}", None, json_body=_user_detail(uid))

        r = raw_client.get(f"/admin/ui/users/{uid}")
        assert r.status_code == 200
        assert str(scan.id) in r.text
        assert "DETAIL_TEST" in r.text
        # Audit log link with scan_id query param
        assert f"/admin/ui/audit-log?scan_id={scan.id}" in r.text

    def test_scans_filter_by_scan_type(self, raw_client, stub_au, db):
        _login(raw_client)
        uid = _make_user(db)
        store = _make_store(db)
        receipt_scan = _make_scan(db, uid, store, scan_type="receipt")
        # second scan as electronic_label — no receipt FK needed since
        # _make_scan creates one ; we just want a different scan_type.
        label_scan = _make_scan(db, uid, store, scan_type="electronic_label")
        stub_au.on(f"/admin/users/{uid}", None, json_body=_user_detail(uid))

        r = raw_client.get(f"/admin/ui/users/{uid}?scan_type=receipt")
        assert r.status_code == 200
        assert str(receipt_scan.id) in r.text
        assert str(label_scan.id) not in r.text

    def test_scans_pagination_offset(self, raw_client, stub_au, db):
        _login(raw_client)
        uid = _make_user(db)
        store = _make_store(db)
        # 3 scans
        scans = [_make_scan(db, uid, store) for _ in range(3)]
        stub_au.on(f"/admin/users/{uid}", None, json_body=_user_detail(uid))

        r = raw_client.get(f"/admin/ui/users/{uid}?limit=1&offset=1")
        assert r.status_code == 200
        # Exactly one scan id should be in the page (the middle one
        # ordered by scanned_at DESC).
        present = sum(1 for s in scans if str(s.id) in r.text)
        assert present == 1

    def test_au_404_renders_error_block(self, raw_client, stub_au, db):
        _login(raw_client)
        uid = uuid.uuid4()
        stub_au.on(f"/admin/users/{uid}", None, status_code=404, json_body={"detail": "user_not_found"})

        r = raw_client.get(f"/admin/ui/users/{uid}")
        assert r.status_code == 200
        assert "introuvable" in r.text.lower()


# ============================================================================
# Audit log enrichment — query param pre-fill
# ============================================================================


def _make_scan_for_audit(db, user_id, store) -> Scan:
    receipt = Receipt(
        id=uuid.uuid4(),
        store_id=store.id,
        purchased_at=date.today(),
        image_r2_key="fake.jpg",
    )
    db.add(receipt)
    db.flush()
    s = Scan(
        id=uuid.uuid4(),
        user_id=user_id,
        store_id=store.id,
        scanned_name="AUDIT_QP",
        price=100,
        quantity=Decimal("1"),
        scan_type="receipt",
        receipt_id=receipt.id,
        status="unresolved",
        rejected_reason="no_fuzzy_candidate",
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _insert_audit(db, *, scan_id=None, parsed_ticket_id=None, event="qp_event") -> uuid.UUID:
    rid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO pipeline_audit_log "
            "(id, scan_id, parsed_ticket_id, phase, level, event, payload) "
            "VALUES (:id, :sid, :ptid, 'match', 'normal', :ev, '{}'::jsonb)"
        ),
        {
            "id": str(rid),
            "sid": str(scan_id) if scan_id else None,
            "ptid": str(parsed_ticket_id) if parsed_ticket_id else None,
            "ev": event,
        },
    )
    db.commit()
    return rid


class TestAuditLogQueryParam:
    def test_scan_id_param_prefills_and_runs(self, raw_client, db):
        _login(raw_client)
        # Insert a real user via raw SQL to satisfy FK on scans.user_id
        uid = _make_user(db)
        store = _make_store(db, name="Audit QP Store")
        scan = _make_scan_for_audit(db, uid, store)
        _insert_audit(db, scan_id=scan.id, event="qp_match_event")

        r = raw_client.get(f"/admin/ui/audit-log?scan_id={scan.id}")
        assert r.status_code == 200
        # Form pre-filled with the id
        assert str(scan.id) in r.text
        # And the matching event renders (auto-run)
        assert "qp_match_event" in r.text

    def test_entity_id_param_still_works(self, raw_client, db):
        """Existing entity_id query param keeps working unchanged."""
        _login(raw_client)
        uid = _make_user(db)
        store = _make_store(db, name="Audit Compat Store")
        scan = _make_scan_for_audit(db, uid, store)
        _insert_audit(db, scan_id=scan.id, event="compat_event")

        r = raw_client.get(f"/admin/ui/audit-log?entity_id={scan.id}")
        assert r.status_code == 200
        assert "compat_event" in r.text
