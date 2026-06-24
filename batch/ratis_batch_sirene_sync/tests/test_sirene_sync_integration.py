"""Integration tests for the SIRENE end-to-end pipeline (PR6).

These tests exercise the full pipeline from Parquet fixture through
normalize/geocode/upsert and assert on DB state.

Each test uses the `db` fixture (SA 2.0 + SAVEPOINT rollback from conftest.py).

Design note — coordinate uniqueness:
    Candidates in most tests are given distinct lat/lng so they never
    fuzzy-match each other through store_consolidation.find_match() (50 m
    radius). The step between consecutive lat values is 0.01 degrees (~1 km),
    safely above the 50 m dedup radius.

Fixture content (stock_etab_sample.parquet):
    - 30 active food rows (APE 47.11B/D/Z, etatAdministratif='A')
    - 10 closed food rows (etatAdministratif='F')
    - 10 non-food (47.51Z) -> filtered out by parser
    - 5 active with enseigne=None (fallback denomination)
    - 5 active with partial address
    - 40 extra food rows with various APEs
    Total food rows = 90, non-food = 10.
"""

from __future__ import annotations

import uuid
from datetime import UTC
from decimal import Decimal
from pathlib import Path

import pytest
from batch_shared.store_consolidation import CandidateStore, UpsertResult
from sirene_sync.upsert import UpsertStats, upsert_candidates
from sqlalchemy import text

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SAMPLE_PARQUET = FIXTURE_DIR / "stock_etab_sample.parquet"

# Base lat/lng for test candidates.
# Step of 0.001 degrees (~111 m) between candidates keeps them well outside
# the 50 m fuzzy dedup radius. The base keeps all coords within valid ranges.
_BASE_LAT = Decimal("48.000000")
_BASE_LNG = Decimal("2.000000")
_COORD_STEP = Decimal("0.001000")


def _lat(idx: int) -> Decimal:
    return _BASE_LAT + _COORD_STEP * idx


def _lng(idx: int) -> Decimal:
    return _BASE_LNG + _COORD_STEP * idx


def _make_candidate(
    idx: int,
    *,
    source: str = "sirene",
    is_disabled: bool = False,
    siret: str | None = None,
    retailer_id: uuid.UUID | None = None,
    name: str | None = None,
) -> CandidateStore:
    """Build a CandidateStore with a unique lat/lng per idx to avoid cross-candidate fuzzy matches.

    When is_disabled=True, disabled_at is set to a fixed UTC datetime so the
    DB CHECK constraint (disabled_at IS NOT NULL when is_disabled=True) is satisfied.
    """
    from datetime import datetime

    disabled_at = datetime(2024, 1, 1, tzinfo=UTC) if is_disabled else None
    return CandidateStore(
        source=source,
        name=name or f"Store {idx:04d}",
        address=f"{idx} RUE DE LA REPUBLIQUE",
        city="PARIS",
        postal_code="75001",
        lat=_lat(idx),
        lng=_lng(idx),
        siret=siret or f"{idx:014d}",
        osm_id=None,
        retailer_id=retailer_id,
        is_disabled=is_disabled,
        disabled_at=disabled_at,
    )


def _count_stores(db, *, source: str | None = None, is_disabled: bool | None = None) -> int:
    parts = ["SELECT COUNT(*) FROM stores WHERE 1=1"]
    params: dict = {}
    if source is not None:
        parts.append("AND source = :source")
        params["source"] = source
    if is_disabled is not None:
        parts.append("AND is_disabled = :is_disabled")
        params["is_disabled"] = is_disabled
    row = db.execute(text(" ".join(parts)), params).first()
    return row[0]


# ---------------------------------------------------------------------------
# test_sirene_sync_inserts_new_stores
# ---------------------------------------------------------------------------


def test_sirene_sync_inserts_new_stores(db):
    """30 active food candidates -> 30 stores source='sirene' inserted."""
    candidates = [_make_candidate(i) for i in range(1, 31)]

    stats = upsert_candidates(
        db,
        candidates,
        dedup_radius_m=50,
        fuzzy_threshold=0.40,
    )
    db.flush()

    assert stats.inserted == 30, f"Expected 30 inserted, got {stats}"
    assert stats.total == 30
    count = _count_stores(db, source="sirene")
    assert count == 30


# ---------------------------------------------------------------------------
# test_sirene_sync_marks_closed_disabled
# ---------------------------------------------------------------------------


