"""AU admin router aggregator.

All admin endpoints under ``/api/v1/admin/*`` for ratis_auth. Mounted only
when ``ADMIN_API_KEY`` is set at lifespan (defense in depth ; see PA #209
and ARCH_admin_endpoints.md).
"""

from __future__ import annotations

from fastapi import APIRouter

from routes.admin.session_bootstrap import router as session_bootstrap_router
from routes.admin.subscription import router as subscription_router
from routes.admin.users import router as users_router

router = APIRouter()
router.include_router(session_bootstrap_router)
router.include_router(subscription_router)
router.include_router(users_router)

__all__ = ["router"]
