"""
Trust-score batch — anti-fraud V1 (NRC).

Recomputes ``users.trust_score``, ``users.total_resolved_scans`` and
``users.is_shadow_banned`` from each user's history of contributions in
``product_name_resolutions`` against the current consensus state for
each ``(store_id, normalized_label)`` pair.

See ``ARCH_anti_fraud.md`` for the full contract. Algorithm summary :

1. Read every ``(store_id, normalized_label)`` that has reached a
   consensus state ∈ {VERIFIED, UNVERIFIED} via the latest
   ``consensus_state_changed`` event in ``pipeline_audit_log``. Snapshot
   each pair's current ``top1_ean`` from that event payload.
2. For every user with at least one contributing row in the ledger
   (``match_method`` ∈ ``barcode | manual_admin | fuzzy_pending |
   observed_name``) on one of those pairs, count :
   - ``total`` — contributions on consensual pairs (any EAN)
   - ``agreed`` — contributions where the user's ``product_ean`` equals
     the pair's current ``top1_ean``
3. ``trust_score = round(agreed / total * 100)`` when ``total >= 1``,
   else ``50`` (neutral default — kept for users with zero consensual
   contributions yet).
4. Sanctions kick in only at ``total >= 100`` (grace period) :
   - ``trust_score < 65`` → ``is_shadow_banned = TRUE`` (silent)
   - ``65 <= trust_score < 75`` → push notif
     ``trust_score_warning`` (visible warn).
5. Persist ``trust_score``, ``total_resolved_scans`` (= ``total``),
   ``is_shadow_banned``, ``trust_score_updated_at = now()``.

The batch is idempotent — running it twice in a row produces the same
state. Notifications are only emitted when the user *transitions* into
the warning band (compare new trust_score against the previous value).

Usage::

    uv run python batch/ratis_batch_trust_score/trust_score.py
    uv run python batch/ratis_batch_trust_score/trust_score.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from dataclasses import dataclass

from ratis_core.database import make_engine
from ratis_core.notifier_client import notify_user
from ratis_core.observability import init_sentry
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("trust_score")

BATCH_NAME = "trust_score"

# Match methods that count toward a user's contributions. Mirrors the
# settings ``name_resolution_consensus.validation_methods`` set —
# duplicated here so the batch stays self-contained even if the live
# settings drift (the trust score is a pure function of the *user's*
# submissions, not of the convergence weighting).
CONTRIBUTING_METHODS = ("barcode", "manual_admin", "fuzzy_pending", "observed_name")

# Consensus states that "count" — only labels that have reached a real
# consensus state can grade a user's submissions. PENDING / CONTROVERSE
# / UNRESOLVED labels have no ground truth to compare against.
CONSENSUS_STATES = ("verified", "unverified")

# Grace period — sanctions don't apply until the user has made at least
# this many consensual contributions. Below the gate every user is left
# at the neutral default (50) regardless of their actual ratio.
GRACE_PERIOD_SCANS = 100

# Score thresholds. Values are inclusive on the lower bound, exclusive
# on the upper. ``ZONE_OK >= TRUST_WARNING_LOW`` AND ``< TRUST_OK_LOW``
# is the warning band ; below ``TRUST_SHADOW_BAN_BELOW`` is the
# automatic shadow-ban band.
TRUST_OK_LOW = 75  # >= 75 → OK, no action
TRUST_WARNING_LOW = 65  # 65..<75 → warn user
TRUST_SHADOW_BAN_BELOW = 65  # <65 → shadow ban


@dataclass(slots=True)
class UserStats:
    user_id: uuid.UUID
    total: int
    agreed: int
    previous_score: int
    previous_shadow_banned: bool

    @property
    def trust_score(self) -> int:
        """Round-half-up percentage of agreed votes."""
        if self.total < 1:
            return 50
        # Use integer math to keep the score deterministic — float
        # rounding would drift on the 0.5 boundary across platforms.
        return (self.agreed * 100 + self.total // 2) // self.total


def _compute_user_stats(db: Session) -> list[UserStats]:
    """Return one ``UserStats`` row per user with at least one
    contribution on a label that reached a consensus state.

    The query joins :

    - ``product_name_resolutions`` (source of contributions)
    - the latest ``consensus_state_changed`` event per
      ``(store_id, normalized_label)`` (provides current state +
      ``top1_ean``).

    Users who have never contributed to a consensual label are not
    returned by this query — their state is left at defaults (50,
    not banned) which is the desired behaviour.
    """
    methods_in = ", ".join(f"'{m}'" for m in CONTRIBUTING_METHODS)
    states_in = ", ".join(f"'{s}'" for s in CONSENSUS_STATES)

    # Latest state event per (store, label). DISTINCT ON is the
    # PG-idiomatic "newest-row-per-group" selector — far cheaper than
    # a window function for this row count.
    rows = db.execute(
        text(
            f"""
            WITH latest_state AS (
                SELECT DISTINCT ON (
                    payload->>'store_id', payload->>'normalized_label'
                )
                    payload->>'store_id'        AS store_id,
                    payload->>'normalized_label' AS normalized_label,
                    payload->>'to_state'         AS state,
                    payload->>'top1_ean'         AS top1_ean
                FROM pipeline_audit_log
                WHERE event = 'consensus_state_changed'
                ORDER BY
                    payload->>'store_id',
                    payload->>'normalized_label',
                    created_at DESC,
                    id DESC
            )
            SELECT
                pnr.user_id                           AS user_id,
                COUNT(*)                              AS total,
                COUNT(*) FILTER (
                    WHERE pnr.product_ean = ls.top1_ean
                )                                     AS agreed,
                u.trust_score                         AS previous_score,
                u.is_shadow_banned                    AS previous_shadow_banned
            FROM product_name_resolutions pnr
            JOIN latest_state ls
                ON ls.store_id::uuid = pnr.store_id
               AND ls.normalized_label = pnr.normalized_label
            JOIN users u ON u.id = pnr.user_id
            WHERE ls.state IN ({states_in})
              AND pnr.match_method IN ({methods_in})
              AND u.is_deleted = false
            GROUP BY pnr.user_id, u.trust_score, u.is_shadow_banned
            """  # noqa: S608 — interpolated values are module-level literals
        )
    ).fetchall()

    return [
        UserStats(
            user_id=r.user_id,
            total=int(r.total),
            agreed=int(r.agreed),
            previous_score=int(r.previous_score),
            previous_shadow_banned=bool(r.previous_shadow_banned),
        )
        for r in rows
    ]


def _decide_shadow_ban(stats: UserStats) -> bool:
    """Apply the sanction rules deterministically.

    Below the grace period → no sanction (never flip TO banned ; do not
    auto-unban either — a manual unban via the admin endpoint is
    expected in the recovery path).
    """
    if stats.total < GRACE_PERIOD_SCANS:
        return stats.previous_shadow_banned
    if stats.trust_score < TRUST_SHADOW_BAN_BELOW:
        return True
    # Score recovered above the threshold — honour the recovery only if
    # the user wasn't manually banned. We can't distinguish "auto-ban
    # then recovered" from "manual-ban from admin" at this layer ; the
    # safe choice is to leave the flag alone once set, requiring an
    # explicit admin PATCH to unban. This avoids an attacker recovering
    # automatically by spamming a few correct scans.
    return stats.previous_shadow_banned


def _decide_warning(stats: UserStats, new_score: int) -> bool:
    """Return True if the user just transitioned into the warning band.

    Transition is detected against the *previous* score :
    - was OK (>= 75) AND now in [65, 75) → warn
    - was already in warning band → do not re-warn (avoids spam)
    - was below 65 (shadow ban) AND now in [65, 75) → recovery,
      do not warn (the user shouldn't see anything at all while
      shadow-banned ; if they're being un-banned manually, the admin
      handles communication out of band)
    """
    if stats.total < GRACE_PERIOD_SCANS:
        return False
    in_warning_band = TRUST_WARNING_LOW <= new_score < TRUST_OK_LOW
    if not in_warning_band:
        return False
    # Only when crossing the boundary downward.
    return stats.previous_score >= TRUST_OK_LOW


def _update_user(db: Session, stats: UserStats, *, dry_run: bool) -> tuple[int, bool, bool]:
    """Persist the new state for a single user. Returns (new_score,
    new_shadow_ban, should_warn).
    """
    new_score = stats.trust_score
    new_ban = _decide_shadow_ban(stats)
    should_warn = _decide_warning(stats, new_score)

    if dry_run:
        return new_score, new_ban, should_warn

    db.execute(
        text(
            """
            UPDATE users
               SET trust_score = :score,
                   total_resolved_scans = :total,
                   is_shadow_banned = :ban,
                   trust_score_updated_at = now()
             WHERE id = :uid
            """
        ),
        {
            "score": new_score,
            "total": stats.total,
            "ban": new_ban,
            "uid": str(stats.user_id),
        },
    )
    return new_score, new_ban, should_warn


def run_batch(session_factory, *, dry_run: bool, notifier: callable = notify_user) -> dict[str, int]:
    """Run the full batch ; returns a stats dict for the sync log.

    ``notifier`` is injected for testability — defaults to the real
    ``notify_user`` (fire-and-forget HTTP), tests pass a recorder.
    """
    counters = {
        "users_processed": 0,
        "shadow_banned_now": 0,
        "warnings_emitted": 0,
        "score_changed": 0,
    }

    with session_factory() as db:
        stats_list = _compute_user_stats(db)

    log.info("Computed stats for %d users", len(stats_list))

    for stats in stats_list:
        with session_factory() as db:
            new_score, new_ban, should_warn = _update_user(db, stats, dry_run=dry_run)
            if not dry_run:
                db.commit()
            counters["users_processed"] += 1
            if new_ban and not stats.previous_shadow_banned:
                counters["shadow_banned_now"] += 1
            if new_score != stats.previous_score:
                counters["score_changed"] += 1

        # Notification path — fire-and-forget, only outside dry-run.
        # Shadow-banned users are NOT notified (silent — that's the
        # whole point of the sanction).
        if should_warn and not new_ban and not dry_run:
            counters["warnings_emitted"] += 1
            try:
                notifier(
                    user_id=stats.user_id,
                    notif_type="trust_score_warning",
                    data={"trust_score": new_score},
                )
            except Exception:  # pragma: no cover — notifier never raises
                log.exception("notifier failed for user %s", stats.user_id)

    return counters


def _write_sync_log(session_factory, status: str, rows_affected: int, dry_run: bool) -> None:
    if dry_run:
        return
    with session_factory() as db:
        db.execute(
            text("INSERT INTO batch_sync_log (batch_name, status, rows_affected) VALUES (:name, :status, :rows)"),
            {"name": BATCH_NAME, "status": status, "rows": rows_affected},
        )
        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ratis trust-score batch")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute everything ; commit nothing ; emit no notification.",
    )
    args = parser.parse_args()

    # Init Sentry first — silent no-op when SENTRY_DSN is absent. Any
    # exception raised during the trust-score recompute is then captured.
    init_sentry("ratis_batch_trust_score")

    if args.dry_run:
        log.info("DRY-RUN mode — no changes will be committed")

    url = os.environ.get("DATABASE_URL")
    if not url:
        log.error("DATABASE_URL environment variable is not set")
        sys.exit(1)

    engine = make_engine(url, pool_pre_ping=True)
    session_factory = sessionmaker(engine)

    try:
        counters = run_batch(session_factory, dry_run=args.dry_run)
    except Exception:
        log.exception("trust_score batch failed")
        try:
            _write_sync_log(session_factory, "failed", 0, args.dry_run)
        except Exception:
            log.exception("Failed to write sync log")
        sys.exit(1)

    log.info(
        "Batch complete: processed=%d shadow_banned_now=%d warnings_emitted=%d score_changed=%d",
        counters["users_processed"],
        counters["shadow_banned_now"],
        counters["warnings_emitted"],
        counters["score_changed"],
    )

    try:
        _write_sync_log(
            session_factory,
            "success",
            counters["users_processed"],
            args.dry_run,
        )
    except Exception:
        log.exception("Failed to write sync log")


if __name__ == "__main__":
    main()
