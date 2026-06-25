"""HSP3 — endpoints internes appelés par n8n (workflow db-write-pipeline).

Trois endpoints, tous protégés par INTERNAL_API_KEY (machine→machine PA↔n8n) :

- POST /admin/db-pipeline/build-summary
    Calcule ``summary_fr`` (M3) à partir du manifest + args. Stateless.

- POST /admin/db-pipeline/compute-flags
    Calcule les 5 flags M4 contre la base prod (db_change_log + history).

- POST /admin/db-pipeline/apply-graduation
    Mute ``app_settings.db_pipeline_trust_levels`` quand une proposition
    mode='graduation' a été approuvée.

Tous renvoient JSON. L'auth est ``INTERNAL_API_KEY`` (verify_internal_key,
cf ratis_core.deps), pas ADMIN_API_KEY — c'est du machine→machine n8n.
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ratis_core.database import get_db
from ratis_core.db_procedure_manifest import ProcedureManifest
from ratis_core.deps import verify_internal_key
from ratis_core.human_anomaly_flags import compute_flags
from ratis_core.human_summary import SummaryError, build_summary_fr
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_internal_key)])


class BuildSummaryRequest(BaseModel):
    procedure: str
    manifest: dict[str, Any]
    args: dict[str, Any]


class BuildSummaryResponse(BaseModel):
    summary_fr: str | None
    summary_error: str | None = None


@router.post("/admin/db-pipeline/build-summary", response_model=BuildSummaryResponse)
def build_summary(body: BuildSummaryRequest) -> BuildSummaryResponse:
    """M3 — résumé français déterministe.

    Renvoie ``summary_fr`` ou ``summary_error`` (jamais raise — l'UI affiche
    un fallback en cas d'erreur, cf design §M3 multi-entity).
    """
    try:
        manifest = ProcedureManifest.model_validate(body.manifest)
    except Exception as exc:  # pragma: no cover — manifest malformé = bug pipeline
        return BuildSummaryResponse(summary_fr=None, summary_error=f"invalid_manifest: {exc}")
    try:
        s = build_summary_fr(manifest, procedure=body.procedure, args=body.args)
        return BuildSummaryResponse(summary_fr=s)
    except SummaryError as exc:
        logger.warning("build_summary: %s", exc)
        return BuildSummaryResponse(summary_fr=None, summary_error=str(exc))


class ComputeFlagsRequest(BaseModel):
    procedure: str
    money_tier: str
    user_id: str
    current_amount_cents: int = 0


class ComputeFlagsResponse(BaseModel):
    anomaly_flags: dict[str, bool]


@router.post("/admin/db-pipeline/compute-flags", response_model=ComputeFlagsResponse)
def compute_flags_endpoint(
    body: ComputeFlagsRequest,
    db: Session = Depends(get_db),
) -> ComputeFlagsResponse:
    """M4 — calcule les 5 anomaly flags structurels."""
    flags = compute_flags(
        db,
        procedure=body.procedure,
        money_tier=body.money_tier,
        user_id=body.user_id,
        current_amount_cents=body.current_amount_cents,
    )
    return ComputeFlagsResponse(anomaly_flags=flags)


# ---------------------------------------------------------------------------
# M5 — apply-graduation
# ---------------------------------------------------------------------------


class ApplyGraduationRequest(BaseModel):
    procedure: str
    new_trust_level: Literal["manual", "caps_only", "frozen"]
    money_tier: Literal["direct", "cab", "non_money"]


class ApplyGraduationResponse(BaseModel):
    status: str
    procedure: str
    previous_trust_level: str | None
    new_trust_level: str


@router.post(
    "/admin/db-pipeline/apply-graduation",
    response_model=ApplyGraduationResponse,
)
def apply_graduation(
    body: ApplyGraduationRequest,
    db: Session = Depends(get_db),
) -> ApplyGraduationResponse:
    """M5 — mute ``app_settings.db_pipeline_trust_levels`` après une
    proposition mode='graduation' approuvée.

    Hardcoded : tier=direct + new_level=caps_only → 422
    ``refused_for_direct_tier`` (cf design §M5). ``frozen`` reste autorisé
    pour direct (sentinelle d'urgence).
    """
    if body.money_tier == "direct" and body.new_trust_level == "caps_only":
        raise HTTPException(
            status_code=422,
            detail="refused_for_direct_tier",
        )

    row = db.execute(
        sa_text("SELECT data FROM app_settings WHERE section='db_pipeline_trust_levels' FOR UPDATE")
    ).first()
    if row is None:
        raise HTTPException(status_code=500, detail="trust_levels_section_missing")
    data = dict(row[0])
    previous = data.get(body.procedure)
    data[body.procedure] = body.new_trust_level

    db.execute(
        sa_text(
            "UPDATE app_settings SET data = CAST(:d AS jsonb), updated_at = now() "
            "WHERE section='db_pipeline_trust_levels'"
        ),
        {"d": _json.dumps(data)},
    )
    db.commit()  # MANDATORY — R02
    logger.info(
        "apply_graduation: procedure=%s %s → %s",
        body.procedure,
        previous,
        body.new_trust_level,
    )
    return ApplyGraduationResponse(
        status="applied",
        procedure=body.procedure,
        previous_trust_level=previous,
        new_trust_level=body.new_trust_level,
    )


# ---------------------------------------------------------------------------
# HSP3.1 — POST /admin/db-pipeline/get-trust-level
# ---------------------------------------------------------------------------


class GetTrustLevelRequest(BaseModel):
    procedure: str
    manifest_trust_level_initial: str


class GetTrustLevelResponse(BaseModel):
    effective_trust_level: Literal["manual", "caps_only", "frozen"]
    source: Literal["override", "manifest"]


@router.post(
    "/admin/db-pipeline/get-trust-level",
    response_model=GetTrustLevelResponse,
)
def get_trust_level(
    body: GetTrustLevelRequest,
    db: Session = Depends(get_db),
) -> GetTrustLevelResponse:
    """HSP3.1 — renvoie le trust level *effectif* d'une procédure.

    L'override BDD (``app_settings.db_pipeline_trust_levels``) prime sur le
    ``trust_level_initial`` du manifeste (cf cycle de confiance HSP3). n8n
    consulte cet endpoint dans le nœud ``Trust level routing`` ; en cas
    d'indisponibilité PA, le nœud retombe en fail-safe sur le manifeste seul.
    """
    row = db.execute(sa_text("SELECT data FROM app_settings WHERE section='db_pipeline_trust_levels'")).first()
    data = dict(row[0]) if row is not None else {}

    override = data.get(body.procedure)
    source: Literal["override", "manifest"]
    if override is not None:
        effective = override
        source = "override"
    else:
        effective = body.manifest_trust_level_initial
        source = "manifest"

    if effective not in ("manual", "caps_only", "frozen"):
        raise HTTPException(
            status_code=422,
            detail=f"invalid_trust_level: {effective}",
        )

    return GetTrustLevelResponse(effective_trust_level=effective, source=source)


# ---------------------------------------------------------------------------
# HSP4 M3 — POST /admin/db-pipeline/validate-args
# ---------------------------------------------------------------------------


class ValidateArgsRequest(BaseModel):
    """Payload pour /validate-args. manifest = dict TOML déjà décodé en JSON
    (n8n le passe brut, on le re-valide via Pydantic dans le handler).
    """

    manifest: dict[str, Any]
    args: dict[str, Any]


class ValidateArgsResponse(BaseModel):
    ok: bool
    detail: str | None = None


@router.post(
    "/admin/db-pipeline/validate-args",
    response_model=ValidateArgsResponse,
)
def validate_args_endpoint(body: ValidateArgsRequest) -> ValidateArgsResponse:
    """HSP4 M3 — valide les args d'une proposition contre son manifeste HSP1.

    n8n appelle cet endpoint juste après ``HMAC Verify`` + ``Validate identity``,
    avant tout traitement métier. Single source de vérité (Python) —
    n'introduit pas de drift JS/Python.
    """
    from ratis_core.db_procedure_args import (
        ProcedureArgsValidationError,
        validate_args,
    )

    try:
        manifest = ProcedureManifest.model_validate(body.manifest)
    except Exception as exc:
        return ValidateArgsResponse(ok=False, detail=f"invalid_manifest: {exc}")

    try:
        validate_args(manifest, body.args)
    except ProcedureArgsValidationError as exc:
        return ValidateArgsResponse(ok=False, detail=str(exc))

    return ValidateArgsResponse(ok=True, detail=None)


# ---------------------------------------------------------------------------
# HSP4 M5 — POST /admin/db-pipeline/check-rowcount
# ---------------------------------------------------------------------------


class CheckRowcountRequest(BaseModel):
    submission_id: str  # UUID en string (n8n passe la valeur du SET LOCAL)
    manifest: dict[str, Any]


class CheckRowcountResponse(BaseModel):
    ok: bool
    observed: dict[str, int]
    expected: dict[str, int]
    mismatches: list[str]


@router.post(
    "/admin/db-pipeline/check-rowcount",
    response_model=CheckRowcountResponse,
)
def check_rowcount_endpoint(
    body: CheckRowcountRequest,
    db: Session = Depends(get_db),
) -> CheckRowcountResponse:
    """HSP4 M5 — confronte db_change_log au manifeste pour décider COMMIT|ROLLBACK.

    n8n appelle cet endpoint après le CALL (dans la même transaction côté
    pipeline). Si ``ok=False``, n8n émet ROLLBACK puis freeze la procédure
    via `apply-graduation`.
    """
    import uuid as _uuid

    from ratis_core.db_pipeline_checksum import check_rowcount

    try:
        sid = _uuid.UUID(body.submission_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid_submission_id: {exc}") from exc

    try:
        manifest = ProcedureManifest.model_validate(body.manifest)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid_manifest: {exc}") from exc

    result = check_rowcount(db, sid, manifest)
    return CheckRowcountResponse(
        ok=result["ok"],
        observed=result["observed"],
        expected=result["expected"],
        mismatches=result["mismatches"],
    )
