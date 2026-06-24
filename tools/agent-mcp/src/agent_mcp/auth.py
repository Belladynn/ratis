"""Caller authentication & per-tool scope enforcement (DA-44).

Two MCP caller tokens live side by side :

* `MCP_AUTH_ADMIN_TOKEN` — full access (interactive humans).
* `MCP_AUTH_OPS_TOKEN`   — read + whitelisted writes (Claude SAs, n8n).

Both are persisted to `~/.config/ratis-agent-mcp/tokens.env` (chmod 600) at
`agent-mcp init` time. The runtime loads them at construction. The presented
token comes from the `MCP_AUTH_TOKEN` environment variable populated by the
client (Claude Code reads `~/.claude/mcp.json env`).

Comparisons are constant-time (`secrets.compare_digest`) to avoid timing
side-channels on token discovery.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Literal

from .config import tokens_file
from .errors import ForbiddenTool

CallerRole = Literal["admin", "ops"]
"""Resolved caller identity once their token has matched a known role."""

ToolScope = Literal["admin", "ops", "both"]
"""Scope a tool declares via the `@register_tool` decorator."""

ADMIN_ENV = "MCP_AUTH_ADMIN_TOKEN"
OPS_ENV = "MCP_AUTH_OPS_TOKEN"
PRESENTED_ENV = "MCP_AUTH_TOKEN"


def _parse_tokens_env(text: str) -> dict[str, str]:
    """Parse a minimal KEY=VALUE per line file (no shell features).

    Lines starting with `#` and blank lines are ignored. Surrounding
    quotes (single or double) on the value are stripped to be tolerant of
    `KEY="value"` style. We deliberately avoid `python-dotenv` to keep the
    dependency surface tiny — this format is internal-only.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


class AuthGate:
    """Resolve callers to roles and enforce per-tool scopes.

    The two role tokens are loaded once at construction. Order of precedence
    for each role : explicit kwarg > process env > `tokens.env` file. This
    matches the typical execution path :

    * `agent-mcp serve` invoked by Claude Code — env is empty, file is read.
    * Tests — pass tokens explicitly via kwargs.
    * Manual override — export envs in shell before running.

    Raises `ForbiddenTool` (and never reveals which role failed) when the
    presented token matches no role or when the role lacks the required scope.
    """

    def __init__(
        self,
        *,
        admin_token: str | None = None,
        ops_token: str | None = None,
        tokens_path: Path | None = None,
    ) -> None:
        env_admin = os.environ.get(ADMIN_ENV)
        env_ops = os.environ.get(OPS_ENV)

        file_tokens: dict[str, str] = {}
        path = tokens_path if tokens_path is not None else tokens_file()
        if path.exists():
            file_tokens = _parse_tokens_env(path.read_text(encoding="utf-8"))

        self._admin: str | None = admin_token or env_admin or file_tokens.get(ADMIN_ENV) or None
        self._ops: str | None = ops_token or env_ops or file_tokens.get(OPS_ENV) or None
        # An MCP with no tokens at all is unusable — fail closed at first call.

    def resolve_caller(self, presented_token: str | None) -> CallerRole:
        """Return the role matching `presented_token` or raise `ForbiddenTool`.

        Constant-time comparison, no info leak about which role would match.
        Empty / missing tokens are rejected uniformly.
        """
        if not presented_token:
            raise ForbiddenTool("missing caller token")

        # We compare against both registered tokens regardless of result to
        # avoid a measurable timing difference between admin / ops / unknown.
        admin_match = self._admin is not None and secrets.compare_digest(presented_token, self._admin)
        ops_match = self._ops is not None and secrets.compare_digest(presented_token, self._ops)

        if admin_match:
            return "admin"
        if ops_match:
            return "ops"
        raise ForbiddenTool("unknown caller token")

    @staticmethod
    def check_scope(caller: CallerRole, required_scope: ToolScope) -> None:
        """Enforce caller-vs-tool scope (DA-44).

        Hierarchy : `admin` > `ops`. `both` accepts either role. An admin can
        always invoke an ops-tagged tool — convenient for interactive
        debugging. The reverse (ops calling an admin tool) raises.
        """
        if required_scope == "both":
            return
        if required_scope == "ops":
            # Admin can call ops tools too (admin > ops).
            if caller in ("admin", "ops"):
                return
        elif required_scope == "admin" and caller == "admin":
            return
        raise ForbiddenTool(f"caller '{caller}' lacks scope '{required_scope}'")

    def presented_token_from_env(self) -> str | None:
        """Read the caller token off `MCP_AUTH_TOKEN` env var.

        This is the contract with Claude Code : it injects the token into the
        MCP server process env per `~/.claude/mcp.json`. Tests can monkeypatch
        the env var freely.
        """
        return os.environ.get(PRESENTED_ENV)
