"""EAS (Expo Application Services) wrappers — Module 2 of agent-mcp (ARCH § Module 2).

Exposes 5 typed tools to Claude Code agents :

* `eas_update_preview`        (admin)  — `eas update --channel preview --environment preview ...`
* `eas_update_production`     (admin)  — same, on `production` channel + pre-publish gate
* `eas_list_updates`          (ops)    — `eas update:list --branch=<channel> --json`
* `eas_list_builds`           (ops)    — `eas build:list --platform=<p> --json`
* `eas_rollback_to_embedded`  (admin)  — `eas update:roll-back-to-embedded --channel <c>`

Token discipline (security-critical, DA-43)
-------------------------------------------
* `EXPO_TOKEN` is fetched FRESH from `Keychain` on every call (account name
  ``eas``). The keychain itself has a 60-second positive cache — the right
  granularity for token rotation.
* The token is injected **only** via the ``env=`` kwarg of `subprocess.run`.
  It NEVER appears in argv, in tool arguments, in returned dicts, in
  exceptions, or in audit log entries. Tests verify this exhaustively.
* Subprocess argv list is constructed defensively : we never pass a single
  shell string (no ``shell=True``), so arg quoting / token leakage via
  ``ps`` is structurally impossible.

Lessons learned hardcoded
-------------------------
* **KP-57** — `eas update` MUST always pass ``--environment`` matching
  ``--channel`` so EAS Update inlines the dashboard `EXPO_PUBLIC_*` vars in
  the bundle. Both `eas_update_preview` and `eas_update_production` enforce
  this : the channel is hardcoded, and the environment defaults to the
  matching value (with `eas_update_production` going further by hardcoding
  both — production publishes never accept any environment override).
* **KP-32** — Channel mismatch (publishing to channel X while installed APK
  listens to Y) silently no-ops. We can't prevent it here per se, but we
  expose `eas_list_builds` so an agent / human can verify the installed
  APK's channel BEFORE publishing.
* **KP-34** — Native deps require a rebuild ; OTA-only ships JS. The MCP
  cannot detect this automatically (V0). Documented in the module docstring
  + README so agents flag it pre-publish.

Pre-publish gate (R34)
----------------------
`eas_update_production` runs a local guard BEFORE invoking eas-cli :
    1. ``git fetch origin main`` (refresh remote pointer).
    2. Compare ``git rev-parse HEAD`` vs ``git rev-parse origin/main``.
    3. If they differ → raise `RuntimeError` (no eas call attempted).

This mirrors the manual R34 pre-publish gate (\"git fetch && git status
clean && HEAD == origin/main\") and prevents an agent from publishing a
feature branch to production by accident.

We deliberately don't check `git status` for unstaged changes — the SHA
comparison is the strict variant : even a clean working tree on a
non-main commit gets refused. This is intentionally over-strict.

Project root
------------
The eas-cli MUST be invoked with ``cwd=<repo>/ratis_client/`` (Expo project
root). We resolve this once via :

    1. `RATIS_PROJECT_ROOT` env var (test-friendly override).
    2. fallback : ``git rev-parse --show-toplevel`` from the cwd of the MCP
       process (production = the user's monorepo checkout).

References
----------
* ARCH_agent_mcp.md § Module 2 (signatures + scopes + KP citations)
* CLAUDE.md R34 (EAS publish discipline)
* DA-43 (Keychain), DA-44 (scopes), DA-48 (audit), DA-49 (typed Python tools)
* KP-57, KP-32, KP-34 in KNOWN_PROBLEMS.md
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ..errors import ProviderError
from ..keychain import Keychain
from ..server import TOOLS_REGISTRY, register_tool

KEYCHAIN_ACCOUNT = "eas"
"""Account name in the macOS Keychain under service `ratis-agent-mcp`."""

CLIENT_DIR_NAME = "ratis_client"
"""Directory name (relative to repo root) that holds the Expo project."""

EAS_TIMEOUT_SEC = 300
"""Per-call timeout for eas-cli invocations.

