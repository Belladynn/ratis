"""Tests for job_queue.py: SQLite-backed scrape queue with dedup."""

from __future__ import annotations

import pytest

from scraper.job_queue import (
    enqueue,
    init_queue,
    mark_done,
    mark_error,
    next_job,
    queue_stats,
    retry_or_fail,
)


@pytest.fixture
def conn(tmp_path):
    """Fresh in-memory-ish SQLite queue in a temp file."""
    db = str(tmp_path / "test.db")
    c = init_queue(db)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------


def test_enqueue_insert(conn):
    inserted = enqueue(conn, "itm", "stores", "https://example.com/stores")
    assert inserted is True


def test_enqueue_dedup(conn):
    enqueue(conn, "itm", "stores", "https://example.com/stores")
    inserted = enqueue(conn, "itm", "stores", "https://example.com/stores")
    assert inserted is False


def test_enqueue_different_phase_not_dedup(conn):
    enqueue(conn, "itm", "stores", "https://example.com/stores")
    inserted = enqueue(conn, "itm", "rayons", "https://example.com/stores")
    assert inserted is True


def test_enqueue_different_enseigne_not_dedup(conn):
    enqueue(conn, "itm", "stores", "https://example.com/stores")
    inserted = enqueue(conn, "carrefour", "stores", "https://example.com/stores")
    assert inserted is True


def test_enqueue_with_payload(conn):
    payload = {"category_id": "123", "offset": 0}
    inserted = enqueue(conn, "carrefour", "rayons", "https://example.com/r", payload=payload)
    assert inserted is True


# ---------------------------------------------------------------------------
# next_job ordering: stores < rayons < fiches
# ---------------------------------------------------------------------------


def test_next_job_ordering_stores_before_fiches(conn):
    enqueue(conn, "itm", "fiches", "https://example.com/fiche/1")
    enqueue(conn, "itm", "stores", "https://example.com/stores")
    job = next_job(conn, "itm")
    assert job["phase"] == "stores"


def test_next_job_ordering_stores_before_rayons(conn):
    enqueue(conn, "itm", "rayons", "https://example.com/rayon/1")
    enqueue(conn, "itm", "stores", "https://example.com/stores")
    job = next_job(conn, "itm")
    assert job["phase"] == "stores"


def test_next_job_ordering_rayons_before_fiches(conn):
    enqueue(conn, "itm", "fiches", "https://example.com/fiche/1")
    enqueue(conn, "itm", "rayons", "https://example.com/rayon/1")
    # Consume the stores (none here) — get rayon before fiche
    job = next_job(conn, "itm")
    assert job["phase"] == "rayons"


# ---------------------------------------------------------------------------
# next_job atomic claim
# ---------------------------------------------------------------------------


def test_next_job_marks_running(conn):
    enqueue(conn, "itm", "stores", "https://example.com/stores")
    job = next_job(conn, "itm")
    assert job is not None
    # Second call: job is now running, no more pending
    assert next_job(conn, "itm") is None


def test_next_job_returns_none_when_empty(conn):
    assert next_job(conn, "itm") is None


def test_next_job_enseigne_filter(conn):
    enqueue(conn, "itm", "stores", "https://example.com/stores-itm")
    enqueue(conn, "carrefour", "stores", "https://example.com/stores-cf")
    job = next_job(conn, "carrefour")
    assert job["enseigne"] == "carrefour"


def test_next_job_payload_deserialized(conn):
    payload = {"category_id": "456", "offset": 20}
    enqueue(conn, "leclerc", "rayons", "https://example.com/r", payload=payload)
    job = next_job(conn, "leclerc")
    assert job["payload"] == payload


def test_next_job_sentinel_store_id_restored_to_none(conn):
    """store_id=None stored as '' internally must come back as None."""
    enqueue(conn, "itm", "stores", "https://example.com/stores", store_id=None)
    job = next_job(conn, "itm")
    assert job["store_id"] is None


# ---------------------------------------------------------------------------
# mark_done / mark_error
# ---------------------------------------------------------------------------


def test_mark_done(conn):
    enqueue(conn, "itm", "stores", "https://example.com/stores")
    job = next_job(conn, "itm")
    mark_done(conn, job["id"])
    stats = queue_stats(conn)
    assert stats["itm"]["stores:done"] == 1


def test_mark_error(conn):
    enqueue(conn, "itm", "stores", "https://example.com/stores")
    job = next_job(conn, "itm")
    mark_error(conn, job["id"], "HTTP 503")
    stats = queue_stats(conn)
    assert stats["itm"]["stores:error"] == 1


