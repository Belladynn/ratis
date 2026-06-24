"""HTTP client for cross-service calls from the admin mini UI to RW.

The mini UI is served by PA, but the admin-settings pages (Bloc D) need
RW endpoints that don't have a local PA equivalent :

- ``PUT /admin/settings/{section}``                 — replace section
- ``GET /admin/settings/audit``                     — paginated audit log
- ``GET /admin/settings/audit/{audit_id}``          — audit detail + diff
- ``POST /admin/settings/{section}/confirm-2fa``    — TOTP confirmation
- ``POST /admin/settings/{section}/cancel-pending`` — cancel pending row
- ``GET /admin/settings/{section}/editable``        — allowlist introspection

This module mirrors ``au_client`` exactly so the cross-service surface of
the admin UI stays uniform : Bearer ``ADMIN_API_KEY`` + ``X-Admin-Operator``
on every call, plus an optional ``X-Admin-TOTP`` header propagated only on
the confirm-2fa POST. The header set is figé in ``ARCH_admin_endpoints.md``
§ Auth and reused here without divergence.

A new ``httpx.AsyncClient`` is created per call. Admin traffic is
human-paced (clicks per minute), so connection pooling is not a hot-path
concern — keeping the resource model trivial avoids surprises (e.g. a
client lifecycle bug propagating across many requests).

Configuration : ``RW_BASE_URL`` env var. **MUST** include the ``/api/v1``
prefix (e.g. ``http://rewards:8004/api/v1`` in compose,
``https://rewards.ratis.app/api/v1`` on Railway). RW's admin router is
mounted at ``/api/v1`` and ``rw_get/put/post`` build paths like
``/admin/settings/{section}`` without re-adding the prefix — a base URL
without ``/api/v1`` produces 404s on every admin settings call. Resolved at
call time via ``_base_url()`` so tests can monkeypatch the env var
per-test ; fail-fast in the PA lifespan via ``require_env`` so a
misconfigured deploy never serves a half-broken admin settings page. See
``ARCH_deployment.md § Cross-service URL conventions``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

_TIMEOUT_SECONDS = 10.0


def _build_headers(operator: str, totp: str | None = None) -> dict[str, str]:
    """Bearer + operator header. Optionally adds X-Admin-TOTP.

    ``totp`` is forwarded only on the ``confirm-2fa`` POST. We treat any
    falsy value (None, empty string) the same way — no header — so a
    caller that fetches an empty TOTP from a form input doesn't
    accidentally leak an empty ``X-Admin-TOTP: `` header that would 401
    on the RW side.
    """
    api_key = os.environ.get("ADMIN_API_KEY", "")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Admin-Operator": operator,
    }
    if totp:
        headers["X-Admin-TOTP"] = totp
    return headers


def _base_url() -> str:
    """Resolve at call time so tests can monkeypatch the env var per-test."""
    return os.environ.get("RW_BASE_URL", "")


async def rw_get(
    path: str,
    *,
    operator: str,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """GET ``{RW_BASE_URL}{path}`` with admin bearer + operator header.

    Used for read endpoints : audit listing, audit detail, editable
    introspection. Returns the raw ``httpx.Response`` so callers can
    branch on status codes without losing access to JSON or headers.
    """
    base = _base_url()
    headers = _build_headers(operator)
    async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT_SECONDS) as client:
        return await client.get(path, params=params, headers=headers)


async def rw_put(
    path: str,
    *,
    operator: str,
    json: dict[str, Any],
) -> httpx.Response:
    """PUT ``{RW_BASE_URL}{path}`` with a JSON body + admin headers.

    Used for the section-replace endpoint. The body shape is contract
    of the RW route (``{data, reason}``) — this layer is transport-only
    and doesn't validate the payload.
    """
    base = _base_url()
    headers = _build_headers(operator)
    async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT_SECONDS) as client:
        return await client.put(path, json=json, headers=headers)


async def rw_post(
    path: str,
    *,
    operator: str,
    json: dict[str, Any],
    totp: str | None = None,
) -> httpx.Response:
    """POST ``{RW_BASE_URL}{path}`` with JSON body + admin headers.

    ``totp`` is forwarded as ``X-Admin-TOTP`` only on the ``confirm-2fa``
    flow. ``cancel-pending`` calls leave it ``None`` so the header is
    absent — RW's TOTP dep is not on the cancel route.
    """
    base = _base_url()
    headers = _build_headers(operator, totp=totp)
    async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT_SECONDS) as client:
        return await client.post(path, json=json, headers=headers)


async def rw_patch(
    path: str,
    *,
    operator: str,
    json: dict[str, Any] | None = None,
) -> httpx.Response:
    """PATCH ``{RW_BASE_URL}{path}`` with optional JSON body + admin headers.

    Used for state-mutating endpoints that only flip a flag (e.g. battle
    pass season ``activate``) where the body is empty — httpx accepts
    ``json=None`` and emits no Content-Type, matching FastAPI's "no body"
    semantics.
    """
    base = _base_url()
    headers = _build_headers(operator)
    async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT_SECONDS) as client:
        return await client.patch(path, json=json, headers=headers)


async def rw_delete(
    path: str,
    *,
    operator: str,
) -> httpx.Response:
    """DELETE ``{RW_BASE_URL}{path}`` with admin headers.

    Used for hard-delete endpoints (mystery product / reward_config /
    streak_tier). RW returns 204 on success ; the caller checks the
    status code directly. No body is sent — operator identity is in the
    ``X-Admin-Operator`` header (server-side audit row carries the
    snapshot).
    """
    base = _base_url()
    headers = _build_headers(operator)
    async with httpx.AsyncClient(base_url=base, timeout=_TIMEOUT_SECONDS) as client:
        return await client.delete(path, headers=headers)
