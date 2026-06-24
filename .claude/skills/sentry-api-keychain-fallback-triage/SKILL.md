---
name: sentry-api-keychain-fallback-triage
description: "Fallback workflow for diagnosing Sentry incidents when the MCP issue/event endpoints return misleading 404s — discover numeric IDs, pull the token from Keychain, query the API directly."
---

# sentry-api-keychain-fallback-triage

The Sentry MCP can sometimes list issues but return a misleading 404 on
issue detail or events — often because of slug-vs-numeric-ID handling. The
fallback is to discover the real org/project numeric IDs, retrieve the
Sentry token from Keychain without printing it, and query the API
directly. This skill is that recovery path. Keep it about the *discovery
steps*, not hardcoded org/account/path values, so it survives API changes.

## When to Use

- Sentry MCP can list issues but issue detail/events fail, or
  project-slug vs numeric-ID handling is inconsistent.
- You need incident detail (exception, breadcrumbs, frames) the MCP won't
  return.

## When NOT to Use

- The Sentry MCP is working normally — use it; the direct-API fallback is
  more friction for no gain.
- The token isn't provisioned in Keychain and you can't establish access
  — escalate rather than improvising credential handling.
- The real blocker is connectivity to Sentry itself, not the MCP — fix
  the network/auth first.

## Procedure

1. **Discover the numeric IDs.** Resolve org/project numeric IDs via the
   direct Sentry API rather than relying on slugs the MCP may mishandle.
2. **Get the token safely.** Retrieve the Sentry token from Keychain
   **without printing it** to logs or output.
3. **Query directly.** Hit issue / events / latest-event endpoints with
   the numeric IDs and summarize the exception, breadcrumbs, handled
   status, and app frames — not raw dumps.
