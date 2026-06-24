"""Full JSONL dump source — ProcessPoolExecutor, on-demand only.

Generic across the four Open*Facts projects (OFF / OBP / OPF / OPFF) — the
active project is encapsulated in the `Source` instance passed by the caller
(see `off_sync/sources.py`). The `Source` dataclass is frozen and picklable so
it can cross the ProcessPoolExecutor boundary verbatim.

Strategy:
  - Main process reads the (optionally gzipped) JSONL sequentially.
  - Lines are batched into CHUNK_SIZE chunks.
  - Each chunk is submitted to a worker process: parse JSON, filter France,
    extract fields, upsert into DB with its own connection.
  - Results (inserted/updated/skipped/invalid) are aggregated in the main process.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from concurrent.futures import Future, ProcessPoolExecutor, as_completed

from ratis_core.database import make_engine
from sqlalchemy.orm import sessionmaker

from off_sync.extractor import extract_product, is_france_product
from off_sync.repository import upsert_products
from off_sync.sources import Source
from off_sync.stats import Stats

log = logging.getLogger(__name__)

CHUNK_SIZE = 5_000  # lines per worker chunk


def _process_chunk(
    chunk_index: int,
    lines: list[str],
    db_url: str,
    dry_run: bool,
    source: Source,
) -> tuple[int, int, int, int]:
    """Worker function — runs in a subprocess.

    Parses JSON lines, filters France products, extracts fields, upserts.
    Returns (inserted, updated, skipped, invalid).
    """
    products = []
    invalid = 0

    for line in lines:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue

        if not is_france_product(raw):
            continue

        p = extract_product(raw, source=source)
        if p:
            products.append(p)
        else:
            invalid += 1

    engine = make_engine(db_url, pool_pre_ping=True)
    Session = sessionmaker(engine)
    try:
        with Session() as db:
            inserted, updated, skipped = upsert_products(db, products, source=source)
            if not dry_run:
                db.commit()
    finally:
        engine.dispose()

    return inserted, updated, skipped, invalid


def run_dump(
    dump_path: str,
    db_url: str,
    workers: int,
    dry_run: bool,
    timeout: int = 3600,
    *,
    source: Source,
) -> Stats:
    """Process a full JSONL (or .jsonl.gz) dump file for `source`.

    Args:
        dump_path: Path to the JSONL or JSONL.gz file.
        db_url: SQLAlchemy database URL passed to each worker.
        workers: Number of parallel worker processes.
        dry_run: Parse and validate without committing.
        timeout: Max total seconds to wait for all chunks. Raises TimeoutError if exceeded.
        source: Active `Source` registry entry — picklable and forwarded
            to each worker subprocess for source isolation in the upsert layer.
    """
    if not os.path.exists(dump_path):
        raise FileNotFoundError(f"Dump file not found: {dump_path}")

    open_fn = gzip.open if dump_path.endswith(".gz") else open
    stats = Stats()
    # futures maps future → (chunk_index, chunk_size) for failure accounting
    futures: dict[Future[tuple[int, int, int, int]], tuple[int, int]] = {}

    log.info("%s dump: reading %s with %d workers", source.name, dump_path, workers)

    with ProcessPoolExecutor(max_workers=workers) as executor:
        chunk: list[str] = []
        chunk_index = 0

        with open_fn(dump_path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                chunk.append(line)
                if len(chunk) >= CHUNK_SIZE:
                    fut = executor.submit(
                        _process_chunk,
                        chunk_index,
                        chunk,
                        db_url,
                        dry_run,
                        source,
                    )
                    futures[fut] = (chunk_index, len(chunk))
                    chunk_index += 1
                    chunk = []

        if chunk:
            fut = executor.submit(
                _process_chunk,
                chunk_index,
                chunk,
                db_url,
                dry_run,
                source,
            )
            futures[fut] = (chunk_index, len(chunk))

        total_chunks = len(futures)
        log.info("%s dump: %d chunks submitted", source.name, total_chunks)

        # as_completed(timeout=) raises TimeoutError when the deadline is reached, but
        # ProcessPoolExecutor.__exit__ still calls shutdown(wait=True) — workers already
        # running finish their current chunk before the process exits. Actual exit time is
        # timeout + duration of the longest in-flight chunk. No data loss (each chunk commits
        # independently). Full dumps are one-shot bootstrap ops, so this is acceptable.
        for done, future in enumerate(as_completed(futures, timeout=timeout), 1):
            idx, chunk_size = futures[future]
            try:
                stats.add(*future.result())
            except Exception as exc:
                log.error("%s dump: chunk %d failed — %s", source.name, idx, exc, exc_info=True)
                stats.add(0, 0, 0, chunk_size)

            if done % 10 == 0 or done == total_chunks:
                log.info("%s dump: %d/%d chunks done — %s", source.name, done, total_chunks, stats)

    log.info("%s dump done: %s", source.name, stats)
    return stats