5 minutes is generous : `eas update` typically completes in 30-60 s including
bundling, but cold-cache CI runs can take ~3 min. We never want to hang the
MCP process forever — `subprocess.TimeoutExpired` surfaces as `ProviderError`.
"""

GIT_TIMEOUT_SEC = 30
"""Per-call timeout for the small `git fetch / rev-parse` commands."""


# ---- internal helpers ---------------------------------------------------


_PROJECT_ROOT_CACHE: Path | None = None
"""Memoized monorepo root — invalidated by `_reset_project_root_cache()` in tests."""


def _project_root() -> Path:
    """Resolve the Ratis monorepo root (the parent of ``ratis_client/``).

    Order of precedence :
        1. ``RATIS_PROJECT_ROOT`` env var (set by tests, or by an operator
           who runs the MCP from outside the repo).
        2. ``git rev-parse --show-toplevel`` from the current working
           directory (production : the MCP is launched from the monorepo).

    Raises `ProviderError` if neither resolution succeeds — at that point
    we cannot safely shell out to eas-cli (the wrong cwd would land us in a
    foreign directory).
    """
    global _PROJECT_ROOT_CACHE
    if _PROJECT_ROOT_CACHE is not None:
        return _PROJECT_ROOT_CACHE

    env_override = os.environ.get("RATIS_PROJECT_ROOT")
    if env_override:
        path = Path(env_override).resolve()
        _PROJECT_ROOT_CACHE = path
        return path

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"eas: failed to detect project root via git: {exc}") from exc

    if result.returncode != 0:
        raise ProviderError("eas: cannot resolve project root — set RATIS_PROJECT_ROOT or run from inside the monorepo")

    path = Path(result.stdout.strip()).resolve()
    _PROJECT_ROOT_CACHE = path
    return path


def _reset_project_root_cache() -> None:
    """Test-only — drop the memoized project root so the next call re-resolves."""
    global _PROJECT_ROOT_CACHE
    _PROJECT_ROOT_CACHE = None


def _client_cwd() -> Path:
    """Return the Expo project directory — `eas-cli` must run there."""
    return _project_root() / CLIENT_DIR_NAME


def _fetch_token() -> str:
    """Read the EXPO_TOKEN from the macOS Keychain.

    A fresh `Keychain()` is constructed each call (the cost is negligible)
    so tests can monkeypatch `Keychain.get` cleanly. Raises `KeychainMiss`
    if the entry is missing — the dispatcher tags this as `keychain_miss`
    in the audit log.
    """
    return Keychain().get(KEYCHAIN_ACCOUNT)


def _build_env(token: str) -> dict[str, str]:
    """Produce the env dict for `subprocess.run`.

    We START from `os.environ` (eas-cli reads PATH, HOME, USER, etc.) and
    OVERLAY ``EXPO_TOKEN`` last so a stray operator-set env var can't
    override the keychain-sourced one.
    """
    env = dict(os.environ)
    env["EXPO_TOKEN"] = token
    return env


def _run_eas(
    argv: list[str],
    *,
    token: str,
    parse_json: bool = True,
    context: str,
) -> Any:
    """Invoke an eas-cli command and return parsed stdout (or raw text).

    Args :
        argv       : full argv list, starting with ``"eas"``.
        token      : EXPO_TOKEN value (injected only into ``env=``).
        parse_json : when True (default), parse stdout as JSON ; on parse
                     failure raise `ProviderError`. When False, return the
                     raw stdout string.
        context    : short label used in error messages (e.g. ``"list_updates"``).

    Raises :
        ProviderError on non-zero exit, timeout, or JSON parse failure.
    """
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_client_cwd()),
            env=_build_env(token),
            timeout=EAS_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderError(f"eas {context}: timed out after {EAS_TIMEOUT_SEC}s") from exc
    except OSError as exc:
        raise ProviderError(f"eas {context}: failed to invoke eas-cli: {exc}") from exc

    if result.returncode != 0:
        # Surface stderr (truncated) — eas-cli puts errors there. Never
        # surface env / argv (which would leak the token).
        stderr_preview = (result.stderr or result.stdout or "").strip()[:500]
        raise ProviderError(f"eas {context}: exit {result.returncode} — {stderr_preview}".rstrip(" —"))

    stdout = result.stdout or ""
    if not parse_json:
        return stdout

    if not stdout.strip():
        # Some eas commands print nothing on success ; return empty dict.
        return {}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        preview = stdout[:200]
        raise ProviderError(f"eas {context}: invalid json output — {preview!r}") from exc


def _verify_main_clean() -> None:
    """Refuse production publish if local HEAD != origin/main HEAD (R34).

    Sequence :
        1. ``git fetch origin main`` — refresh the remote-tracking ref.
        2. ``git rev-parse HEAD`` vs ``git rev-parse origin/main``.

    Any failure (network, permission, missing remote) raises `ProviderError`.
    A clean SHA mismatch raises `RuntimeError` with both short SHAs in the
    message, so the agent / operator can immediately see what's off.
    """
    project = _project_root()

    # Step 1 — fetch. We do NOT pass --depth ; the dev/prod machine has the
    # full clone and we want the canonical origin/main pointer.
    try:
        fetch_result = subprocess.run(
            ["git", "fetch", "origin", "main"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(project),
            timeout=GIT_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"eas pre-publish gate: git fetch failed: {exc}") from exc
    if fetch_result.returncode != 0:
        raise ProviderError(
            f"eas pre-publish gate: git fetch returned exit {fetch_result.returncode} — "
            f"{(fetch_result.stderr or '').strip()[:200]}"
        )

    # Step 2 — compare HEAD vs origin/main.
    head = _git_rev_parse(project, "HEAD")
    origin = _git_rev_parse(project, "origin/main")
    if head != origin:
        raise RuntimeError(
            f"agent-mcp: refusing production publish — HEAD {head[:7]} != origin/main {origin[:7]} (R34). "
            "Sync your local checkout (git pull --ff-only) and ensure the merge commit is visible upstream "
            "before re-trying."
        )


def _git_rev_parse(project: Path, ref: str) -> str:
    """Return the full SHA of `ref` in `project`. ProviderError on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", ref],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(project),
            timeout=GIT_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"eas pre-publish gate: git rev-parse {ref} failed: {exc}") from exc
    if result.returncode != 0:
        raise ProviderError(f"eas pre-publish gate: git rev-parse {ref} returned exit {result.returncode}")
    return result.stdout.strip()


