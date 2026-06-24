"""Consensus state enum for the name-resolution ledger (NRC).

A single ``ConsensusState`` value captures the lifecycle of a
``(store_id, normalized_label)`` pair as observed by the read-only
consensus computation in ``name_resolution_repository``.

Defined in its own module so consumers (repository, matcher, admin
endpoints, tests) can import it without forming a cycle through the
heavier repository module which depends on ratis_core models.

See ``ARCH_name_resolution_consensus.md`` § "États dérivés" for the
full state-mapping table and transition semantics.
"""

from __future__ import annotations

from enum import StrEnum


class ConsensusState(StrEnum):
    """Derived state of a ``(store_id, normalized_label)`` pair.

    Values
    ------
    UNRESOLVED
        No contributing validation row exists yet (ledger empty for this
        pair). The matcher cascade falls through to legacy steps.
    PENDING
        At least one contributing validation row but
        ``distinct_validators < min_distinct_users`` (default 3). Not
        enough crowd volume to promote.
    CONTROVERSE
        Quorum reached (``distinct_validators >= min_distinct_users``)
        but convergence failed (``top1_pct < convergence_threshold_pct``
        OR ``top1_weight < min_top1_lead_factor * top2_weight``). Cold-
        start divergence — was never verified, drives the admin queue.
    UNVERIFIED
        Was VERIFIED at some point in the past (per audit log) but the
        live computation no longer satisfies the convergence rules. This
        is a strong fraud / data-quality signal — admin alert.

        TODO: Bloc C — detect via ``was_ever_verified()`` against the
        audit log. Until then this state is never produced by the
        repository.
    VERIFIED
        Quorum reached AND convergence rules satisfied. Promotes to
        green icon in the frontend ; matcher cascade short-circuits to
        ``consensus_match``.
    """

    UNRESOLVED = "unresolved"
    PENDING = "pending"
    CONTROVERSE = "controverse"
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
