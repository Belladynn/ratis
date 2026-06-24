---
name: sqlite-concurrent-writer-resilience
description: "Use when many parallel workers write to one SQLite DB (scraper/ETL job queue) and you hit 'database is locked' — applies WAL + busy_timeout + BEGIN IMMEDIATE retry + commit-per-row + crash-safe finally."
---

# sqlite-concurrent-writer-resilience

When several workers, threads, or processes write to a single SQLite
file, intermittent `database is locked` errors appear under load and can
corrupt a run or lose already-collected data on a crash. This skill
applies the standard hardening stack so a concurrent SQLite pipeline
survives contention and Ctrl+C / kill -9.

This pattern is **SQLite-specific** — Postgres/MySQL have their own MVCC
and locking and gain nothing from it.

## When to Use

- Multiple workers/threads/processes write to **the same SQLite file**
  (local job queue, scraper, one-shot ETL).
- Symptom : intermittent `sqlite3.OperationalError: database is locked`
  under concurrency.
- Requirement : lose no already-collected data even on Ctrl+C / kill -9.

## When NOT to Use

- A server DB (Postgres/MySQL) — different lock model; this pattern is
  meaningless and the retry/WAL overhead is wasted.
- Single-writer code with no concurrency — the WAL/retry machinery is
  pure overhead for no benefit.
- Sustained high-throughput writes (millions/s) where SQLite is the
  wrong tool — migrate to Postgres instead of hardening SQLite further.

## Procedure

1. **Enable WAL at open** : `PRAGMA journal_mode=WAL;` — lets readers run
   concurrently with a writer and cuts lock contention.
2. **Set a busy_timeout** : `PRAGMA busy_timeout=5000;` (or
   `sqlite3.connect(..., timeout=5)`) so the driver waits instead of
   raising "locked" immediately.
3. **Use short `BEGIN IMMEDIATE` transactions for writes** : take the
   write-lock up front to avoid the deferred→exclusive upgrade deadlock;
   keep each transaction as short as possible.
4. **Commit per row (or per small batch)** : never hold one transaction
   open across a whole job loop. Each committed observation isolates loss
   on a crash (cf KP-24: external service OK then DB crash = orphan).
5. **Retry loop around the insert** : on `OperationalError: database is
   locked`, mark the job `retryable=True` and re-attempt with short
   backoff. Distinguish **permanent** (schema/constraint) from
   **retryable** (lock) — only retry the latter.
6. **Crash-safety in `finally`** : commit/flush collected data in a
   `finally` block and let WAL auto-recover the in-flight job on kill -9.
   `TRUNCATE`/reset the work queue only after a confirmed commit.
7. **Use an absolute DB path** (`/.../drive_idf.db`), never relative to
   CWD, to avoid creating a ghost DB depending on the launch directory.
8. **Stagger worker startup** with a small increasing per-worker delay to
   avoid a thundering-herd lock storm at boot.
