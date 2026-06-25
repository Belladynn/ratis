"""HSP4 M3 — validation runtime des args d'une procédure HSP1.

Construit un modèle Pydantic v2 dynamique depuis le manifeste TOML
HSP1 (cf db_procedure_manifest.ProcedureManifest) et applique :

* mapping type Postgres → type Python (uuid → UUID, integer → int, etc.) ;
* bornes min/max via Field(ge=..., le=...) ;
* required → champ obligatoire, sinon optional ;
* strict mode (model_config extra='forbid') — argument inconnu raise.

Single source de vérité : l'endpoint
`POST /api/v1/admin/db-pipeline/validate-args` délègue à ce module.
n8n l'appelle ; les batchs Python peuvent aussi (defense in depth).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from .db_procedure_manifest import ArgSpec, ProcedureManifest


class ProcedureArgsValidationError(ValueError):
    """Args ne respectent pas le contrat manifeste.

    Le message inclut le détail Pydantic (chemin de champ, raison).
    """


# Mapping type Postgres → type Python.
_PG_TO_PY: dict[str, type] = {
    "uuid": UUID,
    "integer": int,
    "int4": int,
    "bigint": int,
    "int8": int,
    "smallint": int,
    "int2": int,
    "text": str,
    "varchar": str,
    "boolean": bool,
    "bool": bool,
    "numeric": float,
    "double precision": float,
    "real": float,
}


def _python_type(pg_type: str) -> type:
    """Renvoie le type Python pour un type Postgres bare.

    Raise ProcedureArgsValidationError si non-mappé (signal explicite
    qu'un nouveau type a été introduit au manifeste sans update du mapping).
    """
    norm = pg_type.strip().lower()
    if norm not in _PG_TO_PY:
        raise ProcedureArgsValidationError(
            f"unsupported postgres type {pg_type!r} — ajoute le mapping dans ratis_core.db_procedure_args._PG_TO_PY"
        )
    return _PG_TO_PY[norm]


def _field_for_arg(arg: ArgSpec) -> tuple[Any, Any]:
    """Retourne (type, Field) à passer à pydantic.create_model.

    Required → Field(...) (no default). Optional → Field(None) (default None).
    min/max → Field(ge=min, le=max).
    """
    py_type = _python_type(arg.type)
    kwargs: dict[str, Any] = {}
    if arg.min is not None:
        kwargs["ge"] = arg.min
    if arg.max is not None:
        kwargs["le"] = arg.max
    if arg.required:
        return (py_type, Field(..., **kwargs))
    # Optional — type | None, default None.
    return (py_type | None, Field(None, **kwargs))


def _pydantic_model_from_manifest(manifest: ProcedureManifest) -> type[BaseModel]:
    """Construit un BaseModel Pydantic dynamique depuis le manifeste.

    Le model name est ``Args_<procedure_name>``. Strict mode :
    `extra='forbid'` (argument inconnu → reject).
    """
    # dict[str, Any] (not tuple[...]) so create_model's **field_definitions
    # overload resolves: pydantic's dynamic field spec accepts a (type, Field)
    # tuple, but its stub types the kwargs as `Any | tuple[str, Any]`.
    fields: dict[str, Any] = {arg.name: _field_for_arg(arg) for arg in manifest.args}
    model = create_model(
        f"Args_{manifest.name}",
        __config__=ConfigDict(extra="forbid", strict=False),
        **fields,
    )
    return model


def validate_args(manifest: ProcedureManifest, args: dict[str, Any]) -> dict[str, Any]:
    """Valide `args` contre le manifeste.

    Retourne le dict normalisé (UUIDs convertis depuis str, ints depuis
    numeric strings tolérés). Raise ProcedureArgsValidationError avec
    un detail lisible si la validation échoue.
    """
    model_cls = _pydantic_model_from_manifest(manifest)
    try:
        instance = model_cls.model_validate(args)
    except ValidationError as exc:
        details = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]
        raise ProcedureArgsValidationError("; ".join(details)) from exc

    # model_dump avec mode='json' convertit UUID→str (alignement avec ce
    # qui sera renvoyé à n8n).
    return instance.model_dump(mode="json", exclude_none=True)
