"""Orchestration de l'upsert via le shared helper store_consolidation.

Safety net F-14 (audit 2026-05-10) : si >10% des candidats ont is_disabled=True,
abort avec ValueError avant tout write (protection anti-catastrophe si INSEE change
le format de etatAdministratif et marque tous les stores fermés par erreur).

Caller responsable du db.commit() (R-DB-02).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from batch_shared.store_consolidation import CandidateStore, UpsertResult, apply_upsert
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)


@dataclass
class UpsertStats:
    inserted: int = 0
    updated: int = 0
    merged: int = 0
    preserved: int = 0
    conflicts: int = 0
    _conflict_messages: list[str] = field(default_factory=list, repr=False)

    def tally(self, result: UpsertResult) -> None:
        """Increment the counter matching result.action."""
        action = result.action  # 'inserted'|'updated'|'merged'|'preserved'|'conflict'
        # 'conflict' maps to self.conflicts (plural), all others are identical.
        attr = "conflicts" if action == "conflict" else action
        if hasattr(self, attr):
            setattr(self, attr, getattr(self, attr) + 1)
        else:
            _log.warning("upsert_stats: unknown action %r — not counted", action)

    def log_conflict(self, msg: str) -> None:
        """Conflict-log callback injected into apply_upsert.

        apply_upsert calls this AND returns action='conflict', so we only
        record the message here; tally() on the UpsertResult handles the
        counter increment to avoid double-counting.
        """
        self._conflict_messages.append(msg)

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.merged + self.preserved + self.conflicts


def upsert_candidates(
    db: Session,
    candidates: Iterable[CandidateStore],
    *,
    dedup_radius_m: int,
    fuzzy_threshold: float,
    dry_run: bool = False,
) -> UpsertStats:
    """Upsert a sequence of CandidateStore via store_consolidation.

    Safety net F-14 : if >10% of candidates have is_disabled=True -> raise
    ValueError before any write. Caller (main()) catches this, logs via Sentry,
    and returns exit code 1.

    In dry_run mode: no DB writes, stats counts all candidates as 'inserted'
    (simulated), with correct closed-count safety check still enforced.

    Caller is responsible for db.commit() (R-DB-02).
    """
    stats = UpsertStats()
    all_candidates = list(candidates)  # materialise to count

    # Safety net F-14 — closure cascade guard.
    if all_candidates:
        closed_count = sum(1 for c in all_candidates if c.is_disabled)
        ratio = closed_count / len(all_candidates)
        if ratio > 0.10:
            raise ValueError(
                f"SIRENE closure safety net triggered: {closed_count}/{len(all_candidates)} "
                f"candidates are closed ({ratio:.1%} > 10% threshold). "
                "Aborting run — likely an INSEE format change. "
                "Investigate and re-run with --full once resolved."
            )

    for c in all_candidates:
        if dry_run:
            # Simulate: count everything as 'inserted' without touching the DB.
            stats.tally(UpsertResult(action="inserted", store_id=None, reason="dry_run"))
            continue
        result = apply_upsert(
            db,
            c,
            conflict_log=stats.log_conflict,
            fuzzy_radius_m=dedup_radius_m,
            fuzzy_threshold=fuzzy_threshold,
        )
        stats.tally(result)

    return stats
