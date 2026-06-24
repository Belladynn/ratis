"""ratis_core.geo — helpers de proximité spatiale partagés (PostGIS).

Toutes les recherches « magasins proches d'un point » passent par ce
module, bâti sur la colonne générée stores.geog + son index GIST.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ratis_core.models.store import Store

_EARTH_RADIUS_KM = 6371.0
# CTE qui matérialise le point de requête une seule fois.
_POINT_CTE = "WITH _q AS (SELECT ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography AS pt)"


@dataclass(frozen=True)
class StoreProximity:
    """Un magasin et sa distance au point de requête."""

    store: Store
    distance_km: float


def _hydrate(db: Session, rows: list) -> list[StoreProximity]:
    """Charge les objets Store ORM en préservant l'ordre des `rows`."""
    if not rows:
        return []
    ids = [r.store_id for r in rows]
    dist = {r.store_id: float(r.distance_km) for r in rows}
    stores = {s.id: s for s in db.scalars(select(Store).where(Store.id.in_(ids)))}
    return [StoreProximity(store=stores[i], distance_km=dist[i]) for i in ids]


def stores_within_radius(
    db: Session,
    lat: float,
    lng: float,
    radius_km: float,
    *,
    include_disabled: bool = False,
    exclude_store_ids: set[uuid.UUID] | None = None,
    retailer_id: uuid.UUID | None = None,
) -> list[StoreProximity]:
    """Magasins dans le rayon, triés du plus proche au plus loin."""
    params: dict = {"lat": lat, "lng": lng, "radius_m": radius_km * 1000.0}
    where = ["s.geog IS NOT NULL", "ST_DWithin(s.geog, _q.pt, :radius_m)"]
    if not include_disabled:
        where.append("NOT s.is_disabled")
    if exclude_store_ids:
        ids = list(exclude_store_ids)
        names = ", ".join(f":ex{i}" for i in range(len(ids)))
        where.append(f"s.id NOT IN ({names})")
        for i, sid in enumerate(ids):
            params[f"ex{i}"] = sid
    if retailer_id is not None:
        where.append("s.retailer_id = :retailer_id")
        params["retailer_id"] = retailer_id
    # S608-clean : `where` ne contient que des fragments SQL littéraux et
    # des placeholders `:param` ; aucune valeur utilisateur n'est interpolée.
    sql = text(
        f"{_POINT_CTE} "  # noqa: S608 — where built from hardcoded literals
        "SELECT s.id AS store_id, "
        "ST_Distance(s.geog, _q.pt) / 1000.0 AS distance_km "
        "FROM stores s, _q "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY s.geog <-> _q.pt"
    )
    return _hydrate(db, db.execute(sql, params).all())


def nearest_stores(
    db: Session,
    lat: float,
    lng: float,
    k: int = 1,
    *,
    max_radius_km: float | None = None,
    retailer_id: uuid.UUID | None = None,
) -> list[StoreProximity]:
    """Les `k` magasins actifs les plus proches (KNN indexé)."""
    params: dict = {"lat": lat, "lng": lng, "k": k}
    where = ["s.geog IS NOT NULL", "NOT s.is_disabled"]
    if max_radius_km is not None:
        where.append("ST_DWithin(s.geog, _q.pt, :radius_m)")
        params["radius_m"] = max_radius_km * 1000.0
    if retailer_id is not None:
        where.append("s.retailer_id = :retailer_id")
        params["retailer_id"] = retailer_id
    # S608-clean : voir stores_within_radius — fragments littéraux uniquement.
    sql = text(
        f"{_POINT_CTE} "  # noqa: S608 — where built from hardcoded literals
        "SELECT s.id AS store_id, "
        "ST_Distance(s.geog, _q.pt) / 1000.0 AS distance_km "
        "FROM stores s, _q "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY s.geog <-> _q.pt LIMIT :k"
    )
    return _hydrate(db, db.execute(sql, params).all())


def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance haversine entre deux points, en km. Calcul pur (sans DB)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return _EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))
