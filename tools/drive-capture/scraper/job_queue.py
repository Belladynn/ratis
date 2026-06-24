"""SQLite-backed scrape job queue with deduplication.

Jobs are stored in a ``scrape_jobs`` table in the same SQLite database as the
parser (path passed by the caller).  The table is created on first call to
``init_queue``.

Phase ordering for ``next_job``: ``stores`` < ``rayons`` < ``fiches`` so that
store discovery always runs before product crawling.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal paths to catalog data files
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent  # tools/drive-capture/

_DDL = """
CREATE TABLE IF NOT EXISTS scrape_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    enseigne    TEXT    NOT NULL,
    phase       TEXT    NOT NULL,
    store_id    TEXT    NOT NULL DEFAULT '',
    rayon_id    TEXT    NOT NULL DEFAULT '',
    url         TEXT    NOT NULL,
    method      TEXT    NOT NULL DEFAULT 'GET',
    payload     TEXT,
    cookies     TEXT,
    status      TEXT    NOT NULL DEFAULT 'pending',
    error_msg   TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    started_at  TEXT,
    done_at     TEXT,
    UNIQUE (enseigne, phase, store_id, rayon_id, url)
);
"""

# Columns added after initial release — applied idempotently via try/except
# because SQLite does not support ALTER TABLE ... ADD COLUMN IF NOT EXISTS.
_MIGRATION_COLUMNS = [
    "ALTER TABLE scrape_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE scrape_jobs ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 3",
    "ALTER TABLE scrape_jobs ADD COLUMN cookies TEXT",
]
# SQLite NULL != NULL so a UNIQUE index on nullable columns doesn't deduplicate
# rows where those columns are NULL.  We store '' (empty string) as the sentinel
# meaning "not applicable" and expose None through the Python API.

# Phase ordering used in ORDER BY inside next_job
_PHASE_ORDER = "CASE phase WHEN 'stores' THEN 0 WHEN 'select_store' THEN 1 WHEN 'infomagasin' THEN 1 WHEN 'rayons' THEN 2 WHEN 'fiches' THEN 3 ELSE 4 END"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_queue(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database and ensure ``scrape_jobs`` exists.

    Returns an open connection with ``row_factory = sqlite3.Row`` set and
    ``isolation_level = None`` (autocommit mode) so we can issue explicit
    ``BEGIN IMMEDIATE`` transactions in ``next_job`` without conflicts.
    """
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_DDL)
    # Idempotent migrations for columns added after initial schema release.
    # SQLite does not support ALTER TABLE ... ADD COLUMN IF NOT EXISTS, so we
    # catch the OperationalError raised when the column already exists.
    for col_sql in _MIGRATION_COLUMNS:
        try:
            conn.execute(col_sql)
        except sqlite3.OperationalError:
            pass  # column already exists
    logger.debug("init_queue: scrape_jobs ready in %s", db_path)
    return conn


def enqueue(
    conn: sqlite3.Connection,
    enseigne: str,
    phase: str,
    url: str,
    method: str = "GET",
    payload: dict | None = None,
    store_id: str | None = None,
    rayon_id: str | None = None,
    cookies: str | None = None,
) -> bool:
    """Insert a job, ignoring duplicates (dedup on UNIQUE constraint).

    Returns ``True`` if the row was newly inserted, ``False`` if it already
    existed (dedup silently skipped).
    """
    payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
    # '' sentinel: SQLite NULL != NULL breaks UNIQUE dedup on nullable columns
    sid = store_id if store_id is not None else ""
    rid = rayon_id if rayon_id is not None else ""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO scrape_jobs
            (enseigne, phase, store_id, rayon_id, url, method, payload, cookies)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (enseigne, phase, sid, rid, url, method, payload_json, cookies),
    )
    # No explicit commit needed: isolation_level=None (autocommit mode)
    inserted = cursor.rowcount == 1
    if inserted:
        logger.debug("enqueued %s %s %s", enseigne, phase, url)
    return inserted


