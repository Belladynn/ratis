"""GlitchTip HTTP API wrappers — Module 1 of agent-mcp (ARCH § Module 1).

DA-47 follow-up : Sentry SaaS sunset, migration vers GlitchTip self-hosted
(protocole Sentry-compatible à ~90 %, mêmes paths `/api/0/...`). Ces 4 MCP
tools admin pointaient encore vers ``https://sentry.io/api/0`` qui est mort
post-DA-47 ; ils sont maintenant câblés à l'instance GlitchTip locale.

Exposes 4 typed tools to Claude Code agents :

* `glitchtip_list_issues`   (ops)   — GET   /api/0/projects/<org>/<project>/issues/
* `glitchtip_get_issue`     (ops)   — GET   /api/0/issues/<id>/
* `glitchtip_list_events`   (ops)   — GET   /api/0/issues/<id>/events/
* `glitchtip_resolve_issue` (admin) — PUT   /api/0/issues/<id>/  (+ optional comment POST)

Token discipline (security-critical, DA-43)
-------------------------------------------
* Token is fetched FRESH from `Keychain` on every call — never cached at module
  level. The `Keychain` instance has its own 60-second positive cache, which
  is the right granularity (rotation surfaces within a minute, no leak risk).
* Token only ever lives in the `Authorization` header of the outbound httpx
  request. It NEVER appears in tool arguments, function returns, exceptions,
  audit log entries or stderr.
* `KeychainMiss` propagates verbatim — the dispatcher maps it to the audit
  status `keychain_miss` so the operator knows to run
  `agent-mcp keychain set admin-glitchtip <token>`.

Backend URL
-----------
Default endpoint is the local GlitchTip self-hosted instance running on the
Mac mini (`http://localhost:8000/api/0`). Override via the `GLITCHTIP_API_URL`
environment variable when targeting a different deployment (staging, future
remote prod).

Org slug
--------
Read from environment variable `GLITCHTIP_ORG`. Falls back to `"ratis"` if
unset (current GlitchTip workspace slug). Override only needed if Ratis ever
moves to a different GlitchTip org.

References
----------
* ARCH_agent_mcp.md § Module 1 (signatures + scopes)
* ARCH_incident_management.md (GlitchTip topology + projects)
* DA-43 (Keychain), DA-44 (scopes), DA-47 (Notion sunset → GlitchTip),
  DA-48 (audit), DA-49 (typed Python tools)
* GlitchTip API docs : https://glitchtip.com/documentation/api (Sentry-v0-compatible)
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..errors import ProviderError
from ..keychain import Keychain
from ..server import TOOLS_REGISTRY, register_tool

GLITCHTIP_BASE_URL = os.environ.get(
    "GLITCHTIP_API_URL",
    "http://localhost:8000/api/0",
)
"""GlitchTip HTTP API base URL — no trailing slash, paths start with `/`.

Resolved at import time from `GLITCHTIP_API_URL` (default
`http://localhost:8000/api/0`, the Mac mini self-hosted instance).
"""

KEYCHAIN_ACCOUNT = "admin-glitchtip"
"""Account name in the macOS Keychain under service `ratis-agent-mcp`.

Pairs with `~/glitchtip/bin/glt` (operator CLI wrapper) which reads the same
entry. Posted via `agent-mcp keychain set admin-glitchtip`.
"""

DEFAULT_ORG_SLUG = "ratis"
"""Fallback when `GLITCHTIP_ORG` is unset (current Ratis GlitchTip workspace)."""

HTTP_TIMEOUT_SEC = 30.0
"""Per-request timeout for outbound GlitchTip calls.

