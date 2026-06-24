"""Core ETL loop for the origins_tags backfill.

Strategy
--------
1. Page through ``products`` rows where ``origins_tags IS NULL`` —
   500 EANs per page (configurable). Two flavours kept open : restrict
   to ``source='off'`` only (the default — non-OFF rows like
   ``source='internal'`` never carry an OFF tag), or all sources via
   ``--all-sources`` for parity tests.
2. For each EAN, call the OFF single-product API ``/api/v2/product/{ean}``
   with ``fields=origins_tags`` to minimise payload size.
3. UPDATE the row with the fetched array. When OFF returns an empty
   array (product known to OFF but no origin metadata) we still write
   ``[]`` rather than NULL, so the row is excluded from future
   backfill passes — exactly like the off_sync nightly extract.
4. When OFF returns 404 (product unknown — possible for internal SKUs
   or stale EANs) we write ``[]`` for the same reason, plus an INFO
   log line so the operator can see how many EANs are stale.
5. Rate-limit between requests via a configurable sleep
   (``OFF_REQUEST_DELAY_SEC``, default ``1.0`` per the OFF community
   guideline ; settable via env for stable network or backoff in case
   of 429 storms).

The OFF Search API supports a multi-code lookup
(``cgi/search.pl?code=A|B|C``) but it (a) requires URL-length
budgeting on >100 EANs and (b) is less explicit on the per-EAN 404
path. The single-product API is slower but unambiguous — and a one-
shot backfill is rate-limited by OFF policy anyway, so the bottleneck
sits on sleep, not throughput.

Tests inject the HTTP fetcher (``fetch_origins_tags``) and the DB
session-factory ; the production wrapper wires the real httpx client +
SQLAlchemy engine in ``main.py``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import tenacity
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Constant : OFF "product not found" status — distinct from a network
# error. Used to decide whether to mark the row with [] (skipped from
# the next run) or leave it NULL (we'll retry next time).
_OFF_STATUS_NOT_FOUND = 0
_OFF_STATUS_FOUND = 1

# Default page size — number of EANs scanned per DB read. Tuned to keep
# the OFF call list manageable per session ; the actual API rate-limit
# is enforced via the inter-request sleep, not the page size.
DEFAULT_PAGE_SIZE = 500

# Default inter-request sleep (seconds). 1 req/s is the OFF community
# baseline. Overridable via env for ops escape hatches.
DEFAULT_REQUEST_DELAY_SEC = 1.0

# Logging cadence — every N rows processed (any outcome).
_LOG_PROGRESS_EVERY = 1000

# HTTP timeout (single product call). 30s mirrors off_sync API timeout.
DEFAULT_HTTP_TIMEOUT_SEC = 30.0

# Default User-Agent — must identify the caller per OFF policy.
DEFAULT_USER_AGENT = "Ratis-origins-backfill/1.0 (contact: hike.muskox5137@eagereverest.com)"


# ---------------------------------------------------------------------------
# Static SELECT variants for _select_eans_page.
#
# Pre-compiled into ``sqlalchemy.text`` clauses so every code path inside
# the runner ships exclusively bound parameters — no dynamic SQL
# composition, no S608/B608 lint noise, no SQL-injection surface.
# ---------------------------------------------------------------------------
_SELECT_SQL_OFF = text(
    "SELECT ean FROM products WHERE origins_tags IS NULL AND source = 'off' ORDER BY ean LIMIT :page_size"
)
_SELECT_SQL_ALL = text("SELECT ean FROM products WHERE origins_tags IS NULL ORDER BY ean LIMIT :page_size")
_SELECT_SQL_OFF_EXCLUDE = text(
    "SELECT ean FROM products "
    "WHERE origins_tags IS NULL AND source = 'off' "
    "  AND ean <> ALL(:excluded) "
    "ORDER BY ean LIMIT :page_size"
)
_SELECT_SQL_ALL_EXCLUDE = text(
    "SELECT ean FROM products WHERE origins_tags IS NULL   AND ean <> ALL(:excluded) ORDER BY ean LIMIT :page_size"
)


@dataclass
class BackfillStats:
    """Aggregated outcome over one backfill run."""

    scanned: int = 0
    updated: int = 0
    not_found: int = 0  # OFF 404 / status=0
    empty_origins: int = 0  # found but origins_tags absent/empty
    errors: int = 0
    # Per-page timing for observability — kept in-memory only ;
    # the entrypoint logs aggregates.
    elapsed_seconds: float = 0.0
    pages_processed: int = 0
    # Mark whether ``[]`` was treated as a write (we DO write it to
    # exclude the row from the next pass). Stats accounting only.
    short_circuited_on_existing: int = field(default=0)

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "updated": self.updated,
            "not_found": self.not_found,
            "empty_origins": self.empty_origins,
            "errors": self.errors,
            "pages_processed": self.pages_processed,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }


def _is_retryable(exc: BaseException) -> bool:
    """Retry on 429, 5xx and transport errors (mirrors off_sync.api)."""
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code == 429 or exc.response.status_code >= 500
    )


_RETRY = tenacity.retry(
    wait=tenacity.wait_exponential(multiplier=1, min=2, max=30),
    stop=tenacity.stop_after_attempt(3),
    retry=tenacity.retry_if_exception(_is_retryable),
    before_sleep=tenacity.before_sleep_log(log, logging.WARNING),
    reraise=True,
)


@_RETRY
def _http_get(client: httpx.Client, url: str) -> httpx.Response:
    r = client.get(url, timeout=DEFAULT_HTTP_TIMEOUT_SEC)
    r.raise_for_status()
    return r


def fetch_origins_tags(
    client: httpx.Client,
    api_base_url: str,
    ean: str,
) -> tuple[str, list[str] | None]:
    """Fetch a product's ``origins_tags`` from the OFF API.

    Returns a tuple ``(outcome, origins_tags)`` :
      * ``outcome='found'``     — product exists ; second value is the
                                  array (possibly empty if OFF has no
                                  origin metadata for this EAN).
      * ``outcome='not_found'`` — OFF returned ``status=0`` ; second
                                  value is ``None``.

    Network / 5xx / 429 errors raise ``httpx.HTTPError`` (the caller
    accumulates an error count instead of crashing the whole run).
    """
    url = f"{api_base_url.rstrip('/')}/api/v2/product/{ean}?fields=origins_tags"
    r = _http_get(client, url)
    payload = r.json()
    status = payload.get("status", _OFF_STATUS_NOT_FOUND)
    if status == _OFF_STATUS_FOUND:
        product = payload.get("product") or {}
        tags = product.get("origins_tags") or []
        # Defensive : OFF *could* return non-strings ; mirror off_sync
        # extractor's ``_sanitize_tags`` semantics by filtering.
        tags = [t for t in tags if isinstance(t, str)]
        return ("found", tags)
    return ("not_found", None)


def _select_eans_page(
    db: Session,
    *,
    page_size: int,
    only_off_source: bool,
    excluded_eans: set[str] | None = None,
) -> list[str]:
    """Return up to ``page_size`` EANs needing backfill.

    Each call returns a fresh page — there's no offset because the
    UPDATE side of the loop removes processed rows from the result of
    the next ``IS NULL`` query. Idempotent by construction.

    When ``only_off_source=True`` (default) we restrict to
    ``source='off'`` : non-OFF rows (``internal``, ``opf``, ``opff``,
    ``obp``) won't have a matching OFF entry anyway, and skipping them
    spares wasted API calls + 404s.

    ``excluded_eans`` carries the per-run set of EANs that have already
    been attempted and failed (network error). They stay ``origins_tags
    IS NULL`` in the DB (eligible for the next run) but the current run
    must not loop on them forever — the exclusion list is the cheap,
    runner-scoped way to skip them without polluting the DB schema.
    """
    # Each variant is a precomputed static SQL literal — every value
    # comes in via bound parameters (page_size, excluded). No dynamic
    # composition, so neither Ruff S608 nor Bandit B608 fire on the
    # final ``db.execute(text(sql), params)`` call site.
    params: dict[str, object] = {"page_size": page_size}
    if excluded_eans:
        params["excluded"] = list(excluded_eans)
        sql = _SELECT_SQL_OFF_EXCLUDE if only_off_source else _SELECT_SQL_ALL_EXCLUDE
    else:
        sql = _SELECT_SQL_OFF if only_off_source else _SELECT_SQL_ALL
    rows = db.execute(sql, params).all()
    return [row.ean for row in rows]


def _update_origins_tags(db: Session, ean: str, tags: list[str]) -> None:
    """Persist the fetched array for a single EAN.

    Always writes — even ``[]`` is written so the row is excluded from
    the next ``origins_tags IS NULL`` page. Commit is deferred to the
    caller (one commit per page) — keeps the SAVEPOINT-isolated test
    fixtures happy while staying resumable in prod (a crashed page
    loses at most ``page_size`` rows, all re-eligible on the next run
    via ``IS NULL``).
    """
    db.execute(
        text("UPDATE products SET origins_tags = :tags WHERE ean = :ean"),
        {"tags": tags, "ean": ean},
    )


# Type alias for the injectable HTTP fetcher (tests pass a fake).
FetchOriginsFn = Callable[[str], tuple[str, list[str] | None]]


def run_backfill(
    session_factory: Callable[[], Session],
    fetch_origins: FetchOriginsFn,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    request_delay_sec: float = DEFAULT_REQUEST_DELAY_SEC,
    only_off_source: bool = True,
    max_eans: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> BackfillStats:
    """Run the backfill loop.

    Args :
        session_factory : callable returning a fresh ``Session`` — one
            new session is opened per page to keep transactions short
            and free pool slots between OFF API calls.
        fetch_origins : ``(ean) -> (outcome, origins_tags|None)``. The
            production wrapper binds this to ``fetch_origins_tags`` +
            an httpx.Client; tests inject a stub.
        page_size : EANs read per DB select. Larger = fewer DB reads,
            same number of API calls.
        request_delay_sec : sleep between OFF API calls (rate-limit).
        only_off_source : restrict to ``source='off'`` rows (default
            True — skip internal / OBP / OPF / OPFF EANs).
        max_eans : abort once this many EANs have been scanned (None =
            no cap). Useful for smoke runs.
        sleep : injectable sleep (tests pass a no-op).

    Returns :
        ``BackfillStats`` aggregating the run.
    """
    stats = BackfillStats()
    start = time.monotonic()
    # Per-run exclusion set : EANs that errored during this invocation.
    # Their rows stay NULL in the DB (re-eligible on the next run),
    # but we don't re-select them in this run's subsequent pages —
    # avoids an infinite loop when the same EAN keeps raising.
    errored_eans: set[str] = set()

    # One session per page : reads the unfilled EANs and writes the
    # results back, committing per-row so the run is resumable. A new
    # session is opened for the next page (releases the connection to
    # the pool between OFF API calls — keeps prod-fleet idle slots
    # plentiful, and avoids re-entering ``begin_nested`` under test
    # fixtures that wrap the session in a SAVEPOINT).
    while True:
        if max_eans is not None and stats.scanned >= max_eans:
            log.info("origins_backfill: reached max_eans=%d — stopping", max_eans)
            break

        with session_factory() as db:
            eans = _select_eans_page(
                db,
                page_size=page_size,
                only_off_source=only_off_source,
                excluded_eans=errored_eans,
            )
            if not eans:
                log.info("origins_backfill: no more EANs to process — done")
                break

            stats.pages_processed += 1
            log.info(
                "origins_backfill: page %d — %d EANs (scanned so far: %d)",
                stats.pages_processed,
                len(eans),
                stats.scanned,
            )

            for ean in eans:
                if max_eans is not None and stats.scanned >= max_eans:
                    break

                stats.scanned += 1
                try:
                    outcome, tags = fetch_origins(ean)
                except Exception as exc:
                    stats.errors += 1
                    errored_eans.add(ean)
                    log.warning(
                        "origins_backfill: fetch failed for ean=%s — leaving NULL (%s)",
                        ean,
                        type(exc).__name__,
                    )
                    # Rate-limit even on error — back-pressure for the OFF
                    # API in case the failure is a 5xx surge.
                    if request_delay_sec > 0:
                        sleep(request_delay_sec)
                    continue

                if outcome == "not_found":
                    stats.not_found += 1
                    # Write [] so the EAN drops out of the next page.
                    # Sentinel value : same shape off_sync writes for
                    # OFF products with no origin metadata. Idempotent.
                    _update_origins_tags(db, ean, [])
                else:
                    assert tags is not None  # outcome='found' guarantees it
                    if not tags:
                        stats.empty_origins += 1
                    else:
                        stats.updated += 1
                    _update_origins_tags(db, ean, tags)

                # Progress log — emitted after every commit so the
                # operator sees movement even on a slow link.
                if stats.scanned % _LOG_PROGRESS_EVERY == 0:
                    log.info(
                        "origins_backfill: progress — scanned=%d updated=%d not_found=%d empty=%d errors=%d",
                        stats.scanned,
                        stats.updated,
                        stats.not_found,
                        stats.empty_origins,
                        stats.errors,
                    )

                if request_delay_sec > 0:
                    sleep(request_delay_sec)

            # Commit the whole page in one transaction — keeps the
            # SAVEPOINT-bound test fixture happy and minimises commit
            # overhead in prod. On crash mid-page, the unfilled rows
            # stay NULL → eligible on the next run (IS NULL filter).
            db.commit()

    stats.elapsed_seconds = time.monotonic() - start
    return stats
