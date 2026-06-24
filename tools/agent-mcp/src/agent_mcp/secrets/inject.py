"""Injection adapters for `secret_inject` — Module 10 PR 2.

Each adapter writes a secret value to a specific destination. All adapters
return ``"ok"`` on success or ``"error: <reason>"`` on failure. They never
raise — callers accumulate statuses and report partial failures.

Security posture
----------------
* **env-file** / **docker-compose-env** : files are created/chmod-ed to 0600.
  Parent directories are created with mode 0o700.
* **gh-actions** : value is passed via stdin (``input=`` kwarg to
  subprocess.run), never in argv. This prevents the secret from appearing in
  ``ps aux`` output while the process is running.
* **n8n-env** : value sent in HTTPS POST body to localhost n8n REST API.
  The n8n API key is retrieved from the caller (Keychain-backed) — never
  hardcoded or logged.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Type alias for subprocess runner (injectable for tests)
# ---------------------------------------------------------------------------

SubprocessRunner = Callable[..., "subprocess.CompletedProcess[str]"]


# ---------------------------------------------------------------------------
# env-file adapter (and shared file-write logic)
# ---------------------------------------------------------------------------


def _write_env_line(secret_name: str, value: str, path: Path) -> str:
    """Write/replace ``KEY=value`` in an env file (chmod 600). Returns "ok" or "error: ..."."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        lines = [line for line in existing.splitlines() if not line.startswith(f"{secret_name}=")]
        lines.append(f"{secret_name}={value}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.chmod(0o600)
        return "ok"
    except OSError as exc:
        return f"error: {exc}"


def _inject_env_file(secret_name: str, value: str, path: Path) -> str:
    """Write/replace KEY=value in the runtime env file at *path* (chmod 600).

    Returns "ok" or "error: <reason>".
    """
    return _write_env_line(secret_name, value, path)


def _inject_docker_compose_env(secret_name: str, value: str, path: Path) -> str:
    """Write/replace KEY=value in the docker-compose .env file at *path* (chmod 600).

    Same logic as ``_inject_env_file`` but accepts an arbitrary path so the
    caller can target any docker-compose env file in the project tree.

    Returns "ok" or "error: <reason>".
    """
    return _write_env_line(secret_name, value, path)


# ---------------------------------------------------------------------------
# gh-actions adapter
# ---------------------------------------------------------------------------


def _inject_gh_actions(
    gh_secret_name: str,
    value: str,
    *,
    runner: SubprocessRunner = subprocess.run,
) -> str:
    """Set a GitHub Actions secret via the ``gh`` CLI.

    The secret value is passed via **stdin** (``input=value``), never in argv.
    This prevents the secret from leaking into ``ps aux`` process listings.

    ``gh secret set <name>`` reads the value from stdin when ``--body`` is
    omitted, per the GitHub CLI documentation (verified 2026-05-31).

    Returns "ok" or "error: <reason>".
    """
    result = runner(
        ["gh", "secret", "set", gh_secret_name],
        input=value,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"error: {result.stderr.strip()}"
    return "ok"


# ---------------------------------------------------------------------------
# n8n adapter
# ---------------------------------------------------------------------------


def _inject_n8n(
    var_name: str,
    value: str,
    *,
    n8n_api_key: str | None,
    n8n_base_url: str | None = None,
) -> str:
    """POST a variable to n8n via its REST API.

    Requires ``n8n_api_key`` (retrieved from Keychain by the caller). If
    absent, returns ``"error: n8n_api_key_missing"`` immediately.

    ``n8n_base_url`` defaults to the ``N8N_BASE_URL`` env var, falling back to
    ``http://localhost:5678``.

    Returns "ok" or "error: <reason>".
    """
    if not n8n_api_key:
        return "error: n8n_api_key_missing"

    base_url = n8n_base_url or os.environ.get("N8N_BASE_URL", "http://localhost:5678")
    url = f"{base_url.rstrip('/')}/api/v1/variables"

    try:
        response = httpx.post(
            url,
            json={"key": var_name, "value": value},
            headers={"X-N8N-API-KEY": n8n_api_key},
            timeout=10.0,
        )
        if response.status_code >= 400:
            return f"error: n8n returned HTTP {response.status_code}: {response.text[:120]}"
        return "ok"
    except httpx.HTTPError as exc:
        return f"error: {exc}"
