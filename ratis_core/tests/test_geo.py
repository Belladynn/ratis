import uuid
from datetime import UTC, datetime
from decimal import Decimal

from ratis_core.models.store import Store

from ratis_core import geo

# Rennes centre ; coords de référence pour les tests.
_REF_LAT, _REF_LNG = 48.1173, -1.6778


def _store(db, *, name, lat, lng, disabled=False, retailer_id=None):
    s = Store(
        id=uuid.uuid4(),
        name=name,
        lat=Decimal(str(lat)),
        lng=Decimal(str(lng)),
        is_disabled=disabled,
        retailer_id=retailer_id,
        # disabled_at_check : disabled_at doit être set ssi is_disabled.
        disabled_at=datetime.now(UTC) if disabled else None,
    )
    db.add(s)
    db.flush()
    return s


def test_stores_within_radius_orders_by_distance(db):
    far = _store(db, name="Loin", lat=48.20, lng=-1.60)
    near = _store(db, name="Proche", lat=48.118, lng=-1.678)
    res = geo.stores_within_radius(db, _REF_LAT, _REF_LNG, radius_km=15)
    assert [p.store.id for p in res] == [near.id, far.id]
    assert res[0].distance_km < res[1].distance_km


def test_stores_within_radius_excludes_outside(db):
    _store(db, name="Hors rayon", lat=49.0, lng=-1.6)
    res = geo.stores_within_radius(db, _REF_LAT, _REF_LNG, radius_km=5)
    assert res == []


def test_stores_within_radius_excludes_disabled_by_default(db):
    _store(db, name="Désactivé", lat=48.118, lng=-1.678, disabled=True)
    res = geo.stores_within_radius(db, _REF_LAT, _REF_LNG, radius_km=5)
    assert res == []
    res_incl = geo.stores_within_radius(db, _REF_LAT, _REF_LNG, radius_km=5, include_disabled=True)
    assert len(res_incl) == 1


def test_stores_within_radius_excludes_zero_zero(db):
    _store(db, name="Fantôme", lat=0, lng=0)
    res = geo.stores_within_radius(db, 0.0, 0.0, radius_km=5)
    assert res == []


def test_stores_within_radius_exclude_ids(db):
    a = _store(db, name="A", lat=48.118, lng=-1.678)
    b = _store(db, name="B", lat=48.119, lng=-1.679)
    res = geo.stores_within_radius(db, _REF_LAT, _REF_LNG, radius_km=5, exclude_store_ids={a.id})
    assert [p.store.id for p in res] == [b.id]


def test_nearest_stores_knn(db):
    near = _store(db, name="Proche", lat=48.118, lng=-1.678)
    _store(db, name="Loin", lat=48.20, lng=-1.60)
    res = geo.nearest_stores(db, _REF_LAT, _REF_LNG, k=1)
    assert len(res) == 1
    assert res[0].store.id == near.id


def test_nearest_stores_respects_max_radius(db):
    _store(db, name="Trop loin", lat=49.0, lng=-1.6)
    res = geo.nearest_stores(db, _REF_LAT, _REF_LNG, k=5, max_radius_km=5)
    assert res == []


def test_distance_km_pure():
    d = geo.distance_km(48.1173, -1.6778, 48.1173, -1.6778)
    assert d == 0.0
    d2 = geo.distance_km(48.1173, -1.6778, 48.20, -1.60)
    assert 8.0 < d2 < 13.0
