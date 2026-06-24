"""Tests for the admin mini UI (ARCH_admin_endpoints § Mini UI, PR UI-1).

Covers :

- Login : valid creds → 302 + cookies set ; invalid key → form re-rendered
- Auth gate : protected page without cookie → 302 to /admin/ui/login
- Stores pending page : renders pending stores ; bulk validate flips
  status + writes history rows.
- Knowledge queue page : renders unresolved rows ; POST applies
  correction OR dismissal ; row not found → flash redirect.
- Audit log page : empty input → no query ; valid scan_id → matching
  events ; invalid UUID → inline error.
- Logout : clears cookies + 302 to login.

Uses ``raw_client`` (TestClient WITHOUT auth bypass) so every test
exercises the real cookie-session dep — that's the surface PR UI-1
is shipping. The JSON ``/api/v1/admin/*`` endpoints have their own
admin tests.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.store import Store
from sqlalchemy import text

# ============================================================================
# Helpers
# ============================================================================


def _make_pending_store(db, *, name: str = "Pending Store") -> Store:
    s = Store(
        id=uuid.uuid4(),
        name=name,
        retailer="lidl",
        address="1 rue Test",
        city="Paris",
        postal_code="75001",
        lat=Decimal("0"),
        lng=Decimal("0"),
        is_disabled=False,
        source="user_suggested",
        validation_status="pending",
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _insert_ocr_knowledge(db, *, raw_ocr: str, corrected: str | None = None) -> uuid.UUID:
    row_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO ocr_knowledge "
            "(id, raw_ocr, corrected, match_type, source, seen_count, type) "
            "VALUES (:id, :raw, :corr, 'sequence', 'ocr_arbitrage', 5, 'product_name')"
        ),
        {"id": str(row_id), "raw": raw_ocr, "corr": corrected},
    )
    db.commit()
    return row_id


def _login(raw_client, api_key: str = "test-admin-key-padded-to-32-chars-min", operator: str = "tester"):
    """Submit the login form and return the response (with cookies)."""
    return raw_client.post(
        "/admin/ui/login",
        data={"api_key": api_key, "operator": operator},
        follow_redirects=False,
    )


# ============================================================================
# Login + auth gate
# ============================================================================
class TestLogin:
    def test_login_get_renders_form(self, raw_client):
        r = raw_client.get("/admin/ui/login")
        assert r.status_code == 200
        assert "ADMIN_API_KEY" in r.text

    def test_login_valid_redirects_and_sets_cookie(self, raw_client):
        r = _login(raw_client)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/"
        # Cookies are set on the response — TestClient persists them on
        # the client session for follow-up requests.
        cookies = r.cookies
        assert "admin_session" in cookies or "admin_session" in raw_client.cookies
        assert "admin_operator" in cookies or "admin_operator" in raw_client.cookies

    def test_login_sets_secure_cookie_under_https(self, db):
        """M2 — under HTTPS the session cookies MUST carry the Secure flag.

        Re-creates a TestClient with ``base_url="https://testserver"`` so
        ``request.url.scheme`` is ``"https"`` ; the route then tags both
        ``admin_session`` and ``admin_operator`` with the ``Secure``
        attribute (browsers refuse to send these over plaintext HTTP).
        """
        from fastapi.testclient import TestClient
        from main import app
        from ratis_core.database import get_db

        def override_get_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app, base_url="https://testserver") as c:
                r = c.post(
                    "/admin/ui/login",
                    data={"api_key": "test-admin-key-padded-to-32-chars-min", "operator": "tester"},
                    follow_redirects=False,
                )
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert r.status_code == 302
        # ``set-cookie`` headers (multi-valued) — assert each cookie line
        # we care about advertises the Secure flag.
        set_cookie_headers = r.headers.get_list("set-cookie")
        session_lines = [h for h in set_cookie_headers if h.startswith("admin_session=")]
        operator_lines = [h for h in set_cookie_headers if h.startswith("admin_operator=")]
        assert session_lines, "admin_session cookie not set"
        assert operator_lines, "admin_operator cookie not set"
        for line in session_lines + operator_lines:
            assert "Secure" in line, f"Secure flag missing on cookie: {line}"

    def test_login_omits_secure_cookie_under_http(self, raw_client):
        """M2 — under plaintext HTTP (dev) the Secure flag is NOT set.

        Browsers would refuse a Secure cookie over http:// regardless,
        so the dev workflow keeps working without HTTPS. The Strict
        SameSite + HttpOnly flags still apply to limit dev surface.
        """
        r = _login(raw_client)
        assert r.status_code == 302
        set_cookie_headers = r.headers.get_list("set-cookie")
        for line in set_cookie_headers:
            if line.startswith("admin_session=") or line.startswith("admin_operator="):
                assert "Secure" not in line, f"Secure flag should be absent on plaintext HTTP: {line}"

    def test_login_invalid_key_re_renders_form(self, raw_client):
        r = raw_client.post(
            "/admin/ui/login",
            data={"api_key": "wrong-key", "operator": "tester"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "Identifiants invalides" in r.text
        assert "admin_session" not in r.cookies

    def test_login_empty_operator_re_renders_form(self, raw_client):
        r = raw_client.post(
            "/admin/ui/login",
            data={"api_key": "test-admin-key-padded-to-32-chars-min", "operator": "   "},
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "Identifiants invalides" in r.text


class TestComputeTokenHmac:
    """M1 — token derivation MUST use HMAC-SHA256, not raw SHA256.

    Raw ``sha256(api_key + ":" + operator)`` is vulnerable to rainbow
    tables for weak / short keys. HMAC-SHA256 is the canonical KDF for
    keyed-message authentication and resists offline brute-force on the
    key when the message (operator handle) is known.
    """

    def test_compute_token_uses_hmac_not_raw_sha256(self):
        """The token must equal hmac.new(key, msg, sha256), NOT sha256(key:msg)."""
        import hashlib
        import hmac

        from admin_ui.auth import compute_token

        api_key = "test-admin-key-padded-to-32-chars-min-some-extra"
        operator = "alice"
        token = compute_token(api_key, operator)

        # Must equal HMAC-SHA256 keyed by api_key over the operator handle.
        expected_hmac = hmac.new(
            key=api_key.encode("utf-8"),
            msg=operator.encode("utf-8"),
            digestmod="sha256",
        ).hexdigest()
        assert token == expected_hmac

        # Must NOT equal the legacy raw SHA256 — guards against an
        # accidental rollback to the previous derivation.
        legacy_sha = hashlib.sha256(f"{api_key}:{operator}".encode()).hexdigest()
        assert token != legacy_sha

    def test_compute_token_deterministic_same_inputs(self):
        """Same (api_key, operator) → same token across calls (recomputable)."""
        from admin_ui.auth import compute_token

        a = compute_token("k" * 32, "bob")
        b = compute_token("k" * 32, "bob")
        assert a == b

    def test_compute_token_changes_when_key_changes(self):
        """Rotating the key invalidates the existing token (verify_session_cookie)."""
        from admin_ui.auth import compute_token

        a = compute_token("k" * 32, "bob")
        b = compute_token(("k" * 31) + "Z", "bob")
        assert a != b

    def test_legacy_sha256_token_does_not_validate(self):
        """A token computed with the OLD raw sha256 must FAIL verify_session_cookie.

        Documents the post-deploy migration : every operator must
        re-login (their existing cookie carries a sha256 token, the new
        verifier expects an HMAC token). Acceptable friction for V1.
        """
        import hashlib

        from admin_ui.auth import verify_session_cookie

        api_key = os.environ["ADMIN_API_KEY"]
        operator = "alice"
        legacy_token = hashlib.sha256(f"{api_key}:{operator}".encode()).hexdigest()
        assert verify_session_cookie(legacy_token, operator) is None


class TestAuthGate:
    def test_dashboard_without_cookie_redirects_to_login(self, raw_client):
        r = raw_client.get("/admin/ui/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_stores_pending_without_cookie_redirects(self, raw_client):
        r = raw_client.get("/admin/ui/stores-pending", follow_redirects=False)
        assert r.status_code == 302

    def test_knowledge_queue_without_cookie_redirects(self, raw_client):
        r = raw_client.get("/admin/ui/knowledge-queue", follow_redirects=False)
        assert r.status_code == 302

    def test_audit_log_without_cookie_redirects(self, raw_client):
        r = raw_client.get("/admin/ui/audit-log", follow_redirects=False)
        assert r.status_code == 302

    def test_dashboard_with_valid_cookie_renders(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/")
        assert r.status_code == 200
        assert "tester" in r.text


class TestLogout:
    def test_logout_clears_cookie_and_redirects(self, raw_client):
        _login(raw_client)
        r = raw_client.post("/admin/ui/logout", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"
        # After logout, dashboard must redirect to login again.
        r2 = raw_client.get("/admin/ui/", follow_redirects=False)
        assert r2.status_code == 302


# ============================================================================
# Page A — Stores pending
# ============================================================================
class TestStoresPending:
    def test_page_lists_pending_stores(self, raw_client, db):
        _login(raw_client)
        store = _make_pending_store(db, name="Aldi Pending")
        r = raw_client.get("/admin/ui/stores-pending")
        assert r.status_code == 200
        assert "Aldi Pending" in r.text
        assert str(store.id) in r.text

    def test_page_omits_non_pending_stores(self, raw_client, db):
        _login(raw_client)
        # confirmed store should NOT appear
        s = Store(
            id=uuid.uuid4(),
            name="Confirmed Lidl",
            retailer="lidl",
            address="x",
            city="Paris",
            postal_code="75001",
            lat=Decimal("48"),
            lng=Decimal("2"),
            is_disabled=False,
            source="osm",
            validation_status="confirmed",
        )
        db.add(s)
        db.commit()
        r = raw_client.get("/admin/ui/stores-pending")
        assert r.status_code == 200
        assert "Confirmed Lidl" not in r.text

    def test_validate_bulk_flips_status_and_logs_history(self, raw_client, db):
        _login(raw_client)
        s1 = _make_pending_store(db, name="S1")
        s2 = _make_pending_store(db, name="S2")
        r = raw_client.post(
            "/admin/ui/stores-pending/validate-bulk",
            data={"ids": [str(s1.id), str(s2.id)]},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/admin/ui/stores-pending" in r.headers["location"]

        # DB state : both flipped to confirmed
        statuses = db.execute(
            text("SELECT id, validation_status FROM stores WHERE id = ANY(:ids)"),
            {"ids": [str(s1.id), str(s2.id)]},
        ).fetchall()
        assert all(row.validation_status == "confirmed" for row in statuses)

        # History rows logged with the operator handle
        hist = db.execute(
            text("SELECT triggered_by FROM store_validation_history WHERE store_id = ANY(:ids)"),
            {"ids": [str(s1.id), str(s2.id)]},
        ).fetchall()
        assert len(hist) == 2
        # store_admin_service prefixes with ``admin:`` — see its _insert_history.
        assert all(row.triggered_by == "admin:tester" for row in hist)

    def test_validate_bulk_empty_selection_redirects_with_flash(self, raw_client, db):
        _login(raw_client)
        r = raw_client.post(
            "/admin/ui/stores-pending/validate-bulk",
            data={},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "Aucune+s" in r.headers["location"] or "Aucune" in r.headers["location"]

    def test_validate_bulk_without_session_redirects(self, raw_client, db):
        s = _make_pending_store(db)
        r = raw_client.post(
            "/admin/ui/stores-pending/validate-bulk",
            data={"ids": [str(s.id)]},
            follow_redirects=False,
        )
        assert r.status_code == 302
        # DB unchanged — store still pending
        row = db.execute(
            text("SELECT validation_status FROM stores WHERE id = :id"),
            {"id": str(s.id)},
        ).first()
        assert row.validation_status == "pending"


# ============================================================================
# Page B — Knowledge queue
# ============================================================================
class TestKnowledgeQueue:
    def test_page_lists_unresolved_rows(self, raw_client, db):
        _login(raw_client)
        rid = _insert_ocr_knowledge(db, raw_ocr="N0TELLA")
        r = raw_client.get("/admin/ui/knowledge-queue")
        assert r.status_code == 200
        assert "N0TELLA" in r.text
        assert str(rid) in r.text

    def test_page_hides_already_corrected_rows(self, raw_client, db):
        _login(raw_client)
        _insert_ocr_knowledge(db, raw_ocr="ALREADY_FIXED", corrected="Already Fixed")
        r = raw_client.get("/admin/ui/knowledge-queue")
        assert "ALREADY_FIXED" not in r.text

    def test_apply_correction_updates_row(self, raw_client, db):
        _login(raw_client)
        rid = _insert_ocr_knowledge(db, raw_ocr="N0TELLA")
        r = raw_client.post(
            f"/admin/ui/knowledge-queue/{rid}",
            data={"corrected": "Nutella"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = db.execute(
            text("SELECT corrected, source FROM ocr_knowledge WHERE id = :id"),
            {"id": str(rid)},
        ).first()
        assert row.corrected == "Nutella"
        assert row.source == "manual"

    def test_apply_dismissal_when_empty(self, raw_client, db):
        _login(raw_client)
        rid = _insert_ocr_knowledge(db, raw_ocr="GIBBERISH")
        r = raw_client.post(
            f"/admin/ui/knowledge-queue/{rid}",
            data={"corrected": ""},
            follow_redirects=False,
        )
        assert r.status_code == 303
        row = db.execute(
            text("SELECT corrected, source FROM ocr_knowledge WHERE id = :id"),
            {"id": str(rid)},
        ).first()
        assert row.corrected is None
        assert row.source == "manual"

    def test_apply_unknown_id_redirects_with_flash(self, raw_client, db):
        _login(raw_client)
        bogus = uuid.uuid4()
        r = raw_client.post(
            f"/admin/ui/knowledge-queue/{bogus}",
            data={"corrected": "foo"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "introuvable" in r.headers["location"]

    def test_apply_writes_audit_log_row(self, raw_client, db):
        _login(raw_client)
        rid = _insert_ocr_knowledge(db, raw_ocr="LIDL_001")
        raw_client.post(
            f"/admin/ui/knowledge-queue/{rid}",
            data={"corrected": "Lidl Item 1"},
            follow_redirects=False,
        )
        events = db.execute(
            text(
                "SELECT event, payload FROM pipeline_audit_log "
                "WHERE phase = 'manual' AND event = 'admin_ocr_knowledge_correction'"
            )
        ).fetchall()
        assert len(events) == 1
        payload = events[0].payload
        assert payload["operator"] == "tester"
        assert payload["via"] == "admin_ui"

    def test_apply_without_session_redirects(self, raw_client, db):
        rid = _insert_ocr_knowledge(db, raw_ocr="UNAUTH")
        r = raw_client.post(
            f"/admin/ui/knowledge-queue/{rid}",
            data={"corrected": "Unauth"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        # Row unchanged
        row = db.execute(
            text("SELECT corrected FROM ocr_knowledge WHERE id = :id"),
            {"id": str(rid)},
        ).first()
        assert row.corrected is None


# ============================================================================
# Page C — Audit log viewer
# ============================================================================


def _make_scan_for_audit(db, user, store) -> Scan:
    """Insert a real ``scans`` row so audit-log FK constraints are happy."""
    receipt = Receipt(
        id=uuid.uuid4(),
        store_id=store.id,
        purchased_at=date.today(),
        image_r2_key="fake-receipt-key.jpg",
    )
    db.add(receipt)
    db.flush()
    s = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id,
        scanned_name="UI_TEST",
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


def _insert_audit_event(
    db,
    *,
    scan_id: uuid.UUID | None = None,
    parsed_ticket_id: uuid.UUID | None = None,
    phase: str = "match",
    level: str = "normal",
    event: str = "test_event",
) -> uuid.UUID:
    row_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO pipeline_audit_log "
            "(id, scan_id, parsed_ticket_id, phase, level, event, payload) "
            "VALUES (:id, :sid, :ptid, :ph, :lv, :ev, '{}'::jsonb)"
        ),
        {
            "id": str(row_id),
            "sid": str(scan_id) if scan_id else None,
            "ptid": str(parsed_ticket_id) if parsed_ticket_id else None,
            "ph": phase,
            "lv": level,
            "ev": event,
        },
    )
    db.commit()
    return row_id


class TestAuditLog:
    def test_page_loads_empty_when_no_query(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/audit-log")
        assert r.status_code == 200
        # No query → no "rows" output ; empty marker absent because we
        # only render the empty state when rows is [], not when None.
        assert "Aucun event trouvé" not in r.text

    def test_query_by_scan_id_returns_matching_events(self, raw_client, db, user, store):
        _login(raw_client)
        scan = _make_scan_for_audit(db, user, store)
        other_scan = _make_scan_for_audit(db, user, store)
        _insert_audit_event(db, scan_id=scan.id, event="match_attempted")
        _insert_audit_event(db, scan_id=other_scan.id, event="other_event")

        r = raw_client.get(f"/admin/ui/audit-log?entity_id={scan.id}")
        assert r.status_code == 200
        assert "match_attempted" in r.text
        assert "other_event" not in r.text

    def test_query_by_invalid_uuid_shows_error(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/audit-log?entity_id=not-a-uuid")
        assert r.status_code == 200
        assert "UUID invalide" in r.text

    def test_query_with_phase_filter(self, raw_client, db, user, store):
        _login(raw_client)
        scan = _make_scan_for_audit(db, user, store)
        _insert_audit_event(db, scan_id=scan.id, phase="match", event="m_event")
        _insert_audit_event(db, scan_id=scan.id, phase="extract", event="e_event")

        r = raw_client.get(f"/admin/ui/audit-log?entity_id={scan.id}&phase=match")
        assert r.status_code == 200
        assert "m_event" in r.text
        assert "e_event" not in r.text

    def test_query_zero_results_shows_empty_marker(self, raw_client, db):
        _login(raw_client)
        unknown = uuid.uuid4()
        r = raw_client.get(f"/admin/ui/audit-log?entity_id={unknown}")
        assert r.status_code == 200
        assert "Aucun event trouvé" in r.text


# ============================================================================
# Mount discipline — UI absent when ADMIN_API_KEY missing
# ============================================================================
# We deliberately do NOT reload ``main`` to test the unset-env path :
# reloading the module mid-suite swaps the global ``app`` object and
# breaks every other test holding a reference. The defense-in-depth
# guard is the same ``if os.environ.get("ADMIN_API_KEY"):`` block that
# already gates ``/api/v1/admin/*``, which has its own coverage. A
# follow-up isolation test would require a separate test process with
# a clean env, out of scope for PR UI-1.
