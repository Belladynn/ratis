"""Stores seed — 14 hardcoded stores.

See ARCH_seed_test_data.md § Step 1 (curated rings 2km / 2-5km / 10-15km
+ 2 edge cases : user_suggested pending and disabled soft-deleted).

Stores 1-12 are OSM-derived (real Levallois-Perret coordinates). Stores
13-14 cover edge cases : a user-suggested pending one and a soft-deleted
one. ``user_lat / user_lng`` reference is ``USER_LAT = 48.891923, USER_LON
= 2.256298`` (Levallois-Perret, 92).

Idempotent : every INSERT is guarded by ``SELECT 1 WHERE id = …`` so
re-runs are no-ops.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ratis_core.models.store import Store
from sqlalchemy import select
from sqlalchemy.orm import Session


# Deterministic UUIDs — last 3 hex chars encode store number (001 → 00e).
# Prefix ``00000000-0000-0000-0002`` distinguishes stores from users (0001).
def _store_uuid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0002-{n:012x}")


STORE_UUIDS = {i: _store_uuid(i) for i in range(1, 15)}


def _now() -> datetime:
    """Helper — timezone-aware UTC now."""
    return datetime.now(UTC)


# Hardcoded store specs. (osm_id is None for now — V1.5 OSM→SIREN pivot
# will resolve real OSM ids ; the schema allows NULL.)
_STORES: list[dict] = [
    # ── Ring 2km — daily shopping (8 stores) ────────────────────────────
    {
        "n": 1,
        "name": "Monoprix",
        "retailer": "Monoprix",
        "lat": "48.891460",
        "lng": "2.254870",
        "address": "Rue Anatole France",
        "city": "Levallois-Perret",
        "postal_code": "92300",
    },
    {
        "n": 2,
        "name": "Franprix",
        "retailer": "Franprix",
        "lat": "48.893710",
        "lng": "2.258170",
        "address": "Rue de Courcelles",
        "city": "Levallois-Perret",
        "postal_code": "92300",
    },
    {
        "n": 3,
        "name": "Carrefour Market",
        "retailer": "Carrefour Market",
        "lat": "48.894430",
        "lng": "2.252660",
        "address": "Boulevard Bineau",
        "city": "Levallois-Perret",
        "postal_code": "92300",
    },
    {
        "n": 4,
        "name": "Carrefour Express",
        "retailer": "Carrefour Express",
        "lat": "48.887760",
        "lng": "2.261390",
        "address": "Rue Jean Jaurès",
        "city": "Levallois-Perret",
        "postal_code": "92300",
    },
    {
        "n": 5,
        "name": "Naturalia",
        "retailer": "Naturalia",
        "lat": "48.895550",
        "lng": "2.249280",
        "address": "Rue Aristide Briand",
        "city": "Levallois-Perret",
        "postal_code": "92300",
    },
    {
        "n": 6,
        "name": "G20",
        "retailer": "G20",
        "lat": "48.898050",
        "lng": "2.247070",
        "address": "Rue Voltaire",
        "city": "Levallois-Perret",
        "postal_code": "92300",
    },
    {
        "n": 7,
        "name": "Carrefour City",
        "retailer": "Carrefour City",
        "lat": "48.885850",
        "lng": "2.245970",
        "address": "Rue Anatole France",
        "city": "Levallois-Perret",
        "postal_code": "92300",
    },
    {
        "n": 8,
        "name": "Aldi",
        "retailer": "Aldi",
        "lat": "48.882510",
        "lng": "2.238560",
        "address": "Rue Victor Hugo",
        "city": "Levallois-Perret",
        "postal_code": "92300",
    },
    # ── Ring 2-5km — driving (3 stores) ──────────────────────────────────
    {
        "n": 9,
        "name": "Auchan Supermarché",
        "retailer": "Auchan",
        "lat": "48.876000",
        "lng": "2.275000",
        "address": "Avenue Charles de Gaulle",
        "city": "Neuilly-sur-Seine",
        "postal_code": "92200",
    },
    {
        "n": 10,
        "name": "Intermarché",
        "retailer": "Intermarché",
        "lat": "48.908000",
        "lng": "2.260000",
        "address": "Avenue de la République",
        "city": "Clichy",
        "postal_code": "92110",
    },
    {
        "n": 11,
        "name": "Le Petit Casino",
        "retailer": "Casino",
        "lat": "48.880000",
        "lng": "2.236000",
        "address": "Avenue Gambetta",
        "city": "Courbevoie",
        "postal_code": "92400",
    },
    # ── Ring 10-15km — out of perimeter (1 store) ────────────────────────
    {
        "n": 12,
        "name": "Carrefour City",
        "retailer": "Carrefour City",
        "lat": "48.980000",
        "lng": "2.310000",
        "address": "Place de la Mairie",
        "city": "Saint-Denis",
        "postal_code": "93200",
    },
]


def _seed_osm_stores(session: Session) -> int:
    """Insert the 12 OSM-derived stores (source='osm', validation_status='confirmed')."""
    inserted = 0
    for spec in _STORES:
        store_id = STORE_UUIDS[spec["n"]]
        existing = session.execute(select(Store).where(Store.id == store_id)).scalar_one_or_none()
        if existing is not None:
            continue
        session.add(
            Store(
                id=store_id,
                name=spec["name"],
                retailer=spec["retailer"],
                address=spec["address"],
                city=spec["city"],
                postal_code=spec["postal_code"],
                lat=Decimal(spec["lat"]),
                lng=Decimal(spec["lng"]),
                is_disabled=False,
                disabled_at=None,
                source="osm",
                validation_status="confirmed",
            )
        )
        inserted += 1
    return inserted


def _seed_user_suggested_pending(session: Session) -> int:
    """Edge case #13 — store suggested by a user, awaiting admin validation.

    Per ARCH § Step 1 lines 281-282 : ``source='user_suggested', lat=0, lng=0,
    validation_status='pending'``. Tests the admin validation flow + the
    "store suggéré en attente de validation" UI state.
    """
    store_id = STORE_UUIDS[13]
    existing = session.execute(select(Store).where(Store.id == store_id)).scalar_one_or_none()
    if existing is not None:
        return 0
    session.add(
        Store(
            id=store_id,
            name="Carrefour Contact (suggéré)",
            retailer="Carrefour Contact",
            address="Adresse à valider",
            city="Levallois-Perret",
            postal_code="92300",
            lat=Decimal("0"),
            lng=Decimal("0"),
            is_disabled=False,
            disabled_at=None,
            source="user_suggested",
            validation_status="pending",
        )
    )
    return 1


def _seed_soft_deleted(session: Session) -> int:
    """Edge case #14 — soft-deleted store (is_disabled=true, disabled_at -30d).

    Per ARCH § Step 1 line 283 : tests the soft-delete UI state + that
    disabled stores don't appear in nearby_stores queries.
    """
    store_id = STORE_UUIDS[14]
    existing = session.execute(select(Store).where(Store.id == store_id)).scalar_one_or_none()
    if existing is not None:
        return 0
    now = _now()
    session.add(
        Store(
            id=store_id,
            name="Lidl (fermé)",
            retailer="Lidl",
            address="Rue Marius Aufan",
            city="Levallois-Perret",
            postal_code="92300",
            lat=Decimal("48.890000"),
            lng=Decimal("2.250000"),
            is_disabled=True,
            disabled_at=now - timedelta(days=30),
            source="osm",
            validation_status="confirmed",
        )
    )
    return 1


def seed_stores(session: Session) -> None:
    """Insert 14 stores (12 OSM-derived + 2 edge cases). See ARCH § Step 1.

    Idempotent — re-runs skip already-inserted rows.
    """
    print("[stores] seeding 14 stores (12 OSM-derived + 2 edge cases)…")
    osm_count = _seed_osm_stores(session)
    pending_count = _seed_user_suggested_pending(session)
    disabled_count = _seed_soft_deleted(session)
    session.flush()
    print(
        f"[stores] done — {osm_count} OSM-derived + {pending_count} pending + "
        f"{disabled_count} soft-deleted (total inserted this run)"
    )
