"""Tests for services.store_creation_service."""

from __future__ import annotations

import uuid
from datetime import UTC
from decimal import Decimal

import pytest
from ratis_core.models.store import Store
from services.store_creation_service import create_store_from_receipt


def test_creates_new_store_when_no_dedup_hit(db):
    store = create_store_from_receipt(
        db,
        retailer="monoprix",
        address_raw="21 place République, Paris",
        coords=(48.8676, 2.3631),
    )
    db.commit()

    assert store.id is not None
    assert store.retailer == "monoprix"
    assert store.source == "user_suggested"
    assert float(store.lat) == pytest.approx(48.8676)
    assert float(store.lng) == pytest.approx(2.3631)


def test_returns_existing_within_dedup_radius(db):
    existing = Store(
        id=uuid.uuid4(),
        name="Monoprix République",
        retailer="monoprix",
        address="21 place République, Paris",
        lat=Decimal("48.86760"),
        lng=Decimal("2.36310"),
    )
    db.add(existing)
    db.flush()
    db.commit()

    # Coords ~10m away — should dedup
    got = create_store_from_receipt(
        db,
        retailer="monoprix",
        address_raw="21 place République",
        coords=(48.86770, 2.36320),
    )
    db.commit()

    assert got.id == existing.id


def test_creates_new_store_when_brand_differs(db):
    existing = Store(
        id=uuid.uuid4(),
        name="Carrefour Rivoli",
        retailer="carrefour",
        lat=Decimal("48.86760"),
        lng=Decimal("2.36310"),
    )
    db.add(existing)
    db.flush()
    db.commit()

    got = create_store_from_receipt(
        db,
        retailer="monoprix",
        address_raw="X",
        coords=(48.86770, 2.36320),
    )
    db.commit()

    assert got.id != existing.id
    assert got.retailer == "monoprix"


def test_ignores_disabled_stores_in_dedup(db):
    from datetime import datetime

    disabled = Store(
        id=uuid.uuid4(),
        name="Monoprix ancien",
        retailer="monoprix",
        lat=Decimal("48.86760"),
        lng=Decimal("2.36310"),
        is_disabled=True,
        disabled_at=datetime.now(UTC),
    )
    db.add(disabled)
    db.flush()
    db.commit()

    got = create_store_from_receipt(
        db,
        retailer="monoprix",
        address_raw="X",
        coords=(48.86770, 2.36320),
    )
    db.commit()

    assert got.id != disabled.id