def next_job(
    conn: sqlite3.Connection,
    enseigne: str | None = None,
    phase: str | None = None,
) -> dict[str, Any] | None:
    """Claim the next pending job atomically.

    Selects the highest-priority pending job (``stores`` first, then
    ``rayons``, then ``fiches``), marks it ``running``, and returns it as a
    plain ``dict``.  Returns ``None`` if no job matches.

    The SELECT + UPDATE are executed inside a single ``BEGIN IMMEDIATE``
    transaction to prevent two workers from picking the same job.
    """
    filters = ["status = 'pending'"]
    params: list[Any] = []

    if enseigne is not None:
        filters.append("enseigne = ?")
        params.append(enseigne)
    if phase is not None:
        filters.append("phase = ?")
        params.append(phase)

    where = " AND ".join(filters)
    # S608 noqa: `where` and `_PHASE_ORDER` are built from trusted literals/ints only
    select_sql = (
        f"SELECT * FROM scrape_jobs WHERE {where}"
        f" ORDER BY {_PHASE_ORDER}, id"
        " LIMIT 1"
    )

    # BEGIN IMMEDIATE acquires a write lock immediately so two concurrent workers
    # cannot claim the same job.  We manage the transaction explicitly because
    # the connection is in isolation_level=None (autocommit) mode.
    # Retry loop for transient "database is locked" — busy_timeout handles most
    # cases but Python's sqlite3 module can still surface OperationalError.
    import time as _time
    for _attempt in range(8):
        try:
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            wait = min(0.5 * (2 ** _attempt), 10)
            logger.debug("next_job: BEGIN IMMEDIATE locked (attempt %d), wait %.1fs", _attempt + 1, wait)
            _time.sleep(wait)
    else:
        raise sqlite3.OperationalError("next_job: could not acquire write lock after 8 attempts")

    try:
        row = conn.execute(select_sql, params).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return None

        job_id: int = row["id"]
        conn.execute(
            "UPDATE scrape_jobs SET status='running', started_at=datetime('now') WHERE id=?",
            (job_id,),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    job = dict(row)
    if job.get("payload"):
        job["payload"] = json.loads(job["payload"])
    # Restore None for sentinel empty strings
    if job.get("store_id") == "":
        job["store_id"] = None
    if job.get("rayon_id") == "":
        job["rayon_id"] = None
    logger.debug("next_job claimed id=%d %s/%s", job_id, job["enseigne"], job["phase"])
    return job


def mark_done(conn: sqlite3.Connection, job_id: int) -> None:
    """Mark a job as successfully completed."""
    conn.execute(
        "UPDATE scrape_jobs SET status='done', done_at=datetime('now') WHERE id=?",
        (job_id,),
    )
    logger.debug("mark_done id=%d", job_id)


def mark_error(conn: sqlite3.Connection, job_id: int, error_msg: str) -> None:
    """Mark a job as failed with an error message."""
    conn.execute(
        "UPDATE scrape_jobs SET status='error', done_at=datetime('now'), error_msg=? WHERE id=?",
        (error_msg, job_id),
    )
    logger.debug("mark_error id=%d msg=%s", job_id, error_msg)


def retry_or_fail(
    conn: sqlite3.Connection,
    job_id: int,
    error_msg: str,
    retryable: bool = True,
) -> bool:
    """Increment retry_count and either reset to pending or mark as error.

    If ``retryable`` is True and the job has not yet exhausted its retry budget
    (``retry_count < max_retries``), the job is reset to ``pending`` so the
    runner picks it up again on the next cycle.  Otherwise the job is
    permanently failed with ``status='error'``.

    Returns ``True`` if the job was re-queued for retry, ``False`` if it was
    failed permanently.
    """
    row = conn.execute(
        "SELECT retry_count, max_retries FROM scrape_jobs WHERE id=?",
        (job_id,),
    ).fetchone()
    if row is None:
        logger.warning("retry_or_fail: job id=%d not found", job_id)
        return False

    retry_count: int = row["retry_count"]
    max_retries: int = row["max_retries"]

    if retryable and retry_count < max_retries:
        conn.execute(
            """
            UPDATE scrape_jobs
               SET status = 'pending',
                   retry_count = retry_count + 1,
                   error_msg = ?,
                   started_at = NULL
             WHERE id = ?
            """,
            (error_msg, job_id),
        )
        logger.debug(
            "retry_or_fail: id=%d requeued (retry %d/%d) — %s",
            job_id, retry_count + 1, max_retries, error_msg,
        )
        return True
    else:
        conn.execute(
            """
            UPDATE scrape_jobs
               SET status = 'error',
                   done_at = datetime('now'),
                   error_msg = ?,
                   retry_count = retry_count + 1
             WHERE id = ?
            """,
            (error_msg, job_id),
        )
        logger.debug(
            "retry_or_fail: id=%d failed permanently (retry_count=%d, retryable=%s) — %s",
            job_id, retry_count + 1, retryable, error_msg,
        )
        return False


def queue_stats(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Return job counts grouped by enseigne × phase × status.

    Structure::

        {
            "itm": {
                "stores:pending": 3,
                "stores:done": 135,
                ...
            },
            ...
        }

    The top-level keys are enseigne names; inner keys are ``"phase:status"``.
    """
    rows = conn.execute(
        """
        SELECT enseigne, phase, status, COUNT(*) AS n
        FROM scrape_jobs
        GROUP BY enseigne, phase, status
        ORDER BY enseigne, phase, status
        """
    ).fetchall()

    result: dict[str, dict[str, int]] = {}
    for row in rows:
        enseigne = row["enseigne"]
        key = f"{row['phase']}:{row['status']}"
        result.setdefault(enseigne, {})[key] = row["n"]
    return result


# ---------------------------------------------------------------------------
# Bulk enqueue helpers
# ---------------------------------------------------------------------------


def enqueue_store_sweep(
    conn: sqlite3.Connection,
    enseigne: str,
    cities: list[dict] | None = None,
) -> int:
    """Generate and enqueue store-locator jobs for every reference city.

    ``cities`` must be a list of dicts with at least ``lat``, ``lng``, and
    ``postal_code`` keys, matching the shape of ``geo_reference_cities.json``.
    If ``None``, the catalog file is loaded automatically.

    Returns the number of newly inserted jobs.
    """
    if cities is None:
        path = _DATA_DIR / "geo_reference_cities.json"
        with path.open(encoding="utf-8") as fh:
            cities = json.load(fh)

    # Import here to avoid a circular dependency if callers import both modules
    from scraper.url_builders import (
        carrefour_store_locator_url,
        itm_store_locator_url,
        leclerc_store_locator_url,
    )

    inserted = 0
    for city in cities:
        lat: float = city["lat"]
        lng: float = city["lng"]
        # geo_reference_cities.json stores "dept" (e.g. "75"); store locator
        # APIs use postal_code as a secondary hint alongside lat/lng, so
        # dept+"000" is a safe fallback when a real postal code is absent.
        postal_code: str = city.get("postal_code") or (city.get("dept", "") + "000")

        if enseigne == "itm":
            url = itm_store_locator_url(lat, lng, postal_code)
        elif enseigne == "carrefour":
            url = carrefour_store_locator_url(lat, lng, postal_code, city=city.get("name", ""))
        elif enseigne == "leclerc":
            url = leclerc_store_locator_url(lat, lng, postal_code)
        elif enseigne in ("auchan", "monoprix"):
            # Single global URL — enqueue once then break
            from scraper.url_builders import (
                auchan_store_locator_url,
                monoprix_stores_url,
            )

            url = (
                auchan_store_locator_url()
                if enseigne == "auchan"
                else monoprix_stores_url()
            )
            if enqueue(conn, enseigne, "stores", url):
                inserted += 1
            break  # only one URL needed for these enseignes
        elif enseigne == "systeme_u":
            from scraper.url_builders import systeme_u_store_locator_url
            url = systeme_u_store_locator_url(lat, lng, postal_code, city.get("name", ""))
        else:
            logger.warning("enqueue_store_sweep: enseigne inconnue %r", enseigne)
            break

        if enqueue(conn, enseigne, "stores", url):
            inserted += 1

    logger.info(
        "enqueue_store_sweep: %d nouveaux jobs stores pour %s", inserted, enseigne
    )
    return inserted


def enqueue_rayons(
    conn: sqlite3.Connection,
    enseigne: str,
    stores: list[dict],
    cookies: str | None = None,
) -> int:
    """Enqueue page-1 rayon jobs for each store × catalog-rayon combination.

    ``stores`` is a list of dicts coming from the store-locator parse step.
    The exact keys needed depend on the enseigne (e.g. Leclerc needs ``silo``,
    ``store_ref``, ``city``; ITM needs no store-specific info in the URL).

    Returns the number of newly inserted jobs.
    """
    path = _DATA_DIR / "rayons_catalog.json"
    with path.open(encoding="utf-8") as fh:
        catalog: dict = json.load(fh)

    inserted = 0

    if enseigne == "itm":
        from scraper.url_builders import itm_rayon_url

        rayons: list[dict] = catalog.get("intermarche", {}).get("rayons", [])
        for store in stores:
            store_id: str = store.get("store_id", "")
            for rayon in rayons:
                url = itm_rayon_url(rayon["path"])
                rayon_id = rayon.get("id", rayon["path"])
                if enqueue(
                    conn,
                    enseigne,
                    "rayons",
                    url,
                    store_id=store_id,
                    rayon_id=str(rayon_id),
                ):
                    inserted += 1

    elif enseigne == "carrefour":
        from scraper.url_builders import carrefour_rayon_url

        # When called from the select_store handler, stores has one entry with
        # store_id and cookies carries the per-store session cookie.
        # store_id is included so each store gets its own rayon jobs with the
        # correct cookie context for per-store pricing.
        categories: list[dict] = catalog.get("carrefour", {}).get("rayons", [])
        for store in stores if stores else [{}]:
            store_id_val: str = store.get("store_id", "") if store else ""
            for cat in categories:
                url = carrefour_rayon_url(cat["slug"])
                if enqueue(
                    conn,
                    enseigne,
                    "rayons",
                    url,
                    store_id=store_id_val or None,
                    rayon_id=str(cat["id"]),
                    cookies=cookies,
                ):
                    inserted += 1

    elif enseigne == "auchan":
        from scraper.url_builders import auchan_rayon_url_from_catalog_entry

        # Auchan rayons are global — catalog URLs are enseigne-wide, not per-store.
        # Prices are embedded in the listing HTML; store context not needed at this
        # phase (rayon = product discovery + prices, fiche = EAN enrichment only).
        # Enqueue once per category, no store_id, so UNIQUE(enseigne, phase, '', rayon_id, url)
        # deduplicates across any number of store-locator results.
        rayons = catalog.get("auchan", {}).get("rayons", [])
        for rayon in rayons:
            url = auchan_rayon_url_from_catalog_entry(rayon)
            rayon_id = str(rayon.get("category_id", rayon.get("url", "")))
            if enqueue(conn, enseigne, "rayons", url, rayon_id=rayon_id):
                inserted += 1

    elif enseigne == "systeme_u":
        from scraper.url_builders import systeme_u_rayon_url

        # Système U rayons are global — coursesu.com/c/{rayon} URLs have no store
        # parameter. Enqueue once per category (same fix as Carrefour/Auchan).
        rayons = catalog.get("systeme_u", {}).get("rayons", [])
        for rayon in rayons:
            url = systeme_u_rayon_url(rayon["url"])
            rayon_id = str(rayon.get("id", rayon["url"]))
            if enqueue(conn, enseigne, "rayons", url, rayon_id=rayon_id):
                inserted += 1

    elif enseigne == "leclerc":
        from scraper.url_builders import leclerc_rayon_url

        rayons = catalog.get("leclerc", {}).get("rayons", [])
        for store in stores:
            store_id = store.get("store_id", "")
            silo: str = store.get("silo", "")
            city: str = store.get("city", "")
            for rayon in rayons:
                # Catalog uses "category_id" — map to rayon_id; extract slug
                # from url_template when not present as a top-level key.
                rayon_id_val = str(rayon.get("rayon_id") or rayon.get("category_id", ""))
                slug: str = rayon.get("slug", "")
                if not slug:
                    import re as _re
                    tpl = rayon.get("url_template", "")
                    m = _re.search(r"rayon-\d+-([^/.]+)\.aspx", tpl)
                    if m:
                        slug = m.group(1)
                url = leclerc_rayon_url(silo, store_id, city, rayon_id_val, slug)
                if enqueue(
                    conn,
                    enseigne,
                    "rayons",
                    url,
                    store_id=store_id,
                    rayon_id=rayon_id_val,
                ):
                    inserted += 1

    elif enseigne == "monoprix":
        # Monoprix: fetch the top-level category list (depth=1 → 28 categories) and seed
        # one rayon job per top-level category. Each top-level ID returns all products in
        # its sub-tree, so 28 calls + pagination (~170 pages total) cover the full catalogue.
        # This is 12× more efficient than queuing one job per leaf category (2152 calls).
        from scraper.http_client import fetch as _http_fetch
        from scraper.url_builders import (
            monoprix_categories_url,
            monoprix_products_url,
        )

        cat_result = _http_fetch(monoprix_categories_url())
        if cat_result.body_json and not cat_result.error:
            top_level = cat_result.body_json if isinstance(cat_result.body_json, list) else []
            for cat in top_level:
                if not isinstance(cat, dict):
                    continue
                cat_id = str(cat.get("categoryId") or cat.get("id") or "").strip()
                if not cat_id:
                    continue
                url = monoprix_products_url(cat_id)
                if enqueue(conn, enseigne, "rayons", url, rayon_id=cat_id):
                    inserted += 1
            logger.info(
                "enqueue_rayons: Monoprix — %d top-level categories seeded from %d returned",
                inserted,
                len(top_level),
            )
        else:
            logger.error(
                "enqueue_rayons: Monoprix categories fetch failed — %s",
                cat_result.error or f"HTTP {cat_result.status}",
            )

    else:
        logger.warning("enqueue_rayons: enseigne inconnue %r", enseigne)

    logger.info(
        "enqueue_rayons: %d nouveaux jobs rayons pour %s", inserted, enseigne
    )
    return inserted


def enqueue_fiche(
    conn: sqlite3.Connection,
    enseigne: str,
    store_id: str,
    product_id: str,
    url: str,
    payload: dict | None = None,
) -> bool:
    """Enqueue a single product-detail (fiche) job.

    Called by the rayon parser after discovering a product.  ``rayon_id`` is
    set to ``None`` for fiche jobs (product-level granularity).

    Returns ``True`` if newly inserted, ``False`` if already queued (dedup).
    """
    method = "POST" if payload is not None else "GET"
    # Fiches are product-level (EAN enrichment) — store_id intentionally omitted
    # so that UNIQUE(enseigne, phase, store_id='', rayon_id=product_id, url)
    # deduplicates across all stores.
    return enqueue(
        conn,
        enseigne,
        "fiches",
        url,
        method=method,
        payload=payload,
        store_id=None,
        rayon_id=product_id,
    )


def enqueue_infomagasin(
    conn: sqlite3.Connection,
    store_ref: str,
) -> bool:
    """Enqueue a Leclerc infomagasin lookup job."""
    from scraper.url_builders import leclerc_infomagasin_url
    url = leclerc_infomagasin_url(store_ref)
    return enqueue(conn, "leclerc", "infomagasin", url, store_id=store_ref)


def enqueue_select_store(
    conn: sqlite3.Connection,
    enseigne: str,
    store_id: str,
    url: str,
    postal_code: str | None = None,
) -> bool:
    """Enqueue a store-selection job (captures session cookie for per-store pricing).

    The runner handles this phase by calling fetch with capture_cookies=True,
    extracting the Set-Cookie response headers, and enqueuing rayon jobs with
    the resulting cookie string for per-store price context.
    """
    return enqueue(conn, enseigne, "select_store", url, store_id=store_id)
