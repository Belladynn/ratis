from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from ratis_core.health import make_health_router
from ratis_core.middleware import RequestIDMiddleware
from ratis_core.observability import init_sentry
from ratis_core.settings import load_settings
from ratis_core.startup import require_env
from routes.notify import router as notify_router
from services.push_rate_limiter import make_redis_rate_limiter

logger = logging.getLogger(__name__)

_REQUIRED_NOTIFIER_KEYS = {
    "retry_attempts",
    "retry_delay_seconds",
    "max_notifications_per_day",
    "quiet_hours_start",
    "quiet_hours_end",
    "expo_push_url",
    "notification_types",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_env("INTERNAL_API_KEY", "REDIS_URL")

    try:
        cfg = load_settings()
    except FileNotFoundError:
        raise RuntimeError(
            "Settings unavailable: app_settings table empty/unreachable and "
            "ratis_settings.json not found in package — aborting."
        )

    if "notifier" not in cfg:
        raise RuntimeError(
            "Settings missing 'notifier' section — aborting. Check app_settings table or ratis_settings.json."
        )

    missing = _REQUIRED_NOTIFIER_KEYS - cfg["notifier"].keys()
    if missing:
        raise RuntimeError(
            f"Settings.notifier missing required keys: {', '.join(sorted(missing))} — aborting. "
            "Check app_settings table or ratis_settings.json."
        )

    init_sentry("ratis_notifier")
    app.state.cfg = cfg
    # V1.1 — push rate-limiter (Redis SETNX cooldowns for visible OS pushes).
    # The redis-py client is lazy : the connection isn't dialled until the
    # first SET ; failure modes are absorbed by the limiter (fail-open).
    app.state.rate_limiter = make_redis_rate_limiter(os.environ["REDIS_URL"])
    logger.info("ratis_notifier started — notifier config loaded")
    yield


# Internal-only service — no interactive docs / OpenAPI schema exposed.
app = FastAPI(
    title="ratis_notifier",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(RequestIDMiddleware)

# Unauthenticated liveness probe (Docker HEALTHCHECK + ops). The notify route
# is INTERNAL_API_KEY-gated ; health must answer with no credentials — see
# ratis_core.health.
app.include_router(make_health_router("ratis_notifier"))
app.include_router(notify_router, prefix="/api/v1")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal_server_error"})
