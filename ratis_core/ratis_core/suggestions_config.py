"""Curated suggestions config loader.

Loads ``ratis_core/config/curated_suggestions_fr.json`` once at first call,
caches the result in-process via ``functools.lru_cache``. The JSON file is a
flat array of EAN strings ordered by importance — the service that consumes
it (``ratis_product_analyser.services.suggestions_service``) uses the order
when topping up tier (c) results with the curated filler.

Fail-fast at load time : missing file / invalid JSON / non-array shape all
raise ``RuntimeError``. The expectation is that the route layer triggers
this loader at FastAPI lifespan startup (so the service refuses to boot if
the config is malformed, surfacing the bug at deploy time rather than at
first request).
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

# Resolved relative to this module : ratis_core/ratis_core/suggestions_config.py
# → ratis_core/ratis_core/config/curated_suggestions_fr.json. Mirrors the layout
# used by ``ratis_core.settings`` (which loads ``config/ratis_settings.json``
# via ``importlib.resources``). We use a plain ``Path`` here so tests can
# monkeypatch ``_CONFIG_PATH`` to a tmp file.
_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config" / "curated_suggestions_fr.json"


@functools.lru_cache(maxsize=1)
def load_curated_eans() -> list[str]:
    """Return the ordered list of curated EANs. Raises ``RuntimeError`` if
    the config is missing or malformed."""
    if not _CONFIG_PATH.exists():
        raise RuntimeError(f"curated_suggestions config missing : {_CONFIG_PATH}")
    try:
        with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"curated_suggestions config invalid JSON : {e}") from e

    if not isinstance(data, list):
        raise RuntimeError(f"curated_suggestions config must be a JSON array, got {type(data).__name__}")
    if not all(isinstance(e, str) for e in data):
        raise RuntimeError("curated_suggestions config must contain only strings")
    return data


# Public re-export so the test fixture can introspect / clear it.
_CACHE = load_curated_eans
