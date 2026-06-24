"""Tests for off_sync.api — HTTP always mocked, DB via conftest."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from off_sync.api import PAGE_SIZE, run_api
from off_sync.sources import get_source
from sqlalchemy import text

_OFF = get_source("off")
_BASE_URL = _OFF.api_base_url


# ── helpers ───────────────────────────────────────────────────────────────────


def _resp(products: list[dict], count: int | None = None) -> MagicMock:
    mock = MagicMock(spec=httpx.Response)
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {
        "count": count if count is not None else len(products),
        "products": products,
    }
    return mock


def _raw(code="3017620422003", name_fr="Nutella", photo="http://img/n.jpg"):
    return {"code": code, "product_name_fr": name_fr, "image_front_url": photo}


def _run(db_url, *, dry_run=False, until_ts=None, source=_OFF):
    return asyncio.run(
        run_api(
            db_url,
            since_ts=0,
            until_ts=until_ts,
            workers=2,
            dry_run=dry_run,
            source=source,
        )
    )


# ── single page ───────────────────────────────────────────────────────────────


def test_run_api_inserts_single_page(db_url, direct_sessionmaker):
    ean = "3017620422020"

    with patch("off_sync.api.httpx.AsyncClient") as MockClient:
        mock_get = AsyncMock(return_value=_resp([_raw(code=ean)], count=1))
        MockClient.return_value.__aenter__.return_value.get = mock_get

        _run(db_url)

    with direct_sessionmaker() as db:
        row = db.execute(text("SELECT name FROM products WHERE ean = :e"), {"e": ean}).one()
    assert row.name == "Nutella"


def test_run_api_dry_run_does_not_persist(db_url, session_factory):
    ean = "3017620422021"

    with patch("off_sync.api.httpx.AsyncClient") as MockClient:
        mock_get = AsyncMock(return_value=_resp([_raw(code=ean)], count=1))
        MockClient.return_value.__aenter__.return_value.get = mock_get

        _run(db_url, dry_run=True)

    with session_factory() as db:
        count = db.execute(text("SELECT COUNT(*) FROM products WHERE ean = :e"), {"e": ean}).scalar()
    assert count == 0


def test_run_api_empty_result(db_url):
    with patch("off_sync.api.httpx.AsyncClient") as MockClient:
        mock_get = AsyncMock(return_value=_resp([], count=0))
        MockClient.return_value.__aenter__.return_value.get = mock_get

        stats = _run(db_url)

    assert stats.inserted == 0
    assert mock_get.call_count == 1  # only the first probe request


# ── multi-page ────────────────────────────────────────────────────────────────


def test_run_api_two_pages(db_url):
    """count > PAGE_SIZE → second page is fetched."""
    ean_p2 = "3017620422031"
    total = PAGE_SIZE + 1

    responses = [
        _resp([_raw(code=f"301762042{i:04d}") for i in range(PAGE_SIZE)], count=total),
        _resp([_raw(code=ean_p2)], count=total),
    ]

    with patch("off_sync.api.httpx.AsyncClient") as MockClient:
        mock_get = AsyncMock(side_effect=responses)
        MockClient.return_value.__aenter__.return_value.get = mock_get

        stats = _run(db_url)

    assert mock_get.call_count == 2
    assert stats.inserted >= 2


def test_run_api_passes_until_ts(db_url):
    """until_ts is forwarded to the API as last_modified_t_lt."""
    with patch("off_sync.api.httpx.AsyncClient") as MockClient:
        mock_get = AsyncMock(return_value=_resp([], count=0))
        MockClient.return_value.__aenter__.return_value.get = mock_get

        _run(db_url, until_ts=9999999999)

    call_params = mock_get.call_args[1]["params"]
    assert "last_modified_t_lt" in call_params
    assert call_params["last_modified_t_lt"] == 9999999999


def test_run_api_no_until_ts_omits_param(db_url):
    with patch("off_sync.api.httpx.AsyncClient") as MockClient:
        mock_get = AsyncMock(return_value=_resp([], count=0))
        MockClient.return_value.__aenter__.return_value.get = mock_get

        _run(db_url)

    call_params = mock_get.call_args[1]["params"]
    assert "last_modified_t_lt" not in call_params


# ── stats ─────────────────────────────────────────────────────────────────────


def test_run_api_skips_invalid_items(db_url):
    """Invalid items are counted in stats.invalid, valid ones inserted."""
    valid_ean = "3017620422040"
    invalid_raw = {"code": "NOTANEAN", "product_name_fr": "Bad"}

    with patch("off_sync.api.httpx.AsyncClient") as MockClient:
        mock_get = AsyncMock(return_value=_resp([invalid_raw, _raw(code=valid_ean)], count=2))
        MockClient.return_value.__aenter__.return_value.get = mock_get

        stats = _run(db_url)

    assert stats.inserted == 1
    assert stats.invalid == 1


# ── deduplication ─────────────────────────────────────────────────────────────


def test_run_api_deduplicates_same_ean_in_page(db_url, direct_sessionmaker):
    """Two items with the same EAN in one page must not raise CardinalityViolation."""
    ean = "3017620422050"
    page = [
        _raw(code=ean, name_fr="Version A"),
        _raw(code=ean, name_fr="Version B"),
    ]

    with patch("off_sync.api.httpx.AsyncClient") as MockClient:
        mock_get = AsyncMock(return_value=_resp(page, count=2))
        MockClient.return_value.__aenter__.return_value.get = mock_get

        stats = _run(db_url)

    assert stats.inserted == 1
    with direct_sessionmaker() as db:
        row = db.execute(text("SELECT name FROM products WHERE ean = :e"), {"e": ean}).one()
    assert row.name == "Version B"  # last occurrence wins


# ── retry behaviour ───────────────────────────────────────────────────────────


def test_run_api_retries_on_transport_error(db_url):
    """TransportError on first attempt → retried, succeeds on second."""
    with patch("off_sync.api.httpx.AsyncClient") as MockClient:
        mock_get = AsyncMock(side_effect=[httpx.ConnectError("connection refused"), _resp([], count=0)])
        MockClient.return_value.__aenter__.return_value.get = mock_get

        stats = _run(db_url)

    assert mock_get.call_count == 2
    assert stats.inserted == 0


def test_run_api_no_inflight_tasks_when_client_closes(db_url):
    """No fetch task is in-flight when the AsyncClient context closes.

    Regression for prod crash on `--mode delta --force-resync` :
    when an unrecoverable error bubbled out of the as_completed loop, the
    `async with httpx.AsyncClient` context exited while sibling fetch tasks
    were still running. Those tasks' next interaction with the (now closed)
    client raised `RuntimeError: Cannot send a request, as the client has
    been closed.` (httpx aclient.py).

    Invariant enforced by the fix : at the moment `httpx.AsyncClient.__aexit__`
    runs, every fetch task created inside `run_api` MUST already be done
    (completed or cancelled). Without that guarantee, in-flight tasks can
    race the client teardown and hit the closed client.

    Setup :
    - 10 pages total, 2 workers → 8 tasks queued on the semaphore.
    - Page 2 finishes fast. Pages 3..10 sleep so they remain in-flight
      (or queued on the semaphore) when the loop aborts.
    - Page 2 upsert raises → as_completed loop exits with pending tasks.
    - We snapshot the state of all created fetch tasks at the instant
      `__aexit__` runs.
    - Pre-fix : ≥1 task pending → assertion fails.
    - Post-fix : all tasks done (cancelled) → assertion passes.
    """
    import asyncio as _asyncio

    n_pages = 10
    total = PAGE_SIZE * n_pages
    page1 = _resp(
        [_raw(code=f"301762042{i:04d}") for i in range(PAGE_SIZE)],
        count=total,
    )
    page2 = _resp([_raw(code="3017620429901")], count=total)

    async def fake_get(url, params=None, **kw):
        page = (params or {}).get("page", 1)
        if page == 1:
            return page1
        if page == 2:
            return page2
        # Pages 3..N : sleep long enough that they remain in-flight when
        # the loop aborts on page 2's upsert error.
        await _asyncio.sleep(1.0)
        return _resp([], count=total)

    created: list[_asyncio.Task] = []
    real_create_task = _asyncio.create_task

    def spy_create_task(coro, *a, **kw):
        t = real_create_task(coro, *a, **kw)
        created.append(t)
        return t

    pending_at_aexit: list[int] = []

    with patch("off_sync.api.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__.return_value.get = AsyncMock(side_effect=fake_get)

        async def snapshot_aexit(*a, **kw):
            # Snapshot at the exact instant the client is about to close.
            pending_at_aexit.append(sum(1 for t in created if not t.done()))
            return False

        MockClient.return_value.__aexit__ = snapshot_aexit

        with (
            patch("off_sync.api.asyncio.create_task", side_effect=spy_create_task),
            patch(
                "off_sync.api._upsert_page_sync",
                side_effect=[
                    (PAGE_SIZE, 0, 0, 0),  # page 1 OK
                    RuntimeError("synthetic DB failure on page 2"),
                ],
            ),
        ):
            # Page 2 upsert raises → loop aborts. Only this synthetic
            # error should propagate, not "client has been closed".
            with pytest.raises(RuntimeError, match="synthetic DB failure"):
                _run(db_url)

    assert pending_at_aexit, "AsyncClient __aexit__ should have run"
    assert created, "expected fetch tasks for pages 2..10"
    assert pending_at_aexit[0] == 0, (
        f"{pending_at_aexit[0]} fetch task(s) still pending when AsyncClient "
        "closed — they would have hit the closed client in prod."
    )


# ── multi-source plumbing ─────────────────────────────────────────────────────


def test_run_api_uses_source_user_agent(db_url):
    """The User-Agent header must come from Source, not a hardcoded literal."""
    _OBP = get_source("obp")
    captured_headers: dict[str, str] = {}

    def _capture_init(*args, **kwargs):
        captured_headers.update(kwargs.get("headers", {}))
        m = MagicMock()
        m.__aenter__ = AsyncMock(return_value=m)
        m.__aexit__ = AsyncMock(return_value=False)
        m.get = AsyncMock(return_value=_resp([], count=0))
        return m

    with patch("off_sync.api.httpx.AsyncClient", side_effect=_capture_init):
        _run(db_url, source=_OBP)

    assert captured_headers.get("User-Agent") == _OBP.user_agent


def test_run_api_uses_source_api_base_url(db_url):
    """The Search API URL is built from Source.api_base_url."""
    _OBP = get_source("obp")
    captured_urls: list[str] = []

    async def _capture_get(url, **kw):
        captured_urls.append(url)
        return _resp([], count=0)

    with patch("off_sync.api.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__.return_value.get = AsyncMock(side_effect=_capture_get)
        _run(db_url, source=_OBP)

    assert captured_urls
    assert captured_urls[0].startswith(_OBP.api_base_url)


def test_run_api_rejects_non_https_source(db_url):
    """Defence-in-depth: a Source crafted with a non-HTTPS URL is refused."""
    import dataclasses

    bad = dataclasses.replace(_OFF, api_base_url="http://insecure.example.com")
    with pytest.raises(ValueError, match="HTTPS"):
        _run(db_url, source=bad)


def test_run_api_retries_on_429(db_url):
    """429 response → retried, succeeds on second attempt."""
    resp_429 = MagicMock(spec=httpx.Response)
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "0"}
    resp_429.raise_for_status.side_effect = httpx.HTTPStatusError(
        "429 Too Many Requests", request=MagicMock(), response=resp_429
    )

    with patch("off_sync.api.httpx.AsyncClient") as MockClient:
        mock_get = AsyncMock(side_effect=[resp_429, _resp([], count=0)])
        MockClient.return_value.__aenter__.return_value.get = mock_get

        _run(db_url)

    assert mock_get.call_count == 2