# ---------------------------------------------------------------------------
# queue_stats
# ---------------------------------------------------------------------------


def test_queue_stats_empty(conn):
    assert queue_stats(conn) == {}


def test_queue_stats_mixed(conn):
    enqueue(conn, "itm", "stores", "https://example.com/s1")
    enqueue(conn, "itm", "stores", "https://example.com/s2")
    job = next_job(conn, "itm")
    mark_done(conn, job["id"])
    enqueue(conn, "carrefour", "rayons", "https://example.com/r1")

    stats = queue_stats(conn)
    assert stats["itm"]["stores:done"] == 1
    assert stats["itm"]["stores:pending"] == 1
    assert stats["carrefour"]["rayons:pending"] == 1


# ---------------------------------------------------------------------------
# NULL sentinel dedup
# ---------------------------------------------------------------------------


def test_null_sentinel_dedup(conn):
    """store_id=None must dedup correctly (uses '' sentinel internally)."""
    enqueue(conn, "itm", "stores", "https://example.com/s", store_id=None)
    inserted = enqueue(conn, "itm", "stores", "https://example.com/s", store_id=None)
    assert inserted is False


def test_null_vs_empty_string_store_id_dedups(conn):
    """Passing store_id=None and store_id='' both map to '' sentinel → dedup."""
    enqueue(conn, "itm", "stores", "https://example.com/s", store_id=None)
    inserted = enqueue(conn, "itm", "stores", "https://example.com/s", store_id="")
    assert inserted is False


# ---------------------------------------------------------------------------
# max_per_enseigne concurrency cap
# ---------------------------------------------------------------------------


def test_max_per_enseigne_skips_saturated_enseigne(conn):
    """With cap=2 and 2 running itm jobs, next_job returns carrefour, not itm."""
    enqueue(conn, "itm", "stores", "https://example.com/itm/1")
    enqueue(conn, "itm", "stores", "https://example.com/itm/2")
    enqueue(conn, "itm", "stores", "https://example.com/itm/3")
    enqueue(conn, "carrefour", "stores", "https://example.com/cf/1")

    # Claim 2 itm jobs → both become running
    j1 = next_job(conn, max_per_enseigne=2)
    j2 = next_job(conn, max_per_enseigne=2)
    assert j1 is not None
    assert j1["enseigne"] == "itm"
    assert j2 is not None
    assert j2["enseigne"] == "itm"

    # itm is now at cap — next_job should skip it and return carrefour
    j3 = next_job(conn, max_per_enseigne=2)
    assert j3 is not None
    assert j3["enseigne"] == "carrefour"


def test_max_per_enseigne_slot_freed_after_done(conn):
    """After one running itm job is marked done, the 3rd itm job is claimable."""
    enqueue(conn, "itm", "stores", "https://example.com/itm/1")
    enqueue(conn, "itm", "stores", "https://example.com/itm/2")
    enqueue(conn, "itm", "stores", "https://example.com/itm/3")
    enqueue(conn, "carrefour", "stores", "https://example.com/cf/1")

    j1 = next_job(conn, max_per_enseigne=2)
    j2 = next_job(conn, max_per_enseigne=2)
    assert j1 is not None
    assert j2 is not None

    # Saturated — carrefour returned
    j3 = next_job(conn, max_per_enseigne=2)
    assert j3 is not None
    assert j3["enseigne"] == "carrefour"

    # Free one itm slot
    mark_done(conn, j1["id"])

    # Now itm slot is available — 3rd itm job can be claimed
    j4 = next_job(conn, max_per_enseigne=2)
    assert j4 is not None
    assert j4["enseigne"] == "itm"


def test_max_per_enseigne_default_does_not_affect_single_worker(conn):
    """Default max_per_enseigne=20 does not block normal single-worker usage."""
    for i in range(5):
        enqueue(conn, "itm", "stores", f"https://example.com/itm/{i}")

    claimed = []
    while True:
        job = next_job(conn)  # default max_per_enseigne=20
        if job is None:
            break
        claimed.append(job)

    assert len(claimed) == 5


# ---------------------------------------------------------------------------
# retry_or_fail
# ---------------------------------------------------------------------------


