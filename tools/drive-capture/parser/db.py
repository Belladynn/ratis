"""SQLite persistence for normalised drive-capture observations.

This database (``drive_prices.db``) is fully independent from Ratis: no
Postgres, no shared schema. Two tables:

* ``observations`` — append-only price history. One row per ``ParsedProduct``;
  re-running a capture inserts new rows (timestamped via ``captured_at``).
* ``stores`` — upserted on the ``(enseigne, store_ref)`` natural key.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable

from parser.model import ParsedProduct, ParsedStore, field_names

logger = logging.getLogger(__name__)

_OBS_COLUMNS = field_names(ParsedProduct)
_STORE_COLUMNS = field_names(ParsedStore)

_DDL = """
CREATE TABLE IF NOT EXISTS observations (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    enseigne                 TEXT    NOT NULL,
    name                     TEXT    NOT NULL,
    captured_at              TEXT    NOT NULL,
    store_ref                TEXT,
    ean                      TEXT,
    brand                    TEXT,
    quantity                 TEXT,
    category                 TEXT,
    price_cents              INTEGER,
    price_per_measure_cents  INTEGER,
    measure_unit             TEXT,
    promo_price_cents        INTEGER,
    promo_pct                INTEGER,
    is_promo                 INTEGER NOT NULL DEFAULT 0,
    product_url              TEXT,
    image_url                TEXT,
    available                INTEGER,
    enseigne_product_id      TEXT,
    parsed_at                TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Dedup guard: same product URL → skip duplicate inserts on job retries.
    -- SQLite NULL != NULL so rows with NULL product_url are never deduplicated
    -- (acceptable: fiche-only enrichments rarely have a product_url).
    UNIQUE (enseigne, product_url)
);

CREATE INDEX IF NOT EXISTS ix_observations_ean      ON observations (ean);
CREATE INDEX IF NOT EXISTS ix_observations_enseigne ON observations (enseigne);
CREATE INDEX IF NOT EXISTS ix_observations_product_url ON observations (product_url);

CREATE TABLE IF NOT EXISTS stores (
    enseigne     TEXT NOT NULL,
    store_ref    TEXT NOT NULL,
    name         TEXT,
    city         TEXT,
    postal_code  TEXT,
    lat          REAL,
    lng          REAL,
    parsed_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (enseigne, store_ref)
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Open (creating if needed) the SQLite database."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they do not exist yet."""
    conn.executescript(_DDL)
    conn.commit()


def _as_db_value(v: object) -> object:
    """SQLite has no bool type — store bools as 0/1, leave None as NULL."""
    if isinstance(v, bool):
        return int(v)
    return v


def insert_observations(conn: sqlite3.Connection, products: Iterable[ParsedProduct]) -> int:
    """Append observations. Returns the number of rows inserted."""
    import time as _time
    placeholders = ", ".join("?" for _ in _OBS_COLUMNS)
    columns = ", ".join(_OBS_COLUMNS)
    # S608 noqa: `columns`/`placeholders` derive from a hardcoded dataclass
    # field list (model.ParsedProduct), never user input; values bound via ?.
    # INSERT OR IGNORE: skip silently if (enseigne, product_url) UNIQUE constraint fires.
    # Prevents double-inserts on job retries while preserving append-only price history
    # for distinct product_url values (NULL product_url rows are never deduplicated by
    # SQLite NULL != NULL semantics, which is acceptable for fiche-only enrichments).
    sql = f"INSERT OR IGNORE INTO observations ({columns}) VALUES ({placeholders})"  # noqa: S608
    rows = [tuple(_as_db_value(getattr(p, col)) for col in _OBS_COLUMNS) for p in products]
    # Retry loop for transient SQLite locking (busy_timeout handles most cases
    # but executemany can still raise OperationalError in rare edge cases).
    for attempt in range(5):
        try:
            conn.executemany(sql, rows)
            conn.commit()
            logger.debug("inserted %d observation row(s)", len(rows))
            return len(rows)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() and attempt < 4:
                wait = 2 ** attempt  # 1, 2, 4, 8 seconds
                logger.warning("insert_observations: locked, retry %d/4 in %ds", attempt + 1, wait)
                try:
                    conn.rollback()
                except Exception:
                    pass
                _time.sleep(wait)
                continue
            raise
    raise sqlite3.OperationalError("insert_observations: max retries exceeded (database locked)")


def update_ean(
    conn: sqlite3.Connection,
    enseigne: str,
    enseigne_product_id: str,
    ean: str,
) -> int:
    """Update EAN on existing observations for a given enseigne_product_id.

    Called after a fiche parse — enriches rayon observations with the EAN
    extracted from the product detail page.  Returns number of rows updated.
    """
    cursor = conn.execute(
        "UPDATE observations SET ean = ? WHERE enseigne = ? AND enseigne_product_id = ? AND ean IS NULL",
        (ean, enseigne, enseigne_product_id),
    )
    conn.commit()
    updated = cursor.rowcount
    if updated:
        logger.debug("update_ean: %s/%s → %s (%d rows)", enseigne, enseigne_product_id, ean, updated)
    return updated


def upsert_stores(conn: sqlite3.Connection, stores: Iterable[ParsedStore]) -> int:
    """Upsert stores on ``(enseigne, store_ref)``. Returns rows processed."""
    updatable = [c for c in _STORE_COLUMNS if c not in ("enseigne", "store_ref")]
    set_clause = ", ".join(f"{c}=excluded.{c}" for c in updatable)
    placeholders = ", ".join("?" for _ in _STORE_COLUMNS)
    columns = ", ".join(_STORE_COLUMNS)
    conflict = "ON CONFLICT (enseigne, store_ref) DO UPDATE SET"
    # S608 noqa: `columns`/`set_clause` derive from a hardcoded dataclass
    # field list (model.ParsedStore), never user input; values bound via ?.
    sql = f"INSERT INTO stores ({columns}) VALUES ({placeholders}) {conflict} {set_clause}, parsed_at=datetime('now')"  # noqa: S608
    rows = [tuple(_as_db_value(getattr(s, col)) for col in _STORE_COLUMNS) for s in stores]
    conn.executemany(sql, rows)
    conn.commit()
    logger.debug("upserted %d store row(s)", len(rows))
    return len(rows)


def load(
    db_path: str,
    products: Iterable[ParsedProduct],
    stores: Iterable[ParsedStore],
) -> tuple[int, int]:
    """Open the DB, ensure the schema, persist products + stores, close.

    Returns ``(n_observations, n_stores)``.
    """
    conn = connect(db_path)
    try:
        init_schema(conn)
        n_obs = insert_observations(conn, products)
        n_stores = upsert_stores(conn, stores)
    finally:
        conn.close()
    logger.info(
        "chargé %d observations + %d magasins dans %s", n_obs, n_stores, db_path
    )
    return n_obs, n_stores
