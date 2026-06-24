"""Tests for ratis_batch_origins_backfill.runner — DB integration."""

from __future__ import annotations

import pytest
from origins_backfill.runner import (
    fetch_origins_tags,
    run_backfill,
)
from sqlalchemy import text

# ── Helpers ───────────────────────────────────────────────────────────


def _seed_product(
    session_factory,
    *,
    ean: str,
    source: str = "off",
    origins_tags: list[str] | None = None,
) -> None:
    """Seed a single product row in the test DB.

    ``source='internal'`` rows must declare a ``unit`` per the PG
    CHECK ``internal_has_unit`` — we default to ``kg`` for the test
    fixtures. EAN starting with ``2`` per the ``internal_ean_prefix``
    CHECK (caller responsibility).
    """
    # source='internal' has a per-CHECK requirement : unit IS NOT NULL.
    unit = "kg" if source == "internal" else None
    with session_factory() as db:
        db.execute(
            text(
                "INSERT INTO products (ean, name, source, unit, origins_tags, "
                "                       created_at, updated_at) "
                "VALUES (:ean, :name, :source, :unit, :origins, now(), now())"
            ),
            {
                "ean": ean,
                "name": f"P-{ean}",
                "source": source,
                "unit": unit,
                "origins": origins_tags,
            },
        )
        db.commit()


def _read_origins(session_factory, ean: str) -> list[str] | None:
    with session_factory() as db:
        row = db.execute(
            text("SELECT origins_tags FROM products WHERE ean = :ean"),
            {"ean": ean},
        ).one_or_none()
    if row is None:
        return None
    return list(row.origins_tags) if row.origins_tags is not None else None


# ── run_backfill — happy paths ────────────────────────────────────────


def test_run_backfill_updates_matching_eans(session_factory):
    """The batch fetches origins_tags for every NULL row and persists
    the result. Idempotent — re-runs skip the row (origins_tags NOT NULL)."""
    _seed_product(session_factory, ean="3017620499100")
    _seed_product(session_factory, ean="3017620499101")

    calls: list[str] = []

    def fake_fetch(ean: str):
        calls.append(ean)
        if ean == "3017620499100":
            return ("found", ["en:france"])
        return ("found", ["en:germany", "en:european-union"])

    stats = run_backfill(
        session_factory,
        fake_fetch,
        page_size=10,
        request_delay_sec=0,
        sleep=lambda _s: None,
    )

    assert stats.scanned == 2
    assert stats.updated == 2
    assert stats.errors == 0
    assert set(calls) == {"3017620499100", "3017620499101"}
    assert _read_origins(session_factory, "3017620499100") == ["en:france"]
    assert _read_origins(session_factory, "3017620499101") == [
        "en:germany",
        "en:european-union",
    ]


def test_run_backfill_is_idempotent_on_already_filled_rows(session_factory):
    """A row that already carries origins_tags must NOT be re-fetched."""
    _seed_product(session_factory, ean="3017620499110", origins_tags=["en:france"])
    calls: list[str] = []

    def fake_fetch(ean: str):
        calls.append(ean)
        return ("found", ["en:NEW"])

    stats = run_backfill(
        session_factory,
        fake_fetch,
        page_size=10,
        request_delay_sec=0,
        sleep=lambda _s: None,
    )

    assert stats.scanned == 0, "Idempotent : no row to backfill"
    assert calls == []
    # Existing value untouched.
    assert _read_origins(session_factory, "3017620499110") == ["en:france"]


def test_run_backfill_writes_empty_array_for_off_not_found(session_factory):
    """OFF returns status=0 → we write [] so the row drops out of the
    next pass. Sentinel value preventing infinite re-fetch loops."""
    _seed_product(session_factory, ean="3017620499120")

    def fake_fetch(ean: str):
        return ("not_found", None)

    stats = run_backfill(
        session_factory,
        fake_fetch,
        page_size=10,
        request_delay_sec=0,
        sleep=lambda _s: None,
    )

    assert stats.scanned == 1
    assert stats.not_found == 1
    assert stats.updated == 0
    assert _read_origins(session_factory, "3017620499120") == []


def test_run_backfill_writes_empty_array_for_no_origin_metadata(
    session_factory,
):
    """OFF returns the product but with origins_tags=[] (real-world
    common shape) → we write [] for the same reason."""
    _seed_product(session_factory, ean="3017620499130")

    def fake_fetch(ean: str):
        return ("found", [])

    stats = run_backfill(
        session_factory,
        fake_fetch,
        page_size=10,
        request_delay_sec=0,
        sleep=lambda _s: None,
    )

    assert stats.scanned == 1
    assert stats.empty_origins == 1
    assert stats.updated == 0
    assert _read_origins(session_factory, "3017620499130") == []


def test_run_backfill_handles_fetch_errors_without_aborting(session_factory):
    """A network error on one EAN must NOT abort the run — the row stays
    NULL (eligible for the next attempt) and the loop continues."""
    _seed_product(session_factory, ean="3017620499140")
    _seed_product(session_factory, ean="3017620499141")

    def fake_fetch(ean: str):
        if ean == "3017620499140":
            raise RuntimeError("simulated network failure")
        return ("found", ["en:france"])

    stats = run_backfill(
        session_factory,
        fake_fetch,
        page_size=10,
        request_delay_sec=0,
        sleep=lambda _s: None,
    )

    assert stats.scanned == 2
    assert stats.errors == 1
    assert stats.updated == 1
    # Failed EAN unchanged (still NULL — re-eligible on next run).
    assert _read_origins(session_factory, "3017620499140") is None
    # Successful EAN persisted.
    assert _read_origins(session_factory, "3017620499141") == ["en:france"]


