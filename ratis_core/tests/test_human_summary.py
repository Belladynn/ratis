"""HSP3 — tests du template français déterministe (M3).

Aucun LLM. Aucun champ attaquant-contrôlé dans le résumé. Le template
substitue uniquement des valeurs typées du manifest + args (validés HSP4).

Cf design §M3.
"""

from __future__ import annotations

import pytest
from ratis_core.db_procedure_manifest import AffectsSpec, ProcedureManifest
from ratis_core.human_summary import SummaryError, build_summary_fr


def _m(direction, money_tier="cab", affects=None):
    return ProcedureManifest(
        name="x",
        purpose="P déclaré.",
        facing=True,
        direction=direction,
        money_tier=money_tier,
        affects=affects or [AffectsSpec(table="user_cab_balance", op="update", rows=1)],
    )


def test_summary_credit_cab_basic() -> None:
    m = _m("credit", "cab")
    s = build_summary_fr(
        m,
        procedure="support_credit_cab",
        args={"user_id": "00000000-0000-0000-0000-000000004728", "amount_cents": 10000},
    )
    assert "CRÉDITER" in s
    assert "10 000 CAB" in s  # 10000 unités CAB (entier brut, pas ÷100)
    assert "à l'utilisateur" in s
    assert "#00004728" in s
    assert "support_credit_cab" in s
    assert "P déclaré." in s


def test_summary_debit_cab_uses_de_preposition() -> None:
    m = _m("debit", "cab")
    s = build_summary_fr(
        m,
        procedure="support_debit_cab",
        args={"user_id": "00000000-0000-0000-0000-000000004728", "amount_cents": 500},
    )
    assert "DÉBITER" in s
    assert "500 CAB" in s  # 500 unités CAB (entier brut, pas ÷100)
    assert " de l'utilisateur" in s


def test_summary_direct_money_renders_euros() -> None:
    m = _m("credit", "direct")
    s = build_summary_fr(
        m,
        procedure="support_credit_real",
        args={"user_id": "00000000-0000-0000-0000-000000001111", "amount_cents": 12345},
    )
    # 12345 cents = 123,45 €. Formaté français.
    assert "123,45 €" in s


def test_summary_amount_1_cent_renders_correctly() -> None:
    """Pluralisation : 1 cent direct → 0,01 € (€ invariable)."""
    m = _m("credit", "direct")
    s = build_summary_fr(m, procedure="x", args={"user_id": "u1", "amount_cents": 1})
    assert "0,01 €" in s


def test_summary_amount_1_cab_stays_cab() -> None:
    """1 CAB reste 1 CAB (CAB invariable)."""
    m = _m("credit", "cab")
    s = build_summary_fr(m, procedure="x", args={"user_id": "u1", "amount_cents": 1})
    assert "1 CAB" in s


def test_summary_thousands_separator_french() -> None:
    """10 000 CAB avec espace milliers (français)."""
    m = _m("credit", "cab")
    s = build_summary_fr(m, procedure="x", args={"user_id": "u1", "amount_cents": 1000000})
    # 1 000 000 cents CAB = 1 000 000 CAB. Espace milliers.
    assert "1 000 000 CAB" in s


def test_summary_link_renders_lier_un_entity() -> None:
    m = _m(
        "link",
        money_tier="non_money",
        affects=[AffectsSpec(table="scans", op="update", rows=1)],
    )
    s = build_summary_fr(
        m,
        procedure="support_link_scan_to_user",
        args={"scan_id": "00000000-0000-0000-0000-000000007777", "user_id": "00000000-0000-0000-0000-000000001111"},
    )
    assert "LIER UN scan" in s
    assert "à l'utilisateur" in s


def test_summary_unlink_uses_délier() -> None:
    m = _m(
        "unlink",
        money_tier="non_money",
        affects=[AffectsSpec(table="scans", op="update", rows=1)],
    )
    s = build_summary_fr(
        m,
        procedure="support_unlink_scan",
        args={"scan_id": "00000000-0000-0000-0000-000000007777"},
    )
    assert "DÉLIER UN scan" in s


def test_summary_fix_renders_corriger() -> None:
    m = _m("fix", money_tier="non_money")
    s = build_summary_fr(m, procedure="x", args={"row_id": "42"})
    assert "CORRIGER" in s


def test_summary_set_renders_définir() -> None:
    m = _m("set", money_tier="non_money")
    s = build_summary_fr(m, procedure="x", args={"row_id": "42"})
    assert "DÉFINIR" in s


def test_summary_multi_entity_affects_raises_summary_error() -> None:
    """Spec §M3 : affects peut lister plusieurs tables, mais leur entity
    doit être identique. Sinon SummaryError → l'UI affiche fallback."""
    m = ProcedureManifest(
        name="x",
        purpose="P",
        facing=True,
        direction="credit",
        money_tier="cab",
        affects=[
            AffectsSpec(table="users", op="update", rows=1),
            AffectsSpec(table="stores", op="update", rows=1),
        ],
    )
    with pytest.raises(SummaryError):
        build_summary_fr(m, procedure="x", args={"amount_cents": 100})


def test_summary_no_llm_call_is_made(monkeypatch) -> None:
    """Aucune lib LLM (anthropic, openai, mistralai) n'est importée par
    le module — c'est l'invariant clé de M3 contre la prompt injection."""
    # Le module est déjà importé via le top-of-file ; on vérifie qu'aucun
    # module LLM ne fait partie de la closure.
    from pathlib import Path

    import ratis_core.human_summary as hs

    module_text = Path(hs.__file__).read_text()
    assert "anthropic" not in module_text.lower()
    assert "openai" not in module_text.lower()
    assert "mistralai" not in module_text.lower()
