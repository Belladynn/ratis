"""Tests for off_sync.sources — Source registry."""

import pytest
from off_sync.sources import SOURCES, get_source


def test_registry_contains_all_four_sources():
    assert set(SOURCES.keys()) == {"off", "obp", "opf", "opff"}


def test_off_source_preserves_current_behaviour():
    s = get_source("off")
    assert s.name == "off"
    assert s.batch_name == "off_sync"
    assert s.api_base_url == "https://world.openfoodfacts.org"
    assert s.dump_url.endswith("openfoodfacts-products.jsonl.gz")
    assert "openfoodfacts.org" in s.user_agent or "Ratis" in s.user_agent
    assert "images.openfoodfacts.org" in s.photo_hosts


def test_obp_source_definition():
    s = get_source("obp")
    assert s.name == "obp"
    assert s.batch_name == "obp_sync"
    assert s.api_base_url == "https://world.openbeautyfacts.org"
    assert s.dump_url == "https://static.openbeautyfacts.org/data/openbeautyfacts-products.jsonl.gz"
    assert "images.openbeautyfacts.org" in s.photo_hosts


def test_opf_source_definition():
    s = get_source("opf")
    assert s.name == "opf"
    assert s.batch_name == "opf_sync"
    assert s.api_base_url == "https://world.openproductsfacts.org"
    assert s.dump_url == "https://static.openproductsfacts.org/data/openproductsfacts-products.jsonl.gz"
    assert "images.openproductsfacts.org" in s.photo_hosts


def test_opff_source_definition():
    s = get_source("opff")
    assert s.name == "opff"
    assert s.batch_name == "opff_sync"
    assert s.api_base_url == "https://world.openpetfoodfacts.org"
    assert s.dump_url == "https://static.openpetfoodfacts.org/data/openpetfoodfacts-products.jsonl.gz"
    assert "images.openpetfoodfacts.org" in s.photo_hosts


def test_get_source_unknown_raises():
    with pytest.raises(KeyError):
        get_source("gs1")


def test_source_is_hashable_and_picklable():
    """Workers in dump.py receive the Source via ProcessPoolExecutor — must pickle."""
    import pickle

    s = get_source("off")
    blob = pickle.dumps(s)
    s2 = pickle.loads(blob)
    assert s2 == s
    assert s2.api_base_url == s.api_base_url


def test_source_is_immutable():
    """Source dataclass is frozen so registry entries cannot be mutated in-place."""
    import dataclasses

    s = get_source("off")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.api_base_url = "https://hijack.example"  # type: ignore[misc]


# ── classify_storage flag (food-only classification) ──────────────────────────


def test_off_source_has_storage_classification_enabled():
    """OFF carries food → storage_type classifier MUST run."""
    assert get_source("off").classify_storage is True


def test_non_food_sources_skip_storage_classification():
    """OBP/OPF/OPFF are non-food catalogues → classifier MUST be skipped.

    Plan PR2 § DA-08 : `storage_type` toujours NULL pour cosmétiques.
    """
    for name in ("obp", "opf", "opff"):
        assert get_source(name).classify_storage is False, (
            f"Source {name!r} must have classify_storage=False (non-food)"
        )
