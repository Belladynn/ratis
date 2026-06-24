"""SIRENE row → CandidateStore normalisation.

Converts one raw SIRENE dict (as yielded by ``parser.stream_etablissements()``)
into a ``CandidateStore`` ready to be upserted via
``batch_shared.store_consolidation.apply_upsert()``.

Design notes
------------
- **lat/lng = None in PR4** : geocoding is added in PR5.  Never invent
  placeholder coordinates (e.g. 0.0) — leave them as ``None``.
- **is_disabled** : driven by ``etatAdministratifEtablissement == 'F'``.
  ``disabled_at`` is set from ``dateFermetureEtablissement`` when present;
  SIRENE legacy data may omit this date even for closed establishments
  (tolerated — ``disabled_at=None`` is valid).
- **retailer_id** : resolved via
  ``batch_shared.retailer_resolution.resolve_or_create_retailer()``.  The
  caller must flush/commit at the appropriate batch boundary (R-DB-02).
- **Returns None** when the row should be skipped (categorieJuridique holding).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from batch_shared.retailer_resolution import resolve_or_create_retailer
from batch_shared.store_consolidation import CandidateStore
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)

# SIRENE date format for dateFermetureEtablissement.
_SIRENE_DATE_FMT = "%Y-%m-%d"


def row_to_candidate(
    row: dict,
    db: Session,
    *,
    settings: dict,
) -> CandidateStore | None:
    """Convert a SIRENE row dict to a ``CandidateStore``.

    Parameters
    ----------
    row:
        Raw SIRENE row dict as yielded by ``parser.stream_etablissements()``.
    db:
        SQLAlchemy session (for retailer resolution).
    settings:
        The ``sirene_sync`` settings section (from ``ratis_settings.json``)
        which must contain ``holding_categories`` (list of categorieJuridique
        codes to skip).

    Returns
    -------
    CandidateStore | None
        ``None`` if the row should be skipped (holding company by
        ``categorieJuridique``).
    """
    # Skip holdings.
    holding_cats = set(settings.get("holding_categories", []))
    cat_juridique = (row.get("categorieJuridiqueUniteLegale") or "").strip()
    if cat_juridique in holding_cats:
        _log.debug(
            "row_to_candidate: skipping siret=%s — holding categorieJuridique=%s",
            row.get("siret"),
            cat_juridique,
        )
        return None

    # Name resolution: enseigne1 → denominationUsuelle → denomination légale.
    name = _resolve_name(row)
    if not name:
        # Nothing we can use as a store name → skip.
        _log.debug(
            "row_to_candidate: skipping siret=%s — no usable name",
            row.get("siret"),
        )
        return None

    siret = (row.get("siret") or "").strip() or None

    address = build_address_sirene(row)
    city = (row.get("libelleCommuneEtablissement") or "").strip() or None
    postal_code = (row.get("codePostalEtablissement") or "").strip() or None

    # Enseigne for retailer resolution (best available brand name).
    enseigne = _best_enseigne(row)
    retailer_id = resolve_or_create_retailer(db, enseigne, alias_source="sirene")

    # Lifecycle.
    state = (row.get("etatAdministratifEtablissement") or "").strip()
    is_disabled = state == "F"
    disabled_at: datetime | None = None
    if is_disabled:
        raw_date = (row.get("dateFermetureEtablissement") or "").strip()
        if raw_date:
            try:
                disabled_at = datetime.strptime(raw_date, _SIRENE_DATE_FMT).replace(tzinfo=UTC)
            except ValueError:
                _log.debug(
                    "row_to_candidate: siret=%s — unparseable dateFermetureEtablissement=%r",
                    siret,
                    raw_date,
                )

    return CandidateStore(
        source="sirene",
        name=name,
        address=address,
        city=city,
        postal_code=postal_code,
        lat=None,  # PR5 fills lat/lng via Géoplateforme geocoding.
        lng=None,
        siret=siret,
        osm_id=None,
        retailer_id=retailer_id,
        phone=None,
        opening_hours=None,
        is_disabled=is_disabled,
        disabled_at=disabled_at,
    )


def build_address_sirene(row: dict) -> str | None:
    """Assemble a street address from SIRENE address components.

    Concatenates ``numeroVoie``, ``typeVoie``, and ``libelleVoie`` (in that
    order), omitting None/blank parts.  Returns ``None`` if no meaningful
    address can be assembled.

    Examples
    --------
    >>> build_address_sirene({"numeroVoie": "12", "typeVoie": "RUE", "libelleVoie": "DE LA PAIX"})
    '12 RUE DE LA PAIX'
    >>> build_address_sirene({"numeroVoie": None, "typeVoie": "RUE", "libelleVoie": "DE LA PAIX"})
    'RUE DE LA PAIX'
    >>> build_address_sirene({"numeroVoie": None, "typeVoie": None, "libelleVoie": None})
    None
    """
    parts = [
        (row.get("numeroVoie") or "").strip(),
        (row.get("typeVoie") or "").strip(),
        (row.get("libelleVoie") or "").strip(),
    ]
    address = " ".join(p for p in parts if p)
    return address or None


def _resolve_name(row: dict) -> str | None:
    """Return the best available name for the establishment.

    Priority: enseigne1Etablissement → denominationUsuelleEtablissement →
    denominationUniteLegale.
    """
    for field in (
        "enseigne1Etablissement",
        "denominationUsuelleEtablissement",
        "denominationUniteLegale",
    ):
        val = (row.get(field) or "").strip()
        if val:
            return val
    return None


def _best_enseigne(row: dict) -> str | None:
    """Return the best enseigne name for retailer resolution.

    Same as ``_resolve_name`` but returns ``None`` (not ``denominationUniteLegale``)
    when no enseigne is available — legal entity names are not reliable as
    brand names for retailer resolution.
    """
    for field in ("enseigne1Etablissement", "denominationUsuelleEtablissement"):
        val = (row.get(field) or "").strip()
        if val:
            return val
    return None
