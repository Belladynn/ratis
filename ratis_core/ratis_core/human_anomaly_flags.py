"""HSP3 — M4 : 6 anomaly flags structurels.

Calculés par n8n via l'endpoint ``POST /api/v1/admin/db-pipeline/compute-flags``
qui appelle ce module. Les résultats sont *figés* dans
``payload.anomaly_flags`` au ``Register approval`` — l'UI ne recalcule
jamais (cf design §M4 « Placement UI »).

Cf design §M4 — 6 flags :
  - first_use_of_procedure
  - amount_above_p95
  - user_repeat_in_24h
  - approaching_daily_cap
  - proposed_outside_business_hours
  - caps_already_warning  (HSP3.1 — caps HSP2 déjà en zone warn)

Tous renvoient bool. Le détail (médiane, count, etc.) peut être ajouté
dans une future itération via ``anomaly_details`` (cf design — reporté).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text


# Hook testable pour now() — substituable en test pour figer l'heure.
def _dt_now(tz: ZoneInfo) -> datetime:  # pragma: no cover — substitué en test
    return datetime.now(tz)


# Seuil warn cab journalier — référence HSP2 (``app_settings.db_pipeline_caps``).
# Hardcoded ici en fallback ; idéalement lu depuis app_settings.
_CAB_DAILY_WARN_CENTS = 20000


def first_use_of_procedure(conn: Any, procedure: str) -> bool:
    """True si aucune approbation status='approved' n'existe encore pour
    cette procédure dans ``db_write_approvals``.

    ``conn`` est un SQLAlchemy Connection ou Session-like avec ``.execute``
    qui retourne un Result.
    """
    row = conn.execute(
        text("SELECT count(*) FROM db_write_approvals WHERE payload->>'procedure' = :p AND status='approved'"),
        {"p": procedure},
    ).scalar_one()
    return int(row) == 0


def amount_above_p95(conn: Any, procedure: str, current_amount_cents: int) -> bool:
    """True si current_amount_cents dépasse le p95 des amounts approuvés
    sur cette procédure dans les 30 derniers jours.

    False si <20 historique (échantillon trop petit pour un p95 stable).
    """
    count = conn.execute(
        text(
            "SELECT count(*) FROM db_write_approvals "
            "WHERE payload->>'procedure' = :p AND status='approved' "
            "AND created_at > now() - interval '30 days'"
        ),
        {"p": procedure},
    ).scalar_one()
    if int(count) < 20:
        return False
    p95 = conn.execute(
        text(
            "SELECT percentile_disc(0.95) WITHIN GROUP "
            "(ORDER BY (payload->'args'->>'amount_cents')::int) "
            "FROM db_write_approvals "
            "WHERE payload->>'procedure' = :p AND status='approved' "
            "AND created_at > now() - interval '30 days'"
        ),
        {"p": procedure},
    ).scalar_one()
    if p95 is None:
        return False
    return int(current_amount_cents) > int(p95)


def user_repeat_in_24h(conn: Any, user_id: str) -> bool:
    """True si cet utilisateur a >3 approvals dans les dernières 24h."""
    n = conn.execute(
        text(
            "SELECT count(*) FROM db_write_approvals "
            "WHERE payload->'args'->>'user_id' = :u "
            "AND status='approved' "
            "AND created_at > now() - interval '24 hours'"
        ),
        {"u": str(user_id)},
    ).scalar_one()
    return int(n) > 3


def approaching_daily_cap(conn: Any, *, money_tier: str, current_amount_cents: int) -> bool:
    """True si SUM(today approved cab) + current > _CAB_DAILY_WARN_CENTS.

    N'applique qu'à money_tier='cab' (les caps HSP2 sont sur les CAB).
    Direct/non_money → toujours False.
    """
    if money_tier != "cab":
        return False
    today_sum = conn.execute(
        text(
            "SELECT COALESCE(SUM((payload->'args'->>'amount_cents')::int), 0) "
            "FROM db_write_approvals "
            "WHERE payload->>'money_tier' = 'cab' "
            "AND status='approved' "
            "AND created_at::date = current_date"
        )
    ).scalar_one()
    return int(today_sum) + int(current_amount_cents) > _CAB_DAILY_WARN_CENTS


def proposed_outside_business_hours() -> bool:
    """True si l'heure courante Paris n'est pas dans 9h-19h."""
    paris = ZoneInfo("Europe/Paris")
    now_paris = _dt_now(paris)
    h = now_paris.hour
    return not (9 <= h < 19)


def caps_already_warning(conn: Any) -> bool:
    """True si les caps HSP2 sont armés (app_settings.db_pipeline_caps.caps_enforced=true)
    ET que le cumul CAB credit des dernières 24h dépasse DÉJÀ le seuil warn.

    Distinct de approaching_daily_cap (qui regarde si CETTE proposition pousse
    au-dessus). caps_already_warning = "on est déjà en zone rouge, indépendamment
    de cette proposition". Lit le seuil depuis app_settings (fallback _CAB_DAILY_WARN_CENTS).
    """
    caps = conn.execute(text("SELECT data FROM app_settings WHERE section = 'db_pipeline_caps'")).scalar_one_or_none()
    if not caps or caps.get("caps_enforced") is not True:
        return False
    warn = caps.get("cab_global_daily_warn")
    warn_cents = int(warn) if warn is not None else _CAB_DAILY_WARN_CENTS
    today_sum = conn.execute(
        text(
            "SELECT COALESCE(SUM(amount), 0) FROM cabecoin_transactions "
            "WHERE direction = 'credit' "
            "AND created_at > now() - interval '24 hours'"
        )
    ).scalar_one()
    return int(today_sum) > warn_cents


def compute_flags(
    conn: Any,
    *,
    procedure: str,
    money_tier: str,
    user_id: str,
    current_amount_cents: int,
) -> dict[str, bool]:
    """Calcule les 6 flags. Renvoie dict bool.

    Convention : appelé par l'endpoint `compute-flags`, le résultat est
    figé dans ``payload.anomaly_flags`` côté n8n avant Register approval.
    """
    return {
        "first_use_of_procedure": first_use_of_procedure(conn, procedure),
        "amount_above_p95": amount_above_p95(conn, procedure, current_amount_cents),
        "user_repeat_in_24h": user_repeat_in_24h(conn, user_id),
        "approaching_daily_cap": approaching_daily_cap(
            conn, money_tier=money_tier, current_amount_cents=current_amount_cents
        ),
        "proposed_outside_business_hours": proposed_outside_business_hours(),
        "caps_already_warning": caps_already_warning(conn),
    }