# ---- tool implementations -----------------------------------------------


def eas_list_updates(channel: str = "preview", limit: int = 5) -> list[dict[str, Any]]:
    """List recent EAS updates on a channel. Read-only. Scope: ops.

    Args :
        channel : EAS branch / channel name (e.g. ``"preview"``, ``"production"``).
        limit   : max number of updates returned (eas-cli default 25).

    Returns the parsed JSON list from ``eas update:list --json``. Each entry
    contains at least ``id``, ``message``, ``createdAt``, ``branch``.
    """
    token = _fetch_token()
    argv = [
        "eas",
        "update:list",
        f"--branch={channel}",
        f"--limit={limit}",
        "--json",
        "--non-interactive",
    ]
    payload = _run_eas(argv, token=token, parse_json=True, context="list_updates")

    # eas-cli sometimes wraps the list in `{"currentPage":[...]}` or returns
    # the bare list. Normalize to a list.
    if isinstance(payload, dict):
        for key in ("currentPage", "updates", "data"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        # Single-update dict (rare). Wrap in list for stable return shape.
        return [payload]
    if isinstance(payload, list):
        return payload
    raise ProviderError(f"eas list_updates: unexpected payload shape {type(payload).__name__}")


def eas_list_builds(platform: str = "android", limit: int = 5) -> list[dict[str, Any]]:
    """List recent EAS builds for a platform. Read-only. Scope: ops.

    Args :
        platform : ``"android"`` or ``"ios"``.
        limit    : max number of builds returned.

    Returns the parsed JSON list from ``eas build:list --json``. Each entry
    contains ``id``, ``status``, ``platform``, ``channel``, ``appVersion``,
    ``createdAt``.
    """
    token = _fetch_token()
    argv = [
        "eas",
        "build:list",
        f"--platform={platform}",
        f"--limit={limit}",
        "--json",
        "--non-interactive",
    ]
    payload = _run_eas(argv, token=token, parse_json=True, context="list_builds")

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("currentPage", "builds", "data"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        return [payload]
    raise ProviderError(f"eas list_builds: unexpected payload shape {type(payload).__name__}")


def eas_update_preview(message: str, environment: str = "preview") -> dict[str, Any]:
    """Push EAS Update on preview channel. Mutating, visible action. Scope: admin.

    Always passes ``--environment`` matching ``--channel preview`` (cf KP-57).
    The default environment is ``preview`` ; callers can override (rare —
    e.g. testing a custom EAS environment) but the channel is hardcoded.

    Args :
        message     : commit-message-style description of the OTA payload.
        environment : EAS environment name ; defaults to ``preview``.

    Returns the JSON dict eas-cli prints for the published update group.
    """
    token = _fetch_token()
    argv = [
        "eas",
        "update",
        "--channel",
        "preview",
        "--environment",
        environment,  # default "preview" — KP-57 enforced
        "--message",
        message,
        "--json",
        "--non-interactive",
    ]
    payload = _run_eas(argv, token=token, parse_json=True, context="update_preview")
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list) and payload:
        # eas update --json sometimes returns a list of platform-specific
        # updates ; expose the first as the "primary" record + keep all in
        # `_all`.
        return {"primary": payload[0], "_all": payload}
    raise ProviderError(f"eas update_preview: unexpected payload shape {type(payload).__name__}")


def eas_update_production(message: str) -> dict[str, Any]:
    """Push EAS Update on production channel. Mutating, visible action. Scope: admin.

    Pre-publish gate (R34) :
        Verifies HEAD == origin/main BEFORE invoking eas-cli. If the local
        checkout is on a feature branch / has unmerged commits, raises
        `RuntimeError` and does NOT call eas. This mirrors the manual R34
        gate (\"git fetch && HEAD == origin/main before eas update\").

    Both ``--channel`` and ``--environment`` are hardcoded to ``production``
    (KP-57) — production publishes do not accept any environment override.

    Args :
        message : commit-message-style description of the OTA payload.

    Returns the JSON dict eas-cli prints for the published update group.
    """
    # Step 1 — pre-publish gate. Raises before we touch eas-cli.
    _verify_main_clean()

    # Step 2 — fetch token + invoke eas. Same shape as preview but with both
    # channel + environment hardcoded to production (no override).
    token = _fetch_token()
    argv = [
        "eas",
        "update",
        "--channel",
        "production",
        "--environment",
        "production",
        "--message",
        message,
        "--json",
        "--non-interactive",
    ]
    payload = _run_eas(argv, token=token, parse_json=True, context="update_production")
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list) and payload:
        return {"primary": payload[0], "_all": payload}
    raise ProviderError(f"eas update_production: unexpected payload shape {type(payload).__name__}")


