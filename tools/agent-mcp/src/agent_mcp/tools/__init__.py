"""Provider-specific tool modules for ratis-agent-mcp.

Each module under this package exposes typed Python functions decorated with
`@register_tool(scope=...)` from `agent_mcp.server`. Importing a module is
sufficient to register its tools — see `agent_mcp.server.load_builtin_tools()`.
"""
