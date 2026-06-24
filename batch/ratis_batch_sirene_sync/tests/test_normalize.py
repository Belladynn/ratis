"""Tests for sirene_sync.normalize — row_to_candidate() + build_address_sirene().

Uses the DB fixture from conftest.py (SAVEPOINT-isolated session).
"""

from __future__ import annotations

from ratis_core.seed.retailers import seed_retailers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_row(**overrides) -> dict:
    """Minimal valid SIRENE row dict for testing."""
    base = {
        "siret": "12345678901234",
        "etatAdministratifEtablissement": "A",
        "dateFermetureEtablissement": None,
        "activitePrincipaleEtablissement": "47.11B",
        "enseigne1Etablissement": "Monop Test",
        "denominationUsuelleEtablissement": None,
        "denominationUniteLegale": "SAS MONOP TEST",
        "numeroVoie": "5",
        "typeVoie": "RUE",
        "libelleVoie": "DE LA PAIX",
        "libelleCommuneEtablissement": "PARIS",
        "codePostalEtablissement": "75001",
        "categorieJuridiqueUniteLegale": "5499",
    }
    base.update(overrides)
    return base


_DEFAULT_SETTINGS = {
    "ape_whitelist": ["47.11B", "47.11D", "47.21Z"],
    "holding_categories": ["6420", "6430", "6499", "1000"],
}


# ---------------------------------------------------------------------------
# row_to_candidate
# ---------------------------------------------------------------------------


class TestRowToCandidateActive:
    def test_normalize_active_row_to_candidate(self, db):
        """Active row produces CandidateStore with source='sirene', is_disabled=False."""
        from sirene_sync.normalize import row_to_candidate

        row = _base_row()
        candidate = row_to_candidate(row, db, settings=_DEFAULT_SETTINGS)
        db.flush()

        assert candidate is not None
        assert candidate.source == "sirene"
        assert candidate.is_disabled is False
        assert candidate.disabled_at is None
        assert candidate.siret == "12345678901234"
        assert candidate.name == "Monop Test"
        assert candidate.lat is None, "lat must be None in PR4 (PR5 fills it)"
        assert candidate.lng is None, "lng must be None in PR4"

    def test_normalize_address_builds_correctly(self, db):
        """Address is assembled from numeroVoie + typeVoie + libelleVoie."""
        from sirene_sync.normalize import row_to_candidate

        row = _base_row(
            numeroVoie="12",
            typeVoie="AVENUE",
            libelleVoie="DE LA LIBERTE",
        )
        candidate = row_to_candidate(row, db, settings=_DEFAULT_SETTINGS)
        assert candidate is not None
        assert candidate.address == "12 AVENUE DE LA LIBERTE"

    def test_normalize_address_partial(self, db):
        """numeroVoie=None → address without number, no crash."""
        from sirene_sync.normalize import row_to_candidate

        row = _base_row(numeroVoie=None)
        candidate = row_to_candidate(row, db, settings=_DEFAULT_SETTINGS)
        assert candidate is not None
        assert candidate.address is not None
        assert "RUE" in candidate.address

    def test_normalize_resolves_retailer(self, db):
        """A seeded enseigne resolves to a non-None retailer_id."""
        from sirene_sync.normalize import row_to_candidate

        seed_retailers(db)
        db.flush()

        row = _base_row(enseigne1Etablissement="Lidl")
        candidate = row_to_candidate(row, db, settings=_DEFAULT_SETTINGS)
        db.flush()
        assert candidate is not None
        assert candidate.retailer_id is not None


class TestRowToCandidateClosed:
    def test_normalize_closed_row_sets_disabled(self, db):
        """etatAdministratif='F' → is_disabled=True."""
        from sirene_sync.normalize import row_to_candidate

        row = _base_row(
            etatAdministratifEtablissement="F",
            dateFermetureEtablissement="2023-06-15",
        )
        candidate = row_to_candidate(row, db, settings=_DEFAULT_SETTINGS)
        assert candidate is not None
        assert candidate.is_disabled is True
        assert candidate.disabled_at is not None
        assert candidate.disabled_at.year == 2023
        assert candidate.disabled_at.month == 6

    def test_normalize_closed_row_no_date(self, db):
        """etatAdministratif='F' with no dateFermeture → is_disabled=True, disabled_at=None."""
        from sirene_sync.normalize import row_to_candidate

        row = _base_row(
            etatAdministratifEtablissement="F",
            dateFermetureEtablissement=None,
        )
        candidate = row_to_candidate(row, db, settings=_DEFAULT_SETTINGS)
        assert candidate is not None
        assert candidate.is_disabled is True
        assert candidate.disabled_at is None


