"""HTML routes for the ``/admin/ui/*`` mini admin UI.

Three pages are surfaced (PR UI-1) :

- ``GET    /admin/ui/login``                          — login form
- ``POST   /admin/ui/login``                          — set session cookie
- ``POST   /admin/ui/logout``                         — clear session
- ``GET    /admin/ui/``                               — dashboard
- ``GET    /admin/ui/stores-pending``                 — list pending stores
- ``POST   /admin/ui/stores-pending/validate-bulk``   — bulk validate
- ``GET    /admin/ui/knowledge-queue``                — OCR knowledge queue
- ``POST   /admin/ui/knowledge-queue/{id}``           — apply correction
- ``GET    /admin/ui/audit-log``                      — audit log viewer

The mutating routes (validate-bulk / knowledge PATCH) call the same
in-process service-layer functions used by the JSON ``/api/v1/admin/*``
API : :mod:`services.store_admin_service` and
:mod:`services.knowledge_admin_service`. We do NOT loop back over HTTP
— the mini UI runs in the same FastAPI app, so a real HTTP call would
just add latency + double-auth.

The audit-log page calls a small in-module helper that runs the same
SQL as ``routes.admin.observability.list_audit_log`` — read-only,
no service-layer indirection needed.

Auth : every protected route depends on
:func:`admin_ui.auth.get_admin_session`. A missing / invalid cookie
raises an ``HTTPException(401, login_required)`` that the local
``Exception`` handler swaps for a 302 redirect to ``/admin/ui/login``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime, time
from datetime import date as date_type
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from ratis_core.database import get_db
from services import (
    knowledge_admin_service,
    store_admin_service,
)
from services import (
    name_resolution_admin_service as nrc_admin_service,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import skills_admin_service
from .au_client import au_get
from .auth import (
    COOKIE_NAME,
    OPERATOR_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    AdminSession,
    build_login_redirect,
    compute_token,
    get_admin_session,
    verify_credentials,
)
from .rw_client import rw_delete, rw_get, rw_patch, rw_post, rw_put
from .settings_sections import (
    EDITABLE_SECTIONS_MIRROR,
    FROZEN_SECTIONS,
)
from .settings_sections import (
    is_editable as is_editable_local,
)

logger = logging.getLogger(__name__)

# Jinja templates packaged alongside this module so the loader path is
# stable regardless of which CWD uvicorn is launched from.
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Mirrors observability.py — keep the same enum so the operator can
# only pick valid filter values (no free-form input attack surface on a
# CHECK-constrained column).
ALLOWED_PHASES = ("extract", "comprehend", "match", "persist", "manual")
ALLOWED_LEVELS = ("verbose", "normal", "production")

router = APIRouter()


def _form_str(form: Any, key: str, default: str = "") -> str:
    """Read a text form field as ``str``, collapsing missing / non-text values.

    Starlette ``FormData.get`` is typed ``str | UploadFile | None``. The admin
    UI forms only ever submit text inputs for these fields, so a non-text or
    empty value collapses to ``default`` — equivalent to the previous
    ``(form.get(key) or default)`` idiom for the real (str / None) inputs while
    keeping the static type a plain ``str``.
    """
    raw = form.get(key)
    return raw if isinstance(raw, str) and raw else default


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    """Render the login form. Always 200, no session check."""
    return templates.TemplateResponse(request, "login.html", {"session": None, "error": None})


@router.post("/login")
def login_submit(
    request: Request,
    api_key: str = Form(...),
    operator: str = Form(...),
) -> Response:
    """Verify the API key, set the session cookie, redirect to dashboard.

    On failure : re-render the form with an inline error (200, not 401
    — the form is the response surface and HTMX-style flows will swap
    it in place).
    """
    operator_clean = operator.strip()
    if not verify_credentials(api_key) or not operator_clean:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "session": None,
                "error": "Identifiants invalides.",
            },
            status_code=status.HTTP_200_OK,
        )

    token = compute_token(api_key, operator_clean)
    response = RedirectResponse(url="/admin/ui/", status_code=status.HTTP_302_FOUND)
    # HTTP-only / Strict — block JS access and CSRF leakage. The mini UI
    # itself uses no third-party origins for its forms (everything is
    # same-origin to /admin/ui/*).
    #
    # M2 — Secure flag is bound to the request scheme so production
    # (HTTPS via Caddy) hardens the cookies while local dev (HTTP) still
    # works without a self-signed cert. A browser that loads the admin UI
    # over HTTP cannot promote to HTTPS without a re-login, so the
    # scheme-bound check is safe : the cookie is Secure exactly when the
    # transport is. An attacker forcing HTTP would not be issued a Secure
    # cookie, but they would also fail the SameSite=Strict + HttpOnly
    # combination on any subsequent same-origin request.
    cookie_secure = request.url.scheme == "https"
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="strict",
        secure=cookie_secure,
        path="/admin/ui",
    )
    response.set_cookie(
        key=OPERATOR_COOKIE_NAME,
        value=operator_clean,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="strict",
        secure=cookie_secure,
        path="/admin/ui",
    )
    return response


@router.post("/logout")
def logout(request: Request) -> Response:
    """Clear session cookies + redirect to login. No session required."""
    response = build_login_redirect()
    response.delete_cookie(COOKIE_NAME, path="/admin/ui")
    response.delete_cookie(OPERATOR_COOKIE_NAME, path="/admin/ui")
    return response


# ---------------------------------------------------------------------------
# Local 401 → 302 redirect handler — see ``main.http_exception_handler``
# ---------------------------------------------------------------------------
# The dep ``get_admin_session`` raises 401 when the cookie is invalid ;
# we want the BROWSER to follow a redirect to /admin/ui/login rather
# than to see a JSON 401. The actual filtering on path + detail lives in
# the global handler in ``main.py`` ; this helper is the unconditional
# 302 builder for the matching case.
def _unauthorized_to_login_handler(request: Request, exc: HTTPException) -> Response:
    """Build a 302 to /admin/ui/login. Caller has already filtered."""
    return build_login_redirect()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Plain dashboard linking to each page. Session-gated.

    Computes the NRC arbitration counter (number of unverified +
    controverse labels) so the operator sees the queue size at a glance
    without leaving the dashboard. Falls back to 0 on any DB error to
    keep the page rendering — the counter is a UX nicety, not a gate.
    """
    try:
        _items, nrc_total = nrc_admin_service.list_arbitration_queue(db, state_filter="all", limit=1, offset=0)
    except Exception:
        logger.warning("nrc queue counter failed — falling back to 0", exc_info=True)
        nrc_total = 0
    return templates.TemplateResponse(
        request,
        "index.html",
        {"session": session, "nrc_queue_total": nrc_total},
    )


