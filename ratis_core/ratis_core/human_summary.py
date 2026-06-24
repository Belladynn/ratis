"""HSP3 — M3 : résumé français déterministe d'une proposition.

Aucun LLM. Aucun champ attaquant-contrôlé n'intervient dans la
substitution — uniquement des valeurs typées du manifest HSP1 (figé
git) et de args (validé HSP4). Le template français est hardcoded.

Le résumé est *figé* dans ``payload.summary_fr`` au moment du
``Register approval`` (n8n Code node `Compute summary FR` qui appelle
``POST /api/v1/admin/db-pipeline/build-summary`` → ce module).

Cf design §M3.
"""

from __future__ import annotations

from ratis_core.db_procedure_manifest import ProcedureManifest


class SummaryError(ValueError):
    """Le manifest+args ne permettent pas un résumé interprétable.

    Cas typique : ``affects`` touche plusieurs entités distinctes (viole
    l'invariant HSP1 atome = 1 entité par appel). L'UI affiche alors un
    fallback ``⚠️ Résumé indisponible — voir détail manuel.``
    """


_VERBE_FR = {
    "credit": "CRÉDITER",
    "debit": "DÉBITER",
    "fix": "CORRIGER",
    "set": "DÉFINIR",
    # link/unlink utilisent un format spécial avec entity.
}

_PREP_FR = {
    "credit": "à",
    "debit": "de",
    "link": "à",
    "unlink": "de",
    "fix": "pour",
    "set": "sur",
}


def _short_user_id(user_id: str) -> str:
    """User ID forme courte pour l'humain : derniers 8 hex chars d'UUID
    sans tirets, préfixé #."""
    stripped = str(user_id).replace("-", "")
    return f"#{stripped[-8:]}"


def _format_french_thousands(n: int) -> str:
    """Formatte un int avec séparateur milliers espace (français)."""
    s = str(abs(int(n)))
    parts = []
    while len(s) > 3:
        parts.insert(0, s[-3:])
        s = s[:-3]
    parts.insert(0, s)
    return ("-" if n < 0 else "") + " ".join(parts)


def _format_amount_unit(amount_cents: int, money_tier: str) -> str:
    """Renvoie ex : '10 000 CAB' (cab) ou '123,45 €' (direct) ou '' (non_money)."""
    if money_tier == "cab":
        # CAB est le montant brut en cents (sigle invariable).
        return f"{_format_french_thousands(amount_cents)} CAB"
    if money_tier == "direct":
        euros = amount_cents // 100
        cents = abs(amount_cents) % 100
        euros_fr = _format_french_thousands(euros)
        return f"{euros_fr},{cents:02d} €"
    return ""  # non_money — non utilisé pour les credit/debit


def _entity_singular(table: str) -> str:
    """Best-effort singulier d'une table : ``scans`` → ``scan``."""
    return table.rstrip("s") if table.endswith("s") else table


def build_summary_fr(
    manifest: ProcedureManifest,
    *,
    procedure: str,
    args: dict,
) -> str:
    """Construit le résumé français de la proposition.

    Raises SummaryError si les invariants sont violés (affects multi-entity).
    """
    # Invariant HSP1 : 1 entité par appel.
    entities = {_entity_singular(a.table) for a in manifest.affects}
    # On tolère plusieurs tables si elles partagent l'entité racine (ex :
    # user_cab_balance + cabecoin_transactions partagent l'entité 'user').
    # Heuristique souple : si toutes les tables affects commencent par le
    # même radical (user_, cabecoin_), on considère que c'est cohérent.
    if len(entities) > 1:
        radicals = {a.table.split("_")[0] for a in manifest.affects}
        if len(radicals) > 1:
            raise SummaryError(f"affects spans multiple entities: {sorted(radicals)}")

    direction = manifest.direction
    money_tier = manifest.money_tier
    purpose = manifest.purpose

    if direction in ("credit", "debit"):
        verbe = _VERBE_FR[direction]
        prep = _PREP_FR[direction]
        amount_cents = int(args.get("amount_cents", 0))
        amount_unit = _format_amount_unit(amount_cents, money_tier)
        user_id = args.get("user_id", "")
        user_short = _short_user_id(user_id)
        first_line = f"TU VAS {verbe} {amount_unit} {prep} l'utilisateur {user_short}."
    elif direction in ("link", "unlink"):
        verbe_root = "LIER UN" if direction == "link" else "DÉLIER UN"
        # Première entité touchée comme cible.
        target_table = manifest.affects[0].table if manifest.affects else "objet"
        entity = _entity_singular(target_table)
        prep = _PREP_FR[direction]
        # User cible si présent dans args.
        user_short = _short_user_id(args["user_id"]) if "user_id" in args else "(cible non précisée)"
        first_line = f"TU VAS {verbe_root} {entity} {prep} l'utilisateur {user_short}."
    elif direction in ("fix", "set"):
        verbe = _VERBE_FR[direction]
        prep = _PREP_FR[direction]
        row_id = args.get("row_id", "(id manquant)")
        first_line = f"TU VAS {verbe} {prep} la ligne {row_id}."
    else:  # pragma: no cover — Pydantic Literal aurait filtré
        raise SummaryError(f"unsupported direction: {direction}")

    return (
        f"{first_line}\n"
        f"Procédure : {procedure}.\n"
        f"Direction : {direction}.\n"
        f"Tier : {money_tier}.\n"
        f"But déclaré : {purpose}"
    )
