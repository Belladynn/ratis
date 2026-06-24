"""HSP4 M3 — validation runtime des args d'une procédure HSP1.

Construit un modèle Pydantic dynamique depuis le manifeste TOML et
applique les types Postgres + bornes min/max. Single source de
vérité — l'endpoint /api/v1/admin/db-pipeline/validate-args délègue
à ce module (et n8n appelle l'endpoint).
"""

from __future__ import annotations

import uuid

import pytest
from ratis_core.db_procedure_args import (
    ProcedureArgsValidationError,
    validate_args,
)
from ratis_core.db_procedure_manifest import ProcedureManifest


def _credit_cab_manifest() -> ProcedureManifest:
    """Manifeste de référence : support_credit_cab (uuid + integer 1..10000)."""
    return ProcedureManifest.model_validate(
        {
            "name": "support_credit_cab",
            "purpose": "test",
            "facing": True,
            "direction": "credit",
            "money_tier": "cab",
            "args": [
                {"name": "p_user_id", "type": "uuid", "required": True},
                {"name": "p_amount", "type": "integer", "required": True, "min": 1, "max": 10000},
            ],
            "affects": [],
            "trust_level_initial": "manual",
            "allowed_callers": ["claude-code-main"],
        }
    )


def test_validate_args_happy_path() -> None:
    """Args valides → retourne le dict normalisé."""
    m = _credit_cab_manifest()
    uid = str(uuid.uuid4())
    out = validate_args(m, {"p_user_id": uid, "p_amount": 100})
    assert out["p_user_id"] == uid
    assert out["p_amount"] == 100


def test_validate_args_rejects_amount_below_min() -> None:
    m = _credit_cab_manifest()
    with pytest.raises(ProcedureArgsValidationError) as exc:
        validate_args(m, {"p_user_id": str(uuid.uuid4()), "p_amount": -1})
    assert "p_amount" in str(exc.value)


def test_validate_args_rejects_amount_above_max() -> None:
    m = _credit_cab_manifest()
    with pytest.raises(ProcedureArgsValidationError) as exc:
        validate_args(m, {"p_user_id": str(uuid.uuid4()), "p_amount": 99999})
    assert "p_amount" in str(exc.value)


def test_validate_args_rejects_non_uuid() -> None:
    m = _credit_cab_manifest()
    with pytest.raises(ProcedureArgsValidationError) as exc:
        validate_args(m, {"p_user_id": "not-a-uuid", "p_amount": 100})
    assert "p_user_id" in str(exc.value)


def test_validate_args_rejects_missing_required() -> None:
    m = _credit_cab_manifest()
    with pytest.raises(ProcedureArgsValidationError) as exc:
        validate_args(m, {"p_amount": 100})  # p_user_id manquant
    assert "p_user_id" in str(exc.value)


def test_validate_args_rejects_unknown_extra_field() -> None:
    """Strict mode — un argument inconnu = reject (anti-typo)."""
    m = _credit_cab_manifest()
    with pytest.raises(ProcedureArgsValidationError) as exc:
        validate_args(m, {"p_user_id": str(uuid.uuid4()), "p_amount": 100, "extra": 1})
    assert "extra" in str(exc.value)


def test_validate_args_maps_pg_types() -> None:
    """integer, bigint, text, boolean, uuid → types Python attendus."""
    m = ProcedureManifest.model_validate(
        {
            "name": "test_types",
            "purpose": "test",
            "facing": True,
            "direction": "fix",
            "money_tier": "non_money",
            "args": [
                {"name": "p_int", "type": "integer", "required": True},
                {"name": "p_big", "type": "bigint", "required": True},
                {"name": "p_text", "type": "text", "required": True},
                {"name": "p_bool", "type": "boolean", "required": True},
                {"name": "p_uuid", "type": "uuid", "required": True},
            ],
            "affects": [],
            "trust_level_initial": "manual",
            "allowed_callers": ["claude-code-main"],
        }
    )
    uid = str(uuid.uuid4())
    out = validate_args(
        m,
        {
            "p_int": 42,
            "p_big": 10**12,
            "p_text": "hello",
            "p_bool": True,
            "p_uuid": uid,
        },
    )
    assert out == {"p_int": 42, "p_big": 10**12, "p_text": "hello", "p_bool": True, "p_uuid": uid}


def test_validate_args_unknown_pg_type_raises_clear() -> None:
    """Un type Postgres non-mappé → erreur explicite (pas un silent bug)."""
    m = ProcedureManifest.model_validate(
        {
            "name": "test_unknown_type",
            "purpose": "test",
            "facing": True,
            "direction": "fix",
            "money_tier": "non_money",
            "args": [{"name": "p_geom", "type": "geometry", "required": True}],
            "affects": [],
            "trust_level_initial": "manual",
            "allowed_callers": ["claude-code-main"],
        }
    )
    with pytest.raises(ProcedureArgsValidationError) as exc:
        validate_args(m, {"p_geom": "POINT(0 0)"})
    assert "geometry" in str(exc.value).lower() or "type" in str(exc.value).lower()


def test_validate_args_optional_arg_absent_ok() -> None:
    """`required=false` + valeur absente → pas d'erreur, sortie sans clé."""
    m = ProcedureManifest.model_validate(
        {
            "name": "test_optional",
            "purpose": "test",
            "facing": True,
            "direction": "set",
            "money_tier": "non_money",
            "args": [
                {"name": "p_required", "type": "text", "required": True},
                {"name": "p_optional", "type": "integer", "required": False},
            ],
            "affects": [],
            "trust_level_initial": "manual",
            "allowed_callers": ["claude-code-main"],
        }
    )
    out = validate_args(m, {"p_required": "x"})
    assert out["p_required"] == "x"
    # p_optional absent OK ; sa présence/absence dans `out` est un détail
    # d'impl — on vérifie juste que la validation ne raise pas.
