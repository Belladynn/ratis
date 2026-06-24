"""Integration tests for GET /api/v1/product/suggestions/default."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ratis_core.models.product import Product
from ratis_core.models.scan import Scan

from tests.conftest import make_token

# 13-digit EANs (constraint ``ean_format``). Disjoint from the test_suggestions_service
# fixture EANs so two test files in the same session can't accidentally share rows.
EAN_C1 = "9300000000001"
EAN_C2 = "9300000000002"
EAN_C3 = "9300000000003"
EAN_C4 = "9300000000004"
EAN_U1 = "9400000000001"


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {make_token(user.id)}"}


def _make_product(db, ean, name="X"):
    p = Product(ean=ean, name=name, source="off")
    db.add(p)
    return p


def _make_scan(db, user, ean, status, scanned_at):
    s = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        scan_type="electronic_label",
        status=status,
        product_ean=ean,
        scanned_at=scanned_at,
        price=100,
        store_status="unknown",
        match_method="manual" if status == "matched" else None,
    )
    db.add(s)
    return s


def test_endpoint_requires_auth(client):
    resp = client.get("/api/v1/product/suggestions/default")
    assert resp.status_code == 401


def test_endpoint_returns_shape_with_items_array(client, user, db, monkeypatch):
    _make_product(db, EAN_C1, name="curated 1")
    _make_product(db, EAN_C2, name="curated 2")
    db.flush()
    db.commit()
    monkeypatch.setattr(
        "services.suggestions_service.load_curated_eans",
        lambda: [EAN_C1, EAN_C2],
    )
    resp = client.get(
        "/api/v1/product/suggestions/default?limit=2",
        headers=_auth(user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert isinstance(body["items"], list)
    assert len(body["items"]) == 2
    assert body["items"][0]["ean"] == EAN_C1
    assert "name" in body["items"][0]
    assert "brands" in body["items"][0]
    assert "source" in body["items"][0]


def test_endpoint_respects_limit(client, user, db, monkeypatch):
    eans = [f"95000000000{i:02d}" for i in range(8)]
    for i, e in enumerate(eans):
        _make_product(db, e, name=f"curated {i}")
    db.flush()
    db.commit()
    monkeypatch.setattr(
        "services.suggestions_service.load_curated_eans",
        lambda: eans,
    )
    resp = client.get(
        "/api/v1/product/suggestions/default?limit=3",
        headers=_auth(user),
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 3


def test_endpoint_clamps_limit_lower_bound(client, user):
    resp = client.get(
        "/api/v1/product/suggestions/default?limit=0",
        headers=_auth(user),
    )
    assert resp.status_code == 422


def test_endpoint_clamps_limit_upper_bound(client, user):
    resp = client.get(
        "/api/v1/product/suggestions/default?limit=21",
        headers=_auth(user),
    )
    assert resp.status_code == 422


def test_endpoint_uses_user_history(client, user, db, monkeypatch):
    _make_product(db, EAN_U1, name="user 1")
    _make_product(db, EAN_C1, name="curated 1")
    _make_product(db, EAN_C2, name="curated 2")
    now = datetime.now(UTC)
    _make_scan(db, user, EAN_U1, "matched", now)
    db.flush()
    db.commit()
    monkeypatch.setattr(
        "services.suggestions_service.load_curated_eans",
        lambda: [EAN_C1, EAN_C2],
    )
    resp = client.get(
        "/api/v1/product/suggestions/default?limit=3",
        headers=_auth(user),
    )
    body = resp.json()
    eans = [item["ean"] for item in body["items"]]
    assert eans == [EAN_U1, EAN_C1, EAN_C2]