def test_sirene_sync_marks_closed_disabled(db):
    """Closed candidates (is_disabled=True) are inserted with is_disabled=True."""
    # 20 active + 2 closed = 2/22 = 9% — below the 10% safety net threshold.
    candidates = [_make_candidate(i) for i in range(1, 21)]
    candidates += [
        _make_candidate(21, is_disabled=True),
        _make_candidate(22, is_disabled=True),
    ]

    stats = upsert_candidates(
        db,
        candidates,
        dedup_radius_m=50,
        fuzzy_threshold=0.40,
    )
    db.flush()

    assert stats.inserted == 22, f"Expected 22 inserted, got {stats}"
    disabled_count = _count_stores(db, is_disabled=True)
    assert disabled_count == 2


# ---------------------------------------------------------------------------
# test_sirene_sync_merges_with_osm
# ---------------------------------------------------------------------------


def test_sirene_sync_merges_with_osm(db):
    """SIRENE candidate matches existing OSM store by fuzzy geo -> merged."""
    # Pre-insert an OSM store.
    osm_store_id = db.execute(
        text(
            """
            INSERT INTO stores (name, source, lat, lng, address, city, postal_code,
                                osm_id, siret, is_disabled)
            VALUES ('Store 9999', 'osm', :lat, :lng,
                    '500 RUE DE LA REPUBLIQUE', 'PARIS', '75001',
                    999001, NULL, false)
            RETURNING id
            """
        ),
        {"lat": float(_lat(500)), "lng": float(_lng(500))},
    ).first()[0]
    db.flush()

    # SIRENE candidate at same location (within 50 m = identical coords), same name.
    sirene_candidate = CandidateStore(
        source="sirene",
        name="Store 9999",
        address="500 RUE DE LA REPUBLIQUE",
        city="PARIS",
        postal_code="75001",
        lat=_lat(500),
        lng=_lng(500),
        siret="99990000000001",
        osm_id=None,
        retailer_id=None,
        is_disabled=False,
    )

    stats = upsert_candidates(
        db,
        [sirene_candidate],
        dedup_radius_m=50,
        fuzzy_threshold=0.40,
    )
    db.flush()

    assert stats.merged == 1, f"Expected merged=1, got {stats}"

    row = db.execute(
        text("SELECT source, osm_id, siret FROM stores WHERE id = :id"),
        {"id": osm_store_id},
    ).first()
    assert row.source == "sirene", f"Expected source='sirene', got {row.source!r}"
    assert row.osm_id == 999001, "osm_id must be preserved after merge"
    assert row.siret == "99990000000001", "siret must be populated after merge"


# ---------------------------------------------------------------------------
# test_sirene_sync_preserves_admin
# ---------------------------------------------------------------------------


def test_sirene_sync_preserves_admin(db):
    """Admin store at same location -> source stays 'admin', siret backfilled only."""
    admin_store_id = db.execute(
        text(
            """
            INSERT INTO stores (name, source, lat, lng, address, city, postal_code,
                                osm_id, siret, is_disabled)
            VALUES ('Store 8888', 'admin', :lat, :lng,
                    '501 RUE DE LA REPUBLIQUE', 'PARIS', '75001',
                    NULL, NULL, false)
            RETURNING id
            """
        ),
        {"lat": float(_lat(501)), "lng": float(_lng(501))},
    ).first()[0]
    db.flush()

    sirene_candidate = CandidateStore(
        source="sirene",
        name="Store 8888",
        address="501 RUE DE LA REPUBLIQUE",
        city="PARIS",
        postal_code="75001",
        lat=_lat(501),
        lng=_lng(501),
        siret="88880000000001",
        osm_id=None,
        retailer_id=None,
        is_disabled=False,
    )

    stats = upsert_candidates(
        db,
        [sirene_candidate],
        dedup_radius_m=50,
        fuzzy_threshold=0.40,
    )
    db.flush()

    assert stats.preserved == 1, f"Expected preserved=1, got {stats}"

    row = db.execute(
        text("SELECT source, siret, name FROM stores WHERE id = :id"),
        {"id": admin_store_id},
    ).first()
    assert row.source == "admin", f"source must stay 'admin', got {row.source!r}"
    assert row.siret == "88880000000001", "siret must be backfilled on admin row"


# ---------------------------------------------------------------------------
# test_sirene_sync_idempotent
# ---------------------------------------------------------------------------


def test_sirene_sync_idempotent(db):
    """Running upsert twice with the same candidates: 2nd run produces 0 insertions."""
    candidates = [_make_candidate(i) for i in range(1, 6)]

    stats1 = upsert_candidates(db, candidates, dedup_radius_m=50, fuzzy_threshold=0.40)
    db.flush()
    assert stats1.inserted == 5

    # Run again with same candidates (same sirets -> SIRET match -> same-source update).
    candidates2 = [_make_candidate(i) for i in range(1, 6)]
    stats2 = upsert_candidates(db, candidates2, dedup_radius_m=50, fuzzy_threshold=0.40)
    db.flush()

    assert stats2.inserted == 0, f"2nd run must have 0 insertions, got {stats2.inserted}"
    assert stats2.updated == 5, f"2nd run must have 5 updated, got {stats2.updated}"
    assert _count_stores(db) == 5, "Total store count must stay at 5"


