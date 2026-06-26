import asyncio
import contextlib
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from limiter import limiter
from ratis_core.exceptions import (
    BadRequest,
    Conflict,
    Forbidden,
    Gone,
    NotFound,
    PaymentRequired,
)
from ratis_core.health import make_health_router
from ratis_core.middleware import RequestIDMiddleware
from ratis_core.observability import init_sentry
from ratis_core.settings import load_settings
from ratis_core.startup import require_env, require_env_min_length
from routes.admin.achievements import router as admin_achievements_router
from routes.admin.battlepass import router as admin_battlepass_router
from routes.admin.cab import router as admin_cab_router
from routes.admin.cashback import router as admin_cashback_router
from routes.admin.cashback_withdrawals import (
    router as admin_cashback_withdrawals_router,
)
from routes.admin.challenges import router as admin_challenges_router
from routes.admin.missions import router as admin_missions_router
from routes.admin.mystery import router as admin_mystery_router
from routes.admin.referral import router as admin_referral_router
from routes.admin.reward_config import router as admin_reward_config_router
from routes.admin.session_bootstrap import router as admin_session_bootstrap_router
from routes.admin.settings import router as admin_settings_router
from routes.admin.stats import router as admin_stats_router
from routes.admin.streak_tier import router as admin_streak_tier_router
from routes.admin.trust_scores import router as admin_trust_scores_router
from routes.gamification.battlepass import router as battlepass_router
from routes.gamification.challenge import router as challenge_router
from routes.gamification.leaderboard import router as leaderboard_router
from routes.gamification.missions import router as missions_router
from routes.gamification.mystery import router as mystery_router
from routes.gamification.streak import router as streak_router
from routes.gamification.xp import router as xp_router
from routes.rewards.achievements import router as achievements_router
from routes.rewards.cab import router as cab_router
from routes.rewards.cashback import router as cashback_router
from routes.rewards.cashback_webhook import router as cashback_webhook_router
from routes.rewards.cashback_withdraw import router as cashback_withdraw_router
from routes.rewards.events import router as events_router
from routes.rewards.gift_cards import router as gift_cards_router
from routes.rewards.referral import router as referral_router
from routes.rewards.settings_public import router as settings_public_router
from routes.rewards.shop import router as shop_router
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Outbox worker — dispatches queued notifications every 30 s.
# Runs in-process so it shares the same DATABASE_URL without extra infra.
# Uses FOR UPDATE SKIP LOCKED so multiple replicas don't double-dispatch.
# ---------------------------------------------------------------------------
_OUTBOX_INTERVAL_SECONDS = 30


def _process_outbox_sync() -> None:
    """Synchronous helper — runs in the thread-pool executor."""
    from ratis_core.database import SessionLocal, _init

    _init()
    db = SessionLocal()
    try:
        from repositories.notification_repository import process_outbox_batch

        n = process_outbox_batch(db)
        if n:
            logger.info("outbox: dispatched %d notification(s)", n)
    except Exception:
        logger.exception("outbox: processing error")
        db.rollback()
    finally:
        db.close()


async def _run_outbox_worker() -> None:
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(_OUTBOX_INTERVAL_SECONDS)
        await loop.run_in_executor(None, _process_outbox_sync)


_REQUIRED_REWARDS_KEYS = {
    "cab_per_receipt_scan",
    "cab_per_label_scan",
    "cab_per_barcode_scan",
    # Phase B (PR #325) action_types — fail-fast if absent so a missing
    # settings key surfaces at startup rather than silently zeroing the
    # award at the first event.
    "cab_per_product_identification",
    "cab_per_fill_product_field",
    "cab_per_scan_distinct",
    "cab_per_promo_found",
    "cab_referral_monthly",
    "cab_referral_annual",
    "cashback_boost_multiplier",
    "cashback_boost_cab_rate",
    "cashback_boost_window_hours",
    "cashback_min_withdrawal",
    "cashback_pending_expiry_days",
}

