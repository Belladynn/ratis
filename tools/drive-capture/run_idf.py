#!/usr/bin/env python3
"""Lance le scraping IDF en parallèle pour Carrefour, Auchan, Système U, Monoprix.

Usage:
    SCRAPE_DO_API_KEY=xxx uv run python run_idf.py [--workers 10] [--db drive_idf.db]
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ---------------------------------------------------------------------------
# Setup path so scraper + parser packages resolve from this directory
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))

from parser.db import connect, init_schema
from scraper.job_queue import (
    enqueue_store_sweep,
    init_queue,
    queue_stats,
)
from scraper.runner import run_one

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_idf")

# ---------------------------------------------------------------------------
# Graceful shutdown — set by SIGINT/SIGTERM, checked by workers
# ---------------------------------------------------------------------------
_STOP_EVENT = threading.Event()


def _install_signal_handlers() -> None:
    """On SIGINT (Ctrl+C) or SIGTERM: set stop flag, let workers finish current job."""
    def _handler(signum, frame):
        if not _STOP_EVENT.is_set():
            logger.warning("Signal %d reçu — arrêt gracieux en cours…", signum)
            _STOP_EVENT.set()
        else:
            # Second signal = force quit
            logger.warning("Second signal — sortie forcée")
            sys.exit(1)
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def _checkpoint(db_path: str) -> None:
    """Force WAL checkpoint so all committed data lands in the main DB file."""
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        logger.info("WAL checkpoint OK — données sauvegardées dans %s", db_path)
    except Exception as exc:
        logger.error("WAL checkpoint échoué : %s", exc)

# ---------------------------------------------------------------------------
# IDF cities (15 points couvrant les 8 depts IDF)
# ---------------------------------------------------------------------------
IDF_DEPTS = {"75", "77", "78", "91", "92", "93", "94", "95"}

ENSEIGNES = ["carrefour", "auchan", "systeme_u", "monoprix"]


def load_idf_cities(geo_path: Path) -> list[dict]:
    with geo_path.open() as f:
        data = json.load(f)
    cities = data["cities"] if isinstance(data, dict) else data
    return [c for c in cities if str(c.get("dept", "")) in IDF_DEPTS]


def worker(db_path: str, worker_id: int) -> int:
    """Worker thread: ouvre ses propres connexions et tourne jusqu'à queue vide."""
    thread_name = f"W{worker_id:02d}"
    import threading
    threading.current_thread().name = thread_name

    # Stagger startup to avoid thundering herd on SQLite BEGIN IMMEDIATE
    time.sleep(worker_id * 0.3)

    queue_conn = init_queue(db_path)
    db_conn = connect(db_path)

    processed = 0
    idle_streak = 0
    while not _STOP_EVENT.is_set():
        try:
            did_work = run_one(queue_conn, db_conn)
        except Exception as exc:
            logger.warning("%s: exception dans run_one (continuée) — %s", thread_name, exc)
            idle_streak += 1
            if idle_streak >= 5:
                # Check if there are actually pending jobs before giving up
                try:
                    total_pending = queue_conn.execute(
                        "SELECT COUNT(*) FROM scrape_jobs WHERE status='pending'"
                    ).fetchone()[0]
                    if total_pending == 0:
                        break
                    idle_streak = 0  # Jobs exist, errors are transient — keep going
                except Exception:
                    break
            time.sleep(1.0)
            continue
        if did_work:
            processed += 1
            idle_streak = 0
        else:
            idle_streak += 1
            if idle_streak >= 3:
                # Only exit when the queue is truly drained.
                # If pending jobs remain, another enseigne is at its concurrency cap
                # or BEGIN IMMEDIATE is congested — reset and keep trying.
                try:
                    total_pending = queue_conn.execute(
                        "SELECT COUNT(*) FROM scrape_jobs WHERE status='pending'"
                    ).fetchone()[0]
                except Exception:
                    total_pending = 0
                if total_pending == 0:
                    break
                idle_streak = 0  # Jobs exist but currently unclaimed — keep trying
            time.sleep(0.5)

    queue_conn.close()
    db_conn.close()
    logger.info("%s terminé — %d jobs traités", thread_name, processed)
    return processed


