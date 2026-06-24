"""SIRENE geocoding — bulk CSV via Géoplateforme + sirene_geocode_cache (PR5).

Geocodes ``CandidateStore`` rows (with lat=None/lng=None) by:
1. Looking up ``sirene_geocode_cache`` by siret + address_hash (TTL-based).
2. Sending cache-miss candidates to ``data.geopf.fr/geocodage/search/csv``
   in chunked CSV bulk requests.
3. Returning enriched ``CandidateStore`` objects (lat/lng filled when the
   Géoplateforme returns a score >= min_score).
4. Upserting results (including low-score/not-found NULL entries) into
   ``sirene_geocode_cache`` so subsequent runs avoid re-calling the API for
   the same unchanged addresses.

Caller is responsible for ``db.commit()`` — this module never commits (R-DB-02).

Design notes
------------
- **Synchronous** — consistent with ``download.py`` and ``parser.py``.
- **No db.commit()** — caller decides transaction boundaries (batch pipeline
  PR6 commits per chunk).
- **Decimal for lat/lng** — ``Decimal(str(float_value))`` avoids float drift,
  consistent with the ``stores.lat/lng`` ``Numeric(9,6)`` contract.
- **address_hash** — SHA-256 of the uppercased/stripped address+postcode+city
  string.  Detects SIRENE address changes and forces a re-geocode even when
  the siret is already cached.
- **Low-score / not-found rows** are still upserted into the cache with
  lat=lng=None so the next run does not retry a dead address within TTL.
- **Retry** — tenacity with exponential back-off on 429/5xx.  After 5
  exhausted attempts, logs ERROR and re-raises (pipeline aborts that chunk).

References
----------
- Plan: ``docs/superpowers/plans/2026-05-10-sirene-impl.md`` § PR5
- API: ``https://data.geopf.fr/geocodage/search/csv`` (BAN Géoplateforme)
  Former ``api-adresse.data.gouv.fr`` decommissioned 2026-01 — do NOT use.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from batch_shared.store_consolidation import CandidateStore
from ratis_core.models.sirene_geocode_cache import SireneGeocodeCache
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tenacity retry configuration — exported so tests can patch it if needed.
# ---------------------------------------------------------------------------

# Number of seconds used for the exponential wait; exposed for testing.
_RETRY_WAIT = wait_exponential(multiplier=1, min=2, max=30)


def _is_retryable(exc: BaseException) -> bool:
    """Return True for 429 / 5xx HTTP errors that should trigger a retry."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def _post_csv_with_retry(client: httpx.Client, url: str, files: dict, data: dict) -> httpx.Response:
    """POST the CSV to Géoplateforme with tenacity retry on 429/5xx.

    Defined as a plain function (not a decorator) so the retry policy can be
    called with the live ``_RETRY_WAIT`` module variable (patchable in tests).
    """

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=_RETRY_WAIT,
        before_sleep=before_sleep_log(_log, logging.WARNING),
        reraise=True,
    )
    def _do_post() -> httpx.Response:
        response = client.post(url, files=files, data=data, timeout=120)
        response.raise_for_status()
        return response

    try:
        return _do_post()
    except httpx.HTTPStatusError as exc:
        _log.error(
            "geocode: tenacity exhausted after 5 attempts — HTTP %d from Géoplateforme",
            exc.response.status_code,
        )
        try:
            import sentry_sdk  # type: ignore[import-untyped]

            sentry_sdk.capture_exception(exc)
        except ImportError:
            pass  # sentry-sdk not installed in this batch — silent no-op
        raise


# ---------------------------------------------------------------------------
# Address hash
# ---------------------------------------------------------------------------


def _address_hash(address: str | None, postal_code: str | None, city: str | None) -> str:
    """Return SHA-256 hex digest of the normalised address string.

    Normalisation: upper-case + strip whitespace on each component, joined by
    ``|``.  Stable across runs and case/whitespace variations in source data.
    """
    addr = (address or "").strip().upper()
    cp = (postal_code or "").strip().upper()
    cty = (city or "").strip().upper()
    raw = f"{addr}|{cp}|{cty}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Cache lookup helpers
# ---------------------------------------------------------------------------


