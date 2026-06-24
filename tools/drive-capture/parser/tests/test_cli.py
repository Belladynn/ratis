"""CLI auto-discovery and end-to-end smoke tests.

Verifies that enseigne parsers are loaded by name (drop-in, no central
registry) and that the logging instrumentation does not break the pipeline.
"""

import logging
from pathlib import Path

import pytest

from parser.__main__ import available_enseignes, load_enseigne, main

_CAPTURE = Path("/Users/guillaume/Cursor/Ratis/tools/drive-capture/captures/20260516_101440/www.carrefour.fr.ndjson")


def test_carrefour_is_auto_discovered():
    assert "carrefour" in available_enseignes()


def test_load_enseigne_returns_module_with_interface():
    module = load_enseigne("carrefour")
    assert callable(module.parse_products)
    assert callable(module.parse_stores)


def test_load_unknown_enseigne_raises():
    with pytest.raises(ModuleNotFoundError):
        load_enseigne("definitely_not_a_real_enseigne")


def test_main_unknown_enseigne_returns_error():
    assert main(["nope", "whatever.ndjson", "--db", "/tmp/unused.db"]) == 1


def test_main_missing_capture_returns_error():
    assert main(["carrefour", "/no/such/file.ndjson", "--db", "/tmp/unused.db"]) == 1


@pytest.mark.skipif(not _CAPTURE.exists(), reason="capture sample not present")
def test_main_end_to_end_with_logging(tmp_path, caplog):
    db_path = tmp_path / "drive_prices.db"
    with caplog.at_level(logging.INFO):
        rc = main(["carrefour", str(_CAPTURE), "--db", str(db_path)])
    assert rc == 0
    assert db_path.exists()
    # logging instrumentation emitted along the route
    messages = " ".join(r.message for r in caplog.records)
    assert "parse capture" in messages
    assert "NDJSON response" in messages
    assert "ParsedProduct extraits" in messages
    assert "observations" in messages
    assert "magasins" in messages
