"""Filesystem layout for ratis-agent-mcp (XDG-aligned).

A single source of truth for the paths the runtime reads / writes :

* `~/.config/ratis-agent-mcp/tokens.env`        — admin + ops MCP tokens (chmod 600)
* `~/.local/state/ratis-agent-mcp/audit.log`    — append-only JSONL audit log (DA-48)

Paths are resolved lazily (each call to the helper) so tests can override
`HOME` via `monkeypatch.setenv("HOME", tmp_path)` without re-importing.
"""

from __future__ import annotations

import os
from pathlib import Path

KEYCHAIN_SERVICE = "ratis-agent-mcp"
"""Default service name used when calling macOS `security` CLI (DA-43)."""

TOKENS_FILE_NAME = "tokens.env"
AUDIT_FILE_NAME = "audit.log"


def config_dir() -> Path:
    """Return the per-user config directory (`~/.config/ratis-agent-mcp/`).

    Honours `XDG_CONFIG_HOME` when set, falling back to `~/.config`.
    """
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "ratis-agent-mcp"


def state_dir() -> Path:
    """Return the per-user state directory (`~/.local/state/ratis-agent-mcp/`).

    Honours `XDG_STATE_HOME` when set, falling back to `~/.local/state`.
    """
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "ratis-agent-mcp"


def tokens_file() -> Path:
    """Path to the MCP admin/ops tokens file (chmod 600 enforced at write)."""
    return config_dir() / TOKENS_FILE_NAME


def audit_log_file() -> Path:
    """Path to the JSONL audit log (chmod 600 enforced at create)."""
    override = os.environ.get("MCP_AUDIT_LOG_PATH")
    if override:
        return Path(override).expanduser()
    return state_dir() / AUDIT_FILE_NAME


def secrets_meta_db_file() -> Path:
    """Path to the SQLite metadata DB for the secrets vault (Module 10).

    Honours ``RATIS_SECRETS_DB_PATH`` env var for test isolation,
    otherwise defaults to ``<state_dir>/ratis_secrets_meta.db``.
    """
    override = os.environ.get("RATIS_SECRETS_DB_PATH")
    if override:
        return Path(override).expanduser()
    return state_dir() / "ratis_secrets_meta.db"


def secrets_audit_dir() -> Path:
    """Directory for secrets vault audit JSONL files (Module 10).

    Honours ``RATIS_SECRETS_AUDIT_DIR`` env var for test isolation,
    otherwise defaults to ``<state_dir>/audit``.
    """
    override = os.environ.get("RATIS_SECRETS_AUDIT_DIR")
    if override:
        return Path(override).expanduser()
    return state_dir() / "audit"


def ensure_dir(path: Path, *, mode: int = 0o700) -> None:
    """Create `path` (and parents) with restrictive permissions if missing."""
    path.mkdir(parents=True, exist_ok=True, mode=mode)
