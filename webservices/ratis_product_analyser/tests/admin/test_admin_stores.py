"""Tests for the PA admin store-validation endpoints (ARCH_admin_endpoints PR5).

Covers :

- ``GET    /api/v1/admin/stores`` — browse + filter
- ``PATCH  /api/v1/admin/stores/{store_id}/validate`` — force-confirm
- ``POST   /api/v1/admin/stores/validate-bulk`` — atomic bulk validate
- ``PATCH  /api/v1/admin/stores/{store_id}/disable`` — soft-delete
- ``PATCH  /api/v1/admin/stores/{store_id}/geocode`` — set lat/lng

Uses the service-level conftest at ``tests/conftest.py`` for DB,
TestClient and admin auth bypass fixtures.
"""

from __future__ import annotations

import uuid
from datetime import UTC
from decimal import Decimal

from ratis_core.models.store import Store
from sqlalchemy import text

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_store(
    db,
    *,
    name: str | None = None,
    retailer: str | None = "lidl",
    city: str | None = "Paris",
    postal_code: str | None = "75001",
    lat: Decimal | float = Decimal("48.8566"),
    lng: Decimal | float = Decimal("2.3522"),
    validation_status: str = "confirmed",
    source: str = "osm",
    is_disabled: bool = False,
) -> Store:
    # PG ``disabled_at_check`` : ``is_disabled=true`` ⇔ ``disabled_at NOT NULL``.
    from datetime import datetime

    disabled_at = datetime.now(UTC) if is_disabled else None
    s = Store(
        id=uuid.uuid4(),
        name=name or f"Store-{uuid.uuid4().hex[:6]}",
        retailer=retailer,
        address="1 rue Test",
        city=city,
        postal_code=postal_code,
        lat=Decimal(str(lat)),
        lng=Decimal(str(lng)),
        is_disabled=is_disabled,
        disabled_at=disabled_at,
        source=source,
        validation_status=validation_status,
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _history_rows(db, store_id: uuid.UUID) -> list:
    return db.execute(
        text(
            "SELECT from_status, to_status, reason, triggered_by, meta "
            "FROM store_validation_history WHERE store_id = :sid "
            "ORDER BY created_at, id"
        ),
        {"sid": str(store_id)},
    ).fetchall()


# ============================================================================
# GET /admin/stores
# ============================================================================
class TestListStores:
    def test_list_filters_by_validation_status_pending(self, admin_client, db):
        _make_store(db, name="A-pending", validation_status="pending", source="user_suggested")
        _make_store(db, name="B-confirmed", validation_status="confirmed")

        r = admin_client.get("/api/v1/admin/stores?validation_status=pending")
        assert r.status_code == 200, r.text
        body = r.json()
        names = [row["name"] for row in body["items"]]
        assert "A-pending" in names
        assert "B-confirmed" not in names

    def test_list_filters_by_validation_status_confirmed(self, admin_client, db):
        _make_store(db, name="C-pending", validation_status="pending", source="user_suggested")
        _make_store(db, name="D-confirmed", validation_status="confirmed")

        r = admin_client.get("/api/v1/admin/stores?validation_status=confirmed")
        assert r.status_code == 200, r.text
        body = r.json()
        names = [row["name"] for row in body["items"]]
        assert "D-confirmed" in names
        assert "C-pending" not in names

    def test_list_filters_by_validation_status_disabled(self, admin_client, db):
        _make_store(db, name="E-active", validation_status="confirmed", is_disabled=False)
        _make_store(db, name="F-disabled", validation_status="confirmed", is_disabled=True)

        r = admin_client.get("/api/v1/admin/stores?validation_status=disabled")
        assert r.status_code == 200, r.text
        body = r.json()
        names = [row["name"] for row in body["items"]]
        assert "F-disabled" in names
        assert "E-active" not in names

    def test_list_filters_by_retailer(self, admin_client, db):
        _make_store(db, name="Lidl-A", retailer="lidl")
        _make_store(db, name="Carrefour-B", retailer="carrefour")

        r = admin_client.get("/api/v1/admin/stores?retailer=lidl")
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["items"]]
        assert "Lidl-A" in names
        assert "Carrefour-B" not in names

    def test_list_filters_by_postal_code(self, admin_client, db):
        _make_store(db, name="Paris-A", postal_code="75001")
        _make_store(db, name="Lyon-B", postal_code="69001")

        r = admin_client.get("/api/v1/admin/stores?postal_code=75001")
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["items"]]
        assert "Paris-A" in names
        assert "Lyon-B" not in names

    def test_list_filters_by_city(self, admin_client, db):
        _make_store(db, name="P-A", city="Paris")
        _make_store(db, name="L-B", city="Lyon")

        r = admin_client.get("/api/v1/admin/stores?city=Paris")
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["items"]]
        assert "P-A" in names
        assert "L-B" not in names

    def test_list_search_text_fuzzy_on_name(self, admin_client, db):
        _make_store(db, name="Marché Bio Centrale")
        _make_store(db, name="Carrefour Express")

        r = admin_client.get("/api/v1/admin/stores?search=marche")
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["items"]]
        assert "Marché Bio Centrale" in names
        assert "Carrefour Express" not in names

    def test_list_paginated(self, admin_client, db):
        for i in range(5):
            _make_store(db, name=f"Pag-{i:02d}", retailer="paginate-test")
        r1 = admin_client.get("/api/v1/admin/stores?retailer=paginate-test&limit=2&offset=0")
        r2 = admin_client.get("/api/v1/admin/stores?retailer=paginate-test&limit=2&offset=2")
        assert r1.status_code == 200
        assert r2.status_code == 200
        items1 = r1.json()["items"]
        items2 = r2.json()["items"]
        assert len(items1) == 2
        assert len(items2) == 2
        ids1 = {row["id"] for row in items1}
        ids2 = {row["id"] for row in items2}
        assert ids1.isdisjoint(ids2)
        assert r1.json()["total"] == 5

    def test_list_orders_by_created_at_desc(self, admin_client, db):
        s_old = _make_store(db, name="Old", retailer="ord-test")
        s_new = _make_store(db, name="New", retailer="ord-test")
        # Force created_at ordering — bump the new one ahead.
        db.execute(
            text("UPDATE stores SET created_at = now() + interval '1 minute' WHERE id = :sid"),
            {"sid": str(s_new.id)},
        )
        db.execute(
            text("UPDATE stores SET created_at = now() - interval '1 minute' WHERE id = :sid"),
            {"sid": str(s_old.id)},
        )
        db.commit()

        r = admin_client.get("/api/v1/admin/stores?retailer=ord-test")
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["items"]]
        assert names == ["New", "Old"]

    def test_list_limit_max_500(self, admin_client, db):
        r = admin_client.get("/api/v1/admin/stores?limit=501")
        assert r.status_code == 422

    def test_list_invalid_validation_status(self, admin_client, db):
        r = admin_client.get("/api/v1/admin/stores?validation_status=bogus")
        assert r.status_code == 422

    def test_list_unauth_without_admin_key_403(self, raw_client):
        r = raw_client.get("/api/v1/admin/stores")
        assert r.status_code == 403
        assert r.json()["detail"] == "forbidden"