def _lookup_cache(
    siret: str,
    address_hash: str,
    db: Session,
    cache_ttl_days: int,
) -> SireneGeocodeCache | None:
    """Return a fresh cache row for *siret* if one exists, else None.

    "Fresh" means: same ``address_hash`` AND ``geocoded_at`` within TTL.
    A stale row (expired TTL) or a row with a different hash (address changed)
    is treated as a cache miss — the caller will re-geocode.
    """
    row = db.get(SireneGeocodeCache, siret)
    if row is None:
        return None

    # Address changed → re-geocode even if TTL not expired.
    if row.address_hash != address_hash:
        return None

    cutoff = datetime.now(UTC) - timedelta(days=cache_ttl_days)
    geocoded_at = row.geocoded_at
    # Ensure timezone-aware comparison.
    if geocoded_at.tzinfo is None:
        geocoded_at = geocoded_at.replace(tzinfo=UTC)

    if geocoded_at < cutoff:
        return None  # stale

    return row


# ---------------------------------------------------------------------------
# Cache upsert
# ---------------------------------------------------------------------------


def _upsert_cache(
    db: Session,
    *,
    siret: str,
    address_hash: str,
    lat: Decimal | None,
    lng: Decimal | None,
    score: Decimal | None,
) -> None:
    """UPSERT into ``sirene_geocode_cache``.

    Uses PostgreSQL ``INSERT ... ON CONFLICT (siret) DO UPDATE`` to avoid race
    conditions.  No ``db.commit()`` — caller decides the transaction boundary.
    """
    stmt = (
        pg_insert(SireneGeocodeCache)
        .values(
            siret=siret,
            address_hash=address_hash,
            lat=lat,
            lng=lng,
            score=score,
            geocoded_at=func.now(),
        )
        .on_conflict_do_update(
            index_elements=["siret"],
            set_={
                "address_hash": address_hash,
                "lat": lat,
                "lng": lng,
                "score": score,
                "geocoded_at": func.now(),
            },
        )
    )
    db.execute(stmt)


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------


