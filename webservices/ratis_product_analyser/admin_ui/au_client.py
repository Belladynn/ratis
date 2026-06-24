"""HTTP client for cross-service calls from the admin mini UI to AU.

The mini UI is served by PA, but two pages need user data that lives in
AU's PG schema (``users`` table, ``support_id`` lookups, refresh-token /
subscription / cashback aggregates) :

- ``GET /admin/ui/users/search`` → AU ``/admin/users``
- ``GET /admin/ui/users/{user_id}`` → AU ``/admin/users/{user_id}``

The third bloc on the detail page (per-user scans) reads PA's local DB
directly via the existing repository ; only AU calls go over HTTP.

Authentication mirrors the JSON ``/api/v1/admin/*`` API : a single
``ADMIN_API_KEY`` is shared across services and forwarded as a Bearer
token. The ``X-Admin-Operator`` header is sent for parity with the
mutation pattern even though the AU read-only endpoints don't audit
caller — keeps the wire shape consistent if AU adds audit later.

Configuration : ``AU_BASE_URL`` env var. **MUST** include the ``/api/v1``
prefix (e.g. ``http://auth:8001/api/v1`` in compose,
``https://auth.ratis.app/api/v1`` on Railway). AU's admin router is mounted
at ``/api/v1`` and ``au_get`` builds paths like ``/admin/users/{id}`` without
re-adding the prefix — a base URL without ``/api/v1`` produces 404s on every
admin UI call. Fail-fast in the PA lifespan via ``require_env`` so a
misconfigured deploy never serves a half-broken admin UI. See
``ARCH_deployment.md § Cross-service URL conventions``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

_TIMEOUT_SECONDS = 10.0


def _build_headers(operator: str) -> dict[str, str]:
    """Bearer + operator header. Operator is informational on read-only AU."""
    api_key = os.environ.get("ADMIN_API_KEY", "")
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Admin-Operator": operator,
    }


def _base_url() -> str:
    """Resolve at call time so tests can monkeypatch the env var per-test."""
    return os.environ.get("AU_BASE_URL", "")


async def au_get(
    path: str,
    *,
    operator: str,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """GET ``{AU_BASE_URL}{path}`` with admin bearer + operator header.

    Returns the raw ``httpx.Response`` so the caller can inspect status
    + JSON body without losing the ability to branch on 404/422 vs 200.
    A new ``AsyncClient`` per call keeps the resource model simple — the
    admin UI traffic is human-paced (clicks per minute), so connection
    pooling is not a hot-path concern here.
    """
    base = _base_url()
    headers = _build_headers(operator)
    async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT_SECONDS) as client:
        return await client.get(path, params=params, headers=headers)