# ---------------------------------------------------------------------------
# test_dry_run_no_writes
# ---------------------------------------------------------------------------


def test_dry_run_no_writes(db):
    """dry_run=True: stats reported but no stores written to DB."""
    candidates = [_make_candidate(i) for i in range(1, 6)]

    stats = upsert_candidates(
        db,
        candidates,
        dedup_radius_m=50,
        fuzzy_threshold=0.40,
        dry_run=True,
    )
    db.flush()

    assert stats.inserted == 5
    assert stats.total == 5
    assert _count_stores(db) == 0, "dry_run must not write to DB"


# ---------------------------------------------------------------------------
# test_closure_safety_net_aborts
# ---------------------------------------------------------------------------


def test_closure_safety_net_aborts(db):
    """>10% closed candidates triggers ValueError, no rows written."""
    # 1 active + 5 closed = 5/6 = 83% -> must abort
    candidates = [_make_candidate(1)]
    candidates += [_make_candidate(i, is_disabled=True) for i in range(2, 7)]

    with pytest.raises(ValueError, match="closure safety net"):
        upsert_candidates(db, candidates, dedup_radius_m=50, fuzzy_threshold=0.40)

    db.flush()
    assert _count_stores(db) == 0, "No rows must be written when safety net aborts"


# ---------------------------------------------------------------------------
# test_sirene_sync_logs_conflicts
# ---------------------------------------------------------------------------


def test_sirene_sync_logs_conflicts(db):
    """Two SIRENE candidates at the same location with different SIRETs -> conflict after 1st merge."""
    # Pre-insert one OSM store.
    db.execute(
        text(
            """
            INSERT INTO stores (name, source, lat, lng, address, city, postal_code,
                                osm_id, siret, is_disabled)
            VALUES ('Carrefour Market', 'osm', :lat, :lng,
                    '502 RUE DE LA REPUBLIQUE', 'PARIS', '75001',
                    888001, NULL, false)
            """
        ),
        {"lat": float(_lat(502)), "lng": float(_lng(502))},
    )
    db.flush()

    # Two SIRENE candidates at the same location, same name, different SIRETs.
    c1 = CandidateStore(
        source="sirene",
        name="Carrefour Market",
        address="502 RUE DE LA REPUBLIQUE",
        city="PARIS",
        postal_code="75001",
        lat=_lat(502),
        lng=_lng(502),
        siret="77700000000001",
        osm_id=None,
        retailer_id=None,
        is_disabled=False,
    )
    c2 = CandidateStore(
        source="sirene",
        name="Carrefour Market",
        address="502 RUE DE LA REPUBLIQUE",
        city="PARIS",
        postal_code="75001",
        lat=_lat(502),
        lng=_lng(502),
        siret="77700000000002",
        osm_id=None,
        retailer_id=None,
        is_disabled=False,
    )

    stats = upsert_candidates(db, [c1, c2], dedup_radius_m=50, fuzzy_threshold=0.40)
    db.flush()

    # c1 merges into the OSM row (upgrades source to 'sirene', populates siret).
    # c2 finds the now-sirene row (siret=77700000000001) and conflicts because
    # both sirets differ (same source, conflicting natural keys).
    assert stats.merged == 1, f"Expected merged=1, got {stats}"
    assert stats.conflicts == 1, f"Expected conflicts=1, got {stats}"
    assert len(stats._conflict_messages) == 1


# ---------------------------------------------------------------------------
# test_upsert_stats_total
# ---------------------------------------------------------------------------


def test_upsert_stats_total():
    """UpsertStats.total sums all action counters."""
    s = UpsertStats()
    s.tally(UpsertResult(action="inserted", store_id=None))
    s.tally(UpsertResult(action="updated", store_id=None))
    s.tally(UpsertResult(action="merged", store_id=None))
    s.tally(UpsertResult(action="preserved", store_id=None))
    s.tally(UpsertResult(action="conflict", store_id=None))
    assert s.total == 5
    assert s.inserted == 1
    assert s.updated == 1
    assert s.merged == 1
    assert s.preserved == 1
    assert s.conflicts == 1


# ---------------------------------------------------------------------------
# test_pipeline_using_parquet_fixture
# ---------------------------------------------------------------------------


