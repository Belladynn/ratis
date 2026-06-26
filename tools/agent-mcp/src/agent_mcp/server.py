"""MCP server runtime for ratis-agent-mcp (DA-45 / DA-46).

The runtime is split in two clearly separated layers :

1. **`Dispatcher`** — pure-Python core. Holds the tool registry, runs the
   `auth → dispatch → audit` pipeline. Has no MCP SDK dependency, so it can
   be unit-tested in isolation (and re-used over a different transport in
   the future, e.g. HTTP MCP per DA-45 migration scenario).
2. **`build_mcp_server()`** — thin glue that hands the dispatcher's
   `list_tools` / `call_tool` to the Anthropic MCP SDK over stdio.

Tool modules register themselves via the `@register_tool(scope=...)` decorator
exposed at module level. Foundation V0 ships an EMPTY registry — the modules
in chunks 2-7 will populate it.

The decorator captures :
    * `fn`            — the implementation (sync or async).
    * `scope`         — declared via the decorator argument (DA-44).
    * `description`   — first line of the function docstring.
    * `input_schema`  — derived from the signature via Pydantic if available,
                        else a permissive `{"type": "object"}` placeholder.

This is the auto-schemagen claim from DA-49. We deliberately do NOT depend on
the MCP SDK's own Pydantic introspection — keeping the schema construction
in-house lets us version the format independently and stay testable without
the SDK installed.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .audit import AuditLog, redact_args
from .auth import AuthGate, ToolScope
from .config import audit_log_file
from .errors import AgentMcpError, ForbiddenTool, ProviderError

ToolImpl = Callable[..., Any]
"""A registered tool implementation — sync or async, freely typed kwargs."""


@dataclass(slots=True)
class ToolEntry:
    """Metadata + impl for one registered tool."""

    name: str
    fn: ToolImpl
    scope: ToolScope
    description: str
    input_schema: dict[str, Any]


# Module-level registry — populated as modules import. Foundation = empty.
TOOLS_REGISTRY: dict[str, ToolEntry] = {}


def register_tool(
    scope: ToolScope,
    *,
    name: str | None = None,
) -> Callable[[ToolImpl], ToolImpl]:
    """Decorator used by tool modules to expose a function as an MCP tool.

    Usage (in a tool module, e.g. `tools/glitchtip_tools.py`) :

        @register_tool(scope="ops")
        def glitchtip_list_issues(project: str, limit: int = 10) -> list[dict]:
            \"\"\"List unresolved issues.\"\"\"
            ...

    The first line of the docstring becomes the tool description seen by
    Claude in `tools/list`. Pydantic-derived JSON Schema is built from the
    signature — see `_build_input_schema()`.
    """

    def decorator(fn: ToolImpl) -> ToolImpl:
        tool_name = name or fn.__name__
        if tool_name in TOOLS_REGISTRY:
            raise ValueError(f"tool '{tool_name}' already registered (by {TOOLS_REGISTRY[tool_name].fn!r})")
        description = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
        input_schema = _build_input_schema(fn)
        TOOLS_REGISTRY[tool_name] = ToolEntry(
            name=tool_name,
            fn=fn,
            scope=scope,
            description=description,
            input_schema=input_schema,
        )
        return fn

    return decorator


def clear_registry() -> None:
    """Test helper — reset the registry (Foundation tests only)."""
    TOOLS_REGISTRY.clear()


def load_builtin_tools() -> None:
    """Register every tool module shipped with agent-mcp (chunks 2-7).

    Called once from `build_mcp_server()` at production boot. Each module
    exposes a `register_all()` that is idempotent — see e.g.
    `agent_mcp.tools.glitchtip_tools.register_all`. Tests register modules
    individually for isolation.

    Registration is done HERE rather than as an import-time decorator
    side-effect so the dispatcher tests stay hermetic (they `clear_registry`
    in autouse fixtures and re-populate per test).
    """
    # GlitchTip — chunk 2 (was `sentry_tools` pre-DA-47, Sentry SaaS sunset).
    from .tools import glitchtip_tools

    glitchtip_tools.register_all()

    # EAS — chunk 3.
    from .tools import eas_tools

    eas_tools.register_all()

    # GitHub — chunk 4.
    from .tools import github_tools

    github_tools.register_all()

    # Notion — REMOVED 2026-05-31 (chunk 5 d'origine). Sunset → GlitchTip self-hosted.
    # Voir ARCH_incident_management.md pour le remplaçant (Sentry-compatible local).
    # Tickets / incidents historiquement gérés via notion_tools sont maintenant gérés
    # via le wrapper CLI `glt` (~/glitchtip/bin/glt) qui appelle l'API GlitchTip.

    # Stripe — chunk 6.
    from .tools import stripe_tools

    stripe_tools.register_all()

    # R2 (Cloudflare, S3-compatible) — chunk 7.
    from .tools import r2_tools

    r2_tools.register_all()

    # Database (Ratis internal Postgres) — module 8.
    from .tools import db_tools

    db_tools.register_all()

    # Docs MCP (ARCH_INVENTORY-backed search/get/find/list) — module 9
    # (phase C of agentic-docs).
    from .tools import docs_tools

    docs_tools.register_all()

    # Secrets vault (JIT token lifecycle) — module 10.
    from .tools import secrets_tools

    secrets_tools.register_all()

    # We do NOT want a generic "scan the package" loader — explicit
    # listing is the audit-friendly choice (you can see at a glance which
    # tools are loaded into the runtime).


def _build_input_schema(fn: ToolImpl) -> dict[str, Any]:
    """Derive a permissive JSON Schema from a function signature.

    Tries Pydantic first (DA-49) for parameter validation ; if Pydantic is
    not importable for some reason we fall back to a fully-permissive
    object schema. Either way the runtime never crashes — schema generation
    is best-effort metadata for the MCP `tools/list` response.
    """
    try:
        from pydantic import create_model
    except ImportError:  # pragma: no cover — pydantic is a hard dep.
        return {"type": "object", "additionalProperties": True}

    sig = inspect.signature(fn)
    fields: dict[str, Any] = {}
    for param_name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            # *args / **kwargs unsupported in MCP schema — skip silently.
            continue
        annotation = param.annotation if param.annotation is not inspect.Parameter.empty else Any
        default = param.default if param.default is not inspect.Parameter.empty else ...
        fields[param_name] = (annotation, default)

    if not fields:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    try:
        model = create_model(f"{fn.__name__}_input", **fields)
        schema = model.model_json_schema()
        # Pydantic emits `$defs`/`title` etc. — keep them, MCP SDK is fine with extras.
        return schema
    except Exception:  # pragma: no cover — defensive fallback for exotic types.
        return {"type": "object", "additionalProperties": True}


@dataclass(slots=True)
class DispatchResult:
    """Outcome of `Dispatcher.dispatch()`. Audit-log-friendly."""

    status: str
    result: Any = None
    error: str | None = None
    latency_ms: int = 0
    caller: str | None = None


class Dispatcher:
    """Pure-Python `auth → dispatch → audit` pipeline.

    Instantiated once per server boot. Holds references to the auth gate and
    the audit log ; reads the registry at call time so module imports done
    after construction still take effect.
    """

    def __init__(
        self,
        *,
        auth: AuthGate | None = None,
        audit: AuditLog | None = None,
        registry: dict[str, ToolEntry] | None = None,
    ) -> None:
        self.auth = auth or AuthGate()
        self.audit = audit or AuditLog(audit_log_file())
        # Reference, not copy — modules can register after construction.
        self.registry: dict[str, ToolEntry] = registry if registry is not None else TOOLS_REGISTRY

    def list_tools(self) -> list[dict[str, Any]]:
        """Return tool metadata in MCP `tools/list` shape.

        Foundation V0 returns an empty list (no tool modules loaded). The
        return shape matches what the MCP SDK expects so wiring is a no-op.
        """
        return [
            {
                "name": entry.name,
                "description": entry.description,
                "inputSchema": entry.input_schema,
            }
            for entry in self.registry.values()
        ]

    async def dispatch(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        presented_token: str | None,
    ) -> DispatchResult:
        """Run the full pipeline for one tool call. Always writes audit.

        Order :
            1. resolve caller from token (raises ForbiddenTool if unknown).
               Pre-resolve happens BEFORE registry lookup so an attacker
               probing for tool names with a bad token gets the same
               `forbidden_tool` response regardless.
            2. lookup tool ; missing → audit `tool_not_registered`.
            3. enforce scope ; mismatch → audit `forbidden_tool`.
            4. invoke (await if coroutine).
            5. audit `ok` with latency.

        Errors raised by the tool itself surface as `provider_error` ; any
        other unexpected exception is also wrapped to `provider_error` with
        the exception type prefixed.
        """
        start = time.monotonic()
        caller: str | None = None
        args_redacted = redact_args(arguments or {})

        # 1. Auth resolution.
        try:
            caller = self.auth.resolve_caller(presented_token)
        except ForbiddenTool as exc:
            return self._finish(
                caller=None,
                tool=tool_name,
                args_redacted=args_redacted,
                status="forbidden_tool",
                start=start,
                error=str(exc),
            )

        # 2. Tool lookup.
        entry = self.registry.get(tool_name)
        if entry is None:
            return self._finish(
                caller=caller,
                tool=tool_name,
                args_redacted=args_redacted,
                status="tool_not_registered",
                start=start,
                error=f"tool '{tool_name}' is not registered",
            )

        # 3. Scope enforcement.
        try:
            AuthGate.check_scope(caller, entry.scope)
        except ForbiddenTool as exc:
            return self._finish(
                caller=caller,
                tool=tool_name,
                args_redacted=args_redacted,
                status="forbidden_tool",
                start=start,
                error=str(exc),
            )

        # 4. Invoke.
        try:
            value = entry.fn(**(arguments or {}))
            if inspect.isawaitable(value):
                value = await value
        except AgentMcpError as exc:
            return self._finish(
                caller=caller,
                tool=tool_name,
                args_redacted=args_redacted,
                status=exc.STATUS,
                start=start,
                error=str(exc),
            )
        except Exception as exc:
            wrapped = ProviderError(f"{type(exc).__name__}: {exc}")
            return self._finish(
                caller=caller,
                tool=tool_name,
                args_redacted=args_redacted,
                status=wrapped.STATUS,
                start=start,
                error=str(wrapped),
            )

        # 5. Success.
        return self._finish(
            caller=caller,
            tool=tool_name,
            args_redacted=args_redacted,
            status="ok",
            start=start,
            error=None,
            result=value,
        )

    def _finish(
        self,
        *,
        caller: str | None,
        tool: str,
        args_redacted: dict[str, Any],
        status: str,
        start: float,
        error: str | None,
        result: Any = None,
    ) -> DispatchResult:
        """Helper — write audit + assemble the result envelope."""
        latency_ms = int((time.monotonic() - start) * 1000)
        # Caller field "anonymous" when auth failed before role resolution —
        # keeps the JSONL schema strict (no nulls in caller column).
        audit_caller = caller or "anonymous"
        # Audit failures are soft — AuditLog has already mirrored the line
        # to stderr (DA-48). We don't want to block the caller because the
        # state directory hit a permission flip.
        with contextlib.suppress(AgentMcpError):
            self.audit.write(
                caller=audit_caller,
                tool=tool,
                args_redacted=args_redacted,
                status=status,  # type: ignore[arg-type]
                latency_ms=latency_ms,
                error=error,
            )
        return DispatchResult(
            status=status,
            result=result,
            error=error,
            latency_ms=latency_ms,
            caller=caller,
        )


# --- MCP SDK glue ---------------------------------------------------------


def build_mcp_server(dispatcher: Dispatcher | None = None) -> Any:
    """Build the MCP `Server` instance and wire dispatcher handlers.

    Imported lazily so tests don't require the `mcp` package on the import
    path — they exercise the `Dispatcher` directly. In production, this is
    called once from `cli:main` when `agent-mcp serve` is invoked.

    Returns the `mcp.server.Server` instance ready to be `await`-ed via
    `mcp.server.stdio.stdio_server()`. The CLI handles the asyncio runtime.
    """
    from mcp.server import Server
    from mcp.types import TextContent, Tool

    from . import __server_name__, __version__

    # Populate the registry with the built-in tool modules (idempotent — each
    # module's `register_all()` short-circuits if already wired).
    load_builtin_tools()

    disp = dispatcher or Dispatcher()
    server: Any = Server(__server_name__, version=__version__)

    @server.list_tools()
    async def _list_tools() -> list[Any]:
        return [
            Tool(
                name=meta["name"],
                description=meta["description"],
                inputSchema=meta["inputSchema"],
            )
            for meta in disp.list_tools()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
        # Read the caller token at the moment of the call — Claude Code may
        # rotate it across sessions. Going through `auth` keeps the source of
        # truth in one place.
        token = disp.auth.presented_token_from_env()
        outcome = await disp.dispatch(
            tool_name=name,
            arguments=arguments or {},
            presented_token=token,
        )
        # MCP `call_tool` is expected to return content blocks. We surface
        # the JSON-encoded result OR error envelope as a single TextContent
        # block — modules can later return richer structures by adapting
        # this handler.
        import json

        envelope: dict[str, Any] = {
            "status": outcome.status,
            "latency_ms": outcome.latency_ms,
        }
        if outcome.error is not None:
            envelope["error"] = outcome.error
        if outcome.result is not None and outcome.status == "ok":
            envelope["result"] = outcome.result
        return [TextContent(type="text", text=json.dumps(envelope, default=str))]

    return server


async def serve_stdio() -> None:
    """Production entry point — run the MCP server over stdio (DA-45).

    Imported lazily so the dispatcher tests don't require the MCP SDK.
    """
    from mcp.server.stdio import stdio_server

    server = build_mcp_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def run_serve() -> None:
    """Synchronous wrapper around `serve_stdio` — used by the CLI."""
    asyncio.run(serve_stdio())
