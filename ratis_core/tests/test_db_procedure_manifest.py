"""Tests for the procedure-manifest Pydantic model + TOML loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from ratis_core.db_procedure_manifest import (
    AffectsSpec,
    ArgSpec,
    ManifestNotFoundError,
    ProcedureManifest,
    load_manifest,
)

_VALID_TOML = """\
name        = "support_credit_cab"
purpose     = "Ajouter des CAB a un user."
facing      = true
direction   = "credit"
money_tier  = "cab"

[[args]]
name      = "p_user_id"
type      = "uuid"
required  = true

[[args]]
name      = "p_amount"
type      = "integer"
required  = true
min       = 1
max       = 10000

[[affects]]
table   = "user_cab_balance"
op      = "update"
rows    = 1
columns = ["balance"]

[[affects]]
table   = "cabecoin_transactions"
op      = "insert"
rows    = 1
"""


def test_load_manifest_round_trip(tmp_path: Path) -> None:
    """Un manifeste TOML valide se charge et expose tous ses champs."""
    p = tmp_path / "support_credit_cab.manifest.toml"
    p.write_text(_VALID_TOML, encoding="utf-8")

    m = load_manifest(p)

    assert isinstance(m, ProcedureManifest)
    assert m.name == "support_credit_cab"
    assert m.facing is True
    assert m.direction == "credit"
    assert m.money_tier == "cab"
    assert len(m.args) == 2
    assert isinstance(m.args[0], ArgSpec)
    assert m.args[1].min == 1
    assert m.args[1].max == 10000
    assert len(m.affects) == 2
    assert isinstance(m.affects[0], AffectsSpec)
    assert m.affects[0].table == "user_cab_balance"
    assert m.affects[0].op == "update"
    assert m.affects[0].columns == ["balance"]
    assert m.affects[1].columns is None


def test_load_manifest_missing_file_raises(tmp_path: Path) -> None:
    """Manifeste absent -> ManifestNotFoundError (sous-classe FileNotFoundError)."""
    p = tmp_path / "support_absent.manifest.toml"
    with pytest.raises(ManifestNotFoundError, match="support_absent"):
        load_manifest(p)
    # subclass relation pour rester compatible `except FileNotFoundError`.
    assert issubclass(ManifestNotFoundError, FileNotFoundError)


def test_manifest_rejects_missing_required_field(tmp_path: Path) -> None:
    """`purpose` manquant -> ValidationError."""
    bad = _VALID_TOML.replace('purpose     = "Ajouter des CAB a un user."\n', "")
    p = tmp_path / "bad.manifest.toml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_manifest(p)


def test_manifest_rejects_bad_direction(tmp_path: Path) -> None:
    """`direction` hors vocabulaire -> ValidationError."""
    bad = _VALID_TOML.replace('direction   = "credit"', 'direction   = "frobnicate"')
    p = tmp_path / "bad.manifest.toml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_manifest(p)


def test_manifest_rejects_bad_money_tier(tmp_path: Path) -> None:
    """`money_tier` hors vocabulaire -> ValidationError."""
    bad = _VALID_TOML.replace('money_tier  = "cab"', 'money_tier  = "crypto"')
    p = tmp_path / "bad.manifest.toml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_manifest(p)


def test_manifest_rejects_bad_affects_op(tmp_path: Path) -> None:
    """`op` hors vocabulaire {insert,update,delete} -> ValidationError."""
    bad = _VALID_TOML.replace('op      = "update"', 'op      = "merge"')
    p = tmp_path / "bad.manifest.toml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_manifest(p)


# ---------------------------------------------------------------------------
# HSP3 — trust_level_initial + allowed_callers
# ---------------------------------------------------------------------------


def test_manifest_trust_level_initial_defaults_to_manual(tmp_path) -> None:
    """When the TOML omits trust_level_initial, the loader defaults to 'manual'.

    Spec HSP3 §M5 décision #10 — manual est l'unique défaut sûr (le gate
    humain s'applique toujours sauf graduation explicite).
    """
    toml = """
name        = "x"
purpose     = "p"
facing      = true
direction   = "credit"
money_tier  = "cab"
"""
    f = tmp_path / "x.manifest.toml"
    f.write_text(toml)
    m = load_manifest(f)
    assert m.trust_level_initial == "manual"


def test_manifest_trust_level_initial_accepts_caps_only_and_frozen(tmp_path) -> None:
    """The Literal allows the three documented values."""
    for level in ("manual", "caps_only", "frozen"):
        toml = f"""
name        = "x"
purpose     = "p"
facing      = true
direction   = "credit"
money_tier  = "cab"
trust_level_initial = "{level}"
"""
        f = tmp_path / f"x_{level}.manifest.toml"
        f.write_text(toml)
        m = load_manifest(f)
        assert m.trust_level_initial == level


def test_manifest_trust_level_initial_rejects_arbitrary_string(tmp_path) -> None:
    """A non-listed level is a Pydantic ValidationError, not silently accepted."""
    import pytest
    from pydantic import ValidationError

    toml = """
name        = "x"
purpose     = "p"
facing      = true
direction   = "credit"
money_tier  = "cab"
trust_level_initial = "auto"
"""
    f = tmp_path / "bad.manifest.toml"
    f.write_text(toml)
    with pytest.raises(ValidationError):
        load_manifest(f)


def test_manifest_allowed_callers_defaults_to_empty_list(tmp_path) -> None:
    """allowed_callers omitted → empty list (no caller allowed yet).

    HSP4 will enforce identity ; HSP3 only declares the field so the
    catalogue is forward-compatible and the migration of the 3 atoms
    happens once.
    """
    toml = """
name        = "x"
purpose     = "p"
facing      = true
direction   = "credit"
money_tier  = "cab"
"""
    f = tmp_path / "x.manifest.toml"
    f.write_text(toml)
    m = load_manifest(f)
    assert m.allowed_callers == []


def test_manifest_allowed_callers_round_trips_list_of_strings(tmp_path) -> None:
    """A list of strings round-trips through Pydantic."""
    toml = """
name        = "x"
purpose     = "p"
facing      = true
direction   = "credit"
money_tier  = "cab"
allowed_callers = ["claude-code-main", "n8n-graduation"]
"""
    f = tmp_path / "x.manifest.toml"
    f.write_text(toml)
    m = load_manifest(f)
    assert m.allowed_callers == ["claude-code-main", "n8n-graduation"]
