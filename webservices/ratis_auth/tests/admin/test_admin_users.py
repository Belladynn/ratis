"""Admin user lookup endpoints — TDD coverage (ARCH_admin_endpoints PR6).

Endpoints under test :

- ``GET /api/v1/admin/users``            — paginated list + filters
- ``GET /api/v1/admin/users/{user_id}``  — full profile (no secrets)

Auth pattern : ``ADMIN_API_KEY`` only (read-only — no TOTP). Response
payloads MUST never expose ``password_hash`` or any refresh token /
OAuth raw token. The test suite asserts those keys are absent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from ratis_core.identifiers import generate_support_id
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Seed helpers — direct INSERTs to bypass the auth_service / OAuth path.
# Tests own the user shape, so admin tests stay decoupled from auth flow.
# ---------------------------------------------------------------------------


def _make_user(
    db,
    *,
    email: str | None = None,
    account_type: str = "oauth",
    is_deleted: bool = False,
    created_at: datetime | None = None,
    support_id: str | None = None,
) -> uuid.UUID:
    user_id = uuid.uuid4()
    email = email or f"u_{user_id.hex[:8]}@test.com"
    created_at = created_at or datetime.now(UTC)
    sid = support_id or generate_support_id()
    # Since H2 Phase 2 the OAuth identity lives in ``user_identities`` ;
    # the ``users`` row only carries an ``account_type`` state and never
    # a ``password_hash`` (OAuth-only).
    db.execute(
        text(
            "INSERT INTO users (id, email, support_id, account_type, "
            "                  password_hash, "
            "                  created_at, updated_at, is_deleted) "
            "VALUES (:id, :email, :sid, :account_type, NULL, "
            "        :created, :created, :deleted)"
        ),
        {
            "id": user_id,
            "email": email,
            "sid": sid,
            "account_type": account_type,
            "created": created_at,
            "deleted": is_deleted,
        },
    )
    db.commit()
    return user_id


def _make_refresh_token(
    db,
    user_id: uuid.UUID,
    *,
    revoked: bool = False,
    expires_in_days: int = 30,
) -> None:
    now = datetime.now(UTC)
    db.execute(
        text(
            "INSERT INTO refresh_tokens (id, jti, user_id, expires_at, "
            "                           revoked_at, created_at) "
            "VALUES (:id, :jti, :uid, :exp, :rev, :now)"
        ),
        {
            "id": uuid.uuid4(),
            "jti": f"jti-{uuid.uuid4().hex}",
            "uid": user_id,
            "exp": now + timedelta(days=expires_in_days),
            "rev": now if revoked else None,
            "now": now,
        },
    )
    db.commit()


def _make_subscription(
    db,
    user_id: uuid.UUID,
    *,
    status: str = "active",
    plan: str | None = "monthly",
    expires_in_days: int = 30,
    payment_ref: str | None = "test_ref_admin",
) -> None:
    now = datetime.now(UTC)
    db.execute(
        text(
            "INSERT INTO subscriptions "
            "  (id, user_id, status, plan, price, paid_with, payment_ref, "
            "   started_at, expires_at, cancelled_at) "
            "VALUES (:id, :uid, :status, :plan, 11.99, 'stripe', :payref, "
            "        :now, :exp, NULL)"
        ),
        {
            "id": uuid.uuid4(),
            "uid": user_id,
            "status": status,
            "plan": plan,
            "payref": payment_ref,
            "now": now,
            "exp": now + timedelta(days=expires_in_days),
        },
    )
    db.commit()


def _make_cashback_withdrawal(db, user_id: uuid.UUID, *, status: str = "pending") -> None:
    """Insert a minimal cashback_withdrawals row to test the count surface.

    PG enforces several coherence CHECKs :
    - ``status='processed'`` ⇒ ``processed_at IS NOT NULL`` AND
      ``cashback_transaction_id IS NOT NULL`` (R10 atomic withdraw audit
      trail).
    - ``status='failed'`` ⇒ ``failure_reason IS NOT NULL``.
    Honour them here so admin-summary tests can seed arbitrary statuses.
    """
    tx_id: uuid.UUID | None = None
    if status == "processed":
        tx_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO cashback_transactions "
                "  (id, user_id, type, amount, status, boost_applied, created_at) "
                "VALUES (:id, :uid, 'WITHDRAWAL', 100, 'confirmed', false, now())"
            ),
            {"id": tx_id, "uid": user_id},
        )
    db.execute(
        text(
            "INSERT INTO cashback_withdrawals "
            "  (id, user_id, amount, status, "
            "   processed_at, failure_reason, cashback_transaction_id, "
            "   requested_at, updated_at) "
            "VALUES (:id, :uid, 100, :status, "
            "        CASE WHEN :status = 'processed' THEN now() ELSE NULL END, "
            "        CASE WHEN :status = 'failed' THEN 'test reason' ELSE NULL END, "
            "        :tx_id, "
            "        now(), now())"
        ),
        {"id": uuid.uuid4(), "uid": user_id, "status": status, "tx_id": tx_id},
    )
    db.commit()


_SECRET_KEYS = {"password_hash", "refresh_token", "refresh_token_hash", "raw_oauth_token"}


def _assert_no_secrets(payload: dict | list) -> None:
    """Recursively assert no secret-shaped key is present in a JSON body."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            assert k not in _SECRET_KEYS, f"forbidden secret key '{k}' in payload"
            _assert_no_secrets(v)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_secrets(item)


