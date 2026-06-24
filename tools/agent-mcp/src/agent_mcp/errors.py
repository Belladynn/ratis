"""Domain exceptions for ratis-agent-mcp.

Each exception maps 1:1 to an audit-log `status` value so post-mortem analysis
of `~/.local/state/ratis-agent-mcp/audit.log` is straightforward (DA-48).
"""

from __future__ import annotations


class AgentMcpError(Exception):
    """Base class for all agent-mcp domain errors.

    Subclasses MUST define `STATUS` (str), the audit-log status string used
    when this error is raised inside a tool dispatch.
    """

    STATUS: str = "error"


class ForbiddenTool(AgentMcpError):
    """Caller token does not match the tool's required scope (DA-44).

    Raised either when the presented MCP_AUTH_TOKEN matches no known role
    or when the resolved role lacks the scope declared by the tool.
    """

    STATUS = "forbidden_tool"


class KeychainMiss(AgentMcpError):
    """A requested provider secret is absent from the macOS Keychain (DA-43).

    Triggered when `security find-generic-password` returns exit code 44.
    Surfaced to the caller so they can run `agent-mcp keychain set <provider>`.
    """

    STATUS = "keychain_miss"


class ProviderError(AgentMcpError):
    """The downstream provider (Sentry, EAS, ...) returned an error.

    Wraps any non-2xx HTTP response or non-zero subprocess exit from a tool
    module. The original message is preserved in the audit log but never
    contains secrets (modules redact before raising).
    """

    STATUS = "provider_error"


class AuditError(AgentMcpError):
    """Failed to append a line to the audit log (DA-48).

    Should be exceedingly rare (disk full, permission flip). When raised the
    server falls back to writing the line to stderr — the call still proceeds
    so the user is not blocked, but observability is degraded.
    """

    STATUS = "audit_error"