class TestRowToCandidateNameFallback:
    def test_normalize_name_fallback_chain_enseigne_first(self, db):
        """enseigne1 is used when present."""
        from sirene_sync.normalize import row_to_candidate

        row = _base_row(
            enseigne1Etablissement="Enseigne1",
            denominationUsuelleEtablissement="DenomUsuelle",
            denominationUniteLegale="DenomLegale",
        )
        candidate = row_to_candidate(row, db, settings=_DEFAULT_SETTINGS)
        assert candidate is not None
        assert candidate.name == "Enseigne1"

    def test_normalize_name_fallback_chain_denom_usuelle(self, db):
        """enseigne1=None → falls back to denominationUsuelle."""
        from sirene_sync.normalize import row_to_candidate

        row = _base_row(
            enseigne1Etablissement=None,
            denominationUsuelleEtablissement="DenomUsuelle",
            denominationUniteLegale="DenomLegale",
        )
        candidate = row_to_candidate(row, db, settings=_DEFAULT_SETTINGS)
        assert candidate is not None
        assert candidate.name == "DenomUsuelle"

    def test_normalize_name_fallback_chain_denom_legale(self, db):
        """enseigne1=None, denominationUsuelle=None → falls back to denominationUniteLegale."""
        from sirene_sync.normalize import row_to_candidate

        row = _base_row(
            enseigne1Etablissement=None,
            denominationUsuelleEtablissement=None,
            denominationUniteLegale="SAS DenomLegale",
        )
        candidate = row_to_candidate(row, db, settings=_DEFAULT_SETTINGS)
        assert candidate is not None
        assert candidate.name == "SAS DenomLegale"

    def test_normalize_name_all_null_returns_none(self, db):
        """All name fields None → row_to_candidate returns None (skip)."""
        from sirene_sync.normalize import row_to_candidate

        row = _base_row(
            enseigne1Etablissement=None,
            denominationUsuelleEtablissement=None,
            denominationUniteLegale=None,
        )
        result = row_to_candidate(row, db, settings=_DEFAULT_SETTINGS)
        assert result is None


class TestRowToCandidateHolding:
    def test_normalize_returns_none_for_holding(self, db):
        """categorieJuridique in holding_categories → None."""
        from sirene_sync.normalize import row_to_candidate

        row = _base_row(categorieJuridiqueUniteLegale="6420")  # in holding list
        result = row_to_candidate(row, db, settings=_DEFAULT_SETTINGS)
        assert result is None

    def test_normalize_passes_when_not_holding(self, db):
        """categorieJuridique NOT in holding_categories → not skipped."""
        from sirene_sync.normalize import row_to_candidate

        row = _base_row(categorieJuridiqueUniteLegale="5499")
        result = row_to_candidate(row, db, settings=_DEFAULT_SETTINGS)
        assert result is not None


# ---------------------------------------------------------------------------
# build_address_sirene
# ---------------------------------------------------------------------------


class TestBuildAddressSirene:
    def test_full_address(self):
        from sirene_sync.normalize import build_address_sirene

        row = {
            "numeroVoie": "12",
            "typeVoie": "RUE",
            "libelleVoie": "DE LA REPUBLIQUE",
        }
        assert build_address_sirene(row) == "12 RUE DE LA REPUBLIQUE"

    def test_no_housenumber(self):
        from sirene_sync.normalize import build_address_sirene

        row = {
            "numeroVoie": None,
            "typeVoie": "AVENUE",
            "libelleVoie": "VICTOR HUGO",
        }
        assert build_address_sirene(row) == "AVENUE VICTOR HUGO"

    def test_all_none_returns_none(self):
        from sirene_sync.normalize import build_address_sirene

        row = {
            "numeroVoie": None,
            "typeVoie": None,
            "libelleVoie": None,
        }
        assert build_address_sirene(row) is None

    def test_empty_strings_treated_as_none(self):
        from sirene_sync.normalize import build_address_sirene

        row = {
            "numeroVoie": "",
            "typeVoie": "",
            "libelleVoie": "",
        }
        assert build_address_sirene(row) is None
