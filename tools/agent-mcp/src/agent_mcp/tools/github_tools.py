"""GitHub HTTP API v3 wrappers — Module 3 of agent-mcp (ARCH § Module 3).

Exposes 5 typed tools to Claude Code agents :

* `github_list_prs`           (ops)   — GET  /repos/<repo>/pulls
* `github_get_pr`             (ops)   — GET  /repos/<repo>/pulls/<n>
* `github_list_check_runs`    (ops)   — GET  /repos/<repo>/pulls/<n> + GET /commits/<sha>/check-runs
* `github_rerun_failed_checks` (admin) — GET check-runs + POST /check-runs/<id>/rerequest per failure
* `github_comment_pr`         (admin) — POST /repos/<repo>/issues/<n>/comments

Backend choice (per ARCH § Module 3)
------------------------------------
We hit `api.github.com` directly via `httpx` rather than shelling out to `gh`.
HTTP-direct is the testability winner — `httpx.MockTransport` gives us byte-
level control over every request, where a `gh` subprocess would be opaque.
The token also stays in a single header (`Authorization: Bearer <token>`),
never crossing a process boundary.

Token discipline (security-critical, DA-43)
-------------------------------------------
* Token is fetched FRESH from `Keychain` on every call (account name
  ``github``). The keychain itself has a 60-second positive cache.
* Token only lives in the `Authorization: Bearer <token>` header of the
  outbound httpx request. It NEVER appears in tool args, URLs, request
  bodies, returned dicts, exceptions, audit log entries or stderr. Tests
  assert this exhaustively (cross-tool sweep).
* No subprocess is spawned in this chunk, so argv leakage is structurally
  impossible — but we still guard URL / body leakage.
* `KeychainMiss` propagates verbatim so the dispatcher tags the audit
  status `keychain_miss` and the operator knows to run
  `agent-mcp keychain set github <token>`.

Repo configuration
------------------
Hardcoded for V0 to ``Belladynn/ratis`` (only repo this MCP serves). An
override env var ``RATIS_GITHUB_REPO`` is supported for tests and for
operators running an instance against a fork.

References
----------
* ARCH_agent_mcp.md § Module 3 (signatures + scopes)
* DA-43 (Keychain), DA-44 (scopes), DA-48 (audit), DA-49 (typed Python tools)
* GitHub REST API : https://docs.github.com/en/rest
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..errors import ProviderError
from ..keychain import Keychain
from ..server import TOOLS_REGISTRY, register_tool

GITHUB_BASE_URL = "https://api.github.com"
"""GitHub HTTP API v3 base URL — no trailing slash, paths start with `/`."""

KEYCHAIN_ACCOUNT = "github"
"""Account name in the macOS Keychain under service `ratis-agent-mcp`."""

DEFAULT_REPO = "Belladynn/ratis"
"""Fallback when `RATIS_GITHUB_REPO` is unset (V0 hardcoded per ARCH)."""

GITHUB_API_VERSION = "2022-11-28"
"""Pin the GitHub API version (sent as `X-GitHub-Api-Version` header)."""

HTTP_TIMEOUT_SEC = 30.0
"""Per-request timeout for outbound GitHub calls.