# =============================================================================
# Auth gate
# =============================================================================
class TestAuthGate:
    def test_list_403_without_admin_key(self, raw_client):
        """``raw_client`` does NOT bypass admin auth → must 403."""
        resp = raw_client.get("/api/v1/admin/users")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "forbidden"

    def test_detail_403_without_admin_key(self, raw_client):
        resp = raw_client.get(f"/api/v1/admin/users/{uuid.uuid4()}")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "forbidden"

    def test_list_403_with_wrong_admin_key(self, raw_client):
        resp = raw_client.get(
            "/api/v1/admin/users",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "forbidden"


# =============================================================================
# GET /admin/users — list
# =============================================================================
class TestAdminListUsers:
    def test_list_returns_users_with_summary_fields(self, admin_client, db):
        u1 = _make_user(db, email="alice@test.com", account_type="oauth")
        u2 = _make_user(db, email="bob@test.com", account_type="oauth")

        resp = admin_client.get("/api/v1/admin/users")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert "users" in body
        assert isinstance(body["users"], list)
        assert "total" in body
        assert body["total"] >= 2
        assert "limit" in body
        assert "offset" in body

        # Find seeded users in the response (alphabetical filtering by email)
        ids = {u["id"] for u in body["users"]}
        assert str(u1) in ids
        assert str(u2) in ids

        # Summary fields per user — and ZERO secrets
        for u in body["users"]:
            assert set(u.keys()) >= {"id", "email", "created_at", "is_deleted", "account_type"}
            _assert_no_secrets(u)

    def test_list_excludes_deleted_by_default(self, admin_client, db):
        active = _make_user(db, email="active@test.com")
        deleted = _make_user(db, email="deleted@test.com", is_deleted=True)

        resp = admin_client.get("/api/v1/admin/users")
        assert resp.status_code == 200
        ids = {u["id"] for u in resp.json()["users"]}
        assert str(active) in ids
        assert str(deleted) not in ids

    def test_list_with_is_deleted_true_includes_deleted(self, admin_client, db):
        deleted = _make_user(db, email="del2@test.com", is_deleted=True)

        resp = admin_client.get("/api/v1/admin/users?is_deleted=true")
        assert resp.status_code == 200
        ids = {u["id"] for u in resp.json()["users"]}
        assert str(deleted) in ids

    def test_list_filter_email_contains(self, admin_client, db):
        match = _make_user(db, email="needle-match@test.com")
        _make_user(db, email="other@example.fr")

        resp = admin_client.get("/api/v1/admin/users?email_contains=needle")
        assert resp.status_code == 200
        body = resp.json()
        ids = {u["id"] for u in body["users"]}
        assert str(match) in ids
        # filter should narrow the result set
        for u in body["users"]:
            assert "needle" in u["email"].lower()

    def test_list_filter_created_since(self, admin_client, db):
        old = _make_user(
            db,
            email="old@test.com",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        recent = _make_user(
            db,
            email="recent@test.com",
            created_at=datetime.now(UTC) - timedelta(days=1),
        )

        cutoff = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()
        resp = admin_client.get(f"/api/v1/admin/users?created_since={cutoff}")
        assert resp.status_code == 200
        ids = {u["id"] for u in resp.json()["users"]}
        assert str(recent) in ids
        assert str(old) not in ids

    def test_list_pagination_limit_offset(self, admin_client, db):
        # Seed 3 users with deterministic emails
        for i in range(3):
            _make_user(db, email=f"page-{i:02d}@test.com")

        resp = admin_client.get("/api/v1/admin/users?email_contains=page-&limit=2&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 2
        assert body["offset"] == 0
        assert len(body["users"]) <= 2
        # When 3 match, total must be 3
        assert body["total"] >= 3

        resp2 = admin_client.get("/api/v1/admin/users?email_contains=page-&limit=2&offset=2")
        assert resp2.status_code == 200
        # Second page must contain at least one row
        assert len(resp2.json()["users"]) >= 1

    def test_list_limit_capped_at_200(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/users?limit=500")
        # Either 422 from Pydantic Le=200 OR clamped — we mandate 422 for clarity.
        assert resp.status_code == 422

    def test_list_response_never_contains_password_hash(self, admin_client, db):
        _make_user(db, email="secret-check@test.com")
        resp = admin_client.get("/api/v1/admin/users?email_contains=secret-check")
        assert resp.status_code == 200
        # Raw text scan — defense in depth even if shape changes.
        assert "password_hash" not in resp.text
        assert "hashed-secret" not in resp.text


# =============================================================================
# GET /admin/users/{user_id} — detail
# =============================================================================
class TestAdminGetUser:
    def test_detail_returns_full_profile(self, admin_client, db):
        uid = _make_user(db, email="detail@test.com", account_type="oauth")
        _make_refresh_token(db, uid)
        _make_refresh_token(db, uid, revoked=True)
        _make_subscription(db, uid, status="active")
        _make_cashback_withdrawal(db, uid)
        # PG ``status_check`` allows ('pending','processed','failed') — not
        # 'paid'. The test was passing pre-Pattern A because ``create_all``
        # didn't materialise the CHECK.
        _make_cashback_withdrawal(db, uid, status="processed")

        resp = admin_client.get(f"/api/v1/admin/users/{uid}")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Core user fields
        assert body["id"] == str(uid)
        assert body["email"] == "detail@test.com"
        assert body["account_type"] == "oauth"
        assert body["is_deleted"] is False
        assert "created_at" in body
        assert "updated_at" in body

        # Aggregates
        assert body["refresh_tokens_active"] == 1  # one revoked, one active
        assert body["subscription_status"] == "active"
        assert body["cashback_withdrawal_count"] == 2

        # Secrets must NEVER surface
        _assert_no_secrets(body)
        assert "password_hash" not in resp.text
        assert "hashed-secret" not in resp.text

    def test_detail_404_when_unknown(self, admin_client):
        resp = admin_client.get(f"/api/v1/admin/users/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "user_not_found"

    def test_detail_handles_user_without_subscription(self, admin_client, db):
        uid = _make_user(db, email="no-sub@test.com")
        resp = admin_client.get(f"/api/v1/admin/users/{uid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["subscription_status"] is None
        assert body["refresh_tokens_active"] == 0
        assert body["cashback_withdrawal_count"] == 0

    def test_detail_returns_deleted_user_too(self, admin_client, db):
        """Detail endpoint is the support escape hatch — returns even
        soft-deleted users so an op can investigate post-anonymize."""
        uid = _make_user(db, email="anonymized@test.com", is_deleted=True)
        resp = admin_client.get(f"/api/v1/admin/users/{uid}")
        assert resp.status_code == 200
        assert resp.json()["is_deleted"] is True


# =============================================================================
# support_id exposure & lookup
# =============================================================================
class TestSupportIdSurface:
    def test_list_includes_support_id(self, admin_client, db):
        sid = "RTS-A3K7XP"
        uid = _make_user(db, email="suplist@test.com", support_id=sid)
        resp = admin_client.get("/api/v1/admin/users?email_contains=suplist")
        assert resp.status_code == 200
        body = resp.json()
        match = next(u for u in body["users"] if u["id"] == str(uid))
        assert match["support_id"] == sid

    def test_detail_includes_support_id(self, admin_client, db):
        sid = "RTS-Z9P4MN"
        uid = _make_user(db, email="supdetail@test.com", support_id=sid)
        resp = admin_client.get(f"/api/v1/admin/users/{uid}")
        assert resp.status_code == 200
        assert resp.json()["support_id"] == sid

    def test_list_filter_by_support_id_exact_match(self, admin_client, db):
        wanted_sid = "RTS-B7Q2WK"
        wanted = _make_user(db, email="exact-sid@test.com", support_id=wanted_sid)
        # A second user that must NOT be returned by the filter.
        _make_user(db, email="other-sid@test.com", support_id="RTS-CDEFGH")

        resp = admin_client.get(f"/api/v1/admin/users?support_id={wanted_sid}")
        assert resp.status_code == 200
        body = resp.json()
        ids = {u["id"] for u in body["users"]}
        assert ids == {str(wanted)}, f"unexpected hits : {ids}"

    def test_list_support_id_unknown_returns_empty(self, admin_client, db):
        _make_user(db, email="not-this@test.com")
        resp = admin_client.get("/api/v1/admin/users?support_id=RTS-ZZZZZZ")
        assert resp.status_code == 200
        body = resp.json()
        assert body["users"] == []
        assert body["total"] == 0

    def test_list_email_contains_and_support_id_mutually_exclusive(self, admin_client):
        resp = admin_client.get("/api/v1/admin/users?email_contains=foo&support_id=RTS-A3K7XP")
        assert resp.status_code == 422
        assert resp.json()["detail"] == "email_contains_and_support_id_mutually_exclusive"

    def test_list_support_id_invalid_format_returns_422(self, admin_client):
        # Lowercase + 'I' (forbidden char) + wrong prefix = three failures
        # but we only need ONE rejection to fire.
        resp = admin_client.get("/api/v1/admin/users?support_id=rts-a3k7xp")
        assert resp.status_code == 422

    def test_list_support_id_with_lookalike_chars_rejected(self, admin_client):
        for bad in ("RTS-IIIIII", "RTS-OOOOOO", "RTS-000000", "RTS-111111"):
            resp = admin_client.get(f"/api/v1/admin/users?support_id={bad}")
            assert resp.status_code == 422, f"expected 422 for {bad}"
