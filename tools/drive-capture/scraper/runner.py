"""Drive-capture scraper runner.

Architecture
============
The runner is the glue between three independent layers:

1. **Queue** (``scraper.job_queue``) — SQLite-backed work list.  Jobs are
   claimed atomically via ``next_job`` (``BEGIN IMMEDIATE``), executed, then
   either ``mark_done`` or ``mark_error``.

2. **HTTP** (``scraper.http_client``) — stdlib-only fetcher that returns a
   ``FetchResult``.  A proxy flag is passed for enseignes that require it
   (currently only Leclerc which has CAPTCHA protection).

3. **Parsers** (``scraper.parsers.*``) — pure-transformation functions that
   convert raw JSON/HTML into a ``ParsedResult`` (stores, products, pagination,
   fiche jobs).  The dispatch table ``_DISPATCH`` maps ``(enseigne, phase)``
   tuples to the appropriate parser callable.

After parsing the runner:

* **Stores phase** — upserts stores in the DB and enqueues rayon jobs.
* **Rayons phase** — inserts price observations, enqueues fiche jobs, and
  enqueues pagination continuations when ``parsed.next_url`` is set.
* **Fiches phase** — inserts price observations (same path as rayons for DB
  persistence).

All exceptions in parsing and persistence are caught at ``run_one`` level so
the loop never crashes; the offending job is marked as error and execution
continues with the next job.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import urllib.parse
from datetime import datetime

# Parser modules loaded via relative import from the parser package at root of
# drive-capture (same SQLite database, different sub-package).
from parser.db import connect, init_schema, insert_observations, update_ean, upsert_stores
from parser.model import ParsedProduct, ParsedStore

from scraper.http_client import FetchResult, fetch
from scraper.job_queue import (
    enqueue,
    enqueue_fiche,
    enqueue_infomagasin,
    enqueue_rayons,
    enqueue_select_store,
    enqueue_store_sweep,
    init_queue,
    mark_done,
    next_job,
    queue_stats,
    retry_or_fail,
)
from scraper.parsers._models import ParsedResult, ProductResult, StoreResult
from scraper.parsers.auchan import parse_fiche as parse_auchan_fiche
from scraper.parsers.auchan import parse_rayon as parse_auchan_rayon
from scraper.parsers.auchan import parse_stores as parse_auchan_stores
from scraper.parsers.carrefour import parse_rayon as parse_carrefour_rayon
from scraper.parsers.carrefour import parse_stores as parse_carrefour_stores
from scraper.parsers.itm import parse_fiche as parse_itm_fiche
from scraper.parsers.itm import parse_rayon as parse_itm_rayon
from scraper.parsers.itm import parse_stores as parse_itm_stores
from scraper.parsers.leclerc import parse_fiche as parse_leclerc_fiche
from scraper.parsers.leclerc import parse_infomagasin as parse_leclerc_infomagasin
from scraper.parsers.leclerc import parse_rayon as parse_leclerc_rayon
from scraper.parsers.leclerc import parse_stores as parse_leclerc_stores
from scraper.parsers.monoprix import parse_rayon as parse_monoprix_rayon
from scraper.parsers.monoprix import parse_stores as parse_monoprix_stores
from scraper.parsers.systeme_u import parse_rayon as parse_sysu_rayon
from scraper.parsers.systeme_u import parse_stores as parse_sysu_stores

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dispatch table — (enseigne, phase) → callable
# ---------------------------------------------------------------------------
# Each callable signature:
#   stores phase: fn(body_json_or_text) -> ParsedResult
#   rayons phase: fn(body_json_or_text) -> ParsedResult
#   fiches phase: fn(body_json_or_text, **kwargs) -> ParsedResult
#
# Leclerc stores is special: expects a list, returns empty result otherwise.

def _parse_leclerc_stores_safe(body_json: object) -> ParsedResult:
    """Guard: only call parse_leclerc_stores when response is a list."""
    if isinstance(body_json, list):
        return parse_leclerc_stores(body_json)
    logger.warning("leclerc/stores: expected list body, got %s — skipping", type(body_json).__name__)
    return ParsedResult()


# The dispatch table maps (enseigne, phase) → a callable that takes the raw
# body (JSON dict/list or text) plus optional kwargs forwarded by run_one.
_DISPATCH: dict[tuple[str, str], object] = {
    ("itm",       "stores"): lambda body, **_: parse_itm_stores(body),
    ("itm",       "rayons"): lambda body, **_: parse_itm_rayon(body),
    ("itm",       "fiches"): lambda body, store_id=None, **_: parse_itm_fiche(body, store_id=store_id),
    ("carrefour", "stores"): lambda body, **_: parse_carrefour_stores(body),
    ("carrefour", "rayons"): lambda body, **_: parse_carrefour_rayon(body),
    ("auchan",    "stores"): lambda body, **_: parse_auchan_stores(body),
    ("auchan",    "rayons"): lambda body, **_: parse_auchan_rayon(body),
    ("auchan",    "fiches"): lambda body, **_: parse_auchan_fiche(body),
    ("systeme_u", "stores"): lambda body, **_: parse_sysu_stores(body),
    ("systeme_u", "rayons"): lambda body, **_: parse_sysu_rayon(body),
    ("leclerc",   "stores"): lambda body, **_: _parse_leclerc_stores_safe(body),
    ("leclerc",   "infomagasin"): lambda body, **_: parse_leclerc_infomagasin(body),
    ("leclerc",   "rayons"): lambda body, **_: parse_leclerc_rayon(body),
    ("leclerc",   "fiches"): lambda body, **_: parse_leclerc_fiche(body),
    ("monoprix",  "stores"): lambda body, **_: parse_monoprix_stores(body),
    ("monoprix",  "rayons"): lambda body, **_: parse_monoprix_rayon(body),
}

# All enseignes go through scrape.do — verified blocking on direct requests.
# ITM is excluded: blocked even via scrape.do (render=true 502, API 403).
_PROXY_ENSEIGNES: frozenset[str] = frozenset({
    "carrefour", "auchan", "systeme_u", "leclerc", "monoprix",
})
# Carrefour stores (/geoloc) passent en direct : scrape.do strip les headers custom
# (X-Requested-With requis par /geoloc). Rayons (/r/*) passent par le proxy.
_NO_PROXY_PHASES: dict[str, frozenset[str]] = {
    "carrefour": frozenset({"stores"}),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _product_to_parsed(
    product: ProductResult,
    job: dict,
    captured_at: str,
) -> ParsedProduct:
    """Convert a ``ProductResult`` (scraper-side) to ``ParsedProduct`` (DB-side).

    ``promo_pct`` is computed from ``price_cents`` and ``promo_price_cents``
    when both values are available; otherwise it is left as ``None``.
    """
    price_cents = product.price_cents
    promo_price_cents = product.promo_price_cents

    promo_pct: int | None = None
    if price_cents and promo_price_cents and price_cents > 0:
        promo_pct = round((price_cents - promo_price_cents) / price_cents * 100)

    return ParsedProduct(
        enseigne=job["enseigne"],
        name=product.name,
        captured_at=captured_at,
        store_ref=job.get("store_id") or None,
        ean=product.ean,
        brand=product.brand,
        quantity=product.quantity,
        category=product.category,
        price_cents=price_cents,
        price_per_measure_cents=None,   # not available from ProductResult
        measure_unit=None,              # not available from ProductResult
        promo_price_cents=promo_price_cents,
        promo_pct=promo_pct,
        is_promo=product.is_promo if product.is_promo is not None else False,
        product_url=product.product_url,
        image_url=product.image_url,
        available=None,                 # not available from ProductResult
        enseigne_product_id=product.internal_id,
    )


def _store_to_parsed(store: StoreResult, enseigne: str) -> ParsedStore:
    """Convert a ``StoreResult`` (scraper-side) to ``ParsedStore`` (DB-side)."""
    return ParsedStore(
        enseigne=enseigne,
        store_ref=store.store_id,
        name=store.name,
        city=store.city,
        postal_code=store.postal_code,
        lat=store.lat,
        lng=store.lng,
    )


def _resolve_next_url(current_url: str, next_url: str) -> str:
    """Resolve a next-page sentinel or relative URL to an absolute URL.

    Handles three cases:
    * Full URL (starts with http) → returned as-is.
    * ``?pageToken={token}`` sentinel (Monoprix) → replaces ``pageToken``
      query parameter in the current URL.
    * Any other relative/partial string → appended as-is (caller's
      responsibility to build a valid URL).
    """
    if next_url.startswith("http"):
        return next_url

    # Monoprix sentinel: "?pageToken=<token>" — swap the parameter in place
    if next_url.startswith("?pageToken="):
        import urllib.parse

        token = next_url.split("?pageToken=", 1)[1]
        parsed = urllib.parse.urlparse(current_url)
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        qs["pageToken"] = [token]
        new_query = urllib.parse.urlencode(qs, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=new_query))

    # Carrefour sentinel: "?page=next" — increment page query param
    if next_url == "?page=next":
        import urllib.parse

        parsed = urllib.parse.urlparse(current_url)
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        current_page = int(qs.get("page", ["1"])[0])
        qs["page"] = [str(current_page + 1)]
        new_query = urllib.parse.urlencode(qs, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=new_query))

    # Fallback: treat as a full replacement URL (shouldn't normally occur)
    logger.warning("_resolve_next_url: unrecognised next_url sentinel %r", next_url)
    return next_url


# ---------------------------------------------------------------------------
# Auchan pagination helper
# ---------------------------------------------------------------------------

def _auchan_next_page_url(current_url: str, n_products: int) -> str | None:
    """Return the next ?page=N URL for Auchan category pagination.

    Auchan category pages paginate via ``?page=N`` on the base category URL
    (e.g. ``/oeufs-produits-laitiers/ca-n01?page=2``).  The old
    ``/search-infinite`` endpoint is dead (404).

    Returns None when there are no products (pagination complete).
    """
    if n_products == 0:
        return None

    import urllib.parse

    parsed = urllib.parse.urlparse(current_url)
    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    current_page = int(qs.get("page", ["1"])[0])
    qs["page"] = [str(current_page + 1)]
    new_query = urllib.parse.urlencode(qs, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


# ---------------------------------------------------------------------------
# Core job-processing function
# ---------------------------------------------------------------------------

def run_one(
    queue_conn: sqlite3.Connection,
    db_conn: sqlite3.Connection,
    *,
    enseigne: str | None = None,
    phase: str | None = None,
) -> bool:
    """Claim and execute one pending job.

    Returns ``True`` if a job was processed (regardless of success/failure),
    ``False`` if the queue was empty.
    """
    job = next_job(queue_conn, enseigne=enseigne, phase=phase)
    if job is None:
        return False

    job_id: int = job["id"]
    job_enseigne: str = job["enseigne"]
    job_phase: str = job["phase"]
    job_url: str = job["url"]

    logger.info(
        "run_one: job id=%d %s/%s url=%s",
        job_id, job_enseigne, job_phase, job_url,
    )

    # ------------------------------------------------------------------
    # 1. Fetch
    # ------------------------------------------------------------------
    use_proxy = (
        job_enseigne in _PROXY_ENSEIGNES
        and job_phase not in _NO_PROXY_PHASES.get(job_enseigne, frozenset())
    )
    capture_cookies = job_phase == "select_store"
    result: FetchResult = fetch(
        job_url,
        method=job.get("method", "GET"),
        payload=job.get("payload"),
        use_proxy=use_proxy,
        cookies=job.get("cookies"),
        capture_cookies=capture_cookies,
    )

    # For select_store phase, 302 is the expected success response (Set-Cookie captured).
    _valid_statuses = (200, 201, 302) if capture_cookies else (200, 201)
    if result.error or result.status not in _valid_statuses:
        error_detail = result.error or f"HTTP {result.status}"
        logger.warning(
            "run_one: fetch failed job=%d %s — %s", job_id, job_url, error_detail
        )
        # Transient: network error (status==0), rate-limit (429), server errors (5xx).
        # Permanent: 404 (URL dead), 403 (blocked — fail fast, retrying won't help).
        status = result.status  # 0 if network/connection error
        retryable = status == 0 or status in (429, 500, 502, 503, 504)
        retry_or_fail(queue_conn, job_id, error_detail, retryable=retryable)
        return True

    # ------------------------------------------------------------------
    # 2. Select store phase (Carrefour) — no parser needed, handled here
    #    before dispatch so the missing-parser guard doesn't fire.
    # ------------------------------------------------------------------
    if job_phase == "select_store":
        if job_enseigne == "carrefour":
            # Extract Set-Cookie from response headers
            raw_cookie = result.response_headers.get("set-cookie", "")
            # scrape.do may concatenate multiple Set-Cookie headers with \n
            # Keep only the cookies relevant for per-store pricing
            cookie_parts = []
            important = {"FRONTAL_STORE", "FRONTONE_SESSION_ID", "FRONTONE_SESSID", "FRONTONE_ONLINE"}
            for cookie_line in raw_cookie.replace("\n", ";").split(";"):
                name = cookie_line.strip().split("=")[0].strip()
                if name in important:
                    cookie_parts.append(cookie_line.strip())
            cookie_str = "; ".join(cookie_parts) if cookie_parts else raw_cookie[:500]

            if cookie_str:
                logger.info(
                    "select_store %s/%s: cookie capturé (%d chars)",
                    job_enseigne, job.get("store_id"), len(cookie_str),
                )
                enqueue_rayons(
                    queue_conn,
                    job_enseigne,
                    [{"store_id": job.get("store_id")}],
                    cookies=cookie_str,
                )
            else:
                logger.warning(
                    "select_store %s/%s: aucun cookie dans la réponse",
                    job_enseigne, job.get("store_id"),
                )
        else:
            logger.warning("run_one: select_store phase not implemented for %s", job_enseigne)
        mark_done(queue_conn, job_id)
        return True

    # ------------------------------------------------------------------
    # 3. Dispatch to parser
    # ------------------------------------------------------------------
    dispatch_key = (job_enseigne, job_phase)
    parser_fn = _DISPATCH.get(dispatch_key)

    if parser_fn is None:
        msg = f"no parser for {dispatch_key}"
        logger.warning("run_one: %s (job id=%d)", msg, job_id)
        retry_or_fail(queue_conn, job_id, msg, retryable=False)
        return True

    # Choose the right raw body: JSON for JSON responses, text otherwise
    body = result.body_json if result.body_json is not None else result.body_text

    try:
        parsed: ParsedResult = parser_fn(body, store_id=job.get("store_id"))
    except Exception as exc:
        msg = f"parse error: {exc}"
        logger.error("run_one: %s (job id=%d)", msg, job_id, exc_info=True)
        # Parse errors are permanent — retrying won't fix a bug in parser code.
        retry_or_fail(queue_conn, job_id, msg, retryable=False)
        return True

    # ------------------------------------------------------------------
    # 4. Persist results
    # ------------------------------------------------------------------
    captured_at = datetime.now().isoformat()

    try:
        # --- Infomagasin phase (Leclerc only): enqueue rayon jobs after silo resolution ---
        if job_phase == "infomagasin" and parsed.stores:
            store_with_silo = parsed.stores[0]
            upsert_stores(db_conn, [_store_to_parsed(store_with_silo, job_enseigne)])
            store_dict = {
                "store_id": store_with_silo.store_id,
                "silo": store_with_silo.extra.get("silo"),
                "city": store_with_silo.extra.get("city") or store_with_silo.city,
                **store_with_silo.extra,
            }
            enqueue_rayons(queue_conn, job_enseigne, [store_dict])
            mark_done(queue_conn, job_id)
            return True

        # --- Stores phase ---
        if parsed.stores:
            parsed_stores = [_store_to_parsed(s, job_enseigne) for s in parsed.stores]
            upsert_stores(db_conn, parsed_stores)

            # Build the list of store dicts that enqueue_rayons expects
            store_dicts = [
                {
                    "store_id": s.store_id,
                    "silo": s.extra.get("silo"),
                    "city": s.city,
                    "postal_code": s.postal_code,
                    **s.extra,
                }
                for s in parsed.stores
            ]
            if job_enseigne == "leclerc":
                # Leclerc needs infomagasin lookup per store to obtain numSilo
                # before rayon URLs can be built
                for s in parsed.stores:
                    enqueue_infomagasin(queue_conn, s.store_id)
            elif job_enseigne == "carrefour":
                # Carrefour prices are per-store: enqueue select_store jobs to
                # capture per-store session cookies before fetching rayons.
                for s in parsed.stores:
                    postal_code = s.postal_code or ""
                    url = (
                        f"https://www.carrefour.fr/set-store/{s.store_id}"
                        f"?postalCode={urllib.parse.quote(postal_code)}"
                    )
                    enqueue_select_store(queue_conn, job_enseigne, s.store_id, url)
            else:
                enqueue_rayons(queue_conn, job_enseigne, store_dicts)

        # --- EAN enrichments (fiche phase) — UPDATE existing rayon observations ---
        if parsed.ean_updates:
            product_id = job.get("rayon_id") or ""
            for sentinel_or_id, ean in parsed.ean_updates:
                # Parser uses "__job_product_id__" sentinel; replace with actual rayon_id
                real_id = product_id if sentinel_or_id == "__job_product_id__" else sentinel_or_id
                if real_id and ean:
                    update_ean(db_conn, job_enseigne, real_id, ean)

        # --- Rayons / fiches phase (product observations) ---
        if parsed.products:
            parsed_products = [
                _product_to_parsed(p, job, captured_at) for p in parsed.products
            ]
            insert_observations(db_conn, parsed_products)

        # --- Fiche jobs enqueued by rayon parsers ---
        for fiche_job in parsed.fiche_jobs:
            enqueue_fiche(
                queue_conn,
                job_enseigne,
                store_id=job.get("store_id") or "",
                product_id=fiche_job["product_id"],
                url=fiche_job["url"],
                payload=fiche_job.get("payload"),
            )

        # --- Auchan-specific: pagination via ?page=N (search-infinite dead) ---
        if (
            job_enseigne == "auchan"
            and job_phase == "rayons"
            and parsed.next_url is None
        ):
            auchan_next = _auchan_next_page_url(job_url, len(parsed.products))
            if auchan_next:
                enqueue(
                    queue_conn,
                    "auchan",
                    "rayons",
                    auchan_next,
                    store_id=job.get("store_id"),  # None for global rayon jobs
                    rayon_id=job.get("rayon_id"),
                )
                logger.debug("run_one: auchan pagination → %s", auchan_next)

        # --- Système U: pagination via ?start=N&sz=18 ---
        if (
            job_enseigne == "systeme_u"
            and job_phase == "rayons"
            and parsed.next_url is None
            and len(parsed.products) > 0
        ):
            import urllib.parse as _urlparse
            _parsed_url = _urlparse.urlparse(job_url)
            _qs = _urlparse.parse_qs(_parsed_url.query, keep_blank_values=True)
            _sz = int(_qs.get("sz", ["18"])[0])
            _start = int(_qs.get("start", ["0"])[0])
            _next_start = _start + _sz
            # Stop if we have total_count and already past it
            _total = parsed.total_count
            if _total is None or _next_start < _total:
                _qs["start"] = [str(_next_start)]
                _new_q = _urlparse.urlencode(_qs, doseq=True)
                su_next = _urlparse.urlunparse(_parsed_url._replace(query=_new_q))
                enqueue(
                    queue_conn,
                    "systeme_u",
                    "rayons",
                    su_next,
                    store_id=job.get("store_id"),
                    rayon_id=job.get("rayon_id"),
                )
                logger.debug("run_one: systeme_u pagination → %s", su_next)

        # --- Pagination ---
        if parsed.next_url is not None:
            next_url = _resolve_next_url(job_url, parsed.next_url)
            enqueue(
                queue_conn,
                job_enseigne,
                job_phase,
                next_url,
                method=job.get("method", "GET"),
                payload=job.get("payload"),
                store_id=job.get("store_id"),
                rayon_id=job.get("rayon_id"),
            )
            logger.debug("run_one: paginating → %s", next_url)

    except Exception as exc:
        msg = f"persistence error: {exc}"
        # "database is locked" is transient — retryable. Real DB bugs are not.
        is_lock_error = "locked" in str(exc).lower()
        if is_lock_error:
            logger.warning("run_one: %s (job id=%d) — will retry", msg, job_id)
        else:
            logger.error("run_one: %s (job id=%d)", msg, job_id, exc_info=True)
        retry_or_fail(queue_conn, job_id, msg, retryable=is_lock_error)
        return True

    # ------------------------------------------------------------------
    # 5. Mark done
    # ------------------------------------------------------------------
    mark_done(queue_conn, job_id)
    logger.info(
        "run_one: done job=%d %s/%s stores=%d products=%d fiches=%d",
        job_id, job_enseigne, job_phase,
        len(parsed.stores), len(parsed.products), len(parsed.fiche_jobs),
    )
    return True


# ---------------------------------------------------------------------------
# Run loop
# ---------------------------------------------------------------------------

def run_loop(
    queue_conn: sqlite3.Connection,
    db_conn: sqlite3.Connection,
    *,
    enseigne: str | None = None,
    phase: str | None = None,
    max_jobs: int | None = None,
    delay_seconds: float = 1.0,
) -> int:
    """Process jobs until the queue is empty (or ``max_jobs`` reached).

    Returns the total number of jobs processed.
    """
    processed = 0
    while True:
        if max_jobs is not None and processed >= max_jobs:
            break
        did_work = run_one(queue_conn, db_conn, enseigne=enseigne, phase=phase)
        if not did_work:
            break
        processed += 1
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return processed


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )

    arg_parser = argparse.ArgumentParser(description="Drive price scraper runner")
    arg_parser.add_argument("--db", default="drive_prices.db")
    arg_parser.add_argument("--enseigne", default=None)
    arg_parser.add_argument("--phase", default=None)
    arg_parser.add_argument("--max-jobs", type=int, default=None)
    arg_parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay between requests (seconds)",
    )
    arg_parser.add_argument(
        "--seed-stores",
        action="store_true",
        help="Seed store sweep jobs before running",
    )
    args = arg_parser.parse_args()

    queue_conn = init_queue(args.db)
    db_conn = connect(args.db)
    init_schema(db_conn)

    if args.seed_stores:
        target_enseigne = args.enseigne or "itm"
        n = enqueue_store_sweep(queue_conn, target_enseigne)
        print(f"Seeded {n} store sweep jobs for {target_enseigne}")

    processed = run_loop(
        queue_conn,
        db_conn,
        enseigne=args.enseigne,
        phase=args.phase,
        max_jobs=args.max_jobs,
        delay_seconds=args.delay,
    )
    print(f"Processed {processed} jobs")
    stats = queue_stats(queue_conn)
    print("Queue stats:", stats)