def monitor(db_path: str, interval: int = 30) -> None:
    """Thread de monitoring — affiche les stats toutes les N secondes."""
    import threading
    threading.current_thread().name = "monitor"
    while True:
        time.sleep(interval)
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            # Stats queue
            rows = conn.execute(
                "SELECT enseigne, phase, status, COUNT(*) as n "
                "FROM scrape_jobs GROUP BY enseigne, phase, status ORDER BY enseigne, phase, status"
            ).fetchall()
            # Stats observations
            obs = conn.execute(
                "SELECT enseigne, COUNT(*) FROM observations GROUP BY enseigne"
            ).fetchall()
            conn.close()

            logger.info("── Queue ─────────────────────────────────────")
            for enseigne, phase, status, n in rows:
                logger.info("  %-12s %-8s %-8s %d", enseigne, phase, status, n)
            logger.info("── Observations ──────────────────────────────")
            for enseigne, n in obs:
                logger.info("  %-12s %d produits", enseigne, n)
        except Exception as e:
            logger.debug("monitor: %s", e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Drive scraper IDF — 10 workers")
    parser.add_argument("--db", default="drive_idf.db", help="Chemin SQLite")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="Ne pas re-seeder (reprend un run existant)",
    )
    parser.add_argument(
        "--geo",
        default=str(Path(__file__).parent.parent.parent.parent / "tools/drive-capture/geo_reference_cities.json"),
        help="Fichier geo_reference_cities.json",
    )
    args = parser.parse_args()

    db_path = str(Path(args.db).resolve())  # chemin absolu — évite ambiguïté selon CWD
    _install_signal_handlers()

    # --- Init DB ---
    # init_queue takes a path string and returns a connection
    queue_conn = init_queue(db_path)
    queue_conn.execute("PRAGMA journal_mode=WAL")
    queue_conn.execute("PRAGMA busy_timeout=10000")

    db_conn = connect(db_path)
    db_conn.execute("PRAGMA journal_mode=WAL")
    init_schema(db_conn)
    db_conn.close()

    # --- Seed ---
    if not args.skip_seed:
        idf_cities = load_idf_cities(Path(args.geo))
        logger.info("IDF: %d points géographiques", len(idf_cities))

        total_seeded = 0
        for enseigne in ENSEIGNES:
            n = enqueue_store_sweep(queue_conn, enseigne, cities=idf_cities)
            logger.info("Seeded %d store-sweep jobs pour %s", n, enseigne)
            total_seeded += n
        logger.info("Total seeded: %d jobs store-sweep", total_seeded)
    else:
        logger.info("--skip-seed: reprise du run existant")

    stats = queue_stats(queue_conn)
    logger.info("Queue initiale: %s", stats)
    queue_conn.close()

    # --- Monitoring thread ---
    import threading
    mon = threading.Thread(target=monitor, args=(db_path,), daemon=True)
    mon.start()

    # --- Workers parallèles (phase unique — les workers restent actifs jusqu'à queue vide) ---
    # Les workers qui finissent leurs store-sweep jobs continuent sur les rayons/fiches
    # enqueués par les autres workers, sans gap de concurrence entre phases.
    logger.info("Lancement de %d workers…", args.workers)
    t0 = time.time()

    total_processed = 0
    try:
        with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="W") as pool:
            futures = {pool.submit(worker, db_path, i): i for i in range(args.workers)}
            for fut in as_completed(futures):
                w_id = futures[fut]
                try:
                    n = fut.result()
                    total_processed += n
                except Exception as e:
                    logger.error("Worker %d: exception — %s", w_id, e)
    finally:
        # Checkpoint WAL → main DB file, même si on a été interrompu
        _checkpoint(db_path)

    if _STOP_EVENT.is_set():
        logger.warning("Run interrompu proprement — données sauvegardées dans %s", db_path)
    logger.info("Run terminé en %.1fs — %d jobs traités", time.time() - t0, total_processed)

    # --- Bilan final ---
    conn = sqlite3.connect(db_path)
    obs_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    store_count = conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
    by_enseigne = conn.execute(
        "SELECT enseigne, COUNT(*) FROM observations GROUP BY enseigne"
    ).fetchall()
    errors = conn.execute(
        "SELECT enseigne, COUNT(*) FROM scrape_jobs WHERE status='error' GROUP BY enseigne"
    ).fetchall()
    conn.close()

    elapsed = time.time() - t0
    print("\n" + "=" * 60)
    print(f"RUN IDF TERMINÉ en {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"Stores: {store_count} | Observations: {obs_count}")
    print("\nPar enseigne:")
    for enseigne, n in by_enseigne:
        print(f"  {enseigne:12s} {n:6d} produits")
    if errors:
        print("\nErreurs:")
        for enseigne, n in errors:
            print(f"  {enseigne:12s} {n:6d} erreurs")
    print(f"\nDB: {db_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
