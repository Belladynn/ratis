from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")  # must run before any import that reads os.environ at module level

import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from limiter import limiter
from ratis_core.health import make_health_router
from ratis_core.middleware import RequestIDMiddleware
from ratis_core.observability import init_sentry
from ratis_core.startup import require_env
from routes.account import router as account_router
from routes.admin import router as admin_router
from routes.auth import router as auth_router
from routes.subscription import router as subscription_router
from routes.webhooks import router as webhooks_router
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Vars OBLIGATOIRES — service refuse de démarrer si absentes.
    # RGPD_ANONYMIZE_SALT (audit F-AU-3) is required so delete_account can
    # produce per-user anon UUIDs without leaking the user→anon mapping to
    # anyone with DB-only access. Rotating the salt orphans existing anon
    # rows (analytics discontinuity) — choose once, never rotate.
    require_env(
        "DATABASE_URL",
        "JWT_PRIVATE_KEY_PATH",
        "JWT_PUBLIC_KEY_PATH",
        "GOOGLE_CLIENT_ID",
        "RGPD_ANONYMIZE_SALT",
    )
    # Vars OPTIONNELLES en alpha (Apple Sign-in, Stripe payments). Si vides,
    # les routes correspondantes retourneront 503 "feature_disabled" au lieu
    # de bloquer le démarrage du service entier.
    # En prod V1+ ces vars seront obligatoires — voir PROD_CHECKLIST.md.
    if not os.environ.get("ADMIN_API_KEY"):
        logger.warning("ADMIN_API_KEY not set — admin endpoints (/api/v1/admin/*) are disabled")
    elif not os.environ.get("ADMIN_TOTP_SECRET"):
        # Soft-warn at boot ; mutations would 500 anyway (admin_totp_not_configured).
        logger.warning(
            "ADMIN_API_KEY set but ADMIN_TOTP_SECRET missing — admin mutations "
            "will return 500 admin_totp_not_configured until configured"
        )
    init_sentry("ratis_auth")
    yield


app = FastAPI(title="ratis_auth", lifespan=lifespan)
app.add_middleware(RequestIDMiddleware)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": "rate_limit_exceeded"})


# Unauthenticated liveness probe (Docker HEALTHCHECK + ops). Mounted first,
# outside any auth / rate-limit dependency — see ratis_core.health.
app.include_router(make_health_router("ratis_auth"))
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(account_router, prefix="/api/v1/account")
app.include_router(subscription_router, prefix="/api/v1/account/subscription")
app.include_router(webhooks_router, prefix="/webhooks")
# Admin routes mounted only when ADMIN_API_KEY is configured (defense in
# depth — same pattern as PA #209). In dev/test the routes are absent (404
# instead of an unauth'd 403 with a misleading message).
if os.environ.get("ADMIN_API_KEY"):
    app.include_router(admin_router, prefix="/api/v1")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal_server_error"})
