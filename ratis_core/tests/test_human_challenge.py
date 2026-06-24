"""HSP3 — tests du module ``ratis_core/human_challenge.py`` (M1).

Le challenge est ``<6 derniers chars procedure> <primary>`` où ``<primary>``
dépend de ``direction`` (manifest HSP1) :

- credit, debit : args.amount_cents
- link, unlink  : args.<entity>_id (le premier id de affects)
- fix, set      : args.<row_id> (la PK ciblée)

Cf design §M1 décision #1.
"""

from __future__ import annotations

import pytest
from ratis_core.db_procedure_manifest import (
    AffectsSpec,
    ProcedureManifest,
)


def _manifest(direction: str, money_tier: str = "cab", affects=None) -> ProcedureManifest:
    """Helper test : construit un manifest minimal."""
    return ProcedureManifest(
        name="x",
        purpose="p",
        facing=True,
        direction=direction,  # type: ignore[arg-type]
        money_tier=money_tier,  # type: ignore[arg-type]
        affects=affects or [],
    )


def test_compute_challenge_credit_uses_amount_cents() -> None:
    from ratis_core.human_challenge import compute_challenge

    m = _manifest("credit")
    c = compute_challenge("support_credit_cab", m, {"amount_cents": 10000})
    # 6 derniers chars de support_credit_cab = "dit_cab" → en fait 7 chars,
    # 6 derniers = "it_cab". Cf design §M1 "Format exact" : `<last 6 chars>`.
    assert c == "it_cab 10000"


def test_compute_challenge_debit_uses_amount_cents() -> None:
    from ratis_core.human_challenge import compute_challenge

    m = _manifest("debit")
    c = compute_challenge("support_debit_cab", m, {"amount_cents": 500})
    # support_debit_cab = 17 chars; last 6 = "it_cab" (same as credit since
    # both end in "_cab"). Design §M1 says LAST_CHARS=6 consistently.
    assert c == "it_cab 500"


def test_compute_challenge_link_uses_first_entity_id_from_affects() -> None:
    """Pour direction=link, on prend le premier *_id de args qui correspond
    à une entité touchée (heuristique : la première colonne *_id qui se
    matche à un table singulier dans affects)."""
    from ratis_core.human_challenge import compute_challenge

    m = _manifest(
        "link",
        affects=[AffectsSpec(table="scans", op="update", rows=1)],
    )
    c = compute_challenge(
        "support_link_scan_to_user",
        m,
        {"scan_id": "00000000-0000-0000-0000-000000004728", "user_id": "00000000-0000-0000-0000-000000001111"},
    )
    # 6 derniers chars de "support_link_scan_to_user" = "o_user".
    # primary = scan_id (entité touchée principale) — short form = derniers 5 chars de l'UUID.
    assert c.startswith("o_user ")
    # Le scan_id est tronqué aux 8 derniers chars pour lisibilité humaine.
    assert c == "o_user 00004728"


def test_compute_challenge_fix_uses_row_id() -> None:
    from ratis_core.human_challenge import compute_challenge

    m = _manifest("fix", money_tier="non_money")
    c = compute_challenge("support_fix_alias", m, {"row_id": "12345"})
    assert c == "_alias 12345"


def test_normalise_case_and_spaces() -> None:
    from ratis_core.human_challenge import normalise

    assert normalise("Dit_Cab 100") == "dit_cab100"
    assert normalise("  DIT_CAB    100  ") == "dit_cab100"
    assert normalise("dit_cab100") == "dit_cab100"


def test_verify_challenge_accepts_normalised_variants() -> None:
    from ratis_core.human_challenge import verify_challenge

    expected = "it_cab 10000"
    assert verify_challenge("it_cab 10000", expected) is True
    assert verify_challenge("IT_CAB 10000", expected) is True
    assert verify_challenge("it_cab10000", expected) is True
    assert verify_challenge("  It_Cab   10000  ", expected) is True


def test_verify_challenge_rejects_mismatch() -> None:
    from ratis_core.human_challenge import verify_challenge

    expected = "it_cab 10000"
    assert verify_challenge("it_cab 10001", expected) is False
    assert verify_challenge("it_cab", expected) is False
    assert verify_challenge("", expected) is False


def test_compute_challenge_short_procedure_name_uses_full_name() -> None:
    """Si le nom de procédure fait <6 chars, on prend le nom entier."""
    from ratis_core.human_challenge import compute_challenge

    m = _manifest("set", money_tier="non_money")
    c = compute_challenge("ban", m, {"row_id": "7"})
    # nom <6 chars → full name
    assert c == "ban 7"


def test_compute_challenge_missing_primary_raises() -> None:
    """Si l'arg primary attendu manque, raise ValueError — l'appelant
    redirige avec un flash 'challenge non calculable, contact ops'."""
    from ratis_core.human_challenge import (
        ChallengeError,
        compute_challenge,
    )

    m = _manifest("credit")
    with pytest.raises(ChallengeError):
        compute_challenge("support_credit_cab", m, {})  # pas d'amount_cents
