"""
Shared liveness-probe router for all Ratis services.

make_health_router
------------------
Returns an APIRouter exposing a single unauthenticated ``GET /health`` that
replies ``200`` with ``{"status": "ok", "service": <service_name>}``.

The route carries NO dependencies on purpose : a liveness probe must answer
with zero credentials (no JWT, no INTERNAL_API_KEY, no rate-limit), so the
Docker HEALTHCHECK and ``curl /health`` keep working regardless of auth state.
It also performs no DB / Redis / upstream I/O — it reports that the process is
up and serving HTTP, not that every dependency is reachable (that is a
readiness concern, intentionally out of scope here).

Usage in any FastAPI main.py — mount it BEFORE/outside any auth-gated router :
    from ratis_core.health import make_health_router
    app.include_router(make_health_router("ratis_auth"))
"""

from __future__ import annotations

from fastapi import APIRouter


def make_health_router(service_name: str) -> APIRouter:
    """Build an APIRouter with an unauthenticated ``GET /health`` for ``service_name``."""
    router = APIRouter()

    @router.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Liveness probe — process is up and serving HTTP. No auth, no I/O."""
        return {"status": "ok", "service": service_name}

    return router
