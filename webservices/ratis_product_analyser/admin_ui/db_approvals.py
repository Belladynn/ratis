"""Admin UI — DB write approval pages (SP6 + HSP3 M2).

Six routes, all cookie-session gated (``get_admin_session``) :

- ``GET  /db-approvals/unlock``                — HSP3 M2 : saisie du HUMAN_APPROVAL_SECRET
- ``POST /db-approvals/unlock``                — HSP3 M2 : vérification + cookie human_approval_session
- ``GET  /db-approvals``                       — list of ``pending`` rows
- ``GET  /db-approvals/{submission_id}``        — full proposal detail
- ``POST /db-approvals/{submission_id}/approve`` — approve + resume n8n
- ``POST /db-approvals/{submission_id}/reject``  — reject + resume n8n

This is a SEPARATE ``APIRouter`` from the monolithic ``routes.py`` —
mounted under ``/admin/ui`` by ``main.py``. The decision routes update
the ``db_write_approvals`` row then POST the n8n ``resume_url`` to lift
the workflow's ``Wait`` node. A decision on an already-decided row is a
no-op (idempotence — the operator may double-click, or two operators
may race).

Money-table proposals (``touches_money_tables``) require the operator to
re-type the procedure name in a ``confirm_procedure`` form field — no
reflex-click approval of a real-money write.

See ``docs/superpowers/specs/2026-05-18-db-approval-ui-sp6-design.md``.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic as _mono
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from ratis_core.database import get_db
from ratis_core.db_procedure_manifest import ProcedureManifest
from ratis_core.human_challenge import (
    ChallengeError,
    compute_challenge,
    verify_challenge,
)
from ratis_core.models.db_write_approval import DbWriteApproval, DbWriteApprovalStatus
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .auth import AdminSession, get_admin_session
from .human_secret import (
    HUMAN_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    HumanSession,
    create_session,
    get_human_session,
    verify_decision_hmac,
    verify_decision_ts,
    verify_secret,
)

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Wall-clock cap on the resume POST to n8n. Short — the Wait node just
# needs the webhook hit ; a slow n8n must not hang the operator's click.
_RESUME_TIMEOUT_SEC = 10.0

router = APIRouter()

# HSP3 — quota d'essais challenge M1.
# Clé : (session_id, submission_id) → (attempt_count, lockout_until_mono).
# Restart process → reset (acceptable, rare). 3 essais → lockout 60s.
_CHALLENGE_MAX_ATTEMPTS = 3
_CHALLENGE_LOCKOUT_SECONDS = 60
_CHALLENGE_ATTEMPTS: dict[tuple[str, str], tuple[int, float]] = defaultdict(lambda: (0, 0.0))


def _challenge_locked(key: tuple[str, str]) -> float:
    """Returns remaining lockout seconds (0 if not locked)."""
    _attempts, lockout_until = _CHALLENGE_ATTEMPTS[key]
    remaining = lockout_until - _mono()
    return max(0.0, remaining)


def _challenge_register_failure(key: tuple[str, str]) -> None:
    """Increment attempts ; on 3rd, arm 60s lockout."""
    attempts, _lockout = _CHALLENGE_ATTEMPTS[key]
    attempts += 1
    lockout_until = _mono() + _CHALLENGE_LOCKOUT_SECONDS if attempts >= _CHALLENGE_MAX_ATTEMPTS else 0.0
    _CHALLENGE_ATTEMPTS[key] = (attempts, lockout_until)


def _challenge_reset(key: tuple[str, str]) -> None:
    _CHALLENGE_ATTEMPTS.pop(key, None)


def _resume_url_redirect(flash: str) -> RedirectResponse:
    """303 back to the list with a flash message."""
    return RedirectResponse(
        url=f"/admin/ui/db-approvals?flash={quote(flash)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _detail_redirect(submission_id: uuid.UUID, flash: str) -> RedirectResponse:
    """303 back to the detail page with a flash message."""
    return RedirectResponse(
        url=f"/admin/ui/db-approvals/{submission_id}?flash={quote(flash)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _post_resume(resume_url: str, body: dict) -> bool:
    """POST the decision to the n8n Wait-node resume URL.

    Best-effort : returns False on any transport error. The row is
    already updated by the caller — a stuck workflow is visible in the
    UI, but the decision itself is durable in our DB.
    """
    try:
        async with httpx.AsyncClient(timeout=_RESUME_TIMEOUT_SEC) as client:
            await client.post(resume_url, json=body)
        return True
    except Exception:
        logger.warning("db-approval resume POST failed: %s", resume_url, exc_info=True)
        return False


@router.get("/db-approvals/unlock", response_class=HTMLResponse)
def db_approvals_unlock_get(
    request: Request,
    flash: str | None = None,
    session: AdminSession = Depends(get_admin_session),
) -> HTMLResponse:
    """HSP3 — page de saisie du HUMAN_APPROVAL_SECRET (M2).

    Cookie admin SP6 requis (``get_admin_session``) — l'unlock secret
    est *au-dessus* de l'admin session, pas à la place. L'opérateur
    arrive ici après login admin classique.
    """
    return templates.TemplateResponse(
        request,
        "db_approval_unlock.html",
        {"session": session, "flash": flash},
    )


@router.post("/db-approvals/unlock")
def db_approvals_unlock_post(
    request: Request,
    secret: str = Form(...),
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> Response:
    """HSP3 — vérifie le secret contre ``app_settings.human_approval``,
    crée une session HMAC en RAM, pose le cookie ``human_approval_session``.

    Réponses :
      303 + cookie + redirect /db-approvals : OK
      401 ``bad_secret`` : secret invalide
      503 ``human_secret_not_initialised`` : seed pas encore peuplé par
        scripts/init-human-approval-secret.py (acte ops).
    """
    row = db.execute(sa_text("SELECT data FROM app_settings WHERE section='human_approval'")).first()
    data = (row[0] if row else None) or {}
    if not data.get("secret_set"):
        return Response(
            content='{"detail":"human_secret_not_initialised"}',
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )
    stored_hash = data.get("argon2_hash") or ""
    if not verify_secret(stored_hash, secret):
        return Response(
            content='{"detail":"bad_secret"}',
            status_code=status.HTTP_401_UNAUTHORIZED,
            media_type="application/json",
        )

    session_id = create_session(secret)
    resp = RedirectResponse(
        url="/admin/ui/db-approvals",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    resp.set_cookie(
        key=HUMAN_COOKIE_NAME,
        value=session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        secure=False,  # dev — prod via Caddy/HTTPS impose secure=True
    )
    return resp


@router.get("/db-approvals", response_class=HTMLResponse)
def db_approvals_list(
    request: Request,
    flash: str | None = None,
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """List ``pending`` proposals, most recent first."""
    rows = (
        db.execute(
            select(DbWriteApproval)
            .where(DbWriteApproval.status == DbWriteApprovalStatus.PENDING)
            .order_by(DbWriteApproval.created_at.desc())
        )
        .scalars()
        .all()
    )
    approvals = [
        {
            "submission_id": str(r.submission_id),
            "short_id": str(r.submission_id)[:8],
            "procedure": r.payload.get("procedure", "?"),
            "mode": r.payload.get("mode", "existing"),
            "rationale": r.payload.get("rationale", ""),
            "touches_money_tables": r.touches_money_tables,
            "llm_unavailable": r.llm_unavailable,
            "break_glass": bool(r.payload.get("break_glass", False)),
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]
    return templates.TemplateResponse(
        request,
        "db_approvals_list.html",
        {"session": session, "approvals": approvals, "flash": flash},
    )


@router.get("/db-approvals/{submission_id}", response_class=HTMLResponse)
def db_approval_detail(
    request: Request,
    submission_id: uuid.UUID,
    flash: str | None = None,
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> Response:
    """Render the full proposal — support context, dry-run, LLM feedback, badges.

    HSP3 — calcule en plus ``challenge_hint`` (le texte que l'opérateur doit
    taper) et passe au template ``summary_fr`` + ``anomaly_flags`` figés au
    Register approval (cf design §M3+M4).
    """
    row = db.get(DbWriteApproval, submission_id)
    if row is None:
        return _resume_url_redirect("Proposition introuvable.")
    p = row.payload or {}

    # HSP3 — recompute le challenge hint pour l'UI (texte affiché en
    # ``<span user-select:none>`` que l'opérateur doit taper).
    challenge_hint = None
    challenge_error = None
    manifest_dict = p.get("manifest") or {}
    if manifest_dict:
        try:
            manifest = ProcedureManifest.model_validate(manifest_dict)
            challenge_hint = compute_challenge(
                p.get("procedure", ""),
                manifest,
                p.get("args", {}),
            )
        except ChallengeError as exc:
            challenge_error = str(exc)
        except Exception as exc:
            challenge_error = str(exc)

    approval = {
        "submission_id": str(row.submission_id),
        "status": row.status.value,
        "mode": row.mode,
        "procedure": p.get("procedure", "?"),
        "args": p.get("args", {}),
        "rationale": p.get("rationale", ""),
        "new_procedure_sql": p.get("new_procedure_sql", ""),
        "client_message": p.get("client_message", ""),
        "investigation": p.get("investigation", ""),
        "checks": p.get("checks", []),
        "llm_feedback": p.get("llm_feedback", []),
        "summary_fr": p.get("summary_fr"),
        "summary_error": p.get("summary_error"),
        "anomaly_flags": p.get("anomaly_flags", {}),
        "challenge_hint": challenge_hint,
        "challenge_error": challenge_error,
        "failed_confirms": int(p.get("failed_confirms", 0)),
        "touches_money_tables": row.touches_money_tables,
        "llm_unavailable": row.llm_unavailable,
        "break_glass": bool(p.get("break_glass", False)),
        "operator": row.operator,
        "decision_reason": row.decision_reason,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "decided_at": row.decided_at.isoformat() if row.decided_at else "",
    }
    return templates.TemplateResponse(
        request,
        "db_approval_detail.html",
        {"session": session, "a": approval, "flash": flash},
    )


@router.post("/db-approvals/{submission_id}/approve")
async def db_approval_approve(
    request: Request,
    submission_id: uuid.UUID,
    session: AdminSession = Depends(get_admin_session),
    human: HumanSession = Depends(get_human_session),
    db: Session = Depends(get_db),
) -> Response:
    """HSP3 — gate humain durci : challenge M1 + HMAC M2 + anti-replay.

    Le body POSTé est lu en bytes pour HMAC-vérifier le payload **avant**
    de le parser. Forme attendue (JSON body, content-type application/json) :

        {"submission_id": "<uuid>", "decision": "approve",
         "challenge": "it_cab 10000", "ts": 1700000000}

    Header requis : ``X-Human-Mac: <hex hmac-sha256(secret, body_bytes)>``.

    Erreurs :
      401 secret_session_expired : cookie absent/périmé (via get_human_session)
      401 bad_mac                : HMAC mismatch
      401 stale_ts               : ts hors ±60s
      404 db_approval_not_found
      409 already_decided        : status != PENDING (idempotence)
      422 challenge_not_computable : manifest+args incohérents (ChallengeError)
      422 challenge_mismatch     : challenge incorrect, incrémente failed_confirms
      429 challenge_locked       : 3 échecs cumulés sur cette session × sid
    """
    import json

    body_bytes = await request.body()
    if not verify_decision_hmac(human.secret, body_bytes, request.headers.get("x-human-mac", "")):
        return Response(
            content='{"detail":"bad_mac"}',
            status_code=status.HTTP_401_UNAUTHORIZED,
            media_type="application/json",
        )
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        return Response(
            content='{"detail":"bad_body"}',
            status_code=status.HTTP_400_BAD_REQUEST,
            media_type="application/json",
        )
    if not verify_decision_ts(payload.get("ts", 0)):
        return Response(
            content='{"detail":"stale_ts"}',
            status_code=status.HTTP_401_UNAUTHORIZED,
            media_type="application/json",
        )

    # Quota d'essais M1 — keyé (session admin operator, submission_id).
    # Restart process = reset (acceptable, rare).
    attempt_key = (session.operator, str(submission_id))
    remaining_lockout = _challenge_locked(attempt_key)
    if remaining_lockout > 0:
        return Response(
            content=f'{{"detail":"challenge_locked","retry_after":{int(remaining_lockout)}}}',
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(int(remaining_lockout))},
            media_type="application/json",
        )

    row = db.get(DbWriteApproval, submission_id)
    if row is None:
        return Response(
            content='{"detail":"db_approval_not_found"}',
            status_code=status.HTTP_404_NOT_FOUND,
            media_type="application/json",
        )
    if row.status != DbWriteApprovalStatus.PENDING:
        return Response(
            content='{"detail":"already_decided"}',
            status_code=status.HTTP_409_CONFLICT,
            media_type="application/json",
        )

    # Recompute le challenge attendu depuis le payload **figé** au
    # register-approval-time (cf design §M1 : la valeur attendue est dérivée
    # du payload, jamais du POST). Le manifest snapshot vit dans
    # row.payload.manifest (figé en Task 6 par le Code node n8n).
    proposal = row.payload or {}
    manifest_dict = proposal.get("manifest") or {}
    try:
        manifest = ProcedureManifest.model_validate(manifest_dict)
        expected = compute_challenge(
            proposal.get("procedure", ""),
            manifest,
            proposal.get("args", {}),
        )
    except (ChallengeError, Exception):
        return Response(
            content='{"detail":"challenge_not_computable"}',
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            media_type="application/json",
        )

    submitted = payload.get("challenge", "")
    if not verify_challenge(submitted, expected):
        _challenge_register_failure(attempt_key)
        # Incrémente le compteur d'audit dans le payload.
        attempts_now, _ = _CHALLENGE_ATTEMPTS[attempt_key]
        proposal["failed_confirms"] = int(proposal.get("failed_confirms", 0)) + 1
        row.payload = {**proposal}
        flag_modified(row, "payload")
        db.commit()
        return Response(
            content=f'{{"detail":"challenge_mismatch","attempts":{attempts_now}}}',
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            media_type="application/json",
        )

    # OK — challenge validé. Reset quota, marque approved, commit, resume n8n
    # avec mac.
    _challenge_reset(attempt_key)
    row.status = DbWriteApprovalStatus.APPROVED
    row.operator = session.operator
    row.decided_at = datetime.now(UTC)
    db.commit()  # MANDATORY — R02

    # Reprise n8n inversée (M2) — calcule le resume_mac depuis
    # N8N_RESUME_SECRET (env) et inclut dans le POST body.
    import hashlib
    import hmac as _hmac
    import os

    resume_secret = os.environ.get("N8N_RESUME_SECRET", "")
    msg = f"{submission_id}approve".encode()
    resume_mac = _hmac.new(resume_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    ok = await _post_resume(
        row.resume_url,
        {
            "decision": "approve",
            "operator": session.operator,
            "submission_id": str(submission_id),
            "mac": resume_mac,
        },
    )
    flash = (
        f"Proposition {str(submission_id)[:8]} approuvée."
        if ok
        else f"Proposition {str(submission_id)[:8]} approuvée — ⚠️ reprise n8n injoignable."
    )
    return _resume_url_redirect(flash)


@router.post("/db-approvals/{submission_id}/reject")
async def db_approval_reject(
    request: Request,
    submission_id: uuid.UUID,
    session: AdminSession = Depends(get_admin_session),
    db: Session = Depends(get_db),
) -> Response:
    """Reject a pending proposal — a non-empty reason is required."""
    row = db.get(DbWriteApproval, submission_id)
    if row is None:
        return _resume_url_redirect("Proposition introuvable.")
    if row.status != DbWriteApprovalStatus.PENDING:
        return _resume_url_redirect("Proposition déjà traitée — sans effet.")

    form = await request.form()
    # ``reason`` is a text form field; Starlette types form.get() as
    # ``str | UploadFile | None`` — narrow to the str (or empty) case.
    reason_raw = form.get("reason")
    reason = (reason_raw if isinstance(reason_raw, str) else "").strip()
    if not reason:
        return _detail_redirect(submission_id, "Un motif de rejet est obligatoire.")

    row.status = DbWriteApprovalStatus.REJECTED
    row.operator = session.operator
    row.decision_reason = reason
    row.decided_at = datetime.now(UTC)
    db.commit()  # MANDATORY — no commit = silent rollback prod (R02)

    ok = await _post_resume(
        row.resume_url,
        {"decision": "reject", "operator": session.operator, "reason": reason},
    )
    flash = (
        f"Proposition {str(submission_id)[:8]} rejetée."
        if ok
        else f"Proposition {str(submission_id)[:8]} rejetée — ⚠️ reprise n8n injoignable."
    )
    return _resume_url_redirect(flash)