30 seconds is generous : GlitchTip's slow paths (large issue with thousands of
events) can take ~10 s. We do NOT retry — the dispatcher surfaces the failure
to the agent which can decide to retry contextually.
"""


# ---- internal helpers ---------------------------------------------------


def _org_slug() -> str:
    """Resolve the GlitchTip org slug at call time (env var takes precedence)."""
    return os.environ.get("GLITCHTIP_ORG") or DEFAULT_ORG_SLUG


def _fetch_token() -> str:
    """Read the GlitchTip API token from the macOS Keychain.

    A fresh `Keychain()` is constructed each call — the cost is negligible
    (no `security` invocation happens until `.get()` is called) and it lets
    tests monkeypatch `Keychain.get` cleanly without juggling instances.

    Raises `KeychainMiss` (from `Keychain.get`) if the entry is missing —
    propagated as-is so the dispatcher tags the audit line `keychain_miss`.
    """
    return Keychain().get(KEYCHAIN_ACCOUNT)


def _build_client(token: str) -> httpx.Client:
    """Construct the per-call `httpx.Client` carrying the Bearer token.

    Tests monkeypatch this function to inject an `httpx.MockTransport`. The
    real implementation never needs an explicit transport — httpx defaults
    are fine for our scale (≤ a few requests/sec).
    """
    return httpx.Client(
        base_url=GLITCHTIP_BASE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=HTTP_TIMEOUT_SEC,
    )


def _raise_for_status(response: httpx.Response, *, context: str) -> None:
    """Convert a non-2xx response into a `ProviderError` with a stable shape.

    We deliberately surface the upstream status + a truncated body — never
    the request headers (where the Bearer token lives).
    """
    if 200 <= response.status_code < 300:
        return
    body_preview = response.text[:500] if response.text else ""
    raise ProviderError(f"glitchtip {context} failed: HTTP {response.status_code} — {body_preview}".rstrip(" —"))


# ---- tool implementations -----------------------------------------------


def glitchtip_list_issues(
    project: str,
    query: str = "is:unresolved",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """List issues for a GlitchTip project. Read-only. Scope: ops.

    Args :
        project : GlitchTip project slug (e.g. ``ratis-backend``).
        query   : Sentry-compatible search syntax. Defaults to unresolved issues.
        limit   : max number of issues returned. GlitchTip caps at 100.

    Returns the raw GlitchTip issue list — each entry is a dict with at least
    ``id``, ``title``, ``status``, ``count``, ``culprit``.
    """
    token = _fetch_token()
    org = _org_slug()
    with _build_client(token) as client:
        response = client.get(
            f"/projects/{org}/{project}/issues/",
            params={"query": query, "limit": str(limit)},
        )
    _raise_for_status(response, context="list_issues")
    payload = response.json()
    if not isinstance(payload, list):
        raise ProviderError(f"glitchtip list_issues: expected list, got {type(payload).__name__}")
    return payload


def glitchtip_get_issue(issue_id: str) -> dict[str, Any]:
    """Get full issue details (stacktrace, breadcrumbs, user context). Read-only. Scope: ops.

    Args :
        issue_id : GlitchTip numeric issue id (string-typed because the API
            accepts both numeric and short-id forms).

    Returns the full issue dict including ``metadata``, ``firstRelease``,
    ``lastSeen``, ``stats``, etc.
    """
    token = _fetch_token()
    with _build_client(token) as client:
        response = client.get(f"/issues/{issue_id}/")
    _raise_for_status(response, context="get_issue")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ProviderError(f"glitchtip get_issue: expected dict, got {type(payload).__name__}")
    return payload


def glitchtip_list_events(
    issue_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """List recent events for a given issue. Read-only. Scope: ops.

    Args :
        issue_id : GlitchTip issue id.
        limit    : max events returned (GlitchTip default 100, we cap to a
                   small number for token-efficient agent context).
    """
    token = _fetch_token()
    with _build_client(token) as client:
        response = client.get(
            f"/issues/{issue_id}/events/",
            params={"limit": str(limit)},
        )
    _raise_for_status(response, context="list_events")
    payload = response.json()
    if not isinstance(payload, list):
        raise ProviderError(f"glitchtip list_events: expected list, got {type(payload).__name__}")
    return payload


def glitchtip_resolve_issue(
    issue_id: str,
    comment: str = "",
) -> dict[str, Any]:
    """Mark an issue as resolved (PUT /issues/<id>/ status=resolved). Mutating. Scope: admin.

    Args :
        issue_id : GlitchTip issue id.
        comment  : Optional human-readable note. When non-empty, posted to
                   ``/issues/<id>/comments/`` AFTER the status update — so a
                   failure to record the comment does NOT roll back the
                   resolution (the audit log will still have the resolution
                   line ; the comment failure surfaces as a `ProviderError`).

    Returns the updated issue dict from the PUT response (GlitchTip returns
    the issue resource with ``status: "resolved"``).
    """
    token = _fetch_token()
    with _build_client(token) as client:
        put_resp = client.put(
            f"/issues/{issue_id}/",
            json={"status": "resolved"},
        )
        _raise_for_status(put_resp, context="resolve_issue")
        result = put_resp.json()

        if comment:
            comment_resp = client.post(
                f"/issues/{issue_id}/comments/",
                json={"text": comment},
            )
            _raise_for_status(comment_resp, context="resolve_issue.comment")

    if not isinstance(result, dict):
        raise ProviderError(f"glitchtip resolve_issue: expected dict, got {type(result).__name__}")
    return result


# ---- registration -------------------------------------------------------

# We register imperatively (rather than at import-time decorator side-effect)
# so the autouse `reset_tools_registry` test fixture can clear and re-register
# cleanly, AND so production code can pick a deterministic registration moment
# from `agent_mcp.cli` / `agent_mcp.server.build_mcp_server()`.

_REGISTERED = False


def register_all() -> None:
    """Register the 4 GlitchTip tools into the module-level registry.

    Idempotent — subsequent calls are no-ops, so importing this module from
    multiple places (CLI bootstrap, tests, future docs generators) is safe.
    """
    global _REGISTERED
    # We can't trust `_REGISTERED` alone — `clear_registry()` (used by tests)
    # wipes the registry but not our flag. Always cross-check the registry.
    if _REGISTERED and "glitchtip_list_issues" in TOOLS_REGISTRY:
        return

    # The decorator raises on duplicate names ; check defensively per-tool so
    # partial state never blocks re-registration.
    if "glitchtip_list_issues" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(glitchtip_list_issues)
    if "glitchtip_get_issue" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(glitchtip_get_issue)
    if "glitchtip_list_events" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(glitchtip_list_events)
    if "glitchtip_resolve_issue" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(glitchtip_resolve_issue)

    _REGISTERED = True


def _reset_for_tests() -> None:
    """Test-only — drop the idempotence flag so `register_all()` re-runs.

    Pairs with `agent_mcp.server.clear_registry()` (autouse in conftest).
    Production code never calls this.
    """
    global _REGISTERED
    _REGISTERED = False
