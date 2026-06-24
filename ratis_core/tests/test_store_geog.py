import uuid
from decimal import Decimal

from ratis_core.models.store import Store
from sqlalchemy import text


def _store(session, *, name, lat, lng):
    s = Store(id=uuid.uuid4(), name=name, lat=Decimal(str(lat)), lng=Decimal(str(lng)))
    session.add(s)
    session.flush()
    return s


def test_geog_generated_for_real_coords(db):
    s = _store(db, name="Carrefour Rennes", lat=48.1173, lng=-1.6778)
    geog = db.execute(text("SELECT geog IS NOT NULL FROM stores WHERE id = :id"), {"id": s.id}).scalar()
    assert geog is True


def test_geog_null_for_zero_zero_placeholder(db):
    s = _store(db, name="Magasin suggéré", lat=0, lng=0)
    geog = db.execute(text("SELECT geog FROM stores WHERE id = :id"), {"id": s.id}).scalar()
    assert geog is None


def test_gist_index_exists(db):
    exists = db.execute(text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_stores_geog'")).scalar()
    assert exists == 1