def _build_request_csv(chunk: list[CandidateStore]) -> bytes:
    """Build the CSV payload to POST to Géoplateforme.

    Columns: siret, adresse, postcode, city
    SIRENE addresses are already in upper-case; the API handles UTF-8 accents.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["siret", "adresse", "postcode", "city"])
    for c in chunk:
        writer.writerow(
            [
                c.siret or "",
                c.address or "",
                c.postal_code or "",
                c.city or "",
            ]
        )
    return buf.getvalue().encode("utf-8")


def _parse_response_csv(content: bytes) -> dict[str, dict]:
    """Parse Géoplateforme CSV response into a dict keyed by siret.

    Expected columns: siret, result_lat, result_lon, result_score, result_status.
    Missing or empty lat/lon → Decimal None equivalent is stored as None.
    """
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    result: dict[str, dict] = {}
    for row in reader:
        siret = row.get("siret", "").strip()
        if not siret:
            continue

        raw_lat = row.get("result_lat", "").strip()
        raw_lon = row.get("result_lon", "").strip()
        raw_score = row.get("result_score", "").strip()
        raw_status = row.get("result_status", "").strip()

        lat = Decimal(str(raw_lat)) if raw_lat else None
        lng = Decimal(str(raw_lon)) if raw_lon else None
        score = Decimal(str(raw_score)) if raw_score else None

        result[siret] = {
            "lat": lat,
            "lng": lng,
            "score": score,
            "status": raw_status,
        }
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def geocode_candidates(
    candidates: Iterable[CandidateStore],
    db: Session,
    *,
    geocode_url: str,
    min_score: float,
    cache_ttl_days: int,
    chunk_size: int = 5000,
) -> Iterator[CandidateStore]:
    """Yield ``CandidateStore`` enriched with lat/lng from Géoplateforme or cache.

    Flow per chunk:
    1. For each candidate in the chunk: look up ``sirene_geocode_cache`` by
       siret + address_hash.
       - Cache hit (fresh + same hash) → use cached coords, yield directly.
       - Cache miss or stale → queue for API geocoding.
    2. POST the to-geocode candidates as CSV to Géoplateforme
       ``{geocode_url}/search/csv``.
    3. Parse CSV response: result_lat, result_lon, result_score, result_status.
    4. score >= min_score → fill candidate lat/lng + UPSERT cache with coords.
    5. score < min_score OR status=='not-found' → lat/lng stays None;
       UPSERT cache with lat=lng=None (suppress retry within TTL).
    6. Yield all candidates from the chunk (with or without lat/lng).

    Caller must ``db.commit()`` at appropriate batch boundaries (R-DB-02).

    Parameters
    ----------
    candidates:
        Iterable of ``CandidateStore`` (lat=None, lng=None expected).
    db:
        Active SQLAlchemy session.
    geocode_url:
        Base URL of the Géoplateforme geocoding service, e.g.
        ``https://data.geopf.fr/geocodage``.
        The endpoint ``/search/csv`` is appended internally.
    min_score:
        Minimum Géoplateforme confidence score to accept coordinates.
        Candidates below this threshold are yielded with lat=lng=None.
    cache_ttl_days:
        Freshness window for ``sirene_geocode_cache``.  Rows older than this
        are re-geocoded even if the siret is already cached.
    chunk_size:
        Number of candidates per POST request to Géoplateforme.
    """
    api_endpoint = geocode_url.rstrip("/") + "/search/csv"
    candidates_list = list(candidates)

    # Process in chunks.
    for chunk_start in range(0, len(candidates_list), chunk_size):
        chunk = candidates_list[chunk_start : chunk_start + chunk_size]

        cache_hit: dict[str, SireneGeocodeCache] = {}  # siret → cache row
        to_geocode: list[CandidateStore] = []

        for candidate in chunk:
            if not candidate.siret:
                to_geocode.append(candidate)
                continue

            h = _address_hash(candidate.address, candidate.postal_code, candidate.city)
            row = _lookup_cache(candidate.siret, h, db, cache_ttl_days)
            if row is not None:
                cache_hit[candidate.siret] = row
            else:
                to_geocode.append(candidate)

        # Call API for cache-miss candidates.
        api_results: dict[str, dict] = {}
        if to_geocode:
            csv_bytes = _build_request_csv(to_geocode)
            with httpx.Client() as client:
                response = _post_csv_with_retry(
                    client,
                    api_endpoint,
                    files={"data": ("input.csv", csv_bytes, "text/csv; charset=utf-8")},
                    data={
                        "columns": ["adresse", "postcode", "city"],
                        "result_columns": ["result_lat", "result_lon", "result_score", "result_status"],
                    },
                )
            api_results = _parse_response_csv(response.content)

            # Upsert cache for all geocoded candidates.
            for candidate in to_geocode:
                siret = candidate.siret
                if not siret:
                    continue
                h = _address_hash(candidate.address, candidate.postal_code, candidate.city)
                geo = api_results.get(siret, {})
                score = geo.get("score")
                score_val = float(score) if score is not None else 0.0

                if score_val >= min_score:
                    lat = geo.get("lat")
                    lng = geo.get("lng")
                else:
                    # Low score or not-found: cache with NULL coords to suppress retry.
                    lat = None
                    lng = None
                    _log.warning(
                        "geocode: siret=%s score=%s < min_score=%s — skipping lat/lng",
                        siret,
                        score,
                        min_score,
                    )

                _upsert_cache(
                    db,
                    siret=siret,
                    address_hash=h,
                    lat=lat,
                    lng=lng,
                    score=score,
                )

        # Yield all candidates in original chunk order.
        for candidate in chunk:
            siret = candidate.siret
            if siret and siret in cache_hit:
                row = cache_hit[siret]
                yield _replace_lat_lng(candidate, row.lat, row.lng)
            elif siret and siret in {c.siret for c in to_geocode}:
                geo = api_results.get(siret, {})
                score = geo.get("score")
                score_val = float(score) if score is not None else 0.0
                if score_val >= min_score:
                    yield _replace_lat_lng(candidate, geo.get("lat"), geo.get("lng"))
                else:
                    yield candidate  # lat/lng stays None
            else:
                # No siret — yield unchanged.
                yield candidate


def _replace_lat_lng(
    candidate: CandidateStore,
    lat: Decimal | None,
    lng: Decimal | None,
) -> CandidateStore:
    """Return a new ``CandidateStore`` with updated lat/lng.

    ``CandidateStore`` is a frozen dataclass; we use ``dataclasses.replace``.
    """
    import dataclasses

    return dataclasses.replace(candidate, lat=lat, lng=lng)
