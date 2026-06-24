"""
Shared OSM → stores normalization helpers.

Used by both :
- ``osm_sync.py`` (Overpass JSON elements — legacy, for small/targeted queries)
- ``osm_bulk_import.py`` (PBF streaming — primary V1 path)

Centralising the logic avoids drift between the two ingestion code paths
(retailer resolution, SIRET cleanup, phone normalization, etc.).

Note (PR7 — 2026-05-31) : retailer resolution delegates to
``batch_shared.retailer_resolution.resolve_or_create_retailer`` (shared
helper). The ``slugify()`` function and PBF-specific normalization remain
here because they are OSM-specific.  ``upsert_store()`` is kept for backward
compatibility with direct callers (tests) but is DEPRECATED — new call sites
should use ``osm_dict_to_candidate()`` + ``batch_shared.store_consolidation.apply_upsert()``.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal

# Shared helper — resolve_or_create_retailer migrated to batch_shared in PR7.
# The local copy is replaced by this import (alias_source='osm' required).
from batch_shared.retailer_resolution import resolve_or_create_retailer
from batch_shared.store_consolidation import CandidateStore
from ratis_core.normalize import normalize_phone
from sqlalchemy import text
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)


_SIRET_DIGITS_RE = re.compile(r"\D")
_SLUGIFY_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """ASCII-ish slug used when auto-creating a retailer for an unknown OSM brand tag.

    Keeps alnum, everything else → hyphen, collapsed and trimmed.
    """
    lowered = value.strip().lower()
    translit = (
        lowered.replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("ë", "e")
        .replace("à", "a")
        .replace("â", "a")
        .replace("ä", "a")
        .replace("î", "i")
        .replace("ï", "i")
        .replace("ô", "o")
        .replace("ö", "o")
        .replace("ù", "u")
        .replace("û", "u")
        .replace("ü", "u")
        .replace("ç", "c")
        .replace("ñ", "n")
    )
    slug = _SLUGIFY_STRIP_RE.sub("-", translit).strip("-")
    return slug


def normalize_siret(raw: str | None) -> str | None:
    """Strip non-digit characters; return 14-digit string or None if invalid."""
    if not raw:
        return None
    digits = _SIRET_DIGITS_RE.sub("", raw)
    return digits if len(digits) == 14 else None


def build_address(housenumber: str | None, street: str | None) -> str | None:
    """Assemble a "<housenumber> <street>" string, or None if both are empty."""
    hn = (housenumber or "").strip()
    st = (street or "").strip()
    joined = f"{hn} {st}".strip()
    return joined or None


def normalize_pbf_tags(
    osm_id: int,
    lat: float | None,
    lon: float | None,
    tags: dict,
    country_code: str,
) -> dict | None:
    """Map raw PBF (osm_id, lat, lon, tags) to a stores row dict.

    Returns None if name or coords are missing (row cannot be inserted).
    Equivalent to the Overpass ``_normalize_osm_element`` path but takes
    already-extracted lat/lon/tags instead of an Overpass JSON element.
    """
    name = tags.get("name")
    if not name:
        return None
    if lat is None or lon is None:
        return None
    # Reject coordinates outside the valid WGS-84 range. OSM is an external,
    # community-edited source — a malformed lat/lng would poison the stores
    # table and break distance queries downstream.
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        _log.warning(
            "normalize_pbf_tags: osm_id=%s rejected — coords out of range (lat=%s, lon=%s)",
            osm_id,
            lat,
            lon,
        )
        return None

    raw_phone = tags.get("phone") or tags.get("contact:phone")
    phone = normalize_phone(raw_phone, country_code=country_code) if raw_phone else None

    return {
        "osm_id": osm_id,
        "name": name,
        # Raw OSM ``brand`` tag — resolved to retailer_id at upsert time.
        "retailer": tags.get("brand"),
        "address": build_address(tags.get("addr:housenumber"), tags.get("addr:street")),
        "city": tags.get("addr:city"),
        "postal_code": tags.get("addr:postcode"),
        "lat": Decimal(str(lat)),
        "lng": Decimal(str(lon)),
        "phone": phone,
        "siret": normalize_siret(tags.get("ref:FR:SIRET")),
        "opening_hours": tags.get("opening_hours"),
    }


def osm_dict_to_candidate(store_data: dict, db: Session) -> CandidateStore:
    """Convert a ``normalize_pbf_tags()`` dict to a ``CandidateStore``.

    Resolves the ``retailer`` brand tag to a ``retailer_id`` via the shared
    ``resolve_or_create_retailer`` helper (alias_source='osm').  The caller
    is responsible for ``db.commit()`` (R-DB-02).

    Precondition : ``store_data`` must have passed ``normalize_pbf_tags()``
    successfully (i.e. name is non-empty, lat/lng are valid Decimals).
    """
    brand_tag = store_data.get("retailer")
    retailer_id = resolve_or_create_retailer(db, brand_tag, alias_source="osm")
    lat = store_data.get("lat")
    lng = store_data.get("lng")
    return CandidateStore(
        source="osm",
        osm_id=store_data.get("osm_id"),
        name=store_data["name"],
        address=store_data.get("address"),
        city=store_data.get("city"),
        postal_code=store_data.get("postal_code"),
        lat=Decimal(str(lat)) if lat is not None else None,
        lng=Decimal(str(lng)) if lng is not None else None,
        siret=store_data.get("siret"),
        retailer_id=retailer_id,
        phone=store_data.get("phone"),
        opening_hours=store_data.get("opening_hours"),
        is_disabled=False,
    )


def osm_composite_key_collision(db, candidate: CandidateStore) -> bool:
    """Return True if ``unique_store`` would collide on INSERT.

    The ``unique_store`` index is:
        UNIQUE (COALESCE(retailer,''), COALESCE(address,''), COALESCE(postal_code,''))

    When a candidate has no osm_id-based or SIRET-based match but shares the
    composite key with an existing row that already has a different osm_id,
    ``apply_upsert`` would attempt an INSERT and fail with IntegrityError.
    This pre-check lets callers skip such candidates — matching the behaviour
    of the deprecated ``upsert_store`` (which also logged a WARNING and
    returned early in this case).

    Returns False (safe to proceed) when:
    - no existing row shares the composite key, or
    - the matching row has ``osm_id IS NULL`` (admin/user-suggested — apply_upsert
      handles this via the trust-priority merge path).
    """
    # Resolve the retailer text from the candidate's retailer_id for the lookup.
    # The unique_store index is on the literal ``retailer`` TEXT column, which is
    # populated by trigger from retailer_id. We replicate the lookup by querying
    # retailers.canonical_name when retailer_id is set.
    retailer_text = None
    if candidate.retailer_id is not None:
        row = db.execute(
            text("SELECT canonical_name FROM retailers WHERE id = CAST(:rid AS uuid) LIMIT 1"),
            {"rid": str(candidate.retailer_id)},
        ).first()
        if row is not None:
            retailer_text = row.canonical_name

    row = db.execute(
        text(
            """
            SELECT osm_id
            FROM stores
            WHERE COALESCE(retailer, '')    = COALESCE(:retailer, '')
              AND COALESCE(address, '')    = COALESCE(:address, '')
              AND COALESCE(postal_code,'') = COALESCE(:postal_code, '')
              AND (osm_id IS DISTINCT FROM :osm_id)
            LIMIT 1
            """
        ),
        {
            "retailer": retailer_text,
            "address": candidate.address,
            "postal_code": candidate.postal_code,
            "osm_id": candidate.osm_id,
        },
    ).first()
    if row is None:
        return False
    # Row exists with different osm_id and that osm_id is already set → collision
    # Row with osm_id IS NULL is an admin/user-suggested row that apply_upsert
    # will merge into — let it through.
    return row.osm_id is not None


# ---------------------------------------------------------------------------
# DEPRECATED — kept for backward compatibility with existing tests that call
# ``normalize.upsert_store`` directly.  New call sites (osm_sync,
# osm_bulk_import) use ``osm_dict_to_candidate() + apply_upsert()`` instead.
# Will be removed in the next major cleanup cycle.
# ---------------------------------------------------------------------------


def _siret_taken_by_other(db: Session, siret: str, osm_id: int | None) -> bool:
    """Return True if another store row already holds ``siret``."""
    row = db.execute(
        text("SELECT 1 FROM stores WHERE siret = :siret   AND (osm_id IS DISTINCT FROM :osm_id) LIMIT 1"),
        {"siret": siret, "osm_id": osm_id},
    ).first()
    return row is not None


def _existing_by_composite_key(
    db: Session,
    retailer: str | None,
    address: str | None,
    postal_code: str | None,
    osm_id: int | None,
):
    """Find a row that collides on the ``unique_store`` composite index.

    The composite unique index is :
        UNIQUE (COALESCE(retailer,''), COALESCE(address,''), COALESCE(postal_code,''))

    Returns the (id, osm_id) of an existing row that matches the same
    triple, or None if no other row would collide. Note that a NULL-only
    triple ('','','') is itself a single bucket in the index — two rows
    with no retailer/address/postal can't coexist. We detect that case the
    same way as a meaningful collision so the caller can short-circuit.
    """
    row = db.execute(
        text(
            """
            SELECT id, osm_id
            FROM stores
            WHERE COALESCE(retailer, '')    = COALESCE(:retailer, '')
              AND COALESCE(address, '')    = COALESCE(:address, '')
              AND COALESCE(postal_code,'') = COALESCE(:postal_code, '')
              AND (osm_id IS DISTINCT FROM :osm_id)
            LIMIT 1
            """
        ),
        {
            "retailer": retailer,
            "address": address,
            "postal_code": postal_code,
            "osm_id": osm_id,
        },
    ).first()
    return row


def upsert_store(db: Session, store_data: dict) -> None:  # DEPRECATED — migré vers batch_shared
    """Upsert a store row, handling all three uniqueness invariants.

    DEPRECATED (PR7) — new call sites use ``osm_dict_to_candidate()`` +
    ``batch_shared.store_consolidation.apply_upsert()``.  This function is
    kept so that existing tests that import ``normalize.upsert_store`` directly
    continue to pass without modification.

    The ``stores`` table carries three overlapping unique constraints :

      - ``uq_stores_osm_id`` — partial UNIQUE on ``osm_id``
      - ``uq_stores_siret``  — partial UNIQUE on ``siret``
      - ``unique_store``     — composite UNIQUE on
                              (retailer, address, postal_code) (NULL-safe)

    PostgreSQL only allows one ``ON CONFLICT`` target per ``INSERT``, so we
    pre-check the auxiliary uniqueness invariant (siret) in Python and
    either NULL-out the conflicting siret or merge onto the existing row
    (composite key) before issuing the canonical ``ON CONFLICT (osm_id)``
    upsert.

    Note (2026-04-27) : phone is no longer unique. A corporate standard
    phone is shared by every franchise of an enseigne, so duplicates are
    legitimate. The ``uq_stores_phone`` partial index has been dropped in
    prod and is not recreated. See ``refactor/phone-as-retailer-signal``.

    Behaviour summary :

    - **siret conflict** with another osm_id → NULL out siret on the new
      row. Preserves the partial UNIQUE invariant; the canonical siret
      owner is whoever was inserted first.
    - **(retailer, address, postal_code) conflict** with a row that has no
      osm_id (admin-seeded, user-suggested) → UPDATE that row in place,
      adopting the new osm_id and OSM-sourced fields. The seeded row is
      effectively absorbed into the OSM identity.
    - **(retailer, address, postal_code) conflict** with a row that already
      has a *different* osm_id → log a WARNING and skip. Don't silently
      overwrite the existing row's osm_id (it would mask a genuine OSM
      data-quality issue).
    - **osm_id conflict** with the same osm_id → in-place ``DO UPDATE SET``
      via ``ON CONFLICT (osm_id)`` — original behaviour preserved.

    Resolves ``store_data['retailer']`` (raw OSM ``brand`` tag) to
    ``retailer_id`` via ``retailer_aliases``. The ``retailer`` TEXT column is
    populated by the ``trg_stores_sync_retailer_text`` trigger from retailer_id.
    """
    retailer_tag = store_data.get("retailer")
    retailer_id = resolve_or_create_retailer(db, retailer_tag, alias_source="osm")

    osm_id = store_data.get("osm_id")
    phone = store_data.get("phone")
    siret = store_data.get("siret")

    # Pre-check siret to avoid IntegrityError on the partial unique index
    # (PG only accepts one ON CONFLICT target per INSERT). Phone duplicates
    # are now allowed — see docstring.
    if siret and _siret_taken_by_other(db, siret, osm_id):
        _log.warning(
            "upsert_store: siret=%s already used by another store; NULL'ing siret on osm_id=%s",
            siret,
            osm_id,
        )
        siret = None

    # Pre-check composite (retailer, address, postal_code) — the ``retailer``
    # TEXT column is populated by trigger from retailer_id, but the unique
    # index is on the literal column values. Mirror the trigger's behaviour
    # by passing the resolved retailer_tag (== retailer.canonical_name once
    # resolved) into the lookup.
    existing = _existing_by_composite_key(
        db,
        retailer=retailer_tag,
        address=store_data.get("address"),
        postal_code=store_data.get("postal_code"),
        osm_id=osm_id,
    )
    if existing is not None:
        if existing.osm_id is None:
            # Admin-seeded / user-suggested row : absorb it. UPDATE in place
            # so we don't violate the composite unique index, and adopt the
            # OSM identity for future runs.
            _log.info(
                "upsert_store: composite key (retailer/address/postal) "
                "matches admin-seeded row id=%s; merging with osm_id=%s",
                existing.id,
                osm_id,
            )
            db.execute(
                text(
                    """
                    UPDATE stores SET
                        name          = :name,
                        retailer_id   = :retailer_id,
                        city          = :city,
                        lat           = :lat,
                        lng           = :lng,
                        phone         = :phone,
                        siret         = :siret,
                        osm_id        = :osm_id,
                        opening_hours = :opening_hours,
                        is_disabled   = false,
                        disabled_at   = NULL
                    WHERE id = :id
                    """
                ),
                {
                    "id": existing.id,
                    "name": store_data["name"],
                    "retailer_id": retailer_id,
                    "city": store_data.get("city"),
                    "lat": store_data["lat"],
                    "lng": store_data["lng"],
                    "phone": phone,
                    "siret": siret,
                    "osm_id": osm_id,
                    "opening_hours": store_data.get("opening_hours"),
                },
            )
            return
        # Composite collision with a *different* OSM row : skip. Both rows
        # claim the same physical address — usually a duplicate OSM node, or
        # the OSM id was renumbered. We don't have a safe automated merge
        # here (would silently overwrite the existing row's identity), so
        # surface it via WARNING and leave the data alone.
        _log.warning(
            "upsert_store: unique_store collision — osm_id=%s shares "
            "(retailer=%r, address=%r, postal_code=%r) with existing "
            "osm_id=%s; skipping new row",
            osm_id,
            retailer_tag,
            store_data.get("address"),
            store_data.get("postal_code"),
            existing.osm_id,
        )
        return

    params = {
        **store_data,
        "retailer_id": retailer_id,
        "retailer": None,
        "phone": phone,
        "siret": siret,
    }

    db.execute(
        text("""
            INSERT INTO stores (
                name, retailer, retailer_id, address, city, postal_code, lat, lng,
                phone, siret, osm_id, opening_hours, is_disabled
            ) VALUES (
                :name, :retailer, :retailer_id, :address, :city, :postal_code,
                :lat, :lng, :phone, :siret, :osm_id, :opening_hours, false
            )
            ON CONFLICT (osm_id) WHERE osm_id IS NOT NULL DO UPDATE SET
                name          = EXCLUDED.name,
                retailer_id   = EXCLUDED.retailer_id,
                address       = EXCLUDED.address,
                city          = EXCLUDED.city,
                postal_code   = EXCLUDED.postal_code,
                lat           = EXCLUDED.lat,
                lng           = EXCLUDED.lng,
                phone         = EXCLUDED.phone,
                siret         = EXCLUDED.siret,
                opening_hours = EXCLUDED.opening_hours,
                is_disabled   = false,
                disabled_at   = NULL
        """),
        params,
    )


def upsert_city(db: Session, postal_code: str, city_name: str, country_code: str) -> None:
    """Upsert (postal_code, city_name) into cities. Idempotent."""
    dept = postal_code[:2] if len(postal_code) >= 2 else None
    db.execute(
        text("""
            INSERT INTO cities (postal_code, city_name, department, country_code)
            VALUES (:postal_code, UPPER(:city_name), :department, :country_code)
            ON CONFLICT (postal_code, city_name) DO NOTHING
        """),
        {
            "postal_code": postal_code,
            "city_name": city_name.strip(),
            "department": dept,
            "country_code": country_code,
        },
    )