def test_retry_or_fail_requeues_on_transient(conn):
    """A transient failure requeues the job as pending up to max_retries times."""
    enqueue(conn, "itm", "stores", "https://example.com/stores")
    job = next_job(conn, "itm")
    job_id = job["id"]

    # First transient failure — requeued (retry_count=1, max_retries=3 default)
    requeued = retry_or_fail(conn, job_id, "HTTP 503", retryable=True)
    assert requeued is True
    row = conn.execute("SELECT status, retry_count FROM scrape_jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "pending"
    assert row["retry_count"] == 1

    # Claim again to get it running before next retry_or_fail
    job2 = next_job(conn, "itm")
    assert job2 is not None

    # Second transient failure — still requeued (retry_count=2)
    requeued = retry_or_fail(conn, job_id, "HTTP 503", retryable=True)
    assert requeued is True
    row = conn.execute("SELECT status, retry_count FROM scrape_jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "pending"
    assert row["retry_count"] == 2

    # Claim again
    job3 = next_job(conn, "itm")
    assert job3 is not None

    # Third transient failure — still requeued (retry_count=3, still < max_retries? No: 3 == max_retries=3)
    # Wait — logic: retry if retry_count < max_retries. At count=2 before this call:
    # retry_count=2 < max_retries=3 → requeue, count becomes 3
    requeued = retry_or_fail(conn, job_id, "HTTP 503", retryable=True)
    assert requeued is True
    row = conn.execute("SELECT status, retry_count FROM scrape_jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "pending"
    assert row["retry_count"] == 3

    # Claim one more time
    job4 = next_job(conn, "itm")
    assert job4 is not None

    # Fourth call — retry_count=3 == max_retries=3 → fail
    requeued = retry_or_fail(conn, job_id, "HTTP 503", retryable=True)
    assert requeued is False
    row = conn.execute("SELECT status, retry_count FROM scrape_jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "error"
    assert row["retry_count"] == 4


def test_retry_or_fail_fails_immediately_on_permanent(conn):
    """A permanent failure marks the job as error immediately."""
    enqueue(conn, "itm", "stores", "https://example.com/stores")
    job = next_job(conn, "itm")
    job_id = job["id"]

    requeued = retry_or_fail(conn, job_id, "HTTP 404", retryable=False)
    assert requeued is False
    row = conn.execute("SELECT status, error_msg FROM scrape_jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "error"
    assert "404" in row["error_msg"]


def test_retry_count_increments(conn):
    """retry_count goes 0→1→2→3 then status becomes error."""
    enqueue(conn, "itm", "stores", "https://example.com/stores")
    job = next_job(conn, "itm")
    job_id = job["id"]

    # Initial state
    row = conn.execute("SELECT retry_count, status FROM scrape_jobs WHERE id=?", (job_id,)).fetchone()
    assert row["retry_count"] == 0
    assert row["status"] == "running"

    # retry_count 0 → 1 (pending)
    retry_or_fail(conn, job_id, "timeout", retryable=True)
    row = conn.execute("SELECT retry_count, status FROM scrape_jobs WHERE id=?", (job_id,)).fetchone()
    assert row["retry_count"] == 1
    assert row["status"] == "pending"

    next_job(conn, "itm")  # claim again

    # retry_count 1 → 2 (pending)
    retry_or_fail(conn, job_id, "timeout", retryable=True)
    row = conn.execute("SELECT retry_count, status FROM scrape_jobs WHERE id=?", (job_id,)).fetchone()
    assert row["retry_count"] == 2
    assert row["status"] == "pending"

    next_job(conn, "itm")  # claim again

    # retry_count 2 → 3 (still pending, 3 < max_retries=3? No: 2 < 3 → requeue)
    retry_or_fail(conn, job_id, "timeout", retryable=True)
    row = conn.execute("SELECT retry_count, status FROM scrape_jobs WHERE id=?", (job_id,)).fetchone()
    assert row["retry_count"] == 3
    assert row["status"] == "pending"

    next_job(conn, "itm")  # claim again

    # retry_count 3 → 4 (error, 3 == max_retries → fail)
    retry_or_fail(conn, job_id, "timeout", retryable=True)
    row = conn.execute("SELECT retry_count, status FROM scrape_jobs WHERE id=?", (job_id,)).fetchone()
    assert row["retry_count"] == 4
    assert row["status"] == "error"


def test_init_queue_idempotent(tmp_path):
    """Calling init_queue twice on the same DB must not crash (ALTER TABLE idempotent)."""
    db = str(tmp_path / "test_idempotent.db")
    c1 = init_queue(db)
    c1.close()
    # Second call must not raise
    c2 = init_queue(db)
    c2.close()
