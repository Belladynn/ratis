"""Search API source — async/httpx, modes delta/weekly/monthly/range.

Generic across the four Open*Facts projects (OFF / OBP / OPF / OPFF) — the
active project is encapsulated in the `Source` instance passed by the caller
(see `off_sync/sources.py`).

Parallelism: semaphore-limited coroutines, one per page.
DB upserts run in a thread pool (asyncio.to_thread) to avoid blocking the loop.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

import httpx
import tenacity
from ratis_core.database import make_engine
from sqlalchemy.orm import sessionmaker

from off_sync.extractor import API_FIELDS, extract_product
from off_sync.repository import upsert_products
from off_sync.sources import Source
from off_sync.stats import Stats

log = logging.getLogger(__name__)

PAGE_SIZE = 200


def _is_retryable(exc: BaseException) -> bool:
    """Retry on 429, 5xx, and transport errors (timeouts, connection failures)."""
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code == 429 or exc.response.status_code >= 500
    )


def _wait_retry_after(retry_state: tenacity.RetryCallState) -> float:
    """Honour the Retry-After header on 429; fall back to exponential backoff."""
    exc = retry_state.outcome.exception()
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        header = exc.response.headers.get("Retry-After", "")
        try:
            seconds = float(header)
            log.warning("%s API: 429 — Retry-After %ss", _CURRENT_SOURCE_NAME[0], seconds)
            return seconds
        except (ValueError, TypeError):
            pass
    return tenacity.wait_exponential(multiplier=1, min=2, max=30)(retry_state)


# Single-element list used as a mutable cell so the retry-after warning can
# log the active source name without changing the tenacity callable signature.
# Set at the start of each `run_api` call.
_CURRENT_SOURCE_NAME: list[str] = ["off"]


# Retry policy for transient API errors (429, 5xx).
_RETRY = tenacity.retry(
    wait=_wait_retry_after,
    stop=tenacity.stop_after_attempt(3),
    retry=tenacity.retry_if_exception(_is_retryable),
    before_sleep=tenacity.before_sleep_log(log, logging.WARNING),
    reraise=True,
)


def _upsert_page_sync(
    Session: sessionmaker,
    raw_products: list[dict],
    dry_run: bool,
    source: Source,
) -> tuple[int, int, int, int]:
    """Parse + upsert one page. Runs in a thread via asyncio.to_thread."""
    products = []
    invalid = 0
    for raw in raw_products:
        p = extract_product(raw, source=source)
        if p:
            products.append(p)
        else:
            invalid += 1

    with Session() as db:
        inserted, updated, skipped = upsert_products(db, products, source=source)
        if not dry_run:
            db.commit()

    return inserted, updated, skipped, invalid


async def run_api(
    db_url: str,
    since_ts: int,
    until_ts: int | None,
    workers: int,
    dry_run: bool,
    *,
    source: Source,
) -> Stats:
    """Fetch a delta from the Search API and upsert into products.

    Args:
        db_url: SQLAlchemy database URL.
        since_ts: Unix timestamp — sync products modified after this point.
        until_ts: Unix timestamp — upper bound (optional).
        workers: Max concurrent HTTP requests.
        dry_run: Fetch and validate without committing.
        source: Active `Source` registry entry — provides api_base_url +
            user_agent, and is forwarded to the upsert layer for source isolation.
    """
    if not source.api_base_url.startswith("https://"):
        raise ValueError(f"source.api_base_url must use HTTPS, got: {source.api_base_url!r}")
    _CURRENT_SOURCE_NAME[0] = source.name
    search_url = source.api_base_url.rstrip("/") + "/api/v2/search"

    engine = make_engine(db_url, pool_pre_ping=True)
    Session = sessionmaker(engine)
    stats = Stats()

    base_params: dict[str, Any] = {
        "countries_tags": "en:france",
        "last_modified_t_gt": since_ts,
        "fields": API_FIELDS,
        "page_size": PAGE_SIZE,
    }
    if until_ts is not None:
        base_params["last_modified_t_lt"] = until_ts

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": source.user_agent},
            timeout=30.0,
        ) as client:

            @_RETRY
            async def _fetch(page: int) -> dict:
                r = await client.get(
                    search_url,
                    params={**base_params, "page": page},
                    timeout=30,
                )
                r.raise_for_status()
                return r.json()

            # First request — get total count and first page
            data = await _fetch(1)
            total = data.get("count", 0)
            if total == 0:
                log.info("%s API: nothing to sync", source.name)
                return stats

            total_pages = math.ceil(total / PAGE_SIZE)
            log.info("%s API: %d products, %d pages", source.name, total, total_pages)

            # Process page 1 immediately
            i, u, s, inv = await asyncio.to_thread(
                _upsert_page_sync,
                Session,
                data.get("products", []),
                dry_run,
                source,
            )
            stats.add(i, u, s, inv)

            if total_pages == 1:
                return stats

            # Fetch remaining pages with bounded concurrency
            sem = asyncio.Semaphore(workers)

            async def _fetch_page(page: int) -> list[dict]:
                async with sem:
                    return (await _fetch(page)).get("products", [])

            tasks = [asyncio.create_task(_fetch_page(p)) for p in range(2, total_pages + 1)]
            try:
                for coro in asyncio.as_completed(tasks):
                    raw_page = await coro
                    i, u, s, inv = await asyncio.to_thread(
                        _upsert_page_sync,
                        Session,
                        raw_page,
                        dry_run,
                        source,
                    )
                    stats.add(i, u, s, inv)
            finally:
                # Critical: ensure no task is still in flight when the
                # `async with httpx.AsyncClient` context exits. Otherwise
                # in-flight coroutines call client.get() on a closed client
                # → "Cannot send a request, as the client has been closed".
                # Triggers : exception inside the loop above (unrecoverable
                # 5xx after retries, DB error in to_thread) OR cancellation
                # propagated from main.py's asyncio.wait_for(timeout=...).
                pending = [t for t in tasks if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

    finally:
        # engine.dispose() closes idle pool connections. Active connections held by
        # asyncio.to_thread workers are kept alive until returned — SQLAlchemy closes
        # them on return instead of recycling. On asyncio.wait_for timeout, those threads
        # may log a connection error after dispose; no data loss (commits already on wire).
        engine.dispose()

    log.info("%s API sync done: %s", source.name, stats)
    return stats