_REQUIRED_MISSIONS_KEYS = {
    "daily_count_per_difficulty",
    "weekly_count_per_difficulty",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_env("DATABASE_URL")
    require_env("JWT_PUBLIC_KEY_PATH")
    require_env("INTERNAL_API_KEY")
    # M1 (audit sécurité 2026-05-03) — minimum length for ADMIN_API_KEY so
    # the cookie HMAC keyed by it (PA admin_ui) and the constant-time check
    # in verify_admin_key have a real security margin.
    require_env_min_length("ADMIN_API_KEY", 32)
    require_env("ADMIN_TOTP_SECRET")
    if not os.environ.get("GIFT_CARD_PROVIDER_KEY"):
        logger.warning("GIFT_CARD_PROVIDER_KEY not set — gift card issuance will be skipped until configured")

    try:
        cfg = load_settings()
    except FileNotFoundError:
        raise RuntimeError(
            "Settings unavailable: app_settings table empty/unreachable and "
            "ratis_settings.json not found in package — aborting. "
            "Run: POST /api/v1/admin/settings/seed"
        )

    if "rewards" not in cfg:
        raise RuntimeError(
            "Settings missing 'rewards' section — aborting. "
            "Check app_settings table (POST /api/v1/admin/settings/seed) "
            "or ratis_settings.json."
        )
    missing = _REQUIRED_REWARDS_KEYS - cfg["rewards"].keys()
    if missing:
        raise RuntimeError(
            f"Settings.rewards missing required keys: {', '.join(sorted(missing))} — aborting. "
            "Check app_settings table or ratis_settings.json."
        )

    # F-RW-6 — partner webhook provider allowlist + HMAC tolerance must be
    # configured so the cashback_webhook route can fail-closed on unknown
    # providers and reject stale signatures.
    webhook_providers = cfg.get("cashback", {}).get("webhook_providers")
    if not webhook_providers:
        raise RuntimeError(
            "Settings missing 'cashback.webhook_providers' allowlist — aborting. "
            "Check app_settings table or ratis_settings.json."
        )

    # AUDIT 2026-05-17 — per-provider webhook secrets. The provider set is
    # config-driven, so the required env vars are derived dynamically :
    # one CASHBACK_WEBHOOK_SECRET_<PROVIDER> per allowlisted provider. A
    # leak of one secret is then contained to a single affiliate network.
    require_env(*(f"CASHBACK_WEBHOOK_SECRET_{p.upper()}" for p in webhook_providers))

    gc_cfg = cfg.get("gift_cards", {})
    if not gc_cfg.get("annual_subscription_brand_id"):
        logger.warning(
            "gift_cards.annual_subscription_brand_id not set — annual subscription gift cards will not be issued"
        )
    if not gc_cfg.get("battlepass_brand_id"):
        logger.warning("gift_cards.battlepass_brand_id not set — battlepass gift card rewards will not be issued")

    init_sentry("ratis_rewards")
    app.state.cfg = cfg
    logger.info("ratis_rewards started — config loaded")

    # Start outbox worker as a background task.
    outbox_task = asyncio.create_task(_run_outbox_worker())
    try:
        yield
    finally:
        outbox_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await outbox_task


app = FastAPI(title="ratis_rewards", lifespan=lifespan)
app.add_middleware(RequestIDMiddleware)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": "rate_limit_exceeded"})


# Unauthenticated liveness probe (Docker HEALTHCHECK + ops). Mounted first,
# outside any auth / rate-limit dependency — see ratis_core.health.
app.include_router(make_health_router("ratis_rewards"))
app.include_router(admin_session_bootstrap_router, prefix="/api/v1")
app.include_router(achievements_router, prefix="/api/v1")
app.include_router(admin_achievements_router, prefix="/api/v1")
app.include_router(admin_battlepass_router, prefix="/api/v1")
app.include_router(admin_cab_router, prefix="/api/v1")
app.include_router(admin_cashback_router, prefix="/api/v1")
app.include_router(admin_cashback_withdrawals_router, prefix="/api/v1")
app.include_router(admin_challenges_router, prefix="/api/v1")
app.include_router(admin_missions_router, prefix="/api/v1")
app.include_router(admin_mystery_router, prefix="/api/v1")
app.include_router(admin_referral_router, prefix="/api/v1")
app.include_router(admin_reward_config_router, prefix="/api/v1")
app.include_router(admin_settings_router, prefix="/api/v1")
app.include_router(admin_stats_router, prefix="/api/v1")
app.include_router(admin_streak_tier_router, prefix="/api/v1")
app.include_router(admin_trust_scores_router, prefix="/api/v1")
app.include_router(battlepass_router, prefix="/api/v1")
app.include_router(cab_router, prefix="/api/v1")
app.include_router(challenge_router, prefix="/api/v1")
app.include_router(mystery_router, prefix="/api/v1")
app.include_router(cashback_router, prefix="/api/v1")
app.include_router(cashback_webhook_router, prefix="/api/v1")
app.include_router(cashback_withdraw_router, prefix="/api/v1")
app.include_router(events_router, prefix="/api/v1")
app.include_router(gift_cards_router, prefix="/api/v1")
app.include_router(leaderboard_router, prefix="/api/v1")
app.include_router(missions_router, prefix="/api/v1")
app.include_router(referral_router, prefix="/api/v1")
app.include_router(settings_public_router, prefix="/api/v1")
app.include_router(shop_router, prefix="/api/v1")
app.include_router(streak_router, prefix="/api/v1")
app.include_router(xp_router, prefix="/api/v1")


@app.exception_handler(NotFound)
async def not_found_handler(request: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": exc.detail})


@app.exception_handler(Conflict)
async def conflict_handler(request: Request, exc: Conflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": exc.detail})


@app.exception_handler(Forbidden)
async def forbidden_handler(request: Request, exc: Forbidden) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": exc.detail})


@app.exception_handler(BadRequest)
async def bad_request_handler(request: Request, exc: BadRequest) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": exc.detail})


@app.exception_handler(PaymentRequired)
async def payment_required_handler(request: Request, exc: PaymentRequired) -> JSONResponse:
    return JSONResponse(status_code=402, content={"detail": exc.detail})


@app.exception_handler(Gone)
async def gone_handler(request: Request, exc: Gone) -> JSONResponse:
    return JSONResponse(status_code=410, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal_server_error"})
