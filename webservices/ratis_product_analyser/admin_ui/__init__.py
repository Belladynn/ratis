"""Admin mini UI — FastAPI + HTMX + Tailwind.

Served by the PA service under ``/admin/ui/*`` when ``ADMIN_API_KEY``
is set at startup. Same defense-in-depth pattern as the JSON
``/api/v1/admin/*`` API : the router is only mounted when the env var
is present, so unauth'd 404 instead of 401-storm.

This is the "shell" PR (UI-1) of the admin mini UI plan
(see ARCH_admin_endpoints.md § Mini UI). Three pages :

- Stores pending — bulk validate user_suggested stores (PR5 backend)
- Knowledge OCR queue — apply correction / dismissal (PR9 backend)
- Audit log viewer — query by receipt/parsed-ticket/scan id (PR4 backend)

Backend calls are **direct in-process** to the same DB session
(``services.knowledge_admin_service`` / ``services.store_admin_service``
/ inline SQL for audit log already in ``routes.admin.observability``).
We do NOT make HTTP loopback calls : the mini UI lives inside the same
FastAPI app, so an HTTP round-trip would just add latency + double-auth.

Auth is cookie-based : the operator logs in once with the shared
``ADMIN_API_KEY`` and a self-declared handle ; the cookie carries a
``sha256(key + handle)`` opaque token verified per-request via
``hmac.compare_digest``. The header-based ``/admin/*`` JSON API stays
in place for external admin tooling — both can run in parallel.
"""

from __future__ import annotations

from .routes import router

__all__ = ["router"]
