"""Tests for sirene_sync.geocode — TDD (PR5).

Uses only the `db` fixture (session-level SA-2.0 with SAVEPOINT isolation)
from conftest.py.  All httpx calls are patched via unittest.mock.patch so no
real network I/O happens.

Coverage:
- test_geocode_cache_hit_skips_api
- test_geocode_cache_stale_calls_api
- test_geocode_api_fills_lat_lng
- test_geocode_low_score_leaves_none
- test_geocode_retry_on_429
- test_geocode_chunks_api_calls
- test_geocode_no_api_call_when_all_cached
- test_address_hash_stable
- test_geocode_low_score_upserts_null_coords
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from batch_shared.store_consolidation import CandidateStore
from ratis_core.models.sirene_geocode_cache import SireneGeocodeCache
from sirene_sync.geocode import _address_hash, geocode_candidates
from tenacity import wait_exponential

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _make_candidate(
    siret: str,
    address: str = "12 RUE DE LA PAIX",
    postal_code: str = "75001",
    city: str = "PARIS",
) -> CandidateStore:
    return CandidateStore(
        source="sirene",
        name="TEST STORE",
        address=address,
        city=city,
        postal_code=postal_code,
        lat=None,
        lng=None,
        siret=siret,
        osm_id=None,
        retailer_id=None,
    )


def _api_response_csv(rows: list[dict]) -> bytes:
    """Build a minimal Géoplateforme-style CSV response."""
    fieldnames = ["siret", "adresse", "postcode", "city", "result_lat", "result_lon", "result_score", "result_status"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def _mock_httpx_response(content: bytes, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.content = content
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


# ---------------------------------------------------------------------------
# _address_hash
# ---------------------------------------------------------------------------


def test_address_hash_stable():
    """Same inputs always produce the same hash regardless of casing/whitespace."""
    h1 = _address_hash("12 Rue de la Paix", "75001", "Paris")
    h2 = _address_hash("12 RUE DE LA PAIX", "75001", "PARIS")
    # Both normalized to upper + stripped → same hash.
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest = 64 chars


def test_address_hash_differs_on_address_change():
    h1 = _address_hash("12 RUE DE LA PAIX", "75001", "PARIS")
    h2 = _address_hash("13 RUE DE LA PAIX", "75001", "PARIS")
    assert h1 != h2


# ---------------------------------------------------------------------------
# Cache hit — no API call
# ---------------------------------------------------------------------------


def test_geocode_cache_hit_skips_api(db):
    """Cache row fresh → lat/lng from cache, zero httpx calls."""
    siret = "10000000000001"
    candidate = _make_candidate(siret)
    expected_hash = _address_hash(candidate.address, candidate.postal_code, candidate.city)

    # Insert a fresh cache row.
    cache_row = SireneGeocodeCache(
        siret=siret,
        address_hash=expected_hash,
        lat=Decimal("48.869380"),
        lng=Decimal("2.330513"),
        score=Decimal("0.92"),
        geocoded_at=datetime.now(UTC),
    )
    db.add(cache_row)
    db.flush()

    with patch("httpx.Client") as mock_client_cls:
        results = list(
            geocode_candidates(
                [candidate],
                db,
                geocode_url="https://data.geopf.fr/geocodage/search/csv",
                min_score=0.7,
                cache_ttl_days=90,
                chunk_size=5000,
            )
        )

    assert len(results) == 1
    assert results[0].lat == Decimal("48.869380")
    assert results[0].lng == Decimal("2.330513")
    mock_client_cls.assert_not_called()


def test_geocode_no_api_call_when_all_cached(db):
    """All candidates fresh in cache → zero httpx.Client instantiation."""
    candidates = []
    for i in range(3):
        siret = f"1000000000000{i + 1}"
        c = _make_candidate(siret)
        h = _address_hash(c.address, c.postal_code, c.city)
        cache_row = SireneGeocodeCache(
            siret=siret,
            address_hash=h,
            lat=Decimal("48.000000"),
            lng=Decimal("2.000000"),
            score=Decimal("0.90"),
            geocoded_at=datetime.now(UTC),
        )
        db.add(cache_row)
        candidates.append(c)
    db.flush()

    with patch("httpx.Client") as mock_client_cls:
        results = list(
            geocode_candidates(
                candidates,
                db,
                geocode_url="https://data.geopf.fr/geocodage/search/csv",
                min_score=0.7,
                cache_ttl_days=90,
                chunk_size=5000,
            )
        )

    assert len(results) == 3
    assert all(r.lat is not None for r in results)
    mock_client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Cache stale — API called, cache updated
# ---------------------------------------------------------------------------


def test_geocode_cache_stale_calls_api(db):
    """Stale cache (geocoded_at > ttl_days) → API called, cache updated."""
    siret = "20000000000001"
    candidate = _make_candidate(siret)
    expected_hash = _address_hash(candidate.address, candidate.postal_code, candidate.city)

    # Insert a stale cache row (100 days old > 90 days TTL).
    stale_at = datetime.now(UTC) - timedelta(days=100)
    cache_row = SireneGeocodeCache(
        siret=siret,
        address_hash=expected_hash,
        lat=Decimal("1.000000"),
        lng=Decimal("1.000000"),
        score=Decimal("0.80"),
        geocoded_at=stale_at,
    )
    db.add(cache_row)
    db.flush()

    api_resp = _api_response_csv(
        [
            {
                "siret": siret,
                "adresse": candidate.address,
                "postcode": candidate.postal_code,
                "city": candidate.city,
                "result_lat": "48.869380",
                "result_lon": "2.330513",
                "result_score": "0.92",
                "result_status": "ok",
            }
        ]
    )

    mock_resp = _mock_httpx_response(api_resp)
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    with patch("httpx.Client", return_value=mock_client):
        results = list(
            geocode_candidates(
                [candidate],
                db,
                geocode_url="https://data.geopf.fr/geocodage/search/csv",
                min_score=0.7,
                cache_ttl_days=90,
                chunk_size=5000,
            )
        )

    assert len(results) == 1
    assert results[0].lat == Decimal("48.869380")
    assert results[0].lng == Decimal("2.330513")
    mock_client.post.assert_called_once()

    # Cache must have been updated.
    db.expire_all()
    updated = db.get(SireneGeocodeCache, siret)
    assert updated is not None
    assert updated.lat == Decimal("48.869380")


# ---------------------------------------------------------------------------
# API fills lat/lng (cache miss)
# ---------------------------------------------------------------------------


def test_geocode_api_fills_lat_lng(db):
    """Cache miss → API called → candidate lat/lng filled, cache upserted."""
    siret = "30000000000001"
    candidate = _make_candidate(siret)

    api_resp = _api_response_csv(
        [
            {
                "siret": siret,
                "adresse": candidate.address,
                "postcode": candidate.postal_code,
                "city": candidate.city,
                "result_lat": "48.869380",
                "result_lon": "2.330513",
                "result_score": "0.92",
                "result_status": "ok",
            }
        ]
    )

    mock_resp = _mock_httpx_response(api_resp)
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    with patch("httpx.Client", return_value=mock_client):
        results = list(
            geocode_candidates(
                [candidate],
                db,
                geocode_url="https://data.geopf.fr/geocodage/search/csv",
                min_score=0.7,
                cache_ttl_days=90,
                chunk_size=5000,
            )
        )

    assert len(results) == 1
    assert results[0].lat == Decimal("48.869380")
    assert results[0].lng == Decimal("2.330513")
    mock_client.post.assert_called_once()

    # Cache must have been inserted.
    db.flush()
    cached = db.get(SireneGeocodeCache, siret)
    assert cached is not None
    assert cached.lat == Decimal("48.869380")
    assert cached.lng == Decimal("2.330513")


# ---------------------------------------------------------------------------
# Low score → lat/lng stays None
# ---------------------------------------------------------------------------


def test_geocode_low_score_leaves_none(db):
    """Score < min_score → candidate lat/lng remains None, cache NOT updated with coords."""
    siret = "40000000000001"
    candidate = _make_candidate(siret)

    api_resp = _api_response_csv(
        [
            {
                "siret": siret,
                "adresse": candidate.address,
                "postcode": candidate.postal_code,
                "city": candidate.city,
                "result_lat": "48.869380",
                "result_lon": "2.330513",
                "result_score": "0.45",
                "result_status": "ok",
            }
        ]
    )

    mock_resp = _mock_httpx_response(api_resp)
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    with patch("httpx.Client", return_value=mock_client):
        results = list(
            geocode_candidates(
                [candidate],
                db,
                geocode_url="https://data.geopf.fr/geocodage/search/csv",
                min_score=0.7,
                cache_ttl_days=90,
                chunk_size=5000,
            )
        )

    assert len(results) == 1
    assert results[0].lat is None
    assert results[0].lng is None

    # Cache should be inserted with NULL coords (to avoid retry next run).
    db.flush()
    cached = db.get(SireneGeocodeCache, siret)
    assert cached is not None
    assert cached.lat is None
    assert cached.lng is None


# ---------------------------------------------------------------------------
# Retry on 429
# ---------------------------------------------------------------------------


def test_geocode_retry_on_429(db):
    """Two 429 then 200 → tenacity retries → success on 3rd attempt."""
    siret = "50000000000001"
    candidate = _make_candidate(siret)

    api_resp = _api_response_csv(
        [
            {
                "siret": siret,
                "adresse": candidate.address,
                "postcode": candidate.postal_code,
                "city": candidate.city,
                "result_lat": "48.869380",
                "result_lon": "2.330513",
                "result_score": "0.92",
                "result_status": "ok",
            }
        ]
    )

    resp_429_1 = _mock_httpx_response(b"", status_code=429)
    resp_429_2 = _mock_httpx_response(b"", status_code=429)
    resp_200 = _mock_httpx_response(api_resp, status_code=200)

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.side_effect = [resp_429_1, resp_429_2, resp_200]

    with patch("httpx.Client", return_value=mock_client):
        # Patch _RETRY_WAIT to a zero-wait object so tests don't actually sleep.
        zero_wait = wait_exponential(multiplier=0, min=0, max=0)
        with patch("sirene_sync.geocode._RETRY_WAIT", zero_wait):
            results = list(
                geocode_candidates(
                    [candidate],
                    db,
                    geocode_url="https://data.geopf.fr/geocodage/search/csv",
                    min_score=0.7,
                    cache_ttl_days=90,
                    chunk_size=5000,
                )
            )

    assert len(results) == 1
    assert results[0].lat == Decimal("48.869380")
    assert mock_client.post.call_count == 3


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def test_geocode_chunks_api_calls(db):
    """12 candidates + chunk_size=5 → 3 API calls (5+5+2)."""
    candidates = [_make_candidate(f"6000000000{i:04d}") for i in range(12)]

    # Build API responses for each chunk.
    def make_chunk_response(chunk_candidates):
        rows = [
            {
                "siret": c.siret,
                "adresse": c.address,
                "postcode": c.postal_code,
                "city": c.city,
                "result_lat": "48.869380",
                "result_lon": "2.330513",
                "result_score": "0.92",
                "result_status": "ok",
            }
            for c in chunk_candidates
        ]
        return _mock_httpx_response(_api_response_csv(rows))

    chunks = [candidates[:5], candidates[5:10], candidates[10:]]
    side_effects = [make_chunk_response(chunk) for chunk in chunks]

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.side_effect = side_effects

    with patch("httpx.Client", return_value=mock_client):
        results = list(
            geocode_candidates(
                candidates,
                db,
                geocode_url="https://data.geopf.fr/geocodage/search/csv",
                min_score=0.7,
                cache_ttl_days=90,
                chunk_size=5,
            )
        )

    assert len(results) == 12
    assert mock_client.post.call_count == 3


# ---------------------------------------------------------------------------
# not-found status → lat/lng stays None
# ---------------------------------------------------------------------------


def test_geocode_not_found_status_leaves_none(db):
    """result_status='not-found' with score=0.0 → lat/lng stays None."""
    siret = "70000000000001"
    candidate = _make_candidate(siret)

    api_resp = _api_response_csv(
        [
            {
                "siret": siret,
                "adresse": candidate.address,
                "postcode": candidate.postal_code,
                "city": candidate.city,
                "result_lat": "",
                "result_lon": "",
                "result_score": "0.0",
                "result_status": "not-found",
            }
        ]
    )

    mock_resp = _mock_httpx_response(api_resp)
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    with patch("httpx.Client", return_value=mock_client):
        results = list(
            geocode_candidates(
                [candidate],
                db,
                geocode_url="https://data.geopf.fr/geocodage/search/csv",
                min_score=0.7,
                cache_ttl_days=90,
                chunk_size=5000,
            )
        )

    assert len(results) == 1
    assert results[0].lat is None
    assert results[0].lng is None