def test_pipeline_using_parquet_fixture(db):
    """Full pipeline using the fixture Parquet (with mocked geocode coordinates).

    Parser emits 90 food rows (30A + 10F + 50 extra) — non-food (47.51Z) filtered.
    10/90 = 11.1% closed exceeds the 10% safety net -> only active rows are upserted
    in the happy-path branch.  A separate test verifies the safety net itself.
    """
    assert SAMPLE_PARQUET.exists(), f"Fixture not found: {SAMPLE_PARQUET}"

    import dataclasses

    from sirene_sync.normalize import row_to_candidate
    from sirene_sync.parser import stream_etablissements

    settings = {
        "ape_whitelist": [
            "47.11A",
            "47.11B",
            "47.11C",
            "47.11D",
            "47.11E",
            "47.11F",
            "47.21Z",
            "47.22Z",
            "47.23Z",
            "47.24Z",
            "47.25Z",
            "47.29Z",
            "47.81Z",
        ],
        "batch_chunk_size": 5000,
        "holding_categories": [],
    }

    raw_rows = list(
        stream_etablissements(
            SAMPLE_PARQUET,
            ape_whitelist=settings["ape_whitelist"],
            chunk_size=settings["batch_chunk_size"],
            include_closed=True,
        )
    )

    # Normalise (lat=None at this stage, no geocode network call).
    normalized = [c for row in raw_rows for c in [row_to_candidate(row, db, settings=settings)] if c is not None]
    assert len(normalized) > 0, "Fixture must produce at least 1 candidate"

    closed_count = sum(1 for c in normalized if c.is_disabled)
    total_count = len(normalized)
    closed_ratio = closed_count / total_count

    # Assign unique coordinates per candidate (offset by index * 0.01 degrees)
    # so no two candidates fuzzy-match each other.
    geocoded = [
        dataclasses.replace(
            c,
            lat=Decimal("48.000000") + Decimal("0.010000") * i,
            lng=Decimal("2.000000") + Decimal("0.010000") * i,
        )
        for i, c in enumerate(normalized)
    ]

    if closed_ratio > 0.10:
        # Safety net would fire on the full set — test only the active subset.
        active = [c for c in geocoded if not c.is_disabled]
        stats = upsert_candidates(db, active, dedup_radius_m=50, fuzzy_threshold=0.40)
        db.flush()
        assert stats.inserted == len(active), (
            f"All active candidates must be inserted, got {stats.inserted}/{len(active)}"
        )
    else:
        stats = upsert_candidates(db, geocoded, dedup_radius_m=50, fuzzy_threshold=0.40)
        db.flush()
        assert stats.inserted == total_count, f"All candidates must be inserted, got {stats.inserted}/{total_count}"


def test_parquet_fixture_closed_ratio_triggers_safety_net(db):
    """The fixture has >10% closed food rows -> safety net fires when passed as-is."""
    assert SAMPLE_PARQUET.exists(), f"Fixture not found: {SAMPLE_PARQUET}"

    import dataclasses

    from sirene_sync.normalize import row_to_candidate
    from sirene_sync.parser import stream_etablissements

    settings = {
        "ape_whitelist": [
            "47.11A",
            "47.11B",
            "47.11C",
            "47.11D",
            "47.11E",
            "47.11F",
            "47.21Z",
            "47.22Z",
            "47.23Z",
            "47.24Z",
            "47.25Z",
            "47.29Z",
            "47.81Z",
        ],
        "batch_chunk_size": 5000,
        "holding_categories": [],
    }

    raw_rows = list(
        stream_etablissements(
            SAMPLE_PARQUET,
            ape_whitelist=settings["ape_whitelist"],
            chunk_size=settings["batch_chunk_size"],
            include_closed=True,
        )
    )

    normalized = [c for row in raw_rows for c in [row_to_candidate(row, db, settings=settings)] if c is not None]

    closed_count = sum(1 for c in normalized if c.is_disabled)
    total_count = len(normalized)

    # Assign unique coords so insert errors don't interfere with safety net check.
    geocoded = [
        dataclasses.replace(
            c,
            lat=Decimal("48.000000") + Decimal("0.010000") * i,
            lng=Decimal("2.000000") + Decimal("0.010000") * i,
        )
        for i, c in enumerate(normalized)
    ]

    if closed_count / total_count > 0.10:
        with pytest.raises(ValueError, match="closure safety net"):
            upsert_candidates(db, geocoded, dedup_radius_m=50, fuzzy_threshold=0.40)
    else:
        pytest.skip(
            f"Fixture closed ratio {closed_count}/{total_count} = "
            f"{closed_count / total_count:.1%} <= 10%, safety net not applicable."
        )
