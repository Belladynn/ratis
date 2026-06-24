"""Admin routes package — ratis_product_analyser.

Mounted under ``/api/v1`` only when ``ADMIN_API_KEY`` is set at startup
(see ``main.py`` lifespan). When the env var is missing, the routes are
absent (404) rather than exposed without auth — defense-in-depth.

Submodules :

- ``debug`` — read-only legacy debug endpoints (PR #126 / #132) :
  ``GET /admin/scans/{scan_id}/debug`` and
  ``GET /admin/receipts/{receipt_id}/debug``. ADMIN_API_KEY only.
- ``scans`` — ARCH_admin_endpoints PR3 manual override + 360 view +
  replay-match. ADMIN_API_KEY + X-Admin-Operator header. No 2FA TOTP
  (not financial — read/edit-pipeline-state only).
- ``barcode`` — pipeline PR-C : list retailers with unparsed
  receipt barcodes + dispatch async re-parse after a new format row is
  added to ``retailer_receipt_formats``. ADMIN_API_KEY +
  X-Admin-Operator header on the mutation.
- ``fraud_suspicions`` — anti-fraud PR5 : queue review + resolution.
  ``GET /admin/fraud_suspicions`` (paginated + filters),
  ``GET /admin/fraud_suspicions/{id}`` (enriched detail),
  ``PATCH /admin/fraud_suspicions/{id}`` (resolve/clear/escalate,
  single-shot). ADMIN_API_KEY on every route, X-Admin-Operator on
  the PATCH.
- ``knowledge`` — ARCH_admin_endpoints PR9 : OCR-knowledge curation
  queue (``GET /admin/knowledge/ocr-queue``) + manual correction /
  dismissal (``PATCH /admin/knowledge/{id}``). ADMIN_API_KEY +
  X-Admin-Operator on the mutation. Product-knowledge endpoints
  (``/admin/knowledge/product-queue`` + ``PATCH
  /admin/product-knowledge/{id}``) deferred — table not yet in schema.
- ``observability`` — ARCH_admin_endpoints PR4 : audit-log lineage
  debug + parsed-ticket detail/browse + async replay of Phase 3+4 on
  a persisted ParsedTicket + Celery task status polling. ADMIN_API_KEY
  on every endpoint, X-Admin-Operator on the replay mutation.
- ``users`` — ARCH_admin_endpoints PR6 : per-user scan listing
  (``GET /admin/users/{user_id}/scans``). Read-only, ADMIN_API_KEY
  only — surfaces store_name via JOIN so operators see human labels.
- ``db_approvals`` — SP6 : registration of DB write proposals reaching
  the human approval gate. ``POST /admin/db-approvals``. ADMIN_API_KEY
  only (machine call from the n8n ``db-write-pipeline`` workflow).

The combined :data:`router` aggregates them so ``main.py`` mounts a
single APIRouter under ``/api/v1``.
"""

from __future__ import annotations

from fastapi import APIRouter

from .barcode import router as barcode_router
from .db_approvals import router as db_approvals_router
from .debug import router as debug_router
from .fraud_suspicions import router as fraud_suspicions_router
from .knowledge import router as knowledge_router
from .name_resolutions import router as name_resolutions_router
from .observability import router as observability_router
from .scans import router as scans_router
from .session_bootstrap import router as session_bootstrap_router
from .stats import router as stats_router
from .stores import router as stores_router
from .users import router as users_router

router = APIRouter()
router.include_router(barcode_router)
router.include_router(db_approvals_router)
router.include_router(debug_router)
router.include_router(fraud_suspicions_router)
router.include_router(knowledge_router)
router.include_router(name_resolutions_router)
router.include_router(observability_router)
router.include_router(scans_router)
router.include_router(session_bootstrap_router)
router.include_router(stats_router)
router.include_router(stores_router)
router.include_router(users_router)

__all__ = ["router"]
