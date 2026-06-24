import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")  # must run before any import that reads os.environ at module level

import logging

from admin_ui import router as admin_ui_router
from admin_ui.db_approvals import router as admin_ui_db_approvals_router
from admin_ui.routes import _unauthorized_to_login_handler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from limiter import limiter
from ratis_core.exceptions import Conflict, Gone, NotFound, ServiceUnavailable, UnprocessableEntity
from ratis_core.health import make_health_router
from ratis_core.middleware import RequestIDMiddleware
from ratis_core.observability import init_sentry
from ratis_core.startup import require_env, require_env_min_length
from ratis_core.suggestions_config import load_curated_eans
from routes.admin import router as admin_router
from routes.admin.db_pipeline import router as admin_db_pipeline_router
from routes.product import router as product_router
from routes.scan import router as scan_router
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_env(
        "DATABASE_URL",
        "JWT_PUBLIC_KEY_PATH",
        "REDIS_URL",
        "R2_ENDPOINT_URL",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET_NAME",
        "N8N_RESUME_SECRET",
        "INTERNAL_API_KEY",
    )
    if not os.environ.get("ADMIN_API_KEY"):
        logger.warning("ADMIN_API_KEY not set — admin endpoints (/api/v1/admin/*) are disabled")
    else:
        # M1 (audit sécurité 2026-05-03) — enforce a minimum key length so the
        # HMAC-SHA256 cookie token (admin_ui.auth.compute_token) has a real
        # security margin against brute-force / rainbow-table attacks.
        require_env_min_length("ADMIN_API_KEY", 32)
        # Admin mini UI uses AU_BASE_URL to query AU's user-lookup endpoints
        # and RW_BASE_URL for admin-settings cross-service calls (Bloc C).
        # When the admin router is mounted, the UI is mounted too — fail
        # fast if either cross-service URL is missing rather than serving
        # a half-broken page that 503s on every click.
        require_env("AU_BASE_URL", "RW_BASE_URL")
    # Validate the curated suggestions config at boot — fail-fast if it's
    # missing or malformed so a bad deploy crashes the healthcheck rather
    # than serving 500s on the first user request (R20).
    _curated = load_curated_eans()
    logger.info("default_suggestions: curated config loaded (%d EANs)", len(_curated))
    init_sentry("ratis_product_analyser")
    yield


app = FastAPI(title="ratis_product_analyser", lifespan=lifespan)
app.add_middleware(RequestIDMiddleware)
app.state.limiter = limiter

# Unauthenticated liveness probe (Docker HEALTHCHECK + ops). Mounted first,
# outside any auth / rate-limit dependency — see ratis_core.health.
app.include_router(make_health_router("ratis_product_analyser"))
app.include_router(scan_router, prefix="/api/v1/scan")
app.include_router(product_router, prefix="/api/v1/product")
# Admin routes mounted only when ADMIN_API_KEY is configured. This is the
# safe default : in dev/test the routes are absent (404 instead of an
# unauth'd path that would 403 with a misleading message).
if os.environ.get("ADMIN_API_KEY"):
    app.include_router(admin_router, prefix="/api/v1")
    # Mini admin UI — same defense-in-depth gate as the JSON API : no
    # ADMIN_API_KEY → routes absent (404) instead of 401-storm.
    app.include_router(admin_ui_router, prefix="/admin/ui")
    app.include_router(admin_ui_db_approvals_router, prefix="/admin/ui")

# HSP3 — db-write-pipeline endpoints (machine→machine n8n, INTERNAL_API_KEY).
# Mounted unconditionally — gated by verify_internal_key, not ADMIN_API_KEY.
app.include_router(admin_db_pipeline_router, prefix="/api/v1")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> Response:
    """Swap mini-UI 401s for a 302 redirect to /admin/ui/login.

    Other HTTPExceptions fall through to FastAPI's default JSON
    handler — we only intercept the ``login_required`` shape used by
    the admin_ui session dep, scoped to ``/admin/ui/*`` paths so the
    JSON ``/api/v1/admin/*`` routes keep their existing 401/403/404
    response shapes intact.
    """
    if request.url.path.startswith("/admin/ui") and exc.status_code == 401 and exc.detail == "login_required":
        return _unauthorized_to_login_handler(request, exc)
    # Delegate to FastAPI's default handler — preserves existing JSON
    # shape for every other route (HTTPException raised by services,
    # admin endpoints, etc).
    from fastapi.exception_handlers import http_exception_handler as _default

    return await _default(request, exc)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": "rate_limit_exceeded"})


@app.exception_handler(NotFound)
async def not_found_handler(request: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": exc.detail})


@app.exception_handler(Conflict)
async def conflict_handler(request: Request, exc: Conflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": exc.detail})


@app.exception_handler(ServiceUnavailable)
async def service_unavailable_handler(request: Request, exc: ServiceUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": exc.detail})


@app.exception_handler(Gone)
async def gone_handler(request: Request, exc: Gone) -> JSONResponse:
    return JSONResponse(status_code=410, content={"detail": exc.detail})


@app.exception_handler(UnprocessableEntity)
async def unprocessable_entity_handler(request: Request, exc: UnprocessableEntity) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal_server_error"})
