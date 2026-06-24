"""CLI entry point for the drive-capture parser.

Usage::

    python -m parser <enseigne> <capture-ndjson> [--db drive_prices.db] [-v]

Parses a Phase-1 capture file into normalised price observations, loads
them into a standalone SQLite database, and also writes an intermediate
``<enseigne>.normalized.ndjson`` for human review.

Enseigne parsers are **drop-in**: each ``enseignes/<name>.py`` module
exposing ``parse_products`` / ``parse_stores`` is auto-discovered by name —
adding a retailer means dropping one file, no central registry to edit.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import logging
import pkgutil
import sys
from pathlib import Path

from parser import db, enseignes

logger = logging.getLogger(__name__)


def available_enseignes() -> list[str]:
    """Names of every drop-in parser module under ``parser.enseignes``.

    ``_``-prefixed modules are shared helpers (``_catalog_api``,
    ``_schemaorg``), not selectable retailers — excluded from the list.
    """
    return sorted(
        info.name
        for info in pkgutil.iter_modules(enseignes.__path__)
        if not info.ispkg and not info.name.startswith("_")
    )


def load_enseigne(name: str):
    """Import the ``enseignes.<name>`` parser module.

    Raises ``ModuleNotFoundError`` if no such module exists — or the name is
    a ``_``-prefixed shared helper, not a retailer — or ``AttributeError``
    if the module does not expose the standard interface.
    """
    if name.startswith("_"):
        raise ModuleNotFoundError(f"No module named 'parser.enseignes.{name}'")
    module = importlib.import_module(f"parser.enseignes.{name}")
    missing = [fn for fn in ("parse_products", "parse_stores") if not hasattr(module, fn)]
    if missing:
        raise AttributeError(
            f"enseigne module '{name}' is missing required function(s): "
            f"{', '.join(missing)}"
        )
    return module


def _configure_logging(verbose: bool) -> None:
    """Console logging — INFO by default, DEBUG with ``-v``."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def _write_normalized(path: Path, products, stores) -> None:
    """Dump parsed objects as an intermediate NDJSON file for review."""
    with open(path, "w", encoding="utf-8") as fh:
        for store in stores:
            row = {"_kind": "store", **dataclasses.asdict(store)}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        for product in products:
            row = {"_kind": "observation", **dataclasses.asdict(product)}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="parser",
        description="Parse a drive-capture NDJSON file into SQLite.",
    )
    parser.add_argument(
        "enseigne",
        help="retailer whose capture format to parse (drop-in module name)",
    )
    parser.add_argument("capture", help="path to the capture .ndjson file")
    parser.add_argument(
        "--db",
        default="drive_prices.db",
        help="SQLite database path (default: drive_prices.db)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable DEBUG-level logging",
    )
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    try:
        module = load_enseigne(args.enseigne)
    except ModuleNotFoundError:
        available = ", ".join(available_enseignes()) or "(none)"
        logger.error(
            "enseigne inconnue: '%s' — enseignes disponibles: %s",
            args.enseigne,
            available,
        )
        return 1
    except AttributeError as exc:
        logger.error("%s", exc)
        return 1

    capture_path = Path(args.capture)
    if not capture_path.exists():
        logger.error("capture file not found: %s", capture_path)
        return 1

    logger.info("parse capture %s (enseigne=%s)", capture_path, args.enseigne)

    products = list(module.parse_products(str(capture_path)))
    stores = list(module.parse_stores(str(capture_path)))

    n_obs, n_stores = db.load(args.db, products, stores)

    normalized_path = Path(f"{args.enseigne}.normalized.ndjson")
    _write_normalized(normalized_path, products, stores)
    logger.debug("normalized ndjson written to %s", normalized_path)

    logger.info(
        "terminé — enseigne=%s, %d observations, %d magasins, db=%s",
        args.enseigne,
        n_obs,
        n_stores,
        args.db,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
