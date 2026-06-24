"""Pure tests for the seed_data module — no DB required."""

from __future__ import annotations

import re

import pytest
from seed_data import SEED_DATA, _make_ean, build_seed_data

EAN_RE = re.compile(r"^\d{8,14}$")
INTERNAL_PREFIX_RE = re.compile(r"^2")
ALLOWED_CATEGORIES = {"FRUITS", "LEGUMES", "EPICERIE"}
ALLOWED_UNITS = {"kg", "l", "unit"}


class TestSeedDataShape:
    def test_seed_data_has_expected_count(self):
        """V1 ships between 50 and 100 entries — sanity bound."""
        assert 50 <= len(SEED_DATA) <= 100

    def test_seed_data_eans_unique(self):
        eans = [e["ean"] for e in SEED_DATA]
        assert len(eans) == len(set(eans)), "duplicate EAN in seed list"

    def test_seed_data_names_unique(self):
        names = [e["name"] for e in SEED_DATA]
        assert len(names) == len(set(names)), "duplicate name in seed list"

    def test_every_entry_matches_db_constraints(self):
        """Each entry must satisfy products table CHECK constraints."""
        for entry in SEED_DATA:
            assert EAN_RE.match(entry["ean"]), f"EAN format invalid : {entry['ean']}"
            assert INTERNAL_PREFIX_RE.match(entry["ean"]), (
                f"internal source requires EAN starting with '2' : {entry['ean']}"
            )
            assert entry["name"], "name must be non-empty"
            assert entry["unit"] in ALLOWED_UNITS, f"unit invalid : {entry['unit']}"
            assert entry["category"] in ALLOWED_CATEGORIES

    def test_seed_data_is_stable(self):
        """build_seed_data() is deterministic — re-running yields identical EANs."""
        a = build_seed_data()
        b = build_seed_data()
        assert [e["ean"] for e in a] == [e["ean"] for e in b]


class TestEanGenerator:
    def test_make_ean_zero_pads_to_thirteen_digits(self):
        assert _make_ean(1) == "2999000000001"
        assert _make_ean(42) == "2999000000042"
        assert _make_ean(999) == "2999000000999"

    def test_make_ean_rejects_zero(self):
        with pytest.raises(ValueError, match="seq out of range"):
            _make_ean(0)

    def test_make_ean_rejects_above_999(self):
        with pytest.raises(ValueError, match="seq out of range"):
            _make_ean(1000)
