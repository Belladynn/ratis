"""TDD coverage for :func:`repositories.retailer_resolution.resolve_retailer_id`.

Bloc B helper : converts a ``store_id`` to its parent ``retailer_id``
for the cross-retailer consensus key. Three outcomes — store with
retailer, store without retailer, store id unknown.
"""

from __future__ import annotations

import uuid

import pytest
from ratis_core.models.retailer import Retailer
from ratis_core.models.store import Store


@pytest.fixture
def retailer(db) -> Retailer:
    r = Retailer(
        id=uuid.uuid4(),
        canonical_name="Intermarche Test",
        slug=f"intermarche-test-{uuid.uuid4().hex[:8]}",
        country_code="FR",
    )
    db.add(r)
    db.flush()
    db.commit()
    return r


def _make_store(db, *, retailer_id: uuid.UUID | None = None) -> Store:
    s = Store(
        id=uuid.uuid4(),
        name="Intermarche Lyon",
        retailer="intermarche",
        retailer_id=retailer_id,
        address="1 rue Test",
        city="Lyon",
        postal_code="69001",
        lat=45.7640,
        lng=4.8357,
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def test_resolve_retailer_id_returns_retailer_for_attached_store(db, retailer):
    """Store with a non-NULL retailer_id → returns the retailer UUID."""
    from repositories.retailer_resolution import resolve_retailer_id

    store = _make_store(db, retailer_id=retailer.id)

    result = resolve_retailer_id(db, store.id)
    assert result == retailer.id


def test_resolve_retailer_id_returns_none_for_store_without_retailer(db):
    """User-suggested unvalidated store has retailer_id IS NULL → None."""
    from repositories.retailer_resolution import resolve_retailer_id

    store = _make_store(db, retailer_id=None)

    result = resolve_retailer_id(db, store.id)
    assert result is None


def test_resolve_retailer_id_returns_none_for_unknown_store(db):
    """Unknown store id → None (defensive — never crashes on stale UUIDs)."""
    from repositories.retailer_resolution import resolve_retailer_id

    result = resolve_retailer_id(db, uuid.uuid4())
    assert result is None
