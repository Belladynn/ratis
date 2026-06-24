"""Franprix drive parser.

Franprix runs the Casino-group ``catalog-api`` backend (see
``_catalog_api`` for the full format description). This module is a thin
wrapper: it binds the enseigne name and delegates the parsing.

Capture host: ``www.franprix.fr``.
"""

from __future__ import annotations

from collections.abc import Iterator

from parser.enseignes import _catalog_api
from parser.model import ParsedProduct, ParsedStore

ENSEIGNE = "franprix"


def parse_products(ndjson_path: str) -> Iterator[ParsedProduct]:
    """Yield every product observation in a Franprix capture file."""
    return _catalog_api.parse_products(ndjson_path, enseigne=ENSEIGNE)


def parse_stores(ndjson_path: str) -> Iterator[ParsedStore]:
    """Yield drive stores from a Franprix capture file."""
    return _catalog_api.parse_stores(ndjson_path, enseigne=ENSEIGNE)
