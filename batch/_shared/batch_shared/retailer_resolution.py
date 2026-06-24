"""Shared helper — resolve an enseigne name to a ``retailers`` row id.

Extracted from ``ratis_batch_osm_sync/normalize.py`` so it can be reused by
any source that injects retailer identity (SIRENE, OSM, Overture, …).

The OSM-specific ``slugify()`` and ``normalize_pbf_tags()`` functions are NOT
moved here — they stay in ``ratis_batch_osm_sync/normalize.py``. Only the
pure lookup+auto-create logic migrates, since it is genuinely source-agnostic.

The OSM batch will switch its import to this module in PR7.  Until then,
``ratis_batch_osm_sync/normalize.py`` keeps its own copy — the two are
identical so no behaviour diverges between now and PR7.

Usage
-----

    from batch_shared.retailer_resolution import resolve_or_create_retailer

    retailer_id = resolve_or_create_retailer(db, "Carrefour Market")
    # Caller must db.flush() and eventually db.commit() (R-DB-02).

Design
------
- **No db.commit()** — the caller decides the transaction boundary (R-DB-02).
- SQL raw (``text()``) to stay within the shared module's dependency surface
  (no ORM imports beyond Session).  Both OSM and SIRENE paths follow this
  convention for the retailer tables.
- Slug generation mirrors ``ratis_batch_osm_sync/normalize.py::slugify()``
  exactly — same transliteration table.  Keep in sync until PR7 deduplicates.
"""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)

_SLUGIFY_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    """ASCII-ish slug for auto-creating an unverified retailer.

    Keeps alphanumeric chars only; everything else collapses to a hyphen.
    Handles the most common French diacritics via explicit transliteration.

    Mirrors ``ratis_batch_osm_sync/normalize.py::slugify()`` — keep in sync
    until PR7 deduplicates by importing from here.
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


def resolve_or_create_retailer(
    db: Session,
    brand_name: str | None,
    *,
    alias_source: str = "sirene",
) -> uuid.UUID | None:
    """Resolve ``brand_name`` to a ``retailers.id``, auto-creating if unknown.

    Behaviour (mirrors DA-34, generalised for any source):

    - ``None`` or blank → returns ``None`` (the store's ``retailer_id`` stays
      NULL, which is valid).
    - Lookup ``retailer_aliases`` by lowercased + trimmed alias.  Hit → return
      the existing ``retailer_id``.
    - Miss → INSERT an unverified retailer (``is_verified=false``) with the
      cleaned name as ``canonical_name`` and its slug.  Register the
      lowercased alias with ``source=alias_source`` so the next run resolves
      without a second INSERT.  Uses ``ON CONFLICT (slug) DO UPDATE`` so
      concurrent runs are safe.

    Caller is responsible for ``db.flush()`` and ``db.commit()`` (R-DB-02).

    Parameters
    ----------
    db:
        SQLAlchemy session.
    brand_name:
        Raw enseigne name from the source dump (may be None).
    alias_source:
        Value inserted into ``retailer_aliases.source``.  Defaults to
        ``'sirene'``; OSM callers should pass ``'osm'``.
    """
    if brand_name is None:
        return None
    cleaned = brand_name.strip()
    if not cleaned:
        return None
    alias_key = cleaned.lower()

    row = db.execute(
        text("SELECT retailer_id FROM retailer_aliases WHERE alias = :alias LIMIT 1"),
        {"alias": alias_key},
    ).first()
    if row is not None:
        return row.retailer_id

    slug = _slugify(cleaned)
    if not slug:
        _log.debug(
            "resolve_or_create_retailer: brand_name=%r produced an empty slug — skipping",
            cleaned,
        )
        return None

    row = db.execute(
        text(
            """
            INSERT INTO retailers (canonical_name, slug, is_verified)
            VALUES (:canonical_name, :slug, false)
            ON CONFLICT (slug) DO UPDATE
                SET canonical_name = retailers.canonical_name
            RETURNING id
            """
        ),
        {"canonical_name": cleaned, "slug": slug},
    ).first()
    retailer_id = row.id

    db.execute(
        text(
            """
            INSERT INTO retailer_aliases (retailer_id, alias, source)
            VALUES (:retailer_id, :alias, :source)
            ON CONFLICT (retailer_id, alias) DO NOTHING
            """
        ),
        {"retailer_id": retailer_id, "alias": alias_key, "source": alias_source},
    )
    return retailer_id
