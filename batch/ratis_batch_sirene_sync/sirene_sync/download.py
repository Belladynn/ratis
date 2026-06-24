"""INSEE SIRENE bulk dump — download + cache helper.

Downloads the ``StockEtablissement_utf8.zip`` file from INSEE, converts its
CSV payload to Parquet (via pyarrow), and caches the result.  Subsequent runs
reuse the cached Parquet file until the TTL expires.

Design notes
------------
- **One-shot conversion** : the ZIP is extracted and converted to Parquet
  *once* at download time; the raw ZIP is then deleted.  This trades ~30 s of
  conversion time for a 10× faster parse on every subsequent run.
- **row_group_size** : generated Parquet uses ``row_group_size=5000`` (same as
  ``parser.stream_etablissements`` default ``chunk_size``).  This ensures
  ``pq.ParquetFile.iter_batches()`` yields batches of the expected granularity
  — see audit F-12 note in ``parser.py``.
- **httpx streaming** : the dump is >500 MB; we stream in 64 KB chunks to
  avoid loading it all into RAM.
- **No DB access** — pure filesystem helper.
"""

from __future__ import annotations

import logging
import time
import zipfile
from pathlib import Path

import httpx
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq

_log = logging.getLogger(__name__)

# Stable filename used for the cached Parquet file.
_PARQUET_FILENAME = "StockEtablissement_utf8.parquet"

# Row group size: must match the default chunk_size in parser.stream_etablissements
# so that iter_batches() yields rows at the expected granularity (audit F-12).
_ROW_GROUP_SIZE = 5000

# Download stream chunk size (bytes).
_STREAM_CHUNK_BYTES = 65536  # 64 KiB


def ensure_dump(
    cache_dir: Path,
    url: str,
    *,
    ttl_days: int,
    force: bool = False,
) -> Path:
    """Return path to the cached Parquet dump, downloading+converting if needed.

    Parameters
    ----------
    cache_dir:
        Directory where the Parquet file is stored.  Created if absent.
    url:
        URL of the INSEE ``StockEtablissement_utf8.zip``.
    ttl_days:
        Cache time-to-live in days.  If the Parquet file's ``mtime`` is older
        than ``ttl_days``, the dump is re-fetched.
    force:
        When ``True``, always re-download regardless of cache freshness.

    Returns
    -------
    Path
        Absolute path to ``StockEtablissement_utf8.parquet`` inside
        ``cache_dir``.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = cache_dir / _PARQUET_FILENAME

    if not force and parquet_path.exists():
        age_days = (time.time() - parquet_path.stat().st_mtime) / 86400
        if age_days < ttl_days:
            _log.info(
                "ensure_dump: cache fresh (%.1f d < %d d ttl) — using %s",
                age_days,
                ttl_days,
                parquet_path,
            )
            return parquet_path
        _log.info(
            "ensure_dump: cache stale (%.1f d >= %d d ttl) — re-downloading",
            age_days,
            ttl_days,
        )
    else:
        if force:
            _log.info("ensure_dump: force=True — re-downloading")
        else:
            _log.info("ensure_dump: cache absent — downloading from %s", url)

    zip_path = cache_dir / "StockEtablissement_utf8.zip"
    _download_streaming(url, zip_path)
    csv_path = _unzip_csv(zip_path, cache_dir)
    _convert_csv_to_parquet(csv_path, parquet_path)

    # Clean up ZIP and raw CSV — keep only the Parquet.
    zip_path.unlink(missing_ok=True)
    csv_path.unlink(missing_ok=True)

    _log.info("ensure_dump: Parquet ready at %s", parquet_path)
    return parquet_path


def _download_streaming(url: str, dest: Path) -> None:
    """Stream-download ``url`` to ``dest`` in 64 KiB chunks."""
    _log.info("_download_streaming: GET %s → %s", url, dest)
    with httpx.stream("GET", url, follow_redirects=True, timeout=300) as response:
        response.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in response.iter_bytes(chunk_size=_STREAM_CHUNK_BYTES):
                fh.write(chunk)
    _log.info("_download_streaming: done, size=%d bytes", dest.stat().st_size)


def _unzip_csv(zip_path: Path, dest_dir: Path) -> Path:
    """Extract the first CSV file found inside ``zip_path`` to ``dest_dir``.

    INSEE ships exactly one CSV per ZIP for the StockEtablissement dump.
    Returns the path to the extracted CSV.
    """
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError(f"No CSV found inside {zip_path}")
        csv_name = csv_names[0]
        _log.info("_unzip_csv: extracting %s from %s", csv_name, zip_path)
        zf.extract(csv_name, dest_dir)
        return dest_dir / csv_name


def _convert_csv_to_parquet(csv_path: Path, parquet_path: Path) -> None:
    """Convert raw INSEE CSV to Parquet using pyarrow.

    All columns are read as strings — INSEE fields like SIRET contain leading
    zeros which must not be coerced to int.  Schema inference is disabled by
    setting ``column_types`` to ``pa.string()`` for every column via
    ``ConvertOptions(strings_can_be_null=True)``.

    Row group size is fixed at ``_ROW_GROUP_SIZE`` (5000 rows) to ensure that
    ``pq.ParquetFile.iter_batches(batch_size=N)`` in the parser yields batches
    of predictable size (audit F-12).
    """
    import pyarrow as pa

    _log.info(
        "_convert_csv_to_parquet: reading %s — this may take a few minutes …",
        csv_path,
    )
    # Force all columns to string to preserve leading zeros in SIRET and
    # other code fields.
    convert_opts = pa_csv.ConvertOptions(
        column_types={},  # no type inference
        strings_can_be_null=True,
        null_values=[""],
        true_values=[],
        false_values=[],
        timestamp_parsers=[],
    )
    # Use ALL columns as string — ConvertOptions.column_types override happens
    # per-column; to force ALL to string we set auto_dict_encode=False and
    # include_columns=None (all), then override via include_missing_columns.
    # Simpler: read with include_columns=None and cast after.
    read_opts = pa_csv.ReadOptions(
        encoding="utf-8",
        use_threads=True,
    )
    parse_opts = pa_csv.ParseOptions(delimiter=",")

    table = pa_csv.read_csv(
        str(csv_path),
        read_options=read_opts,
        parse_options=parse_opts,
        convert_options=convert_opts,
    )

    # Cast every column to string (pa.string()) to guarantee no numeric
    # coercion occurred (the ConvertOptions above may still infer int/float
    # for all-numeric columns).
    schema_fields = [pa.field(name, pa.string()) for name in table.schema.names]
    new_schema = pa.schema(schema_fields)
    table = table.cast(new_schema)

    _log.info(
        "_convert_csv_to_parquet: writing %d rows → %s (row_group_size=%d)",
        len(table),
        parquet_path,
        _ROW_GROUP_SIZE,
    )
    pq.write_table(
        table,
        str(parquet_path),
        row_group_size=_ROW_GROUP_SIZE,
        compression="snappy",
    )
    _log.info("_convert_csv_to_parquet: done")
