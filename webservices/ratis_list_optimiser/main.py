from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from ratis_core.health import make_health_router
from ratis_core.middleware import RequestIDMiddleware
from ratis_core.observability import init_sentry
from ratis_core.settings import load_settings
from ratis_core.startup import require_env
from routes.optimization import router as optimization_router
from routes.shopping_lists import router as shopping_lists_router
from routes.suggestions import router as suggestions_router

logger = logging.getLogger(__name__)

_REQUIRED_KEYS = {
    "min_items_per_store",
    "suggestion_min_receipts",
    "suggestion_frequency_threshold",
    "route_expiry_hours",
    "national_avg_min_datapoints",
    "osrm_timeout_seconds",
    "max_items_per_list",
    "max_stores_in_route",
    "max_quantity_per_item",
    "max_templates_per_user",
    "default_search_radius_km",
    "default_transport_mode",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_env("OSRM_BASE_URL")
    require_env("REDIS_URL")
    require_env("JWT_PUBLIC_KEY_PATH")

    try:
        cfg = load_settings()
    except FileNotFoundError:
        raise RuntimeError(
            "Settings unavailable: app_settings table empty/unreachable and "
            "ratis_settings.json not found in package — aborting."
        )

    if "list_optimiser" not in cfg:
        raise RuntimeError(
            "Settings missing 'list_optimiser' section — aborting. Check app_settings table or ratis_settings.json."
        )

    missing = _REQUIRED_KEYS - cfg["list_optimiser"].keys()
    if missing:
        raise RuntimeError(
            f"Settings.list_optimiser missing required keys: {', '.join(sorted(missing))} — aborting. "
            "Check app_settings table or ratis_settings.json."
        )

    init_sentry("ratis_list_optimiser")
    app.state.cfg = cfg
    logger.info("ratis_list_optimiser started — config loaded")
    yield


app = FastAPI(title="ratis_list_optimiser", lifespan=lifespan)
app.add_middleware(RequestIDMiddleware)

# Unauthenticated liveness probe (Docker HEALTHCHECK + ops). Mounted first,
# outside any auth dependency — see ratis_core.health.
app.include_router(make_health_router("ratis_list_optimiser"))
app.include_router(shopping_lists_router, prefix="/api/v1")
app.include_router(optimization_router, prefix="/api/v1")
app.include_router(suggestions_router, prefix="/api/v1")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal_server_error"})
