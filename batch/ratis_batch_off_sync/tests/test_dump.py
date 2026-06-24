"""Tests for off_sync.dump — ProcessPoolExecutor with real tmp files."""

import gzip
import json

import pytest
from off_sync.dump import CHUNK_SIZE, run_dump
from off_sync.sources import get_source
from sqlalchemy import text

_OFF = get_source("off")
_OBP = get_source("obp")


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_jsonl(products: list[dict], path: str, gzipped: bool = False) -> str:
    """Write a list of raw OFF dicts to a JSONL (or JSONL.gz) file."""
    lines = "\n".join(json.dumps(p) for p in products)
    if gzipped:
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(lines)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(lines)
    return path


def _france_raw(code: str, name: str = "Produit", photo: str | None = None) -> dict:
    return {
        "code": code,
        "product_name_fr": name,
        "image_front_url": photo,
        "countries_tags": ["en:france"],
    }


# ── basic insert ──────────────────────────────────────────────────────────────


def test_dump_inserts_france_product(db_url, direct_sessionmaker, tmp_path):
    ean = "3017620422100"
    path = _make_jsonl([_france_raw(ean)], str(tmp_path / "off.jsonl"))

    stats = run_dump(path, db_url, workers=1, dry_run=False, source=_OFF)

    assert stats.inserted == 1
    with direct_sessionmaker() as db:
        row = db.execute(text("SELECT name FROM products WHERE ean = :e"), {"e": ean}).one()
    assert row.name == "Produit"


def test_dump_reads_gzipped_file(db_url, tmp_path):
    ean = "3017620422101"
    path = _make_jsonl([_france_raw(ean)], str(tmp_path / "off.jsonl.gz"), gzipped=True)

    stats = run_dump(path, db_url, workers=1, dry_run=False, source=_OFF)

    assert stats.inserted == 1


# ── France filter ─────────────────────────────────────────────────────────────


def test_dump_skips_non_france_products(db_url, direct_sessionmaker, tmp_path):
    non_france = {
        "code": "3017620422102",
        "product_name_fr": "German",
        "countries_tags": ["en:germany"],
    }
    path = _make_jsonl([non_france], str(tmp_path / "off.jsonl"))

    stats = run_dump(path, db_url, workers=1, dry_run=False, source=_OFF)

    assert stats.inserted == 0
    with direct_sessionmaker() as db:
        count = db.execute(text("SELECT COUNT(*) FROM products WHERE ean = '3017620422102'")).scalar()
    assert count == 0


def test_dump_mixed_countries(db_url, tmp_path):
    """Only France product inserted when both are present."""
    france = _france_raw("3017620422103")
    non_france = {"code": "3017620422104", "product_name_fr": "UK", "countries_tags": ["en:united-kingdom"]}
    path = _make_jsonl([france, non_france], str(tmp_path / "off.jsonl"))

    stats = run_dump(path, db_url, workers=1, dry_run=False, source=_OFF)

    assert stats.inserted == 1


# ── dry run ───────────────────────────────────────────────────────────────────


def test_dump_dry_run_does_not_persist(db_url, direct_sessionmaker, tmp_path):
    ean = "3017620422110"
    path = _make_jsonl([_france_raw(ean)], str(tmp_path / "off.jsonl"))

    run_dump(path, db_url, workers=1, dry_run=True, source=_OFF)

    with direct_sessionmaker() as db:
        count = db.execute(text("SELECT COUNT(*) FROM products WHERE ean = :e"), {"e": ean}).scalar()
    assert count == 0


# ── invalid lines ─────────────────────────────────────────────────────────────


def test_dump_skips_invalid_json_lines(db_url, tmp_path):
    valid = _france_raw("3017620422120")
    path = str(tmp_path / "off.jsonl")
    with open(path, "w") as f:
        f.write("NOT_JSON\n")
        f.write(json.dumps(valid) + "\n")

    stats = run_dump(path, db_url, workers=1, dry_run=False, source=_OFF)

    assert stats.inserted == 1
    assert stats.invalid == 1


def test_dump_skips_invalid_ean(db_url, tmp_path):
    bad_ean = {"code": "NOTANEAN", "product_name_fr": "Bad", "countries_tags": ["en:france"]}
    path = _make_jsonl([bad_ean], str(tmp_path / "off.jsonl"))

    stats = run_dump(path, db_url, workers=1, dry_run=False, source=_OFF)

    assert stats.inserted == 0
    assert stats.invalid == 1


# ── chunking ──────────────────────────────────────────────────────────────────


def test_dump_processes_multiple_chunks(db_url, tmp_path):
    """More lines than CHUNK_SIZE → multiple workers are used."""
    products = [_france_raw(f"3017620{i:06d}") for i in range(CHUNK_SIZE + 1)]
    path = _make_jsonl(products, str(tmp_path / "off.jsonl"))

    stats = run_dump(path, db_url, workers=2, dry_run=False, source=_OFF)

    assert stats.inserted == len(products)


# ── deduplication ─────────────────────────────────────────────────────────────


def test_dump_deduplicates_same_ean_in_chunk(db_url, direct_sessionmaker, tmp_path):
    """Duplicate EAN in same chunk must not raise CardinalityViolation."""
    ean = "3017620422130"
    products = [
        _france_raw(ean, name="Version A"),
        _france_raw(ean, name="Version B"),
    ]
    path = _make_jsonl(products, str(tmp_path / "off.jsonl"))

    stats = run_dump(path, db_url, workers=1, dry_run=False, source=_OFF)

    assert stats.inserted == 1
    with direct_sessionmaker() as db:
        row = db.execute(text("SELECT name FROM products WHERE ean = :e"), {"e": ean}).one()
    assert row.name == "Version B"  # last occurrence wins


# ── file not found ────────────────────────────────────────────────────────────


def test_dump_raises_on_missing_file(db_url):
    with pytest.raises(FileNotFoundError):
        run_dump("/nonexistent/path/off.jsonl", db_url, workers=1, dry_run=False, source=_OFF)


# ── multi-source plumbing (Source crosses ProcessPoolExecutor boundary) ───────


def test_dump_writes_with_obp_source(db_url, direct_sessionmaker, tmp_path):
    """OBP `Source` is picklable across the worker boundary and flows into upsert."""
    ean = "3000000088888"
    path = _make_jsonl([_france_raw(ean, name="Crème OBP")], str(tmp_path / "obp.jsonl"))

    stats = run_dump(path, db_url, workers=1, dry_run=False, source=_OBP)

    assert stats.inserted == 1
    with direct_sessionmaker() as db:
        row = db.execute(text("SELECT name, source FROM products WHERE ean = :e"), {"e": ean}).one()
    assert row.name == "Crème OBP"
    assert row.source == "obp"