def test_run_backfill_skips_non_off_source_by_default(session_factory):
    """Default ``only_off_source=True`` — internal / OBP / OPF / OPFF
    rows are skipped (no OFF entry to fetch)."""
    _seed_product(session_factory, ean="3017620499150", source="off")
    _seed_product(session_factory, ean="2000000099151", source="internal")
    _seed_product(session_factory, ean="3017620499152", source="obp")

    calls: list[str] = []

    def fake_fetch(ean: str):
        calls.append(ean)
        return ("found", ["en:france"])

    stats = run_backfill(
        session_factory,
        fake_fetch,
        page_size=10,
        request_delay_sec=0,
        sleep=lambda _s: None,
    )

    assert stats.scanned == 1
    assert calls == ["3017620499150"]
    assert _read_origins(session_factory, "3017620499150") == ["en:france"]
    # Other sources untouched.
    assert _read_origins(session_factory, "2000000099151") is None
    assert _read_origins(session_factory, "3017620499152") is None


def test_run_backfill_all_sources_flag_fetches_every_row(session_factory):
    """``only_off_source=False`` falls back to scanning every row,
    regardless of ``source``. Operator escape hatch."""
    _seed_product(session_factory, ean="3017620499160", source="off")
    _seed_product(session_factory, ean="2000000099161", source="internal")

    calls: list[str] = []

    def fake_fetch(ean: str):
        calls.append(ean)
        return ("found", ["en:france"])

    stats = run_backfill(
        session_factory,
        fake_fetch,
        page_size=10,
        request_delay_sec=0,
        only_off_source=False,
        sleep=lambda _s: None,
    )

    assert stats.scanned == 2
    assert set(calls) == {"3017620499160", "2000000099161"}


def test_run_backfill_respects_max_eans(session_factory):
    """``max_eans`` bounds the run for smoke tests."""
    for i in range(5):
        _seed_product(session_factory, ean=f"301762049917{i}")

    def fake_fetch(ean: str):
        return ("found", ["en:france"])

    stats = run_backfill(
        session_factory,
        fake_fetch,
        page_size=10,
        request_delay_sec=0,
        max_eans=3,
        sleep=lambda _s: None,
    )

    assert stats.scanned == 3
    # The 3 picked EANs should have been written ; the remaining 2 stay NULL.
    null_count_after = 0
    with session_factory() as db:
        null_count_after = db.execute(
            text(
                "SELECT COUNT(*) FROM products WHERE origins_tags IS NULL "
                "AND ean LIKE '301762049917%' AND source = 'off'"
            )
        ).scalar()
    assert null_count_after == 2


def test_run_backfill_paginates_across_many_rows(session_factory):
    """When the unfilled set is larger than page_size, multiple pages
    run in sequence — each writes its rows and the next SELECT sees
    only the remaining NULL rows."""
    for i in range(7):
        _seed_product(session_factory, ean=f"301762049920{i}")

    def fake_fetch(ean: str):
        return ("found", ["en:france"])

    stats = run_backfill(
        session_factory,
        fake_fetch,
        page_size=3,  # 7 rows / 3 = 3 pages (3 + 3 + 1)
        request_delay_sec=0,
        sleep=lambda _s: None,
    )

    assert stats.scanned == 7
    assert stats.updated == 7
    assert stats.pages_processed == 3


def test_run_backfill_rate_limit_is_observed(session_factory):
    """The configured ``request_delay_sec`` must be passed to the sleep
    callable — once per EAN scanned (success or error)."""
    _seed_product(session_factory, ean="3017620499300")
    _seed_product(session_factory, ean="3017620499301")
    sleep_calls: list[float] = []

    def fake_fetch(ean: str):
        return ("found", ["en:france"])

    stats = run_backfill(
        session_factory,
        fake_fetch,
        page_size=10,
        request_delay_sec=0.42,
        sleep=lambda s: sleep_calls.append(s),
    )

    assert stats.scanned == 2
    assert sleep_calls == [0.42, 0.42]


# ── fetch_origins_tags — http path ────────────────────────────────────


def test_fetch_origins_tags_found(httpx_mock_factory):
    """OFF returns status=1 + origins_tags array → outcome='found'."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": "3017620499400",
                "status": 1,
                "product": {"origins_tags": ["en:france", "en:european-union"]},
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        outcome, tags = fetch_origins_tags(client, "https://world.openfoodfacts.org", "3017620499400")
    assert outcome == "found"
    assert tags == ["en:france", "en:european-union"]


def test_fetch_origins_tags_not_found():
    """OFF status=0 → outcome='not_found', tags=None."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": "3017620499401", "status": 0, "status_verbose": "product not found"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome, tags = fetch_origins_tags(client, "https://world.openfoodfacts.org", "3017620499401")
    assert outcome == "not_found"
    assert tags is None


def test_fetch_origins_tags_missing_origins_field_returns_empty():
    """OFF returns the product but no origins_tags field → empty list."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": "3017620499402", "status": 1, "product": {}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome, tags = fetch_origins_tags(client, "https://world.openfoodfacts.org", "3017620499402")
    assert outcome == "found"
    assert tags == []


def test_fetch_origins_tags_drops_non_string_items():
    """Defensive : non-string entries in origins_tags are filtered out."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": "3017620499403", "status": 1, "product": {"origins_tags": ["en:france", 42, None]}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome, tags = fetch_origins_tags(client, "https://world.openfoodfacts.org", "3017620499403")
    assert outcome == "found"
    assert tags == ["en:france"]


# Helper fixture so the test file declares a single httpx_mock_factory.
# Each test builds its own MockTransport inline (above) so this fixture
# is a placeholder — kept so future tests with shared mocks can use it.
@pytest.fixture
def httpx_mock_factory():
    return None