# ============================================================================
# PATCH /admin/stores/{store_id}/validate
# ============================================================================
class TestValidateStore:
    def test_validate_changes_status_to_confirmed(self, admin_client, db):
        s = _make_store(db, validation_status="pending", source="user_suggested")
        r = admin_client.patch(
            f"/api/v1/admin/stores/{s.id}/validate",
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["validation_status"] == "confirmed"

        row = db.execute(
            text("SELECT validation_status FROM stores WHERE id = :sid"),
            {"sid": str(s.id)},
        ).first()
        assert row.validation_status == "confirmed"

    def test_validate_404_when_not_found(self, admin_client, db):
        r = admin_client.patch(
            f"/api/v1/admin/stores/{uuid.uuid4()}/validate",
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "store_not_found"

    def test_validate_409_when_already_confirmed(self, admin_client, db):
        s = _make_store(db, validation_status="confirmed")
        r = admin_client.patch(
            f"/api/v1/admin/stores/{s.id}/validate",
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 409
        assert r.json()["detail"] == "store_already_confirmed"

    def test_validate_logs_history_with_operator(self, admin_client, db):
        s = _make_store(db, validation_status="pending", source="user_suggested")
        admin_client.patch(
            f"/api/v1/admin/stores/{s.id}/validate",
            headers={"X-Admin-Operator": "alice"},
        )
        rows = _history_rows(db, s.id)
        assert len(rows) == 1
        h = rows[0]
        assert h.from_status == "pending"
        assert h.to_status == "confirmed"
        assert h.reason == "admin_validate"
        assert h.triggered_by == "admin:alice"

    def test_validate_requires_admin_operator_header(self, admin_client, db):
        s = _make_store(db, validation_status="pending", source="user_suggested")
        r = admin_client.patch(f"/api/v1/admin/stores/{s.id}/validate")
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_required"

    def test_validate_unauth_without_admin_key_403(self, raw_client, db):
        s = _make_store(db, validation_status="pending", source="user_suggested")
        r = raw_client.patch(
            f"/api/v1/admin/stores/{s.id}/validate",
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 403


# ============================================================================
# POST /admin/stores/validate-bulk
# ============================================================================
class TestValidateBulk:
    def test_bulk_validates_all_pending(self, admin_client, db):
        ids = [_make_store(db, validation_status="pending", source="user_suggested").id for _ in range(3)]
        r = admin_client.post(
            "/api/v1/admin/stores/validate-bulk",
            json={"ids": [str(i) for i in ids]},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert sorted(body["validated"]) == sorted(str(i) for i in ids)
        assert body["skipped_already_confirmed"] == []
        assert body["not_found"] == []

        for sid in ids:
            row = db.execute(
                text("SELECT validation_status FROM stores WHERE id = :sid"),
                {"sid": str(sid)},
            ).first()
            assert row.validation_status == "confirmed"

    def test_bulk_skips_already_confirmed(self, admin_client, db):
        pending_id = _make_store(db, validation_status="pending", source="user_suggested").id
        confirmed_id = _make_store(db, validation_status="confirmed").id
        r = admin_client.post(
            "/api/v1/admin/stores/validate-bulk",
            json={"ids": [str(pending_id), str(confirmed_id)]},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["validated"] == [str(pending_id)]
        assert body["skipped_already_confirmed"] == [str(confirmed_id)]
        assert body["not_found"] == []

    def test_bulk_reports_not_found(self, admin_client, db):
        pending_id = _make_store(db, validation_status="pending", source="user_suggested").id
        ghost_id = uuid.uuid4()
        r = admin_client.post(
            "/api/v1/admin/stores/validate-bulk",
            json={"ids": [str(pending_id), str(ghost_id)]},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["validated"] == [str(pending_id)]
        assert body["not_found"] == [str(ghost_id)]

    def test_bulk_requires_admin_operator_header(self, admin_client, db):
        pending_id = _make_store(db, validation_status="pending", source="user_suggested").id
        r = admin_client.post(
            "/api/v1/admin/stores/validate-bulk",
            json={"ids": [str(pending_id)]},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_required"

    def test_bulk_logs_history_per_validated(self, admin_client, db):
        ids = [_make_store(db, validation_status="pending", source="user_suggested").id for _ in range(2)]
        admin_client.post(
            "/api/v1/admin/stores/validate-bulk",
            json={"ids": [str(i) for i in ids]},
            headers={"X-Admin-Operator": "alice"},
        )
        for sid in ids:
            rows = _history_rows(db, sid)
            assert len(rows) == 1
            assert rows[0].triggered_by == "admin:alice"
            assert rows[0].reason == "admin_validate_bulk"

    def test_bulk_empty_ids_rejected(self, admin_client, db):
        r = admin_client.post(
            "/api/v1/admin/stores/validate-bulk",
            json={"ids": []},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 422

    def test_bulk_unauth_without_admin_key_403(self, raw_client):
        r = raw_client.post(
            "/api/v1/admin/stores/validate-bulk",
            json={"ids": [str(uuid.uuid4())]},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 403


# ============================================================================
# PATCH /admin/stores/{store_id}/disable
# ============================================================================
class TestDisableStore:
    def test_disable_sets_is_disabled_true(self, admin_client, db):
        s = _make_store(db, validation_status="confirmed", is_disabled=False)
        r = admin_client.patch(
            f"/api/v1/admin/stores/{s.id}/disable",
            json={"reason": "permanently closed"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["is_disabled"] is True

        row = db.execute(
            text("SELECT is_disabled, disabled_at FROM stores WHERE id = :sid"),
            {"sid": str(s.id)},
        ).first()
        assert row.is_disabled is True
        assert row.disabled_at is not None

    def test_disable_404_when_not_found(self, admin_client, db):
        r = admin_client.patch(
            f"/api/v1/admin/stores/{uuid.uuid4()}/disable",
            json={"reason": "abc"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "store_not_found"

    def test_disable_409_when_already_disabled(self, admin_client, db):
        s = _make_store(db, is_disabled=True)
        r = admin_client.patch(
            f"/api/v1/admin/stores/{s.id}/disable",
            json={"reason": "duplicate"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 409
        assert r.json()["detail"] == "store_already_disabled"

    def test_disable_requires_reason_min_3_chars(self, admin_client, db):
        s = _make_store(db)
        r = admin_client.patch(
            f"/api/v1/admin/stores/{s.id}/disable",
            json={"reason": "ab"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 422

    def test_disable_requires_admin_operator_header(self, admin_client, db):
        s = _make_store(db)
        r = admin_client.patch(
            f"/api/v1/admin/stores/{s.id}/disable",
            json={"reason": "ghost store"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_required"

    def test_disable_logs_history_with_operator_and_reason(self, admin_client, db):
        s = _make_store(db, validation_status="confirmed")
        admin_client.patch(
            f"/api/v1/admin/stores/{s.id}/disable",
            json={"reason": "fake store reported"},
            headers={"X-Admin-Operator": "bob"},
        )
        rows = _history_rows(db, s.id)
        assert len(rows) == 1
        h = rows[0]
        assert h.from_status == "confirmed"
        assert h.to_status == "confirmed"  # validation_status unchanged
        assert h.reason == "admin_disable"
        assert h.triggered_by == "admin:bob"
        assert h.meta == {"disable_reason": "fake store reported"}

    def test_disable_unauth_without_admin_key_403(self, raw_client, db):
        s = _make_store(db)
        r = raw_client.patch(
            f"/api/v1/admin/stores/{s.id}/disable",
            json={"reason": "abc"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 403


# ============================================================================
# PATCH /admin/stores/{store_id}/geocode
# ============================================================================
class TestGeocodeStore:
    def test_geocode_sets_lat_lng(self, admin_client, db):
        s = _make_store(db, lat=Decimal("0"), lng=Decimal("0"), source="user_suggested")
        r = admin_client.patch(
            f"/api/v1/admin/stores/{s.id}/geocode",
            json={"lat": 48.8566, "lng": 2.3522},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert abs(body["lat"] - 48.8566) < 1e-4
        assert abs(body["lng"] - 2.3522) < 1e-4

        row = db.execute(
            text("SELECT lat, lng FROM stores WHERE id = :sid"),
            {"sid": str(s.id)},
        ).first()
        assert abs(float(row.lat) - 48.8566) < 1e-4
        assert abs(float(row.lng) - 2.3522) < 1e-4

    def test_geocode_validates_lat_range(self, admin_client, db):
        s = _make_store(db)
        r = admin_client.patch(
            f"/api/v1/admin/stores/{s.id}/geocode",
            json={"lat": 91.0, "lng": 2.0},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 422

    def test_geocode_validates_lng_range(self, admin_client, db):
        s = _make_store(db)
        r = admin_client.patch(
            f"/api/v1/admin/stores/{s.id}/geocode",
            json={"lat": 48.0, "lng": 181.0},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 422

    def test_geocode_404_when_not_found(self, admin_client, db):
        r = admin_client.patch(
            f"/api/v1/admin/stores/{uuid.uuid4()}/geocode",
            json={"lat": 48.0, "lng": 2.0},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "store_not_found"

    def test_geocode_requires_admin_operator_header(self, admin_client, db):
        s = _make_store(db)
        r = admin_client.patch(
            f"/api/v1/admin/stores/{s.id}/geocode",
            json={"lat": 48.0, "lng": 2.0},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_required"

    def test_geocode_logs_history_with_payload(self, admin_client, db):
        s = _make_store(db, lat=Decimal("0"), lng=Decimal("0"), source="user_suggested")
        admin_client.patch(
            f"/api/v1/admin/stores/{s.id}/geocode",
            json={"lat": 48.8566, "lng": 2.3522},
            headers={"X-Admin-Operator": "carol"},
        )
        rows = _history_rows(db, s.id)
        assert len(rows) == 1
        h = rows[0]
        assert h.reason == "admin_geocode"
        assert h.triggered_by == "admin:carol"
        assert h.meta is not None
        assert abs(h.meta["lat"] - 48.8566) < 1e-4
        assert abs(h.meta["lng"] - 2.3522) < 1e-4

    def test_geocode_unauth_without_admin_key_403(self, raw_client, db):
        s = _make_store(db)
        r = raw_client.patch(
            f"/api/v1/admin/stores/{s.id}/geocode",
            json={"lat": 48.0, "lng": 2.0},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 403


def test_module_collects():
    assert True