GitHub's slow paths (large PR with many checks) can take several seconds.
30s is generous ; we do NOT retry — the dispatcher surfaces the failure
to the agent which can decide to retry contextually.
"""


# ---- internal helpers ---------------------------------------------------


def _repo() -> str:
    """Resolve the GitHub repo at call time (env var takes precedence)."""
    return os.environ.get("RATIS_GITHUB_REPO") or DEFAULT_REPO


def _fetch_token() -> str:
    """Read the GitHub PAT from the macOS Keychain.

    A fresh `Keychain()` is constructed each call — same pattern as
    `glitchtip_tools` / `eas_tools`. Tests monkeypatch `Keychain.get`.

    Raises `KeychainMiss` if the entry is missing — propagated as-is so the
    dispatcher tags the audit line `keychain_miss`.
    """
    return Keychain().get(KEYCHAIN_ACCOUNT)


def _build_client(token: str) -> httpx.Client:
    """Construct the per-call `httpx.Client` carrying the Bearer token.

    Tests monkeypatch this function to inject an `httpx.MockTransport`. The
    real implementation never needs an explicit transport — httpx defaults
    are fine for our scale.
    """
    return httpx.Client(
        base_url=GITHUB_BASE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
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
    raise ProviderError(f"github {context} failed: HTTP {response.status_code} — {body_preview}".rstrip(" —"))


def _label_names(raw: Any) -> list[str]:
    """Extract the `.name` of each label from a raw GitHub `labels` array.

    GitHub returns labels as full objects (name, color, description, id...).
    Agents only ever reason about the names — keep just those.
    """
    if not isinstance(raw, list):
        return []
    return [item["name"] for item in raw if isinstance(item, dict) and "name" in item]


def _trim_pr_summary(pr: dict[str, Any]) -> dict[str, Any]:
    """Project a raw GitHub PR dict down to the fields a list view needs.

    Used by `github_list_prs` — drops `body`, `_links`, the full `user` /
    `head` / `base` objects, etc. A 30-PR listing shrinks dramatically.
    """
    user = pr.get("user") or {}
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "state": pr.get("state"),
        "draft": pr.get("draft"),
        "user_login": user.get("login") if isinstance(user, dict) else None,
        "html_url": pr.get("html_url"),
        "head_ref": head.get("ref") if isinstance(head, dict) else None,
        "base_ref": base.get("ref") if isinstance(base, dict) else None,
        "labels": _label_names(pr.get("labels")),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
    }


def _trim_pr_detail(pr: dict[str, Any]) -> dict[str, Any]:
    """Project a raw GitHub PR dict down to the fields a detail view needs.

    Used by `github_get_pr` — keeps merge state + diff stats + `body`, drops
    the dozens of URL / timestamp / actor sub-objects GitHub ships.
    """
    user = pr.get("user") or {}
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "state": pr.get("state"),
        "draft": pr.get("draft"),
        "merged": pr.get("merged"),
        "mergeable": pr.get("mergeable"),
        "mergeable_state": pr.get("mergeable_state"),
        "user_login": user.get("login") if isinstance(user, dict) else None,
        "html_url": pr.get("html_url"),
        "head_ref": head.get("ref") if isinstance(head, dict) else None,
        "base_ref": base.get("ref") if isinstance(base, dict) else None,
        "labels": _label_names(pr.get("labels")),
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "changed_files": pr.get("changed_files"),
        "commits": pr.get("commits"),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "body": pr.get("body"),
    }


def _trim_check_run(run: dict[str, Any]) -> dict[str, Any]:
    """Project a raw GitHub check-run dict down to the fields an agent needs.

    Used by `github_list_check_runs` — drops `output` (summary / text /
    annotations — the dominant source of payload bloat), plus `app`,
    `check_suite`, `pull_requests`, `id`, `node_id`, `external_id`,
    `details_url`.

    NOTE : `_extract_check_runs` deliberately stays raw — `github_rerun_
    failed_checks` needs the `id` field to POST rerequests. The trim is
    applied ONLY at the `github_list_check_runs` return boundary.
    """
    return {
        "name": run.get("name"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "html_url": run.get("html_url"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
    }


def _extract_check_runs(payload: Any, context: str) -> list[dict[str, Any]]:
    """Pull the `check_runs` list out of a check-runs response.

    GitHub's check-runs endpoint returns ``{"total_count": n, "check_runs": [...]}``.
    Defensive helper — raises `ProviderError` on unexpected shape rather than
    silently returning an empty list (which would mask real upstream errors).
    """
    if isinstance(payload, dict) and isinstance(payload.get("check_runs"), list):
        return payload["check_runs"]
    raise ProviderError(f"github {context}: unexpected check-runs payload shape {type(payload).__name__}")


# ---- tool implementations -----------------------------------------------


def github_list_prs(state: str = "open", limit: int = 20) -> list[dict[str, Any]]:
    """List PRs on the ratis monorepo. Read-only. Scope: ops.

    Args :
        state : ``"open"`` (default) / ``"closed"`` / ``"all"``.
        limit : max PRs returned (GitHub caps `per_page` at 100).

    Returns a trimmed PR list — each entry keeps only ``number``, ``title``,
    ``state``, ``draft``, ``user_login``, ``html_url``, ``head_ref``,
    ``base_ref``, ``labels`` (names), ``created_at``, ``updated_at``. The raw
    GitHub payload (with ``body``, ``_links``, full actor objects) is dropped
    to keep the agent's context budget lean.
    """
    token = _fetch_token()
    repo = _repo()
    with _build_client(token) as client:
        response = client.get(
            f"/repos/{repo}/pulls",
            params={"state": state, "per_page": str(limit)},
        )
    _raise_for_status(response, context="list_prs")
    payload = response.json()
    if not isinstance(payload, list):
        raise ProviderError(f"github list_prs: expected list, got {type(payload).__name__}")
    return [_trim_pr_summary(pr) for pr in payload if isinstance(pr, dict)]


def github_get_pr(pr_number: int) -> dict[str, Any]:
    """Get full PR details (title, body, status, checks). Read-only. Scope: ops.

    Args :
        pr_number : the PR number (e.g. 297).

    Returns a trimmed PR dict — ``number``, ``title``, ``state``, ``draft``,
    ``merged``, ``mergeable``, ``mergeable_state``, ``user_login``,
    ``html_url``, ``head_ref``, ``base_ref``, ``labels`` (names),
    ``additions``, ``deletions``, ``changed_files``, ``commits``,
    ``created_at``, ``updated_at``, ``body``. The dozens of URL / sub-object
    fields GitHub ships are dropped to keep the agent's context lean.
    """
    token = _fetch_token()
    repo = _repo()
    with _build_client(token) as client:
        response = client.get(f"/repos/{repo}/pulls/{pr_number}")
    _raise_for_status(response, context="get_pr")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ProviderError(f"github get_pr: expected dict, got {type(payload).__name__}")
    return _trim_pr_detail(payload)


def github_list_check_runs(pr_number: int) -> list[dict[str, Any]]:
    """List CI check runs for a given PR. Read-only. Scope: ops.

    GitHub's check-runs endpoint is keyed by commit SHA, not by PR number.
    We do TWO requests : first GET the PR to obtain ``head.sha``, then GET
    the check-runs for that SHA. The cost is one extra round-trip but the
    interface stays PR-centric (which is what agents reason about).

    Args :
        pr_number : the PR number.

    Returns a trimmed list of check-run dicts — each keeps only ``name``,
    ``status``, ``conclusion``, ``html_url``, ``started_at``,
    ``completed_at``. The verbose ``output`` block (summary / text /
    annotations) and other metadata are dropped — that block is the dominant
    source of payload bloat (a 30-run listing was 106 KB raw).
    """
    token = _fetch_token()
    repo = _repo()
    with _build_client(token) as client:
        pr_resp = client.get(f"/repos/{repo}/pulls/{pr_number}")
        _raise_for_status(pr_resp, context="list_check_runs.head")
        pr = pr_resp.json()
        if not isinstance(pr, dict):
            raise ProviderError(f"github list_check_runs: PR payload not a dict ({type(pr).__name__})")
        head_sha = pr.get("head", {}).get("sha")
        if not isinstance(head_sha, str) or not head_sha:
            raise ProviderError(f"github list_check_runs: PR {pr_number} has no head.sha")

        runs_resp = client.get(f"/repos/{repo}/commits/{head_sha}/check-runs")
    _raise_for_status(runs_resp, context="list_check_runs.runs")
    # Trim at the tool boundary ONLY — `_extract_check_runs` stays raw so
    # `github_rerun_failed_checks` keeps the `id` it needs to POST rerequests.
    runs = _extract_check_runs(runs_resp.json(), context="list_check_runs")
    return [_trim_check_run(run) for run in runs if isinstance(run, dict)]


def github_rerun_failed_checks(pr_number: int) -> dict[str, Any]:
    """Re-run only failed CI check runs on a PR. Mutating. Scope: admin.

    Sequence :
        1. GET the PR to obtain ``head.sha``.
        2. GET the check-runs for that SHA.
        3. Filter on ``conclusion == "failure"`` (skip success / neutral /
           cancelled / pending — only the explicit failures get re-run).
        4. For each failure, POST ``/check-runs/<id>/rerequest``.

    Args :
        pr_number : the PR number.

    Returns ``{"rerequested": [<id>...], "total_failed": n}``. ``n`` equals
    ``len(rerequested)`` on the happy path ; if a per-id POST fails we
    re-raise the `ProviderError` so the caller knows the run was partial.
    """
    token = _fetch_token()
    repo = _repo()
    with _build_client(token) as client:
        # 1. resolve head SHA from the PR.
        pr_resp = client.get(f"/repos/{repo}/pulls/{pr_number}")
        _raise_for_status(pr_resp, context="rerun_failed_checks.head")
        pr = pr_resp.json()
        if not isinstance(pr, dict):
            raise ProviderError(f"github rerun_failed_checks: PR payload not a dict ({type(pr).__name__})")
        head_sha = pr.get("head", {}).get("sha")
        if not isinstance(head_sha, str) or not head_sha:
            raise ProviderError(f"github rerun_failed_checks: PR {pr_number} has no head.sha")

        # 2. fetch check-runs.
        runs_resp = client.get(f"/repos/{repo}/commits/{head_sha}/check-runs")
        _raise_for_status(runs_resp, context="rerun_failed_checks.runs")
        check_runs = _extract_check_runs(runs_resp.json(), context="rerun_failed_checks")

        # 3. filter failures (explicit `conclusion == "failure"` — pending
        # checks have `conclusion == None` and are intentionally skipped).
        failed_ids = [
            run["id"]
            for run in check_runs
            if isinstance(run, dict) and run.get("conclusion") == "failure" and "id" in run
        ]

        # 4. POST rerequest per failed id (no batched endpoint exists).
        rerequested: list[int] = []
        for run_id in failed_ids:
            post_resp = client.post(f"/repos/{repo}/check-runs/{run_id}/rerequest")
            _raise_for_status(post_resp, context=f"rerun_failed_checks.rerequest({run_id})")
            rerequested.append(run_id)

    return {"rerequested": rerequested, "total_failed": len(failed_ids)}


def github_comment_pr(pr_number: int, body: str) -> dict[str, Any]:
    """Post a comment on a PR. Mutating. Scope: admin.

    GitHub treats PR comments as Issue comments (since a PR IS an issue) —
    we POST to ``/repos/<repo>/issues/<n>/comments``, not the PR-specific
    review comments endpoint (which targets a specific diff line).

    Args :
        pr_number : the PR number.
        body      : the markdown comment body.

    Returns the created comment dict (with ``id``, ``html_url``, etc.).
    """
    token = _fetch_token()
    repo = _repo()
    with _build_client(token) as client:
        response = client.post(
            f"/repos/{repo}/issues/{pr_number}/comments",
            json={"body": body},
        )
    _raise_for_status(response, context="comment_pr")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ProviderError(f"github comment_pr: expected dict, got {type(payload).__name__}")
    return payload


# ---- registration -------------------------------------------------------

# Imperative registration — mirrors `glitchtip_tools` / `eas_tools`. The
# autouse `reset_tools_registry` test fixture clears the registry, so we
# re-populate deterministically.

_REGISTERED = False


def register_all() -> None:
    """Register the 5 GitHub tools into the module-level registry.

    Idempotent — subsequent calls are no-ops, so importing this module from
    multiple places (CLI bootstrap, tests, future docs generators) is safe.
    """
    global _REGISTERED
    if _REGISTERED and "github_list_prs" in TOOLS_REGISTRY:
        return

    # Per-tool defensive check — `clear_registry()` (used by tests) wipes the
    # registry but not our flag. Cross-check before registering each tool.
    if "github_list_prs" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(github_list_prs)
    if "github_get_pr" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(github_get_pr)
    if "github_list_check_runs" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(github_list_check_runs)
    if "github_rerun_failed_checks" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(github_rerun_failed_checks)
    if "github_comment_pr" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(github_comment_pr)

    _REGISTERED = True


def _reset_for_tests() -> None:
    """Test-only — drop the idempotence flag so `register_all()` re-runs."""
    global _REGISTERED
    _REGISTERED = False