def eas_rollback_to_embedded(channel: str) -> dict[str, Any]:
    """Roll a channel back to the embedded bundle (recovery). Mutating. Scope: admin.

    Use when a freshly-shipped OTA broke the app — this command makes the
    next app launch fall back to the JS bundle compiled into the installed
    APK / AAB, no rebuild required (cf R34 ``broken OTA recovery``).

    Args :
        channel : EAS channel name to roll back (e.g. ``"preview"``).

    Returns the parsed JSON output from eas-cli (success indicator + branch
    metadata).
    """
    token = _fetch_token()
    argv = [
        "eas",
        "update:roll-back-to-embedded",
        "--channel",
        channel,
        "--non-interactive",
    ]
    # eas update:roll-back-to-embedded doesn't always support --json ;
    # we try parse_json first and fall back to raw stdout.
    try:
        payload = _run_eas([*argv, "--json"], token=token, parse_json=True, context="rollback")
    except ProviderError:
        # Retry without --json — capture the human-readable success message.
        raw = _run_eas(argv, token=token, parse_json=False, context="rollback")
        return {"channel": channel, "rolledBack": True, "_stdout": raw.strip()[:500]}

    if isinstance(payload, dict):
        return payload
    return {"channel": channel, "result": payload}


# ---- registration -------------------------------------------------------

# Imperative registration — same pattern as `glitchtip_tools` (chunk 2). The
# autouse `reset_tools_registry` test fixture clears the registry, so we
# need a way to re-populate it deterministically.

_REGISTERED = False


def register_all() -> None:
    """Register the 5 EAS tools into the module-level registry.

    Idempotent — subsequent calls are no-ops, so importing this module from
    multiple places (CLI bootstrap, tests, future docs generators) is safe.
    """
    global _REGISTERED
    if _REGISTERED and "eas_update_preview" in TOOLS_REGISTRY:
        return

    # Per-tool defensive check — `clear_registry()` (used by tests) wipes the
    # registry but not our flag. Cross-check before registering each tool.
    if "eas_list_updates" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(eas_list_updates)
    if "eas_list_builds" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(eas_list_builds)
    if "eas_update_preview" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(eas_update_preview)
    if "eas_update_production" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(eas_update_production)
    if "eas_rollback_to_embedded" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(eas_rollback_to_embedded)

    _REGISTERED = True


def _reset_for_tests() -> None:
    """Test-only — drop the idempotence flag so `register_all()` re-runs."""
    global _REGISTERED
    _REGISTERED = False