# ---------------------------------------------------------------------------
# Page A — Stores pending
# ---------------------------------------------------------------------------
@router.get("/stores-pending", response_class=HTMLResponse)
def stores_pending_page(
    request: Request,
    flash: str | None = None,
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """List ``user_suggested`` / ``pending`` stores with multi-select.

    Reads ``stores`` directly via the same SQL shape as
    ``routes.admin.stores.list_stores`` filtered to
    ``validation_status='pending'`` + ``is_disabled=false``. We don't
    paginate at the UI layer — the queue is small in alpha (a handful
    of submissions per day) ; if it grows we'll add LIMIT/OFFSET wired
    to query params.
    """
    rows = db.execute(
        text(
            "SELECT id, name, retailer, address, city, postal_code, "
            "       lat, lng, is_disabled, validation_status, source "
            "FROM stores "
            "WHERE validation_status = 'pending' AND is_disabled = false "
            "ORDER BY created_at DESC, id"
        )
    ).fetchall()
    stores = [
        {
            "id": str(r.id),
            "name": r.name,
            "retailer": r.retailer,
            "address": r.address,
            "city": r.city,
            "postal_code": r.postal_code,
            "lat": float(r.lat) if r.lat is not None else 0.0,
            "lng": float(r.lng) if r.lng is not None else 0.0,
        }
        for r in rows
    ]
    return templates.TemplateResponse(
        request,
        "stores_pending.html",
        {
            "session": session,
            "stores": stores,
            "flash": flash,
        },
    )


@router.post("/stores-pending/validate-bulk")
async def stores_validate_bulk(
    request: Request,
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> Response:
    """Apply the bulk validate via :mod:`store_admin_service`.

    Reads the form's repeated ``ids`` field. Empty selection → flash
    redirect (no-op). The service emits ``store_validation_history``
    rows with the operator handle so the audit trail is preserved.

    ``async`` because we need ``await request.form()`` to read the
    multi-valued ``ids`` field (FastAPI's ``Form(...)`` doesn't list-
    bind a repeated checkbox cleanly without a hand-rolled list type).
    """
    raw = await request.form()
    raw_ids = raw.getlist("ids")
    parsed: list[uuid.UUID] = []
    for s in raw_ids:
        try:
            parsed.append(uuid.UUID(str(s)))
        except (ValueError, TypeError):
            # Silently drop malformed ids — they came from a hidden
            # checkbox value, a UI bug rather than a hostile actor.
            logger.warning("dropping malformed store id from validate-bulk: %r", s)

    if not parsed:
        return RedirectResponse(
            url="/admin/ui/stores-pending?flash=Aucune+sélection.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    result = store_admin_service.validate_stores_bulk(db, parsed, session.operator)
    db.commit()

    flash = (
        f"Validés : {len(result['validated'])} · "
        f"déjà confirmés : {len(result['skipped_already_confirmed'])} · "
        f"introuvables : {len(result['not_found'])}"
    )
    return RedirectResponse(
        url=f"/admin/ui/stores-pending?flash={flash}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Page B — Knowledge OCR queue
# ---------------------------------------------------------------------------
@router.get("/knowledge-queue", response_class=HTMLResponse)
def knowledge_queue_page(
    request: Request,
    flash: str | None = None,
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """List ``ocr_knowledge`` rows pending manual correction."""
    items = knowledge_admin_service.list_ocr_queue(db, limit=50, offset=0)
    serialized = [
        {
            "id": str(it.id),
            "raw_ocr": it.raw_ocr,
            "seen_count": it.seen_count,
            "created_at": it.created_at.isoformat() if it.created_at else "",
        }
        for it in items
    ]
    return templates.TemplateResponse(
        request,
        "knowledge_queue.html",
        {
            "session": session,
            "items": serialized,
            "flash": flash,
        },
    )


@router.post("/knowledge-queue/{ocr_knowledge_id}")
def knowledge_apply(
    ocr_knowledge_id: uuid.UUID,
    corrected: str = Form(""),
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> Response:
    """Apply a correction (or dismissal when empty) on one row.

    Mirrors the JSON ``PATCH /admin/knowledge/{id}`` semantics : empty
    string maps to a dismissal (``corrected=None``). The service layer
    re-normalises whitespace-only as None as well, so ``"   "`` is
    safely a dismissal too.
    """
    payload = corrected if corrected.strip() else None
    try:
        knowledge_admin_service.apply_ocr_correction(
            db,
            ocr_knowledge_id=ocr_knowledge_id,
            corrected=payload,
            operator=session.operator,
        )
    except knowledge_admin_service.OcrKnowledgeNotFound:
        return RedirectResponse(
            url="/admin/ui/knowledge-queue?flash=Row+introuvable.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Mirror the audit log emission of the JSON route so the operator's
    # action surfaces alongside pipeline events. Best-effort — a failed
    # audit insert never gates the user-facing mutation.
    is_dismissal = payload is None
    event = "admin_ocr_knowledge_dismissal" if is_dismissal else "admin_ocr_knowledge_correction"
    try:
        db.execute(
            text(
                "INSERT INTO pipeline_audit_log "
                "(phase, level, event, scan_id, parsed_ticket_id, payload) "
                "VALUES ('manual', 'normal', :event, NULL, NULL, "
                "        CAST(:payload AS jsonb))"
            ),
            {
                "event": event,
                "payload": json.dumps(
                    {
                        "operator": session.operator,
                        "ocr_knowledge_id": str(ocr_knowledge_id),
                        "via": "admin_ui",
                        "corrected": payload,
                    }
                ),
            },
        )
    except Exception:
        logger.warning("audit insert failed for %s", ocr_knowledge_id, exc_info=True)
    db.commit()

    flash = "Correction appliquée." if not is_dismissal else "Dismissal appliqué."
    return RedirectResponse(
        url=f"/admin/ui/knowledge-queue?flash={flash}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Page C — Audit log viewer
# ---------------------------------------------------------------------------
@router.get("/audit-log", response_class=HTMLResponse)
def audit_log_page(
    request: Request,
    entity_id: str | None = None,
    scan_id: str | None = None,
    receipt_id: str | None = None,
    parsed_ticket_id: str | None = None,
    phase: str | None = None,
    level: str | None = None,
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Query ``pipeline_audit_log`` by receipt / parsed_ticket / scan id.

    The single ``entity_id`` input is matched against three ids in
    cascade : receipt → parsed_ticket_id (resolved via the ``receipts``
    table), parsed_ticket_id, scan_id. We OR the matching predicates
    together so the operator does not need to know which kind of id
    they're holding.

    Empty input → no query (rows=None) — distinct from "0 results"
    (rows=[]).
    """
    rows: list[dict[str, Any]] | None = None
    error: str | None = None

    # Validate enum filters early — display them on the form.
    if phase and phase not in ALLOWED_PHASES:
        error = "Phase invalide."
        phase = None
    if level and level not in ALLOWED_LEVELS:
        error = "Level invalide."
        level = None

    # Cross-page deep-link support : a scan-detail row links to
    # ``/admin/ui/audit-log?scan_id=<uuid>``. We accept three typed
    # aliases (``scan_id``, ``receipt_id``, ``parsed_ticket_id``) and
    # fold them into ``entity_id`` — the underlying SQL already ORs the
    # three matching predicates so the kind of id is irrelevant. The
    # explicit ``entity_id`` form param wins when both are provided
    # (manual operator override).
    if not (entity_id and entity_id.strip()):
        for typed in (scan_id, parsed_ticket_id, receipt_id):
            if typed and typed.strip():
                entity_id = typed.strip()
                break

    if entity_id and entity_id.strip():
        try:
            uid = uuid.UUID(entity_id.strip())
        except ValueError:
            error = "UUID invalide."
            return templates.TemplateResponse(
                request,
                "audit_log.html",
                {
                    "session": session,
                    "rows": None,
                    "entity_id": entity_id,
                    "phase": phase,
                    "level": level,
                    "error": error,
                    "allowed_phases": ALLOWED_PHASES,
                    "allowed_levels": ALLOWED_LEVELS,
                },
            )

        # Resolve receipt → parsed_ticket_id (if applicable). The lookup
        # is best-effort : a non-receipt UUID just returns None and the
        # OR predicate falls back to scan_id / parsed_ticket_id matches.
        rcpt = db.execute(
            text("SELECT parsed_ticket_id FROM receipts WHERE id = :rid"),
            {"rid": str(uid)},
        ).first()
        receipt_pt_id = rcpt.parsed_ticket_id if rcpt else None

        # Static SQL — no string concat with user-derived parts. Optional
        # filters are bound to NULL when absent so the IS-NULL-OR-equal
        # predicate degenerates to a no-op. ``receipt_pt_id`` is bound
        # to the resolved parsed_ticket id from the receipts table when
        # the input UUID happened to be a receipt id ; NULL otherwise.
        # Casts on the typed bind parameters force psycopg to commit to
        # a column type when the value is NULL — PG otherwise raises
        # ``could not determine data type of parameter`` on the
        # ``IS NULL OR = :p`` shape with a missing optional filter.
        sql = text(
            "SELECT id, parsed_ticket_id, scan_id, phase, level, event, "
            "       payload, created_at "
            "FROM pipeline_audit_log "
            "WHERE (scan_id = CAST(:uid AS uuid) "
            "       OR parsed_ticket_id = CAST(:uid AS uuid) "
            "       OR parsed_ticket_id = CAST(:receipt_pt_id AS uuid)) "
            "  AND (CAST(:phase AS text) IS NULL OR phase = CAST(:phase AS text)) "
            "  AND (CAST(:level AS text) IS NULL OR level = CAST(:level AS text)) "
            "ORDER BY created_at DESC, id DESC "
            "LIMIT :limit"
        )
        params: dict[str, Any] = {
            "uid": str(uid),
            "receipt_pt_id": str(receipt_pt_id) if receipt_pt_id else None,
            "phase": phase,
            "level": level,
            "limit": 200,
        }
        result = db.execute(sql, params).fetchall()
        rows = [
            {
                "id": str(r.id),
                "parsed_ticket_id": str(r.parsed_ticket_id) if r.parsed_ticket_id else None,
                "scan_id": str(r.scan_id) if r.scan_id else None,
                "phase": r.phase,
                "level": r.level,
                "event": r.event,
                "payload_json": json.dumps(r.payload, indent=2, ensure_ascii=False),
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in result
        ]

    return templates.TemplateResponse(
        request,
        "audit_log.html",
        {
            "session": session,
            "rows": rows,
            "entity_id": entity_id,
            "phase": phase,
            "level": level,
            "error": error,
            "allowed_phases": ALLOWED_PHASES,
            "allowed_levels": ALLOWED_LEVELS,
        },
    )


# ---------------------------------------------------------------------------
# Page D — User search (UI-1.5)
# ---------------------------------------------------------------------------
# Format-detection regex constants. Compiled once at import — both shapes
# are tight enough that a malformed value short-circuits before any HTTP
# call to AU.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SUPPORT_ID_RE = re.compile(r"^RTS-[A-HJ-NP-Z2-9]{6}$")

# Email-search safety net : the AU endpoint caps page size at 200 ; we
# request 50 to keep the table readable and signal "truncated" when the
# upstream reports more.
_EMAIL_SEARCH_LIMIT = 50


@router.get("/users/search", response_class=HTMLResponse)
def users_search_page(
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """Render the empty user-search form. POST is the worker route below."""
    return templates.TemplateResponse(
        request,
        "users_search.html",
        {
            "session": session,
            "query": None,
            "error": None,
            "results": None,
            "truncated": False,
        },
    )


def _render_search(
    request: Request,
    session: AdminSession,
    *,
    query: str | None = None,
    error: str | None = None,
    results: list[dict[str, Any]] | None = None,
    truncated: bool = False,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    """Shared helper to re-render the search page with state."""
    return templates.TemplateResponse(
        request,
        "users_search.html",
        {
            "session": session,
            "query": query,
            "error": error,
            "results": results,
            "truncated": truncated,
        },
        status_code=status_code,
    )


@router.post("/users/search")
async def users_search_submit(
    request: Request,
    query: str = Form(""),
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Auto-detect the input format and route to the matching AU lookup.

    Three branches :

    1. UUID  → ``GET /admin/users/{uuid}`` ; on 200 redirect to detail,
       on 404 re-render with an error.
    2. RTS-XXXXXX → ``GET /admin/users?support_id=...`` ; on a single
       hit redirect to detail, on 0 hits show "introuvable", on multiple
       (defense — the column has UNIQUE) show the table for review.
    3. Email partial → ``GET /admin/users?email_contains=...`` ; render
       the table or "aucun utilisateur trouvé" on 0 hits.

    303 (See Other) on redirect rather than 302 so HTMX-style flows
    preserve the POST → GET semantics for browser back-button safety.
    """
    q = (query or "").strip()
    if not q:
        return _render_search(
            request,
            session,
            query=None,
            error="Saisissez un identifiant à rechercher.",
        )

    # Branch 1 — UUID
    if _UUID_RE.match(q):
        resp = await au_get(f"/admin/users/{q}", operator=session.operator)
        if resp.status_code == 200:
            return RedirectResponse(url=f"/admin/ui/users/{q}", status_code=status.HTTP_303_SEE_OTHER)
        if resp.status_code == 404:
            return _render_search(
                request,
                session,
                query=q,
                error="Utilisateur introuvable.",
            )
        # Anything else (5xx, 422, 403) : surface the AU detail when
        # available, fall back to a generic message so the operator can
        # retry / report.
        return _render_search(
            request,
            session,
            query=q,
            error=f"Erreur lookup AU (HTTP {resp.status_code}).",
        )

    # Branch 2 — Support ID (exact match)
    if _SUPPORT_ID_RE.match(q):
        resp = await au_get(
            "/admin/users",
            operator=session.operator,
            params={"support_id": q},
        )
        if resp.status_code != 200:
            return _render_search(
                request,
                session,
                query=q,
                error=f"Erreur AU (HTTP {resp.status_code}).",
            )
        body = resp.json()
        users = body.get("users") or []
        if len(users) == 1:
            return RedirectResponse(
                url=f"/admin/ui/users/{users[0]['id']}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        if not users:
            return _render_search(
                request,
                session,
                query=q,
                error="Utilisateur introuvable.",
            )
        # Multiple matches on a UNIQUE column = data inconsistency. We
        # don't crash the UI ; the operator deserves a list and a hint.
        return _render_search(
            request,
            session,
            query=q,
            error="Plusieurs utilisateurs partagent ce support_id (incohérence — vérifier).",
            results=users,
        )

    # Branch 3 — Email partial
    resp = await au_get(
        "/admin/users",
        operator=session.operator,
        params={"email_contains": q, "limit": _EMAIL_SEARCH_LIMIT},
    )
    if resp.status_code != 200:
        return _render_search(
            request,
            session,
            query=q,
            error=f"Erreur AU (HTTP {resp.status_code}).",
        )
    body = resp.json()
    users = body.get("users") or []
    total = int(body.get("total") or 0)
    if not users:
        return _render_search(
            request,
            session,
            query=q,
            error="Aucun utilisateur trouvé.",
        )
    return _render_search(
        request,
        session,
        query=q,
        results=users,
        truncated=(total > _EMAIL_SEARCH_LIMIT),
    )


# ---------------------------------------------------------------------------
# Page E — User detail (UI-1.5)
# ---------------------------------------------------------------------------
# Allowed enum values for the scan filter form. Mirrors PA's admin scans
# endpoint contract. Free-form input is rejected client-side via the
# ``<select>`` element ; defense-in-depth on the server below.
_ALLOWED_SCAN_TYPES = ("receipt", "electronic_label", "manual")
_ALLOWED_SCAN_STATUSES = (
    "pending",
    "matched",
    "unresolved",
    "rejected",
    "accepted",
)


def _date_to_start_dt(d: date_type) -> datetime:
    """Mirror of routes/admin/users.py — ``since=YYYY-MM-DD`` → start-of-day UTC."""
    return datetime.combine(d, time(0, 0, 0), tzinfo=UTC)


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail_page(
    request: Request,
    user_id: uuid.UUID,
    scan_type: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    since: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Render identity (AU) + paginated scans (PA local DB) for one user.

    Identity comes from AU ``GET /admin/users/{user_id}``. Scans come
    from PA's local DB via the same SQL shape as
    ``routes.admin.users.admin_list_user_scans`` — we don't HTTP-loopback
    to PA itself (same in-process service).

    Filters propagate to GET params so URLs are bookmarkable and the
    pagination links round-trip the current filter state.
    """
    # Drop unknown enum values silently — rather than 422 from a copy-
    # pasted bad URL, render with no filter applied + the form keeps the
    # user-supplied value visible for correction.
    scan_type_clean = scan_type if scan_type in _ALLOWED_SCAN_TYPES else None
    status_clean = status_filter if status_filter in _ALLOWED_SCAN_STATUSES else None

    # ``since`` parsed lazily — invalid dates ignored (stale bookmark).
    since_dt: datetime | None = None
    if since:
        try:
            since_dt = _date_to_start_dt(date_type.fromisoformat(since))
        except ValueError:
            since_dt = None

    # 1. AU identity call
    au_resp = await au_get(f"/admin/users/{user_id}", operator=session.operator)
    if au_resp.status_code == 404:
        return templates.TemplateResponse(
            request,
            "user_detail.html",
            {
                "session": session,
                "user": None,
                "error": "Utilisateur introuvable.",
                "scans": [],
                "scans_total": 0,
                "filters": {
                    "scan_type": scan_type,
                    "status": status_filter,
                    "since": since,
                    "limit": limit,
                    "offset": offset,
                },
                "prev_qs": "",
                "next_qs": "",
                "has_next": False,
            },
        )
    if au_resp.status_code != 200:
        return templates.TemplateResponse(
            request,
            "user_detail.html",
            {
                "session": session,
                "user": None,
                "error": f"Erreur AU (HTTP {au_resp.status_code}).",
                "scans": [],
                "scans_total": 0,
                "filters": {
                    "scan_type": scan_type,
                    "status": status_filter,
                    "since": since,
                    "limit": limit,
                    "offset": offset,
                },
                "prev_qs": "",
                "next_qs": "",
                "has_next": False,
            },
        )

    user_payload = au_resp.json()

    # 2. Scans local query — same SQL as routes/admin/users.py admin_list_user_scans
    where_parts: list[str] = ["s.user_id = :uid"]
    params: dict[str, Any] = {"uid": str(user_id)}
    if scan_type_clean:
        where_parts.append("s.scan_type = :scan_type")
        params["scan_type"] = scan_type_clean
    if status_clean:
        where_parts.append("s.status = :status")
        params["status"] = status_clean
    if since_dt is not None:
        where_parts.append("s.scanned_at >= :since")
        params["since"] = since_dt
    where_clause = " WHERE " + " AND ".join(where_parts)

    count_sql = "SELECT COUNT(*) AS n FROM scans s" + where_clause  # noqa: S608
    list_sql = (
        "SELECT s.id, s.scan_type, s.status, s.scanned_name, s.product_ean, "
        "       s.store_id, st.name AS store_name, s.match_method, s.scanned_at "
        "FROM scans s LEFT JOIN stores st ON st.id = s.store_id"
        + where_clause
        + " ORDER BY s.scanned_at DESC, s.id LIMIT :limit OFFSET :offset"
    )
    total_row = db.execute(text(count_sql), params).first()
    total = int(total_row.n) if total_row is not None else 0
    scan_rows = db.execute(
        text(list_sql),
        {**params, "limit": limit, "offset": offset},
    ).fetchall()
    scans = [
        {
            "id": str(r.id),
            "scan_type": r.scan_type,
            "status": r.status,
            "scanned_name": r.scanned_name,
            "product_ean": r.product_ean,
            "store_name": r.store_name,
            "match_method": r.match_method,
            "created_at": r.scanned_at.isoformat() if r.scanned_at else "",
        }
        for r in scan_rows
    ]

    # Pagination query strings — preserve the active filters so the
    # operator's filter state survives a Next/Previous click.
    base_qs: dict[str, Any] = {}
    if scan_type:
        base_qs["scan_type"] = scan_type
    if status_filter:
        base_qs["status"] = status_filter
    if since:
        base_qs["since"] = since
    base_qs["limit"] = limit
    prev_offset = max(offset - limit, 0)
    next_offset = offset + limit
    prev_qs = urlencode({**base_qs, "offset": prev_offset})
    next_qs = urlencode({**base_qs, "offset": next_offset})

    return templates.TemplateResponse(
        request,
        "user_detail.html",
        {
            "session": session,
            "user": user_payload,
            "error": None,
            "scans": scans,
            "scans_total": total,
            "filters": {
                "scan_type": scan_type,
                "status": status_filter,
                "since": since,
                "limit": limit,
                "offset": offset,
            },
            "prev_qs": prev_qs,
            "next_qs": next_qs,
            "has_next": next_offset < total,
        },
    )


# ---------------------------------------------------------------------------
# Page F — NRC arbitration (Bloc D admin endpoints surfaced in the mini UI)
# ---------------------------------------------------------------------------
_NRC_ALLOWED_STATES = ("all", "unverified", "controverse")


@router.get("/name-resolutions/queue", response_class=HTMLResponse)
def nrc_queue_page(
    request: Request,
    state: str = Query(default="all"),
    store_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    flash: str | None = None,
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Render the NRC arbitration queue (Bloc D mini UI).

    The page renders the same data as ``GET /api/v1/admin/name-resolutions/queue``
    but invokes the service in-process (no HTTP loopback, mirrors the
    pattern used for stores-pending / knowledge-queue).
    """
    error: str | None = None
    if state not in _NRC_ALLOWED_STATES:
        error = "État invalide."
        state = "all"

    parsed_store: uuid.UUID | None = None
    if store_id and store_id.strip():
        try:
            parsed_store = uuid.UUID(store_id.strip())
        except ValueError:
            error = (error + " " if error else "") + "Store ID invalide."

    items: list[Any] = []
    total = 0
    try:
        items_raw, total = nrc_admin_service.list_arbitration_queue(
            db,
            state_filter=state,
            store_id=parsed_store,
            limit=limit,
            offset=offset,
        )
        items = [
            {
                "store_id": it.store_id,
                "store_name": it.store_name,
                "normalized_label": it.normalized_label,
                "current_state": it.current_state,
                "distinct_validators": it.distinct_validators,
                "top_eans": [
                    {
                        "ean": t.ean,
                        "weighted_count": t.weighted_count,
                        "pct": t.pct,
                        "product_name": t.product_name,
                    }
                    for t in it.top_eans
                ],
                "challenger_count": it.challenger_count,
                "previously_verified_ean": it.previously_verified_ean,
                "last_resolution_at": it.last_resolution_at,
            }
            for it in items_raw
        ]
    except Exception:
        logger.exception("NRC queue render failed")
        error = (error + " " if error else "") + "Erreur lors du chargement."

    return templates.TemplateResponse(
        request,
        "name_resolutions_queue.html",
        {
            "session": session,
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "state": state,
            "store_id": store_id,
            "flash": flash,
            "error": error,
        },
    )


@router.get(
    "/name-resolutions/{store_id}/{normalized_label:path}",
    response_class=HTMLResponse,
)
def nrc_detail_page(
    request: Request,
    store_id: uuid.UUID,
    normalized_label: str,
    flash: str | None = None,
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Render the full detail page for one ``(store, label)`` pair."""
    try:
        detail = nrc_admin_service.get_label_detail(db, store_id=store_id, normalized_label=normalized_label)
    except nrc_admin_service.LabelNotFound:
        return templates.TemplateResponse(
            request,
            "name_resolution_detail.html",
            {
                "session": session,
                "detail": {
                    "store_id": str(store_id),
                    "store_name": None,
                    "normalized_label": normalized_label,
                    "current_state": "unresolved",
                    "previously_verified_ean": None,
                    "resolutions": [],
                    "events": [],
                },
                "detail_top_eans": [],
                "flash": None,
                "error": "Label introuvable.",
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Convenience : also pull the queue snippet for top_eans (so the
    # detail form can suggest top1/top2 values without re-querying).
    top_eans: list[dict[str, Any]] = []
    try:
        items, _ = nrc_admin_service.list_arbitration_queue(
            db, state_filter="all", store_id=store_id, limit=200, offset=0
        )
        for it in items:
            if it.normalized_label == normalized_label:
                top_eans = [{"ean": t.ean, "pct": t.pct} for t in it.top_eans]
                break
    except Exception:
        logger.warning("top_eans suggestion fetch failed", exc_info=True)
        top_eans = []

    return templates.TemplateResponse(
        request,
        "name_resolution_detail.html",
        {
            "session": session,
            "detail": detail,
            "detail_top_eans": top_eans,
            "flash": flash,
            "error": None,
        },
    )


@router.post("/name-resolutions/resolve")
async def nrc_resolve_submit(
    request: Request,
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> Response:
    """Apply a resolve action from the queue table OR the detail form.

    Form fields :
    - ``store_id``         (uuid)
    - ``normalized_label`` (text)
    - ``target_ean``       (text, required)
    - ``operator_note``    (text, optional)
    - ``redirect_to``      (``"queue"`` default OR ``"detail"`` from detail page)
    """
    form = await request.form()
    store_id_raw = _form_str(form, "store_id").strip()
    normalized_label = _form_str(form, "normalized_label").strip()
    target_ean = _form_str(form, "target_ean").strip()
    operator_note = _form_str(form, "operator_note").strip() or None
    redirect_to = _form_str(form, "redirect_to", "queue")

    try:
        store_id = uuid.UUID(store_id_raw)
    except (ValueError, TypeError):
        return RedirectResponse(
            url="/admin/ui/name-resolutions/queue?flash=Store+ID+invalide.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not normalized_label or not target_ean:
        return RedirectResponse(
            url="/admin/ui/name-resolutions/queue?flash=Champs+manquants.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        nrc_admin_service.resolve_label(
            db,
            store_id=store_id,
            normalized_label=normalized_label,
            target_ean=target_ean,
            operator=session.operator,
            operator_note=operator_note[:300] if operator_note else None,
        )
        db.commit()
        flash = f"Résolu : {target_ean}."
    except nrc_admin_service.LabelNotFound:
        flash = "Label introuvable (no scan to anchor)."

    if redirect_to == "detail":
        return RedirectResponse(
            url=(f"/admin/ui/name-resolutions/{store_id}/{normalized_label}?flash={flash}"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/admin/ui/name-resolutions/queue?flash={flash}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/name-resolutions/reject-challenges")
async def nrc_reject_challenges_submit(
    request: Request,
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> Response:
    """Apply a reject-challenges action.

    Form fields : same shape as the resolve form except ``target_ean``
    is implicit (read from the audit log).
    """
    form = await request.form()
    store_id_raw = _form_str(form, "store_id").strip()
    normalized_label = _form_str(form, "normalized_label").strip()
    operator_note = _form_str(form, "operator_note").strip() or None
    redirect_to = _form_str(form, "redirect_to", "queue")

    try:
        store_id = uuid.UUID(store_id_raw)
    except (ValueError, TypeError):
        return RedirectResponse(
            url="/admin/ui/name-resolutions/queue?flash=Store+ID+invalide.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not normalized_label:
        return RedirectResponse(
            url="/admin/ui/name-resolutions/queue?flash=Label+manquant.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        nrc_admin_service.reject_challenges(
            db,
            store_id=store_id,
            normalized_label=normalized_label,
            operator=session.operator,
            operator_note=operator_note[:300] if operator_note else None,
        )
        db.commit()
        flash = "Challengers rejetés."
    except nrc_admin_service.LabelNotFound:
        flash = "Label introuvable."
    except nrc_admin_service.StateMismatch:
        flash = "Action invalide : état ≠ unverified."

    if redirect_to == "detail":
        return RedirectResponse(
            url=(f"/admin/ui/name-resolutions/{store_id}/{normalized_label}?flash={flash}"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/admin/ui/name-resolutions/queue?flash={flash}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Page G — Admin Settings (Bloc D)
# ---------------------------------------------------------------------------
# Source-of-truth for the editable / frozen split lives in RW
# (``services/admin/settings_service.EDITABLE_SECTIONS``). The UI mirrors
# the allowlist locally to render 26 tiles without firing 26 HTTP calls.
# A contract test (``test_admin_ui_settings.test_editable_sections_mirror_matches_rw``)
# diffs the two on every CI run so drift fails the build.

# Cap the JSON body size we accept on a settings PUT. Section payloads are
# small JSON objects (≤ a few KB) ; anything over ~64 KB is either a paste
# error or an attack surface we don't want to forward to RW.
_SETTINGS_BODY_MAX_BYTES = 64 * 1024


def _human_updated_at(dt: datetime | None) -> str:
    """Return a short ISO-8601 form for tile metadata. Empty if absent."""
    if dt is None:
        return ""
    # Strip microseconds for readability — operators don't need µs precision.
    return dt.replace(microsecond=0).isoformat()


def _unwrap_rw_detail(payload: Any) -> tuple[str | None, str | None]:
    """Best-effort unwrap of the ``detail`` field returned by RW HTTPException.

    FastAPI surfaces ``HTTPException(detail=<dict>)`` as ``{"detail": <dict>}``
    so a frozen-key error arrives at the UI as
    ``{"detail": {"detail": "frozen_key_modified", "key": "feed_jack"}}``.
    Unwrap one level to expose the inner ``detail`` code + optional ``key``.
    Falls back to a string-flat detail for the more common errors
    (``section_frozen``, ``reason_too_short``).
    """
    if not isinstance(payload, dict):
        return None, None
    outer = payload.get("detail")
    if isinstance(outer, str):
        return outer, None
    if isinstance(outer, dict):
        inner = outer.get("detail")
        key = outer.get("key")
        return (
            inner if isinstance(inner, str) else None,
            key if isinstance(key, str) else None,
        )
    return None, None


@router.get("/settings", response_class=HTMLResponse)
async def settings_list_page(
    request: Request,
    flash: str | None = None,
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Render the catalogue : editable + frozen sections.

    Reads ``app_settings`` rows directly via SQLAlchemy to surface
    metadata (key count + updated_at) without 26 HTTP round-trips. The
    editable / frozen split comes from the local mirror constant.
    Sections in the mirror but absent from ``app_settings`` render as
    "non seedée" so the operator can spot a missing seed.
    """
    rows = db.execute(text("SELECT section, data, updated_at FROM app_settings")).fetchall()
    metadata: dict[str, dict[str, Any]] = {}
    for r in rows:
        keys = r.data.keys() if isinstance(r.data, dict) else []
        metadata[r.section] = {
            "key_count": len(list(keys)),
            "updated_at_human": _human_updated_at(r.updated_at),
            "seeded": True,
        }

    def _tile(section: str) -> dict[str, Any]:
        meta = metadata.get(section)
        if meta is None:
            return {
                "section": section,
                "seeded": False,
                "key_count": 0,
                "updated_at_human": "",
            }
        return {"section": section, **meta}

    editable = [_tile(s) for s in sorted(EDITABLE_SECTIONS_MIRROR)]
    frozen = [_tile(s) for s in sorted(FROZEN_SECTIONS)]

    return templates.TemplateResponse(
        request,
        "settings_list.html",
        {
            "session": session,
            "editable": editable,
            "frozen": frozen,
            "flash": flash,
        },
    )


def _render_settings_detail(
    request: Request,
    session: AdminSession,
    *,
    section: str,
    data: dict[str, Any] | None,
    editable: bool,
    frozen_keys: list[str],
    flash: str | None = None,
    error: str | None = None,
    reason: str | None = None,
    status_code: int = status.HTTP_200_OK,
    pretty_override: str | None = None,
) -> HTMLResponse:
    """Shared helper to render the detail page in a consistent shape."""
    if pretty_override is not None:
        data_pretty = pretty_override
    elif data is None:
        data_pretty = "{}"
    else:
        data_pretty = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
    return templates.TemplateResponse(
        request,
        "settings_detail.html",
        {
            "session": session,
            "section": section,
            "data_pretty": data_pretty,
            "editable": editable,
            "frozen_keys": frozen_keys,
            "flash": flash,
            "error": error,
            "reason": reason,
        },
        status_code=status_code,
    )


# ---------------------------------------------------------------------------
# Bloc E — Settings audit log (list + detail)
# ---------------------------------------------------------------------------
# Declared BEFORE the ``/settings/{section}`` catch-all so FastAPI matches
# the literal ``/settings/audit`` first. Path-param routes are evaluated
# in registration order ; without this ordering ``/settings/audit`` would
# be served by ``settings_detail_page(section="audit")`` and 404 on RW.
#
# All RW reads go through ``rw_get`` (Bearer ADMIN_API_KEY +
# X-Admin-Operator). The page is read-only so we never call ``rw_put`` /
# ``rw_post`` here. Filters are forwarded as-is — RW is the validation
# authority for the ``status`` enum (applied / pending_2fa / expired /
# cancelled). The local mirror only constrains the ``section`` dropdown.

#: Status enum values surfaced in the filter dropdown. Mirrors RW's
#: ``AdminSettingsAuditStatus`` enum.
_AUDIT_STATUSES: tuple[str, ...] = ("applied", "pending_2fa", "expired", "cancelled")


def _audit_pagination_qs(
    *,
    section: str | None,
    status_filter: str | None,
    limit: int,
    offset: int,
) -> str:
    """Build a query-string preserving filters + the given offset.

    ``urlencode`` drops None values via the explicit dict-comprehension
    filter so an absent filter doesn't surface as ``section=&``.
    """
    base: dict[str, Any] = {
        k: v
        for k, v in {
            "section": section,
            "status": status_filter,
            "limit": limit,
            "offset": offset,
        }.items()
        if v is not None and v != ""
    }
    return urlencode(base)


@router.get("/settings/audit", response_class=HTMLResponse)
async def settings_audit_list_page(
    request: Request,
    section: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """List settings audit entries — paginated, filtered by section / status.

    Calls RW ``GET /admin/settings/audit?section=&status=&limit=&offset=``
    and renders ``settings_audit.html``. RW errors degrade gracefully :
    the page still renders with an empty list + an error banner so the
    operator sees what's broken rather than a generic 500.
    """
    error: str | None = None
    items: list[dict[str, Any]] = []
    total = 0

    params: dict[str, Any] = {
        "limit": limit,
        "offset": offset,
    }
    if section:
        params["section"] = section
    if status_filter:
        params["status"] = status_filter

    resp = await rw_get(
        "/admin/settings/audit",
        operator=session.operator,
        params=params,
    )
    if resp.status_code == 200:
        try:
            body = resp.json()
            items = list(body.get("items") or [])
            total = int(body.get("total") or 0)
        except (ValueError, TypeError):
            error = "Réponse RW invalide."
    else:
        error = f"HTTP {resp.status_code}"
        logger.warning("settings audit list — RW returned %s", resp.status_code)

    next_offset = offset + limit
    has_next = next_offset < total
    prev_offset = max(offset - limit, 0)
    prev_qs = _audit_pagination_qs(
        section=section,
        status_filter=status_filter,
        limit=limit,
        offset=prev_offset,
    )
    next_qs = _audit_pagination_qs(
        section=section,
        status_filter=status_filter,
        limit=limit,
        offset=next_offset,
    )

    # Section dropdown : union of editable + frozen, sorted. Mirrors the
    # list page's catalogue so the operator sees the same surface.
    all_sections = sorted(set(EDITABLE_SECTIONS_MIRROR) | set(FROZEN_SECTIONS))

    return templates.TemplateResponse(
        request,
        "settings_audit.html",
        {
            "session": session,
            "items": items,
            "total": total,
            "section": section,
            "status": status_filter,
            "limit": limit,
            "offset": offset,
            "has_next": has_next,
            "prev_qs": prev_qs,
            "next_qs": next_qs,
            "all_sections": all_sections,
            "all_statuses": _AUDIT_STATUSES,
            "error": error,
        },
    )


@router.get("/settings/audit/{audit_id}", response_class=HTMLResponse)
async def settings_audit_detail_page(
    audit_id: uuid.UUID,
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """Render one audit row : header + reason + diff + old/new data side-by-side.

    Calls RW ``GET /admin/settings/audit/{audit_id}`` which returns the
    full payload (``old_data`` / ``new_data`` / ``diff``). RW computes
    ``diff`` on-fly when the column is NULL so we always render a non-
    empty diff section when both sides are non-null.

    Returns 404 to the browser when RW returns 404 — mirrors the upstream
    semantic so a stale bookmark to a deleted audit row gives the right
    HTTP signal (the template renders a friendly "introuvable" message).
    """
    resp = await rw_get(
        f"/admin/settings/audit/{audit_id}",
        operator=session.operator,
    )

    if resp.status_code == 404:
        return templates.TemplateResponse(
            request,
            "settings_audit_detail.html",
            {
                "session": session,
                "audit": None,
                "old_data_pretty": "",
                "new_data_pretty": "",
                "error": "Entrée d'audit introuvable.",
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if resp.status_code != 200:
        logger.warning("settings audit detail — RW returned %s for %s", resp.status_code, audit_id)
        return templates.TemplateResponse(
            request,
            "settings_audit_detail.html",
            {
                "session": session,
                "audit": None,
                "old_data_pretty": "",
                "new_data_pretty": "",
                "error": f"Erreur RW (HTTP {resp.status_code}).",
            },
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    try:
        audit = resp.json()
    except ValueError:
        return templates.TemplateResponse(
            request,
            "settings_audit_detail.html",
            {
                "session": session,
                "audit": None,
                "old_data_pretty": "",
                "new_data_pretty": "",
                "error": "Réponse RW invalide.",
            },
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    old_data = audit.get("old_data")
    new_data = audit.get("new_data")
    old_data_pretty = (
        json.dumps(old_data, indent=2, ensure_ascii=False, sort_keys=True)
        if old_data is not None
        else "(aucune valeur précédente)"
    )
    new_data_pretty = (
        json.dumps(new_data, indent=2, ensure_ascii=False, sort_keys=True)
        if new_data is not None
        else "(aucune nouvelle valeur)"
    )

    return templates.TemplateResponse(
        request,
        "settings_audit_detail.html",
        {
            "session": session,
            "audit": audit,
            "old_data_pretty": old_data_pretty,
            "new_data_pretty": new_data_pretty,
            "error": None,
        },
    )


@router.get("/settings/{section}", response_class=HTMLResponse)
async def settings_detail_page(
    section: str,
    request: Request,
    flash: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """Render the detail page : form if editable, read-only preview if frozen.

    Two RW calls : current data + editable / frozen_keys metadata. Both
    are read-only ; we don't gate the page on a single failure — surface
    a partial state with an ``error`` flash so the operator sees what's
    broken rather than a blank 500.
    """
    # 1. Current data
    data: dict[str, Any] | None = None
    error: str | None = None
    data_resp = await rw_get(f"/admin/settings/{section}", operator=session.operator)
    if data_resp.status_code == 200:
        try:
            data = data_resp.json()
        except ValueError:
            error = f"Réponse RW invalide (HTTP {data_resp.status_code})."
    elif data_resp.status_code == 404:
        # Not seeded yet — render with empty data so the operator can
        # write the first version (rare but possible after seed_settings
        # is run for the first time on a fresh deploy).
        data = {}
    else:
        error = f"Erreur RW lecture (HTTP {data_resp.status_code})."

    # 2. Editable / frozen metadata
    editable = is_editable_local(section)
    frozen_keys: list[str] = []
    edit_resp = await rw_get(f"/admin/settings/{section}/editable", operator=session.operator)
    if edit_resp.status_code == 200:
        try:
            payload = edit_resp.json()
            editable = bool(payload.get("editable", editable))
            frozen_keys = list(payload.get("frozen_keys") or [])
        except ValueError:
            pass  # Keep the local mirror as fallback.

    return _render_settings_detail(
        request,
        session,
        section=section,
        data=data,
        editable=editable,
        frozen_keys=frozen_keys,
        flash=flash,
        error=error,
    )


async def _re_render_with_error(
    request: Request,
    session: AdminSession,
    section: str,
    *,
    data_pretty_override: str,
    reason: str,
    error: str,
) -> Response:
    """Re-render the detail form preserving the operator's input + error.

    Refetches editable / frozen metadata so the form's enabled state
    matches the section policy. Falls back to the local mirror on RW
    error so the operator can still see their input.
    """
    editable = is_editable_local(section)
    frozen_keys: list[str] = []
    try:
        edit_resp = await rw_get(f"/admin/settings/{section}/editable", operator=session.operator)
        if edit_resp.status_code == 200:
            payload = edit_resp.json()
            editable = bool(payload.get("editable", editable))
            frozen_keys = list(payload.get("frozen_keys") or [])
    except Exception:
        logger.warning("editable refetch failed for %s", section, exc_info=True)
    return _render_settings_detail(
        request,
        session,
        section=section,
        data=None,
        editable=editable,
        frozen_keys=frozen_keys,
        error=error,
        reason=reason,
        pretty_override=data_pretty_override,
    )


@router.post("/settings/{section}")
async def settings_save_submit(
    section: str,
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Submit a save — validate locally then forward PUT to RW.

    Local validation (cheap, fail-fast before HTTP) :
    - ``reason`` must be ≥ 8 chars (mirrors the RW contract).
    - ``data`` must be parseable JSON.

    RW responses :
    - 200 ``{audit_id, status='applied'}`` → 303 redirect with flash.
    - 200 ``{audit_id, status='pending_2fa'}`` → render 2FA page.
    - 403 ``section_frozen`` / ``frozen_key_modified`` → 303 redirect with
      flash (key name surfaced for frozen_key_modified).
    - other → 303 redirect with generic error flash.
    """
    from urllib.parse import quote

    form = await request.form()
    raw_data = form.get("data") or ""
    reason = _form_str(form, "reason").strip()

    # 1. reason length check (fail-fast before HTTP)
    if len(reason) < 8:
        return await _re_render_with_error(
            request,
            session,
            section,
            data_pretty_override=str(raw_data),
            reason=reason,
            error="La raison doit contenir au moins 8 caractères (motivation business).",
        )

    # 2. JSON parse — body size cap before parsing prevents pathological
    # JSON DoS vectors at the UI edge.
    if isinstance(raw_data, str) and len(raw_data.encode("utf-8")) > _SETTINGS_BODY_MAX_BYTES:
        return await _re_render_with_error(
            request,
            session,
            section,
            data_pretty_override=str(raw_data),
            reason=reason,
            error="JSON trop volumineux (> 64 KB).",
        )
    try:
        parsed = json.loads(raw_data) if isinstance(raw_data, str) else None
    except json.JSONDecodeError as exc:
        return await _re_render_with_error(
            request,
            session,
            section,
            data_pretty_override=str(raw_data),
            reason=reason,
            error=f"JSON invalide : {exc.msg} (ligne {exc.lineno}).",
        )
    if not isinstance(parsed, dict):
        return await _re_render_with_error(
            request,
            session,
            section,
            data_pretty_override=str(raw_data),
            reason=reason,
            error="JSON invalide : un objet ({}) est attendu en racine.",
        )

    # 3. PUT to RW
    resp = await rw_put(
        f"/admin/settings/{section}",
        operator=session.operator,
        json={"data": parsed, "reason": reason},
    )
    if resp.status_code == 200:
        body = resp.json()
        rw_status = body.get("status")
        audit_id = body.get("audit_id")
        if rw_status == "applied":
            return RedirectResponse(
                url=f"/admin/ui/settings/{section}?flash=Saved%21",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        if rw_status == "pending_2fa":
            return templates.TemplateResponse(
                request,
                "settings_2fa_pending.html",
                {
                    "session": session,
                    "section": section,
                    "audit_id": audit_id,
                    "expires_at_iso": body.get("expires_at"),
                    "error": None,
                },
            )
        # Unknown status — surface as a generic error.
        return RedirectResponse(
            url=f"/admin/ui/settings/{section}?flash={quote('Réponse RW inconnue.')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # 4. Error responses
    try:
        err_payload = resp.json() if resp.content else None
    except ValueError:
        err_payload = None
    detail_code, detail_key = _unwrap_rw_detail(err_payload)
    if resp.status_code == 403 and detail_code == "frozen_key_modified" and detail_key:
        flash_msg = f"Sous-clé frozen modifiée : {detail_key} (refusé)."
    elif resp.status_code == 403 and detail_code == "section_frozen":
        flash_msg = "Section frozen : modification interdite via UI."
    elif resp.status_code == 422 and detail_code == "reason_too_short":
        flash_msg = "Raison trop courte (8 caractères minimum)."
    else:
        flash_msg = f"Erreur RW (HTTP {resp.status_code})."
    return RedirectResponse(
        url=f"/admin/ui/settings/{section}?flash={quote(flash_msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/settings/{section}/confirm-2fa")
async def settings_confirm_2fa_submit(
    section: str,
    request: Request,
    audit_id: str = Form(...),
    totp: str = Form(...),
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Forward TOTP to RW ``confirm-2fa`` and redirect / re-render.

    - 200 → redirect to detail with success flash.
    - 401 ``totp_invalid`` → re-render the 2FA page with an error.
    - 410 ``audit_expired`` / 409 ``audit_not_pending`` → redirect with flash.
    """
    from urllib.parse import quote

    resp = await rw_post(
        f"/admin/settings/{section}/confirm-2fa",
        operator=session.operator,
        json={"audit_id": audit_id},
        totp=totp.strip() or None,
    )
    if resp.status_code == 200:
        return RedirectResponse(
            url=f"/admin/ui/settings/{section}?flash={quote('Appliqué via 2FA.')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if resp.status_code == 401:
        # Re-render the 2FA page so the operator can retry within the
        # same grace period without re-submitting the original PUT.
        return templates.TemplateResponse(
            request,
            "settings_2fa_pending.html",
            {
                "session": session,
                "section": section,
                "audit_id": audit_id,
                "expires_at_iso": None,
                "error": "Code TOTP invalide. Réessayez.",
            },
            status_code=status.HTTP_200_OK,
        )
    if resp.status_code == 410:
        flash_msg = "Délai expiré — modification abandonnée."
    elif resp.status_code == 409:
        flash_msg = "Modification déjà résolue (applied / cancelled)."
    elif resp.status_code == 404:
        flash_msg = "Audit row introuvable."
    else:
        flash_msg = f"Erreur RW (HTTP {resp.status_code})."
    return RedirectResponse(
        url=f"/admin/ui/settings/{section}?flash={quote(flash_msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/settings/{section}/cancel-pending")
async def settings_cancel_pending_submit(
    section: str,
    request: Request,
    audit_id: str = Form(...),
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Forward cancel to RW and redirect to the detail page with a flash."""
    from urllib.parse import quote

    resp = await rw_post(
        f"/admin/settings/{section}/cancel-pending",
        operator=session.operator,
        json={"audit_id": audit_id},
    )
    if resp.status_code == 200:
        flash_msg = "Modification annulée."
    elif resp.status_code == 410:
        flash_msg = "Délai expiré — modification abandonnée."
    elif resp.status_code == 409:
        flash_msg = "Modification déjà résolue."
    elif resp.status_code == 404:
        flash_msg = "Audit row introuvable."
    else:
        flash_msg = f"Erreur RW (HTTP {resp.status_code})."
    return RedirectResponse(
        url=f"/admin/ui/settings/{section}?flash={quote(flash_msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Bloc D — Battle Pass admin UI (PR2)
# ---------------------------------------------------------------------------
# Pages :
#   GET  /admin/ui/battlepass                       — list seasons + create form
#   GET  /admin/ui/battlepass/{season_id}           — season detail + milestone form
#   POST /admin/ui/battlepass                       — create season (form POST)
#   POST /admin/ui/battlepass/{season_id}           — create milestone (form POST)
#   POST /admin/ui/battlepass/{season_id}/validate  — activate season (PATCH proxy)
#
# All RW calls are routed through ``rw_get`` / ``rw_post`` / ``rw_patch``
# so the auth header model (Bearer ADMIN_API_KEY + X-Admin-Operator)
# stays uniform with the rest of the admin UI. Errors degrade gracefully
# — the page renders with an error banner rather than a generic 500.


def _bp_season_form_body(form: Any) -> dict[str, Any]:
    """Coerce the BP season create-form into the JSON body RW expects."""
    return {
        "name": (form.get("name") or "").strip(),
        "season_number": int(form.get("season_number") or 0),
        "started_at": form.get("started_at") or "",
        "ends_at": form.get("ends_at") or "",
    }


def _bp_milestone_form_body(form: Any) -> dict[str, Any]:
    """Coerce the milestone create-form into the JSON body RW expects.

    Checkbox ``subscriber_only`` follows HTML form semantics : present in
    the body when checked (value ``on`` / ``true`` / ``1``), absent
    otherwise. We map every truthy text to ``True`` and the rest to
    ``False`` so the operator can either tick the box (browser default)
    or post the form programmatically with ``"true"``.
    """
    sub = (form.get("subscriber_only") or "").strip().lower()
    return {
        "milestone_number": int(form.get("milestone_number") or 0),
        "cab_required": int(form.get("cab_required") or 0),
        "reward_type": (form.get("reward_type") or "").strip(),
        "reward_value": int(form.get("reward_value") or 0),
        "subscriber_only": sub in ("on", "true", "1", "yes"),
    }


async def _bp_fetch_seasons(
    session: AdminSession,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch the season list. Returns (seasons, error_msg_or_None)."""
    resp = await rw_get(
        "/admin/battlepass/seasons",
        operator=session.operator,
    )
    if resp.status_code == 200:
        try:
            body = resp.json()
            return list(body.get("seasons") or []), None
        except ValueError:
            return [], "Réponse RW invalide."
    logger.warning("battlepass list — RW returned %s", resp.status_code)
    return [], f"Erreur RW (HTTP {resp.status_code})."


def _admin_flash_for_status(status_code: int, body: dict[str, Any]) -> str:
    """Map RW response status → user-visible flash for admin actions.

    Shared by both BP and Missions actions — surfaces RW's structured
    ``detail`` codes (e.g. ``season_number_conflict``,
    ``mission_uniqueness_conflict``) so the operator sees the wire-level
    rejection reason rather than a generic HTTP code.
    """
    if status_code in (200, 201):
        return "OK."
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("detail") or str(detail)
    if status_code == 409 and detail:
        return f"Conflict — {detail}"
    if status_code == 404 and detail:
        return f"Not found — {detail}"
    if status_code == 422 and detail:
        return f"Invalid — {detail}"
    if status_code == 403:
        return "Accès refusé."
    return f"Erreur RW (HTTP {status_code})."


def _safe_json(resp: Any) -> dict[str, Any]:
    """Read JSON from an httpx response, swallow malformed bodies."""
    try:
        body = resp.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}


@router.get("/battlepass", response_class=HTMLResponse)
async def battlepass_list_page(
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """List seasons + render the create-season form."""
    seasons, fetch_error = await _bp_fetch_seasons(session)
    return templates.TemplateResponse(
        request,
        "battlepass_list.html",
        {
            "session": session,
            "seasons": seasons,
            "flash": flash,
            "error": error or fetch_error,
        },
    )


@router.get("/battlepass/{season_id}", response_class=HTMLResponse)
async def battlepass_detail_page(
    season_id: uuid.UUID,
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """Render one season + milestone create-form.

    The RW surface (PR1) does not yet expose ``GET /admin/battlepass/seasons/{id}``
    nor a milestone listing endpoint. We re-use the list endpoint and
    filter client-side. A missing season → 404 page (the route still
    returns an HTML body for human-friendly debugging vs a JSON 404).
    """
    seasons, fetch_error = await _bp_fetch_seasons(session)
    season = next((s for s in seasons if str(s.get("id")) == str(season_id)), None)
    if season is None:
        return templates.TemplateResponse(
            request,
            "battlepass_detail.html",
            {
                "session": session,
                "season": None,
                "flash": None,
                "error": fetch_error or "Saison introuvable.",
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return templates.TemplateResponse(
        request,
        "battlepass_detail.html",
        {
            "session": session,
            "season": season,
            "flash": flash,
            "error": error or fetch_error,
        },
    )


@router.post("/battlepass")
async def battlepass_create_action(
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Create a new battle pass season. Form POST → RW POST."""
    from urllib.parse import quote as _q

    form = await request.form()
    body = _bp_season_form_body(form)
    resp = await rw_post(
        "/admin/battlepass/seasons",
        operator=session.operator,
        json=body,
    )
    if resp.status_code == 201:
        flash_msg = f"Saison « {body['name']} » créée (inactive)."
        return RedirectResponse(
            url=f"/admin/ui/battlepass?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    return RedirectResponse(
        url=f"/admin/ui/battlepass?error={_q(flash_msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/battlepass/{season_id}")
async def battlepass_milestone_create_action(
    season_id: uuid.UUID,
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Create a milestone (tier) for a season. Form POST → RW POST."""
    from urllib.parse import quote as _q

    form = await request.form()
    body = _bp_milestone_form_body(form)
    resp = await rw_post(
        f"/admin/battlepass/seasons/{season_id}/tiers",
        operator=session.operator,
        json=body,
    )
    if resp.status_code == 201:
        flash_msg = f"Milestone #{body['milestone_number']} créé."
        return RedirectResponse(
            url=f"/admin/ui/battlepass/{season_id}?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    return RedirectResponse(
        url=f"/admin/ui/battlepass/{season_id}?error={_q(flash_msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/battlepass/{season_id}/validate")
async def battlepass_validate_action(
    season_id: uuid.UUID,
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Activate a season (single-active invariant enforced by RW).

    The brief calls this ``validate`` ; the RW endpoint is
    ``PATCH /admin/battlepass/seasons/{id}/activate``. We keep the public
    URL aligned with the brief vocabulary and translate to PATCH on the wire.
    """
    from urllib.parse import quote as _q

    resp = await rw_patch(
        f"/admin/battlepass/seasons/{season_id}/activate",
        operator=session.operator,
    )
    if resp.status_code == 200:
        flash_msg = "Saison activée."
        return RedirectResponse(
            url=f"/admin/ui/battlepass/{season_id}?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    # 404 → list page (the season doesn't exist anymore) ; everything
    # else → detail page (so the operator can adjust + retry).
    target = (
        f"/admin/ui/battlepass?error={_q(flash_msg)}"
        if resp.status_code == 404
        else f"/admin/ui/battlepass/{season_id}?error={_q(flash_msg)}"
    )
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Bloc D — Missions admin UI (PR2)
# ---------------------------------------------------------------------------
# Pages :
#   GET  /admin/ui/missions               — list catalogue + create form
#   GET  /admin/ui/missions/{mission_id}  — detail / edit form
#   POST /admin/ui/missions               — create catalogue row (form POST)
#   POST /admin/ui/missions/{mission_id}  — partial update (PATCH proxy)
#
# RW endpoints proxied (PR1) :
#   GET   /admin/missions/templates
#   POST  /admin/missions/templates
#   PATCH /admin/missions/templates/{id}


def _mission_form_body(form: Any) -> dict[str, Any]:
    """Coerce mission form into the JSON body RW expects.

    Checkboxes (``is_active`` / ``is_boostable``) follow HTML form
    semantics : present in the body when checked. Mapped to bool here
    so the wire payload is type-correct (Pydantic strict bool).
    """

    def _bool(name: str) -> bool:
        return (form.get(name) or "").strip().lower() in ("on", "true", "1", "yes")

    return {
        "action_type": (form.get("action_type") or "").strip(),
        "frequency": (form.get("frequency") or "").strip(),
        "difficulty": (form.get("difficulty") or "").strip(),
        "target_count": int(form.get("target_count") or 0),
        "cab_reward": int(form.get("cab_reward") or 0),
        "is_active": _bool("is_active"),
        "is_boostable": _bool("is_boostable"),
    }


async def _missions_fetch_list(
    session: AdminSession,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch the catalogue list. Returns (templates, error_msg_or_None)."""
    resp = await rw_get(
        "/admin/missions/templates",
        operator=session.operator,
    )
    if resp.status_code == 200:
        try:
            body = resp.json()
            return list(body.get("templates") or []), None
        except ValueError:
            return [], "Réponse RW invalide."
    logger.warning("missions list — RW returned %s", resp.status_code)
    return [], f"Erreur RW (HTTP {resp.status_code})."


@router.get("/missions", response_class=HTMLResponse)
async def missions_list_page(
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """List catalogue + render the create-template form."""
    rows, fetch_error = await _missions_fetch_list(session)
    return templates.TemplateResponse(
        request,
        "missions_list.html",
        {
            "session": session,
            "templates": rows,
            "flash": flash,
            "error": error or fetch_error,
        },
    )


@router.get("/missions/{mission_id}", response_class=HTMLResponse)
async def missions_detail_page(
    mission_id: uuid.UUID,
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """Render one template's edit form.

    No RW ``GET /admin/missions/templates/{id}`` exists yet ; we re-use
    the list endpoint and filter client-side. Missing row → 404 page.
    """
    rows, fetch_error = await _missions_fetch_list(session)
    mission = next((m for m in rows if str(m.get("id")) == str(mission_id)), None)
    if mission is None:
        return templates.TemplateResponse(
            request,
            "missions_detail.html",
            {
                "session": session,
                "mission": None,
                "flash": None,
                "error": fetch_error or "Mission introuvable.",
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return templates.TemplateResponse(
        request,
        "missions_detail.html",
        {
            "session": session,
            "mission": mission,
            "flash": flash,
            "error": error or fetch_error,
        },
    )


@router.post("/missions")
async def missions_create_action(
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Create a new catalogue row. Form POST → RW POST."""
    from urllib.parse import quote as _q

    form = await request.form()
    body = _mission_form_body(form)
    resp = await rw_post(
        "/admin/missions/templates",
        operator=session.operator,
        json=body,
    )
    if resp.status_code == 201:
        flash_msg = f"Template {body['action_type']} / {body['frequency']} / {body['difficulty']} créé."
        return RedirectResponse(
            url=f"/admin/ui/missions?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    return RedirectResponse(
        url=f"/admin/ui/missions?error={_q(flash_msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/missions/{mission_id}")
async def missions_update_action(
    mission_id: uuid.UUID,
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Partial update of a catalogue row. Form POST → RW PATCH.

    The form surfaces every column the operator can change and we forward
    them all on each save (full-state replace from the operator's POV).
    RW's PATCH is permissive (every field optional) so this stays
    backwards-compatible if we add columns later.
    """
    from urllib.parse import quote as _q

    form = await request.form()
    body = _mission_form_body(form)
    resp = await rw_patch(
        f"/admin/missions/templates/{mission_id}",
        operator=session.operator,
        json=body,
    )
    if resp.status_code == 200:
        flash_msg = "Template mis à jour."
        return RedirectResponse(
            url=f"/admin/ui/missions/{mission_id}?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    target = (
        f"/admin/ui/missions?error={_q(flash_msg)}"
        if resp.status_code == 404
        else f"/admin/ui/missions/{mission_id}?error={_q(flash_msg)}"
    )
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Bloc D — Challenges admin UI (PR3)
# ---------------------------------------------------------------------------
# Pages :
#   GET  /admin/ui/challenges                              — list + create form
#   GET  /admin/ui/challenges/{challenge_id}               — detail + milestone form
#   POST /admin/ui/challenges                              — create (form POST)
#   POST /admin/ui/challenges/{challenge_id}               — activate / deactivate (PATCH proxy)
#   POST /admin/ui/challenges/{challenge_id}/milestones    — create milestone (form POST)
#
# RW endpoints proxied :
#   GET   /admin/challenges
#   POST  /admin/challenges
#   POST  /admin/challenges/{id}/milestones
#   PATCH /admin/challenges/{id}/activate
#   PATCH /admin/challenges/{id}/deactivate


def _challenge_create_form_body(form: Any) -> dict[str, Any]:
    """Coerce the challenge create-form into RW's JSON body shape."""
    description = (form.get("description") or "").strip()
    grace_raw = (form.get("grace_period_days") or "").strip()
    body: dict[str, Any] = {
        "title": (form.get("title") or "").strip(),
        "action_type": (form.get("action_type") or "").strip(),
        "objective": int(form.get("objective") or 0),
        "starts_at": form.get("starts_at") or "",
        "ends_at": form.get("ends_at") or "",
    }
    if description:
        body["description"] = description
    if grace_raw:
        with contextlib.suppress(ValueError):
            body["grace_period_days"] = int(grace_raw)
    return body


async def _challenges_fetch_list(
    session: AdminSession,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch the challenges list. Returns (challenges, error_msg_or_None).

    RW returns a bare JSON array, not a wrapped ``{challenges: [...]}``
    envelope (mirrors ``list_challenges_with_state`` shape). We handle both
    shapes defensively in case the contract changes — the array branch is
    the current truth.
    """
    resp = await rw_get("/admin/challenges", operator=session.operator)
    if resp.status_code == 200:
        try:
            body = resp.json()
            if isinstance(body, list):
                return list(body), None
            if isinstance(body, dict):
                items = body.get("challenges")
                if isinstance(items, list):
                    return list(items), None
            return [], "Réponse RW invalide."
        except ValueError:
            return [], "Réponse RW invalide."
    logger.warning("challenges list — RW returned %s", resp.status_code)
    return [], f"Erreur RW (HTTP {resp.status_code})."


@router.get("/challenges", response_class=HTMLResponse)
async def challenges_list_page(
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """List challenges + render the create-challenge form."""
    challenges, fetch_error = await _challenges_fetch_list(session)
    return templates.TemplateResponse(
        request,
        "challenges_list.html",
        {
            "session": session,
            "challenges": challenges,
            "flash": flash,
            "error": error or fetch_error,
        },
    )


@router.get("/challenges/{challenge_id}", response_class=HTMLResponse)
async def challenges_detail_page(
    challenge_id: uuid.UUID,
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """Render one challenge + milestone create-form.

    No ``GET /admin/challenges/{id}`` exists — we re-use the listing call
    and filter client-side. Missing challenge → 404 page (HTML body for
    human-friendly debugging vs JSON 404).
    """
    challenges, fetch_error = await _challenges_fetch_list(session)
    challenge = next((c for c in challenges if str(c.get("id")) == str(challenge_id)), None)
    if challenge is None:
        return templates.TemplateResponse(
            request,
            "challenges_detail.html",
            {
                "session": session,
                "challenge": None,
                "flash": None,
                "error": fetch_error or "Challenge introuvable.",
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return templates.TemplateResponse(
        request,
        "challenges_detail.html",
        {
            "session": session,
            "challenge": challenge,
            "flash": flash,
            "error": error or fetch_error,
        },
    )


@router.post("/challenges")
async def challenges_create_action(
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Create a new challenge. Form POST → RW POST."""
    from urllib.parse import quote as _q

    form = await request.form()
    body = _challenge_create_form_body(form)
    resp = await rw_post(
        "/admin/challenges",
        operator=session.operator,
        json=body,
    )
    if resp.status_code == 201:
        flash_msg = f"Challenge « {body.get('title', '')} » créé (inactive)."
        return RedirectResponse(
            url=f"/admin/ui/challenges?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    return RedirectResponse(
        url=f"/admin/ui/challenges?error={_q(flash_msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/challenges/{challenge_id}")
async def challenges_update_action(
    challenge_id: uuid.UUID,
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Activate / deactivate a challenge (form action=activate|deactivate).

    The brief calls for a single ``POST /challenges/{id}`` route ; RW
    exposes two PATCH endpoints (``/activate`` and ``/deactivate``). We
    branch on the hidden form field ``action`` so the operator's two
    buttons (Activer / Désactiver) hit the right RW route. Anything other
    than the two known values → flash error redirect to detail.
    """
    from urllib.parse import quote as _q

    form = await request.form()
    action = _form_str(form, "action").strip().lower()
    if action not in ("activate", "deactivate"):
        return RedirectResponse(
            url=(f"/admin/ui/challenges/{challenge_id}?error={_q('Action inconnue (activate / deactivate attendu).')}"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    resp = await rw_patch(
        f"/admin/challenges/{challenge_id}/{action}",
        operator=session.operator,
    )
    if resp.status_code == 200:
        flash_msg = "Challenge activé." if action == "activate" else "Challenge désactivé."
        return RedirectResponse(
            url=f"/admin/ui/challenges/{challenge_id}?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    target = (
        f"/admin/ui/challenges?error={_q(flash_msg)}"
        if resp.status_code == 404
        else f"/admin/ui/challenges/{challenge_id}?error={_q(flash_msg)}"
    )
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/challenges/{challenge_id}/milestones")
async def challenges_milestone_create_action(
    challenge_id: uuid.UUID,
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Create a milestone for a challenge. Form POST → RW POST.

    ``reward_value`` is a JSON object on the wire (RW schema says
    ``dict[str, Any]``). We parse the form-supplied JSON locally before
    forwarding so a malformed paste fails fast and re-renders with an
    error rather than 422-ing on RW.
    """
    from urllib.parse import quote as _q

    form = await request.form()
    threshold_raw = _form_str(form, "threshold").strip()
    reward_type = _form_str(form, "reward_type").strip()
    reward_value_raw = _form_str(form, "reward_value").strip()
    label = _form_str(form, "label").strip()
    sort_order_raw = _form_str(form, "sort_order", "0").strip()

    # Parse reward_value JSON locally — fail-fast before HTTP.
    try:
        reward_value = json.loads(reward_value_raw) if reward_value_raw else None
    except json.JSONDecodeError:
        return RedirectResponse(
            url=(f"/admin/ui/challenges/{challenge_id}?error={_q('reward_value invalide : JSON requis.')}"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not isinstance(reward_value, dict):
        return RedirectResponse(
            url=(f"/admin/ui/challenges/{challenge_id}?error={_q('reward_value doit être un objet JSON ({}).')}"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        threshold = int(threshold_raw or 0)
        sort_order = int(sort_order_raw or 0)
    except ValueError:
        return RedirectResponse(
            url=(f"/admin/ui/challenges/{challenge_id}?error={_q('threshold / sort_order doivent être entiers.')}"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    body: dict[str, Any] = {
        "threshold": threshold,
        "reward_type": reward_type,
        "reward_value": reward_value,
        "sort_order": sort_order,
    }
    if label:
        body["label"] = label

    resp = await rw_post(
        f"/admin/challenges/{challenge_id}/milestones",
        operator=session.operator,
        json=body,
    )
    if resp.status_code == 201:
        flash_msg = f"Milestone seuil={threshold} créé."
        return RedirectResponse(
            url=f"/admin/ui/challenges/{challenge_id}?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    return RedirectResponse(
        url=f"/admin/ui/challenges/{challenge_id}?error={_q(flash_msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Bloc D — Mystery Product admin UI (PR3)
# ---------------------------------------------------------------------------
# Pages :
#   GET  /admin/ui/mystery                              — list + create form
#   GET  /admin/ui/mystery/{mystery_id}                 — detail + edit form
#   POST /admin/ui/mystery                              — create (form POST)
#   POST /admin/ui/mystery/{mystery_id}                 — partial update (PATCH proxy)
#   GET  /admin/ui/mystery/{mystery_id}/delete          — confirm page
#   POST /admin/ui/mystery/{mystery_id}/delete          — execute delete (DELETE proxy)
#
# RW endpoints proxied :
#   GET    /admin/mystery
#   POST   /admin/mystery
#   PATCH  /admin/mystery/{id}
#   DELETE /admin/mystery/{id}
#
# Delete flow rationale : HTML forms cannot issue DELETE directly, and we
# want a confirmation step (irreversible action). The pattern is :
#   1. detail page links to GET /delete (confirm page)
#   2. confirm page has a POST form to /delete
#   3. POST handler calls RW DELETE and redirects.


def _parse_json_array(raw: str) -> tuple[list[Any] | None, str | None]:
    """Parse a JSON array from a form field. Returns (parsed, error_msg)."""
    if not raw or not raw.strip():
        return [], None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"JSON invalide ligne {exc.lineno} : {exc.msg}"
    if not isinstance(parsed, list):
        return None, "Doit être un array JSON ([])."
    return parsed, None


def _mystery_create_form_body(form: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Coerce mystery create-form into RW JSON body. Returns (body, error)."""
    starts_at = (form.get("starts_at") or "").strip()
    product_ean = (form.get("product_ean") or "").strip()
    category_filter = (form.get("category_filter") or "").strip()
    reward_tiers_raw = form.get("reward_tiers") or ""
    clues_raw = form.get("clues") or ""

    reward_tiers, err = _parse_json_array(str(reward_tiers_raw))
    if err is not None:
        return None, f"reward_tiers : {err}"
    clues, err = _parse_json_array(str(clues_raw))
    if err is not None:
        return None, f"clues : {err}"

    body: dict[str, Any] = {
        "starts_at": starts_at,
        "reward_tiers": reward_tiers,
        "clues": clues,
    }
    if product_ean:
        body["product_ean"] = product_ean
    if category_filter:
        body["category_filter"] = category_filter
    return body, None


def _mystery_update_form_body(form: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Coerce mystery update-form into RW JSON body (every field optional).

    Returns (body, error). The body only includes keys the operator
    actually filled in — RW's PATCH is permissive (every field optional).
    """
    body: dict[str, Any] = {}
    starts_at = (form.get("starts_at") or "").strip()
    product_ean = (form.get("product_ean") or "").strip()
    reward_tiers_raw = (form.get("reward_tiers") or "").strip()
    clues_raw = (form.get("clues") or "").strip()

    if starts_at:
        body["starts_at"] = starts_at
    if product_ean:
        body["product_ean"] = product_ean
    if reward_tiers_raw:
        parsed, err = _parse_json_array(reward_tiers_raw)
        if err is not None:
            return None, f"reward_tiers : {err}"
        body["reward_tiers"] = parsed
    if clues_raw:
        parsed, err = _parse_json_array(clues_raw)
        if err is not None:
            return None, f"clues : {err}"
        body["clues"] = parsed
    return body, None


async def _mystery_fetch_list(
    session: AdminSession,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch the mystery challenges list. RW returns a bare JSON array."""
    resp = await rw_get("/admin/mystery", operator=session.operator)
    if resp.status_code == 200:
        try:
            body = resp.json()
            if isinstance(body, list):
                return list(body), None
            return [], "Réponse RW invalide."
        except ValueError:
            return [], "Réponse RW invalide."
    logger.warning("mystery list — RW returned %s", resp.status_code)
    return [], f"Erreur RW (HTTP {resp.status_code})."


@router.get("/mystery", response_class=HTMLResponse)
async def mystery_list_page(
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """List mystery challenges + render the create form."""
    challenges, fetch_error = await _mystery_fetch_list(session)
    return templates.TemplateResponse(
        request,
        "mystery_list.html",
        {
            "session": session,
            "challenges": challenges,
            "flash": flash,
            "error": error or fetch_error,
        },
    )


@router.get("/mystery/{mystery_id}", response_class=HTMLResponse)
async def mystery_detail_page(
    mystery_id: uuid.UUID,
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """Render one mystery challenge's edit form.

    Re-uses the listing call and filters client-side — no RW
    ``GET /admin/mystery/{id}`` endpoint exists.
    """
    challenges, fetch_error = await _mystery_fetch_list(session)
    challenge = next((c for c in challenges if str(c.get("id")) == str(mystery_id)), None)
    if challenge is None:
        return templates.TemplateResponse(
            request,
            "mystery_detail.html",
            {
                "session": session,
                "challenge": None,
                "flash": None,
                "error": fetch_error or "Défi introuvable.",
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return templates.TemplateResponse(
        request,
        "mystery_detail.html",
        {
            "session": session,
            "challenge": challenge,
            "flash": flash,
            "error": error or fetch_error,
        },
    )


@router.post("/mystery")
async def mystery_create_action(
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Create a new mystery challenge. Form POST → RW POST."""
    from urllib.parse import quote as _q

    form = await request.form()
    body, parse_error = _mystery_create_form_body(form)
    if parse_error is not None:
        return RedirectResponse(
            url=f"/admin/ui/mystery?error={_q(parse_error)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    # _mystery_create_form_body contract: parse_error is None ⟹ body is set.
    assert body is not None
    resp = await rw_post(
        "/admin/mystery",
        operator=session.operator,
        json=body,
    )
    if resp.status_code in (200, 201):
        flash_msg = "Défi mystère créé."
        return RedirectResponse(
            url=f"/admin/ui/mystery?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    return RedirectResponse(
        url=f"/admin/ui/mystery?error={_q(flash_msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/mystery/{mystery_id}")
async def mystery_update_action(
    mystery_id: uuid.UUID,
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Partial update of a mystery challenge. Form POST → RW PATCH."""
    from urllib.parse import quote as _q

    form = await request.form()
    body, parse_error = _mystery_update_form_body(form)
    if parse_error is not None:
        return RedirectResponse(
            url=f"/admin/ui/mystery/{mystery_id}?error={_q(parse_error)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    resp = await rw_patch(
        f"/admin/mystery/{mystery_id}",
        operator=session.operator,
        json=body,
    )
    if resp.status_code == 200:
        flash_msg = "Défi mis à jour."
        return RedirectResponse(
            url=f"/admin/ui/mystery/{mystery_id}?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    target = (
        f"/admin/ui/mystery?error={_q(flash_msg)}"
        if resp.status_code == 404
        else f"/admin/ui/mystery/{mystery_id}?error={_q(flash_msg)}"
    )
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/mystery/{mystery_id}/delete", response_class=HTMLResponse)
async def mystery_delete_confirm_page(
    mystery_id: uuid.UUID,
    request: Request,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """Render a confirmation page before executing the irreversible delete."""
    challenges, fetch_error = await _mystery_fetch_list(session)
    challenge = next((c for c in challenges if str(c.get("id")) == str(mystery_id)), None)
    if challenge is None:
        return templates.TemplateResponse(
            request,
            "mystery_delete_confirm.html",
            {
                "session": session,
                "challenge": None,
                "error": fetch_error or "Défi introuvable.",
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return templates.TemplateResponse(
        request,
        "mystery_delete_confirm.html",
        {
            "session": session,
            "challenge": challenge,
            "error": error or fetch_error,
        },
    )


@router.post("/mystery/{mystery_id}/delete")
async def mystery_delete_action(
    mystery_id: uuid.UUID,
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Execute the delete after confirmation. POST → RW DELETE."""
    from urllib.parse import quote as _q

    resp = await rw_delete(
        f"/admin/mystery/{mystery_id}",
        operator=session.operator,
    )
    if resp.status_code in (200, 204):
        flash_msg = "Défi supprimé."
        return RedirectResponse(
            url=f"/admin/ui/mystery?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    target = (
        f"/admin/ui/mystery?error={_q(flash_msg)}"
        if resp.status_code == 404
        else f"/admin/ui/mystery/{mystery_id}?error={_q(flash_msg)}"
    )
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Bloc D — RewardConfig admin UI (PR3)
# ---------------------------------------------------------------------------
# Pages :
#   GET  /admin/ui/reward-config                — list + create form
#   GET  /admin/ui/reward-config/{id}           — detail (edit form)
#   POST /admin/ui/reward-config                — create
#   POST /admin/ui/reward-config/{id}           — partial update (PATCH proxy)
#   GET  /admin/ui/reward-config/{id}/delete    — confirm page
#   POST /admin/ui/reward-config/{id}/delete    — execute delete (DELETE proxy)
#
# RW endpoints proxied :
#   GET    /admin/rewards/configs
#   GET    /admin/rewards/configs/{id}
#   POST   /admin/rewards/configs
#   PATCH  /admin/rewards/configs/{id}
#   DELETE /admin/rewards/configs/{id}


def _reward_config_form_body(form: Any) -> dict[str, Any]:
    """Coerce the reward_config form into RW JSON body shape."""
    base_raw = (form.get("base_amount") or "").strip()
    body: dict[str, Any] = {
        "action_type": (form.get("action_type") or "").strip(),
    }
    try:
        body["base_amount"] = int(base_raw) if base_raw else 0
    except ValueError:
        body["base_amount"] = 0
    return body


@router.get("/reward-config", response_class=HTMLResponse)
async def reward_config_list_page(
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """List reward_config rows + render the create form."""
    configs: list[dict[str, Any]] = []
    fetch_error: str | None = None
    resp = await rw_get(
        "/admin/rewards/configs",
        operator=session.operator,
    )
    if resp.status_code == 200:
        try:
            body = resp.json()
            configs = list(body.get("configs") or [])
        except (ValueError, AttributeError):
            fetch_error = "Réponse RW invalide."
    else:
        fetch_error = f"Erreur RW (HTTP {resp.status_code})."
        logger.warning("reward_config list — RW returned %s", resp.status_code)

    return templates.TemplateResponse(
        request,
        "reward_config_list.html",
        {
            "session": session,
            "configs": configs,
            "flash": flash,
            "error": error or fetch_error,
        },
    )


async def _reward_config_fetch_one(
    session: AdminSession, reward_config_id: uuid.UUID
) -> tuple[dict[str, Any] | None, int, str | None]:
    """Fetch a single reward_config by id. Returns (row, http_status, error)."""
    resp = await rw_get(
        f"/admin/rewards/configs/{reward_config_id}",
        operator=session.operator,
    )
    if resp.status_code == 200:
        try:
            return resp.json(), 200, None
        except ValueError:
            return None, 502, "Réponse RW invalide."
    if resp.status_code == 404:
        return None, 404, "Configuration introuvable."
    return None, resp.status_code, f"Erreur RW (HTTP {resp.status_code})."


@router.get("/reward-config/{reward_config_id}", response_class=HTMLResponse)
async def reward_config_detail_page(
    reward_config_id: uuid.UUID,
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """Render one reward_config edit form."""
    config, http_status, fetch_error = await _reward_config_fetch_one(session, reward_config_id)
    if config is None:
        return templates.TemplateResponse(
            request,
            "reward_config_detail.html",
            {
                "session": session,
                "config": None,
                "flash": None,
                "error": fetch_error,
            },
            status_code=(status.HTTP_404_NOT_FOUND if http_status == 404 else status.HTTP_502_BAD_GATEWAY),
        )
    return templates.TemplateResponse(
        request,
        "reward_config_detail.html",
        {
            "session": session,
            "config": config,
            "flash": flash,
            "error": error,
        },
    )


@router.post("/reward-config")
async def reward_config_create_action(
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Create a new reward_config row. Form POST → RW POST."""
    from urllib.parse import quote as _q

    form = await request.form()
    body = _reward_config_form_body(form)
    resp = await rw_post(
        "/admin/rewards/configs",
        operator=session.operator,
        json=body,
    )
    if resp.status_code == 201:
        flash_msg = f"Config « {body.get('action_type', '')} » créée."
        return RedirectResponse(
            url=f"/admin/ui/reward-config?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    return RedirectResponse(
        url=f"/admin/ui/reward-config?error={_q(flash_msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/reward-config/{reward_config_id}")
async def reward_config_update_action(
    reward_config_id: uuid.UUID,
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Partial update of a reward_config. Form POST → RW PATCH."""
    from urllib.parse import quote as _q

    form = await request.form()
    body = _reward_config_form_body(form)
    resp = await rw_patch(
        f"/admin/rewards/configs/{reward_config_id}",
        operator=session.operator,
        json=body,
    )
    if resp.status_code == 200:
        flash_msg = "Config mise à jour."
        return RedirectResponse(
            url=f"/admin/ui/reward-config/{reward_config_id}?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    target = (
        f"/admin/ui/reward-config?error={_q(flash_msg)}"
        if resp.status_code == 404
        else f"/admin/ui/reward-config/{reward_config_id}?error={_q(flash_msg)}"
    )
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/reward-config/{reward_config_id}/delete", response_class=HTMLResponse)
async def reward_config_delete_confirm_page(
    reward_config_id: uuid.UUID,
    request: Request,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """Render the irreversible-delete confirm page."""
    config, http_status, fetch_error = await _reward_config_fetch_one(session, reward_config_id)
    if config is None:
        return templates.TemplateResponse(
            request,
            "reward_config_delete_confirm.html",
            {
                "session": session,
                "config": None,
                "error": fetch_error,
            },
            status_code=(status.HTTP_404_NOT_FOUND if http_status == 404 else status.HTTP_502_BAD_GATEWAY),
        )
    return templates.TemplateResponse(
        request,
        "reward_config_delete_confirm.html",
        {
            "session": session,
            "config": config,
            "error": error,
        },
    )


@router.post("/reward-config/{reward_config_id}/delete")
async def reward_config_delete_action(
    reward_config_id: uuid.UUID,
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Execute delete after confirmation. POST → RW DELETE."""
    from urllib.parse import quote as _q

    resp = await rw_delete(
        f"/admin/rewards/configs/{reward_config_id}",
        operator=session.operator,
    )
    if resp.status_code in (200, 204):
        flash_msg = "Configuration supprimée."
        return RedirectResponse(
            url=f"/admin/ui/reward-config?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    target = (
        f"/admin/ui/reward-config?error={_q(flash_msg)}"
        if resp.status_code == 404
        else f"/admin/ui/reward-config/{reward_config_id}?error={_q(flash_msg)}"
    )
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Bloc D — StreakTier admin UI (PR3)
# ---------------------------------------------------------------------------
# Pages :
#   GET  /admin/ui/streak-tier              — list + create form
#   GET  /admin/ui/streak-tier/{id}         — detail (edit form)
#   POST /admin/ui/streak-tier              — create
#   POST /admin/ui/streak-tier/{id}         — partial update (PATCH proxy)
#   GET  /admin/ui/streak-tier/{id}/delete  — confirm page
#   POST /admin/ui/streak-tier/{id}/delete  — execute delete (DELETE proxy)
#
# RW endpoints proxied :
#   GET    /admin/rewards/streak-tiers
#   GET    /admin/rewards/streak-tiers/{id}
#   POST   /admin/rewards/streak-tiers
#   PATCH  /admin/rewards/streak-tiers/{id}
#   DELETE /admin/rewards/streak-tiers/{id}


def _streak_tier_form_body(form: Any) -> dict[str, Any]:
    """Coerce the streak_tier form into RW JSON body shape.

    ``multiplier`` is sent as a string — RW accepts ``Decimal`` via Pydantic
    string coercion. We forward the raw form value (after strip) so the
    operator's exact decimal precision survives the round trip.
    """
    days_raw = (form.get("days") or "").strip()
    body: dict[str, Any] = {
        "label": (form.get("label") or "").strip(),
        "multiplier": (form.get("multiplier") or "").strip(),
    }
    try:
        body["days"] = int(days_raw) if days_raw else 0
    except ValueError:
        body["days"] = 0
    return body


@router.get("/streak-tier", response_class=HTMLResponse)
async def streak_tier_list_page(
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """List streak_tier rows + render the create form."""
    tiers: list[dict[str, Any]] = []
    fetch_error: str | None = None
    resp = await rw_get(
        "/admin/rewards/streak-tiers",
        operator=session.operator,
    )
    if resp.status_code == 200:
        try:
            body = resp.json()
            tiers = list(body.get("tiers") or [])
        except (ValueError, AttributeError):
            fetch_error = "Réponse RW invalide."
    else:
        fetch_error = f"Erreur RW (HTTP {resp.status_code})."
        logger.warning("streak_tier list — RW returned %s", resp.status_code)

    return templates.TemplateResponse(
        request,
        "streak_tier_list.html",
        {
            "session": session,
            "tiers": tiers,
            "flash": flash,
            "error": error or fetch_error,
        },
    )


async def _streak_tier_fetch_one(
    session: AdminSession, streak_tier_id: uuid.UUID
) -> tuple[dict[str, Any] | None, int, str | None]:
    """Fetch a single streak_tier by id. Returns (row, http_status, error)."""
    resp = await rw_get(
        f"/admin/rewards/streak-tiers/{streak_tier_id}",
        operator=session.operator,
    )
    if resp.status_code == 200:
        try:
            return resp.json(), 200, None
        except ValueError:
            return None, 502, "Réponse RW invalide."
    if resp.status_code == 404:
        return None, 404, "Tier introuvable."
    return None, resp.status_code, f"Erreur RW (HTTP {resp.status_code})."


@router.get("/streak-tier/{streak_tier_id}", response_class=HTMLResponse)
async def streak_tier_detail_page(
    streak_tier_id: uuid.UUID,
    request: Request,
    flash: str | None = None,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """Render one streak_tier edit form."""
    tier, http_status, fetch_error = await _streak_tier_fetch_one(session, streak_tier_id)
    if tier is None:
        return templates.TemplateResponse(
            request,
            "streak_tier_detail.html",
            {
                "session": session,
                "tier": None,
                "flash": None,
                "error": fetch_error,
            },
            status_code=(status.HTTP_404_NOT_FOUND if http_status == 404 else status.HTTP_502_BAD_GATEWAY),
        )
    return templates.TemplateResponse(
        request,
        "streak_tier_detail.html",
        {
            "session": session,
            "tier": tier,
            "flash": flash,
            "error": error,
        },
    )


@router.post("/streak-tier")
async def streak_tier_create_action(
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Create a new streak_tier row. Form POST → RW POST."""
    from urllib.parse import quote as _q

    form = await request.form()
    body = _streak_tier_form_body(form)
    resp = await rw_post(
        "/admin/rewards/streak-tiers",
        operator=session.operator,
        json=body,
    )
    if resp.status_code == 201:
        flash_msg = f"Tier « {body.get('label', '')} » créé."
        return RedirectResponse(
            url=f"/admin/ui/streak-tier?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    return RedirectResponse(
        url=f"/admin/ui/streak-tier?error={_q(flash_msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/streak-tier/{streak_tier_id}")
async def streak_tier_update_action(
    streak_tier_id: uuid.UUID,
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Partial update of a streak_tier. Form POST → RW PATCH."""
    from urllib.parse import quote as _q

    form = await request.form()
    body = _streak_tier_form_body(form)
    resp = await rw_patch(
        f"/admin/rewards/streak-tiers/{streak_tier_id}",
        operator=session.operator,
        json=body,
    )
    if resp.status_code == 200:
        flash_msg = "Tier mis à jour."
        return RedirectResponse(
            url=f"/admin/ui/streak-tier/{streak_tier_id}?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    target = (
        f"/admin/ui/streak-tier?error={_q(flash_msg)}"
        if resp.status_code == 404
        else f"/admin/ui/streak-tier/{streak_tier_id}?error={_q(flash_msg)}"
    )
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/streak-tier/{streak_tier_id}/delete", response_class=HTMLResponse)
async def streak_tier_delete_confirm_page(
    streak_tier_id: uuid.UUID,
    request: Request,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """Render the irreversible-delete confirm page."""
    tier, http_status, fetch_error = await _streak_tier_fetch_one(session, streak_tier_id)
    if tier is None:
        return templates.TemplateResponse(
            request,
            "streak_tier_delete_confirm.html",
            {
                "session": session,
                "tier": None,
                "error": fetch_error,
            },
            status_code=(status.HTTP_404_NOT_FOUND if http_status == 404 else status.HTTP_502_BAD_GATEWAY),
        )
    return templates.TemplateResponse(
        request,
        "streak_tier_delete_confirm.html",
        {
            "session": session,
            "tier": tier,
            "error": error,
        },
    )


@router.post("/streak-tier/{streak_tier_id}/delete")
async def streak_tier_delete_action(
    streak_tier_id: uuid.UUID,
    request: Request,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Execute delete after confirmation. POST → RW DELETE."""
    from urllib.parse import quote as _q

    resp = await rw_delete(
        f"/admin/rewards/streak-tiers/{streak_tier_id}",
        operator=session.operator,
    )
    if resp.status_code in (200, 204):
        flash_msg = "Tier supprimé."
        return RedirectResponse(
            url=f"/admin/ui/streak-tier?flash={_q(flash_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    flash_msg = _admin_flash_for_status(resp.status_code, _safe_json(resp))
    target = (
        f"/admin/ui/streak-tier?error={_q(flash_msg)}"
        if resp.status_code == 404
        else f"/admin/ui/streak-tier/{streak_tier_id}?error={_q(flash_msg)}"
    )
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Page — Skills review (Hermes claude-code-postmortem candidates)
# ---------------------------------------------------------------------------
@router.get("/skills", response_class=HTMLResponse)
def skills_review_page(
    request: Request,
    bucket: str | None = None,
    search: str | None = None,
    reviewed: str | None = "true",
    flash: str | None = None,
    error: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """Render the skills-review table across the 3 buckets.

    ``bucket`` filters to one of {candidate, active, archived} ;
    unknown values are silently ignored (URL-friendly). ``search``
    matches name OR description (case-insensitive substring). Both
    filters are applied in-process — the on-disk corpus is small
    (tens of skills max) so no pagination is needed at this stage.

    ``reviewed`` filters on the POC 8 Layer 2-3 review surface :

    - ``"true"`` (default) : show only candidates that have been reviewed
      by the ``claude-skill-reviewer`` skill ; active + archived buckets
      bypass the filter (they have a separate audit trail).
    - ``"false"`` : show only unreviewed candidates (the queue waiting on
      the daily routine).
    - ``"any"`` : disable the filter — every candidate, reviewed or not.

    A "pending review" banner counts the unreviewed candidates regardless
    of the active filter so the operator never loses track of the queue.
    """
    all_skills = skills_admin_service.list_skills_all()
    counts = {
        "active": sum(1 for s in all_skills if s.bucket == "active"),
        "candidate": sum(1 for s in all_skills if s.bucket == "candidate"),
        "archived": sum(1 for s in all_skills if s.bucket == "archived"),
    }
    # Unreviewed-candidates banner counter — independent of the active
    # filter so it always reflects the real queue depth.
    unreviewed_candidates_count = sum(1 for s in all_skills if s.bucket == "candidate" and not s.reviewed_by_claude)
    filtered = all_skills
    if bucket in ("candidate", "active", "archived"):
        filtered = [s for s in filtered if s.bucket == bucket]
    # `reviewed` filter only applies to the candidate bucket — active and
    # archived skills are not subject to Layer 2-3 review.
    reviewed_norm = (reviewed or "true").strip().lower()
    if reviewed_norm not in ("true", "false", "any"):
        reviewed_norm = "true"
    if reviewed_norm == "true":
        filtered = [s for s in filtered if s.bucket != "candidate" or s.reviewed_by_claude]
    elif reviewed_norm == "false":
        filtered = [s for s in filtered if s.bucket != "candidate" or not s.reviewed_by_claude]
    if search:
        needle = search.lower().strip()
        if needle:
            filtered = [s for s in filtered if needle in s.name.lower() or needle in s.description.lower()]
    return templates.TemplateResponse(
        request,
        "skills_review_list.html",
        {
            "session": session,
            "skills": [s.to_dict() for s in filtered],
            "counts": counts,
            "unreviewed_candidates_count": unreviewed_candidates_count,
            "bucket": bucket or "",
            "search": search or "",
            "reviewed": reviewed_norm,
            "flash": flash,
            "error": error,
        },
    )


def _skills_action_redirect(flash: str | None = None, error: str | None = None) -> RedirectResponse:
    """Build the post-action 303 back to the listing.

    Preserves a flash OR error message via query-param so the operator
    sees the outcome inline above the table.
    """
    from urllib.parse import quote as _q

    parts: list[str] = []
    if flash:
        parts.append(f"flash={_q(flash)}")
    if error:
        parts.append(f"error={_q(error)}")
    suffix = ("?" + "&".join(parts)) if parts else ""
    return RedirectResponse(
        url=f"/admin/ui/skills{suffix}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/skills/{name}/promote")
def skills_promote(
    name: str,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Promote a candidate skill to active. 404 → inline error."""
    try:
        skill = skills_admin_service.promote_skill(name=name, operator=session.operator)
    except HTTPException as exc:
        return _skills_action_redirect(error=f"Promote {name} : {exc.detail}")
    return _skills_action_redirect(flash=f"Skill « {skill.name} » promu en active.")


@router.post("/skills/{name}/archive")
def skills_archive(
    name: str,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Archive a skill (from candidate or active). 404 → inline error."""
    try:
        skill = skills_admin_service.archive_skill(name=name, operator=session.operator)
    except HTTPException as exc:
        return _skills_action_redirect(error=f"Archive {name} : {exc.detail}")
    return _skills_action_redirect(flash=f"Skill « {skill.name} » archivé.")


@router.post("/skills/{name}/drop")
def skills_drop(
    name: str,
    session: AdminSession = Depends(get_admin_session),
) -> Response:
    """Permanently delete a candidate skill. 400 if not in candidate bucket."""
    try:
        skills_admin_service.drop_skill(name=name, operator=session.operator)
    except HTTPException as exc:
        return _skills_action_redirect(error=f"Drop {name} : {exc.detail}")
    return _skills_action_redirect(flash=f"Skill candidate « {name} » supprimé.")
