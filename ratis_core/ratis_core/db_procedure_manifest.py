"""Pydantic model + TOML loader for a stored-procedure manifest sidecar.

HSP1 — chaque procédure stockée `support_*.sql` a un manifeste sidecar
`<name>.manifest.toml` qui declare son contrat (effet attendu, bornes,
sensibilite argent, accessibilite agent). Ce module valide ce manifeste.

Le verifier `db_procedure_verifier.py` confronte ensuite le `.sql` au
manifeste via `pglast`. `apply_procedure` (Alembic) charge le manifeste
puis lance le verifier avant tout `op.execute` (defense en profondeur).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ManifestNotFoundError(FileNotFoundError):
    """Raised when a `.manifest.toml` sidecar is absent.

    Sub-classes `FileNotFoundError` so callers can `except FileNotFoundError`
    if they want a generic missing-file handler — the more specific class
    surfaces the manifest case in tracebacks.
    """


class ArgSpec(BaseModel):
    """One argument of a stored procedure — name, Postgres type, bounds."""

    name: str
    type: str
    required: bool
    min: int | None = None
    max: int | None = None


class AffectsSpec(BaseModel):
    """One table touched by the procedure — op + expected rowcount."""

    table: str
    op: Literal["insert", "update", "delete"]
    rows: int
    columns: list[str] | None = None


class ProcedureManifest(BaseModel):
    """Full manifest of one `support_*` stored procedure.

    Validated at load time : `direction` and `money_tier` use Literal
    vocabularies (rejected if off-list), `args` and `affects` are
    sub-models (each row is independently validated).

    HSP3 (2026-05-21) adds two fields :

    - ``trust_level_initial`` : the *default* trust level for this
      procedure. Runtime override lives in
      ``app_settings.db_pipeline_trust_levels`` (JSONB keyed by
      procedure name) ; the manifest gives the git-versioned baseline.
      Always ``"manual"`` at merge time — graduation is a deliberate
      `db_propose_write(mode="graduation")` proposal, never silent.
    - ``allowed_callers`` : list of identity strings allowed to propose
      this procedure (HSP4 enforcement). Declared in HSP3 so the
      catalogue is forward-compatible without a second TOML refactor.
    """

    name: str
    purpose: str
    facing: bool
    direction: Literal["credit", "debit", "link", "unlink", "fix", "set"]
    money_tier: Literal["direct", "cab", "non_money"]
    args: list[ArgSpec] = Field(default_factory=list)
    affects: list[AffectsSpec] = Field(default_factory=list)
    trust_level_initial: Literal["manual", "caps_only", "frozen"] = "manual"
    allowed_callers: list[str] = Field(default_factory=list)


def load_manifest(path: Path) -> ProcedureManifest:
    """Read a TOML manifest file and return its validated model.

    Raises :
        ManifestNotFoundError : the file does not exist.
        pydantic.ValidationError : the TOML parses but the schema rejects it.
        tomllib.TOMLDecodeError : the file is not valid TOML.
    """
    if not path.exists():
        raise ManifestNotFoundError(f"manifest sidecar not found: {path}")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return ProcedureManifest.model_validate(data)
