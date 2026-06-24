"""Pure-function helpers for multi-source store ingestion.

Used by ``ratis_batch_sirene_sync`` (PR3-6) and ``ratis_batch_osm_sync``
(refactored in PR7) to consolidate stores rows across multiple sources of
truth :

- ``admin``         : human-curated, never overwritten by batches.
- ``sirene``        : INSEE SIRET registry (FR primary).
- ``overture``      : Overture Maps (international, V3 anticipation).
- ``osm``           : OpenStreetMap (international fallback for V1).
- ``user_suggested``: user-suggested via mobile flow.

Trust order (higher = wins) : admin > sirene > overture > osm > user_suggested.

API surface
-----------
- ``trust_priority(source)``    : pure-function lookup table.
- ``find_match(db, candidate)`` : DB lookup with 3-tier resolution
                                  (siret → osm_id → fuzzy).
- ``apply_upsert(db, candidate, *, conflict_log=None)``
                                : INSERT / UPDATE / merge / preserve / conflict.
                                  Caller is responsible for ``db.commit()``
                                  (R-DB-02).

Design notes
------------
- **Stateless** — no module-level state ; the ``stores`` table IS the state.
- **No db.commit()** — caller decides when to commit, so the helper composes
  cleanly inside a chunked batch transaction (cf. ``osm_sync.run_batch``).
- **Future-proof** — a new source (Overture, etc.) is a one-line addition to
  ``TrustPriority`` plus a focused test ; no other code change.
- **Conflict logging is injected** as a callable so the helper stays testable
  and the batch can plug structured logging without import dependencies.

References
----------
- Plan : ``docs/superpowers/plans/2026-05-10-sirene-impl.md`` § PR2.
- Audit : ``docs/audits/2026-05-10-deep-audit-sirene-foundation.md`` § F-10
  (cube/earthdistance prereq, installed in migration
  ``20260511_0900_pg_earthdistance``).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import IntEnum

from ratis_core.models.store import Store
from sqlalchemy import text
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)


# ============================================================
# Trust hierarchy
# ============================================================


class TrustPriority(IntEnum):
    """Higher = more trusted.

    Used to decide whether an incoming candidate overrides the existing row's
    source, and which fields the upsert is allowed to overwrite.
    """

    USER_SUGGESTED = 1
    OSM = 2
    OVERTURE = 3
    SIRENE = 4
    ADMIN = 5


_SOURCE_PRIORITY: dict[str, TrustPriority] = {
    "user_suggested": TrustPriority.USER_SUGGESTED,
    "osm": TrustPriority.OSM,
    "overture": TrustPriority.OVERTURE,
    "sirene": TrustPriority.SIRENE,
    "admin": TrustPriority.ADMIN,
}


def trust_priority(source: str) -> TrustPriority:
    """Look up the trust priority for a ``stores.source`` value.

    Raises ``ValueError`` for any value not in the closed enum, surfacing
    typos at the call site rather than silently treating them as zero.
    """
    try:
        return _SOURCE_PRIORITY[source]
    except KeyError as exc:
        raise ValueError(f"unknown source {source!r} — expected one of {sorted(_SOURCE_PRIORITY)}") from exc


# ============================================================
# Candidate input + upsert result
# ============================================================


@dataclass(frozen=True)
class CandidateStore:
    """Source-agnostic candidate row to upsert.

    Lat/lng are ``Decimal`` to match the ``stores.lat/lng`` ``Numeric(9,6)``
    contract — converting through ``float`` would silently lose precision.
    """

    source: str
    name: str
    address: str | None
    city: str | None
    postal_code: str | None
    lat: Decimal | None
    lng: Decimal | None
    siret: str | None
    osm_id: int | None
    retailer_id: uuid.UUID | None
    phone: str | None = None
    opening_hours: str | None = None
    is_disabled: bool = False
    disabled_at: datetime | None = None


@dataclass(frozen=True)
class UpsertResult:
    """Outcome of one ``apply_upsert()`` call.

    ``action`` :
        - ``inserted`` : new row created.
        - ``updated``  : in-place UPDATE, same source.
        - ``merged``   : in-place UPDATE that *upgraded* the source (e.g.
                         OSM row absorbed into SIRENE identity).
        - ``preserved``: candidate is lower-trust than existing — only NULL
                         identifier fields backfilled (e.g. SIRET filled on
                         an admin-curated row).
        - ``conflict`` : two same-source rows would collide on different
                         natural keys (e.g. two SIRETs claim the same
                         physical location). Nothing written.
    """

    action: str  # 'inserted' | 'updated' | 'merged' | 'preserved' | 'conflict'
    store_id: uuid.UUID | None
    reason: str | None = None


# ============================================================
# Tunables — names chosen so a future settings-driven override
# can swap them in without touching call sites.
# ============================================================

# Fuzzy radius for considering two rows as the same physical store.
# 50 m matches the spec's "30-50 m typical INSEE↔OSM jitter" finding.
DEFAULT_FUZZY_RADIUS_M = 50

# pg_trgm similarity floor for the name match in the fuzzy branch. 0.40 is
# permissive (e.g. "Carrefour Market Bastille" vs "Carrefour Bastille" still
# matches) but rejects unrelated names (e.g. "Carrefour" vs "Monoprix").
DEFAULT_FUZZY_NAME_THRESHOLD = 0.40


# ============================================================
# find_match
# ============================================================


def find_match(
    db: Session,
    candidate: CandidateStore,
    *,
    fuzzy_radius_m: int = DEFAULT_FUZZY_RADIUS_M,
    fuzzy_threshold: float = DEFAULT_FUZZY_NAME_THRESHOLD,
) -> Store | None:
    """Resolve ``candidate`` to an existing ``stores`` row, or ``None``.

    Resolution order :

    1. **Exact SIRET match** — if ``candidate.siret`` is non-empty, look up
       ``stores.siret = candidate.siret``. Wins over any other signal because
       SIRET is a national unique identifier.
    2. **Exact osm_id match** — same idea, using ``stores.osm_id`` (unique
       partial index).
    3. **Fuzzy geo + name + retailer** — within ``fuzzy_radius_m`` of
       ``(candidate.lat, candidate.lng)``, same ``retailer_id`` (if both
       sides have one), with ``similarity(stores.name_normalized,
       candidate.name) >= fuzzy_threshold``. Returns the closest such row.

    Returns ``None`` if nothing matches.

    Notes
    -----
    - Requires the ``cube`` + ``earthdistance`` extensions for the fuzzy
      branch (installed via migration ``20260511_0900_pg_earthdistance``).
    - The function is read-only — no flush, no commit.
    - Soft-deleted rows (``is_disabled = true``) are still considered for
      matching : a re-emerging SIRET should re-activate the existing row
      rather than create a duplicate.
    """
    # 1. SIRET takes precedence — national unique identifier.
    if candidate.siret:
        row = db.query(Store).filter(Store.siret == candidate.siret).first()
        if row is not None:
            return row

    # 2. osm_id is the OSM-side unique identifier.
    if candidate.osm_id is not None:
        row = db.query(Store).filter(Store.osm_id == candidate.osm_id).first()
        if row is not None:
            return row

    # 3. Fuzzy : geo radius + retailer + name trgm similarity.
    if candidate.lat is None or candidate.lng is None:
        return None

    # ``earth_distance(ll_to_earth(...))`` returns metres ; ``earth_box``
    # gives an index-friendly bounding box. We don't have a GiST index on
    # ``ll_to_earth(lat,lng)`` yet (cf audit § F-10 perf note), so the
    # bounding-box predicate is a plain filter at this stage. At <10 k FR
    # supermarkets a seq scan + filter is sub-second on dev hardware ;
    # if/when the table grows past ~100 k rows we'll add the GiST index in
    # a focused follow-up PR.
    #
    # The retailer filter is written as a fixed SQL predicate that
    # short-circuits when the candidate's retailer_id is NULL — keeping
    # one prepared statement instead of branching at the Python level
    # (avoids S608, plus the planner can reuse the plan).
    sql_fuzzy_match = text(
        """
        SELECT stores.id AS id,
               earth_distance(
                   ll_to_earth(stores.lat::float8, stores.lng::float8),
                   ll_to_earth(:lat, :lng)
               ) AS dist_m
        FROM stores
        WHERE earth_box(ll_to_earth(:lat, :lng), :radius_m)
              @> ll_to_earth(stores.lat::float8, stores.lng::float8)
          AND earth_distance(
                   ll_to_earth(stores.lat::float8, stores.lng::float8),
                   ll_to_earth(:lat, :lng)
              ) <= :radius_m
          AND similarity(stores.name_normalized, UPPER(immutable_unaccent(:name)))
              >= :threshold
          AND (
              CAST(:retailer_id AS uuid) IS NULL
              OR stores.retailer_id IS NULL
              OR stores.retailer_id = CAST(:retailer_id AS uuid)
          )
        ORDER BY dist_m ASC
        LIMIT 1
        """
    )
    row = db.execute(
        sql_fuzzy_match,
        {
            "lat": float(candidate.lat),
            "lng": float(candidate.lng),
            "name": candidate.name,
            "radius_m": fuzzy_radius_m,
            "threshold": fuzzy_threshold,
            "retailer_id": (str(candidate.retailer_id) if candidate.retailer_id is not None else None),
        },
    ).first()
    if row is None:
        return None
    return db.get(Store, row.id)


# ============================================================
# apply_upsert
# ============================================================


def apply_upsert(
    db: Session,
    candidate: CandidateStore,
    *,
    conflict_log: Callable[[str], None] | None = None,
    fuzzy_radius_m: int = DEFAULT_FUZZY_RADIUS_M,
    fuzzy_threshold: float = DEFAULT_FUZZY_NAME_THRESHOLD,
) -> UpsertResult:
    """Apply ``candidate`` to ``stores`` based on trust priority.

    Behaviour
    ---------
    - **No existing match** → INSERT new row, ``action='inserted'``.
    - **Existing row with identical natural key** (same SIRET / osm_id) and
      **same source** → in-place UPDATE of mutable fields,
      ``action='updated'``.
    - **Existing row, candidate strictly *higher* trust** (e.g. SIRENE over
      OSM) → in-place UPDATE that ALSO upgrades ``source``, while preserving
      the existing identifiers (``osm_id`` stays, new ``siret`` populated),
      ``action='merged'``.
    - **Existing row, candidate strictly *lower* trust** (e.g. OSM trying
      to overwrite an admin row) → only NULL identifier fields are
      backfilled (``siret``, ``osm_id``, ``phone``, ``opening_hours``).
      ``source`` and curated fields preserved, ``action='preserved'``.
    - **Same source, conflicting natural keys** (e.g. two distinct SIRETs
      on the same physical location) → ``conflict_log`` called with a
      diagnostic string, no write, ``action='conflict'``.

    Caller is responsible for ``db.commit()`` (R-DB-02). The helper issues a
    ``db.flush()`` after each write so the new ``id`` is available, but never
    commits.

    Parameters
    ----------
    db
        SQLAlchemy 2.0 session (caller-managed transaction).
    candidate
        Source-agnostic representation of the incoming row.
    conflict_log
        Optional callable invoked with one diagnostic string per conflict.
        Defaults to module-level WARNING log.
    fuzzy_radius_m, fuzzy_threshold
        Forwarded to ``find_match()``. Exposed so callers can tune per-source
        (SIRENE-vs-OSM jitter is typically tighter than user-suggested noise).
    """
    if conflict_log is None:
        conflict_log = _log.warning

    existing = find_match(
        db,
        candidate,
        fuzzy_radius_m=fuzzy_radius_m,
        fuzzy_threshold=fuzzy_threshold,
    )

    if existing is None:
        store_id = _insert(db, candidate)
        return UpsertResult(action="inserted", store_id=store_id)

    cand_p = trust_priority(candidate.source)
    exist_p = trust_priority(existing.source)

    # Same-source conflict : two different natural keys for the same physical
    # spot. Refuse to silently merge — surface for human review.
    if cand_p == exist_p:
        conflict = _detect_same_source_conflict(existing, candidate)
        if conflict is not None:
            conflict_log(
                f"store_consolidation : same-source conflict — "
                f"existing store_id={existing.id} ({existing.source}, "
                f"siret={existing.siret!r}, osm_id={existing.osm_id!r}) vs "
                f"candidate ({candidate.source}, siret={candidate.siret!r}, "
                f"osm_id={candidate.osm_id!r}) — {conflict}"
            )
            return UpsertResult(
                action="conflict",
                store_id=existing.id,
                reason=conflict,
            )
        # Same source, no conflict → in-place UPDATE of mutable fields.
        _update_in_place(db, existing, candidate)
        return UpsertResult(action="updated", store_id=existing.id)

    # Candidate strictly higher trust → upgrade the row.
    if cand_p > exist_p:
        _merge_upgrade(db, existing, candidate)
        return UpsertResult(action="merged", store_id=existing.id)

    # Candidate strictly lower trust → preserve existing, only backfill nulls.
    _preserve_backfill(db, existing, candidate)
    return UpsertResult(action="preserved", store_id=existing.id)


# ============================================================
# Internal writers — kept private so the public surface stays
# the four functions documented at module-top.
# ============================================================


_INSERT_SQL = text(
    """
    INSERT INTO stores (
        name, retailer_id, address, city, postal_code, lat, lng,
        phone, siret, osm_id, opening_hours, source,
        is_disabled, disabled_at
    ) VALUES (
        :name, :retailer_id, :address, :city, :postal_code, :lat, :lng,
        :phone, :siret, :osm_id, :opening_hours, :source,
        :is_disabled, :disabled_at
    )
    RETURNING id
    """
)


def _insert(db: Session, c: CandidateStore) -> uuid.UUID:
    row = db.execute(
        _INSERT_SQL,
        {
            "name": c.name,
            "retailer_id": c.retailer_id,
            "address": c.address,
            "city": c.city,
            "postal_code": c.postal_code,
            "lat": c.lat,
            "lng": c.lng,
            "phone": c.phone,
            "siret": c.siret,
            "osm_id": c.osm_id,
            "opening_hours": c.opening_hours,
            "source": c.source,
            "is_disabled": c.is_disabled,
            "disabled_at": c.disabled_at,
        },
    ).first()
    db.flush()
    return row.id


def _update_in_place(db: Session, existing: Store, c: CandidateStore) -> None:
    """Same-source UPDATE — overwrite mutable fields, keep source."""
    db.execute(
        text(
            """
            UPDATE stores SET
                name          = :name,
                retailer_id   = COALESCE(:retailer_id, retailer_id),
                address       = COALESCE(:address, address),
                city          = COALESCE(:city, city),
                postal_code   = COALESCE(:postal_code, postal_code),
                lat           = COALESCE(:lat, lat),
                lng           = COALESCE(:lng, lng),
                phone         = COALESCE(:phone, phone),
                siret         = COALESCE(:siret, siret),
                osm_id        = COALESCE(:osm_id, osm_id),
                opening_hours = COALESCE(:opening_hours, opening_hours),
                is_disabled   = :is_disabled,
                disabled_at   = :disabled_at
            WHERE id = :id
            """
        ),
        {
            "id": existing.id,
            "name": c.name,
            "retailer_id": c.retailer_id,
            "address": c.address,
            "city": c.city,
            "postal_code": c.postal_code,
            "lat": c.lat,
            "lng": c.lng,
            "phone": c.phone,
            "siret": c.siret,
            "osm_id": c.osm_id,
            "opening_hours": c.opening_hours,
            "is_disabled": c.is_disabled,
            "disabled_at": c.disabled_at,
        },
    )
    db.flush()


def _merge_upgrade(db: Session, existing: Store, c: CandidateStore) -> None:
    """Candidate has higher trust : UPDATE + upgrade ``source``.

    Curated identifiers from the lower-trust row (e.g. ``osm_id``) are NOT
    overwritten — we only fill in NULL slots — so the merged row keeps its
    cross-source identity. Mutable display fields (``name``, ``address``,
    ``city``, etc.) are taken from the candidate.
    """
    db.execute(
        text(
            """
            UPDATE stores SET
                source        = :source,
                name          = :name,
                retailer_id   = COALESCE(:retailer_id, retailer_id),
                address       = COALESCE(:address, address),
                city          = COALESCE(:city, city),
                postal_code   = COALESCE(:postal_code, postal_code),
                lat           = COALESCE(:lat, lat),
                lng           = COALESCE(:lng, lng),
                phone         = COALESCE(:phone, phone),
                siret         = COALESCE(siret, :siret),
                osm_id        = COALESCE(osm_id, :osm_id),
                opening_hours = COALESCE(:opening_hours, opening_hours),
                is_disabled   = :is_disabled,
                disabled_at   = :disabled_at
            WHERE id = :id
            """
        ),
        {
            "id": existing.id,
            "source": c.source,
            "name": c.name,
            "retailer_id": c.retailer_id,
            "address": c.address,
            "city": c.city,
            "postal_code": c.postal_code,
            "lat": c.lat,
            "lng": c.lng,
            "phone": c.phone,
            "siret": c.siret,
            "osm_id": c.osm_id,
            "opening_hours": c.opening_hours,
            "is_disabled": c.is_disabled,
            "disabled_at": c.disabled_at,
        },
    )
    db.flush()


def _preserve_backfill(db: Session, existing: Store, c: CandidateStore) -> None:
    """Candidate is lower-trust — only fill in NULL identifier slots.

    Curated fields (``name``, ``address``, ``city``, lat/lng) are preserved.
    Only auxiliary identifiers and contact info get backfilled when missing :
    ``siret``, ``osm_id``, ``phone``, ``opening_hours``. ``source`` and
    ``retailer_id`` stay untouched.
    """
    db.execute(
        text(
            """
            UPDATE stores SET
                siret         = COALESCE(siret, :siret),
                osm_id        = COALESCE(osm_id, :osm_id),
                phone         = COALESCE(phone, :phone),
                opening_hours = COALESCE(opening_hours, :opening_hours)
            WHERE id = :id
            """
        ),
        {
            "id": existing.id,
            "siret": c.siret,
            "osm_id": c.osm_id,
            "phone": c.phone,
            "opening_hours": c.opening_hours,
        },
    )
    db.flush()


def _detect_same_source_conflict(existing: Store, c: CandidateStore) -> str | None:
    """Return a human-readable reason string if same-source upsert is unsafe,
    or ``None`` if the rows agree on their natural key.

    Rationale : if two SIRENE candidates claim the same fuzzy location but
    declare different SIRETs, one of the two readings is wrong (or it is two
    distinct establishments sharing an address). Silently overwriting either
    would lose data ; surface for manual review instead.
    """
    if c.siret and existing.siret and c.siret != existing.siret:
        return f"siret mismatch (existing={existing.siret}, candidate={c.siret})"
    if c.osm_id is not None and existing.osm_id is not None and c.osm_id != existing.osm_id:
        return f"osm_id mismatch (existing={existing.osm_id}, candidate={c.osm_id})"
    return None
