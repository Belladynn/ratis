"""RGPD anonymization helpers (audit F-AU-3).

This module provides the building blocks for the RGPD `DELETE /account` flow
to break cross-table correlation when a user is anonymized in place.

Background — why we need this
==============================
Until F-AU-3, ``delete_account`` anonymized the ``users`` row (tombstone) but
left ``user_id`` inline in 15+ behavioral and event-tracking tables (achievements,
reward_events, missions, battlepass, community_*, etc.). Even though the
tombstone row carries no PII identifiers, an attacker with DB read access
could trivially correlate the tombstone UUID across every table to reconstruct
the deleted user's full activity history — defeating the spirit of in-place
anonymization (and contradicting the ARCH/PRIVACY claim of "behavioral PII
purged").

Two-tier strategy
-----------------
The fix introduces two anonymization primitives, applied per-table according
to the table's analytics value and legal-retention status :

1. **Per-user anon UUID** (this module's :func:`anonymize_user_id`) — a
   deterministic hash of ``user_id + salt``, mapped onto a UUID. The same
   real user always hashes to the same anon UUID, so per-user analytics
   ("X% of users unlocked achievement Y", "average mission completion rate")
   stay accurate. But the mapping is **not invertible without the salt**, so
   leaking the DB alone does not let anyone link the anon UUID back to the
   tombstone ``users.id``. Applied to : ``user_achievements``,
   ``reward_events``, ``user_missions``, ``user_battlepass_progress``,
   ``user_battlepass_claims``, ``community_challenge_claims``,
   ``community_multipliers``, ``mystery_challenge_finds``, ``label_sessions``,
   ``mission_xp_records``, ``xp_transactions``, ``referral_uses``,
   ``product_name_resolutions``.

2. **Static anon sentinel** (:data:`ANON_SENTINEL_USER_ID`) — a single fixed
   UUID shared by ALL deleted users, used for tables we are LEGALLY required
   to keep (``cabecoin_transactions``, ``cashback_transactions``,
   ``cashback_withdrawals``, ``gift_card_orders``). The row is preserved
   intact (financial audit trail) but the FK is anonymized to the sentinel,
   breaking per-user correlation entirely.

3. **Hard DELETE** for tables that have no analytics or legal value once the
   user is gone (``user_savings_snapshot``, ``user_xp_balance``,
   ``notification_outbox``). These are handled directly in
   ``delete_account`` without going through this module.

Salt management
---------------
The salt is read from the env var ``RGPD_ANONYMIZE_SALT``. It must be set
in production AND must remain stable across the lifetime of the database —
rotating the salt would orphan all previously-anonymized rows (analytics
discontinuity, sentinel rows uncorrelated, etc.). The salt is loaded lazily
at first use to keep test setup simple ; production lifespan should also
``require_env("RGPD_ANONYMIZE_SALT")`` at boot.

Security note
-------------
The hash is SHA-256, not a slow KDF. Brute-forcing the anon → real_uuid
mapping requires enumerating 2^128 UUIDs ; with the salt secret this is
infeasible. Without the salt secret, a DB-only leak does not expose any
mapping (the only thing one sees is "row X has user_id=00000000-... or
some random-looking UUID"). The salt MUST never be logged, exported, or
checked into git.
"""

from __future__ import annotations

import hashlib
import os
from uuid import UUID

# Static sentinel for NEVER-PURGE financial/audit tables. A single row
# with this exact id must exist in ``public.users`` (seeded by migration).
# Cf. ``alembic/versions/20260511_*_rgpd_anonymize_completeness.py``.
ANON_SENTINEL_USER_ID: UUID = UUID("00000000-0000-0000-0000-000000000001")


def anonymize_user_id(user_id: UUID, salt: str) -> UUID:
    """Return a deterministic anon UUID derived from ``user_id`` + ``salt``.

    Properties
    ----------
    - **Deterministic** : same ``(user_id, salt)`` always produces the same
      output UUID. Required so per-user analytics still group rows correctly
      after anonymization.
    - **One-way** : given the output UUID and the salt is unknown, recovering
      ``user_id`` requires brute-forcing the SHA-256 preimage over 2^128
      possibilities (infeasible).
    - **Non-colliding in practice** : the output is the first 16 bytes of
      ``SHA-256(user_id_str || salt)``. Birthday-bound collisions occur at
      ~2^64 users ; safe well past V1 scale.

    The output is **never** equal to :data:`ANON_SENTINEL_USER_ID` (with
    overwhelming probability — the sentinel is a fixed all-zero-then-one
    UUID and the hash space is 2^128). The collision probability is ~2^-128,
    so we do not bother checking.

    :param user_id: the real ``users.id`` to anonymize. Must be a :class:`UUID`.
    :param salt: a secret string. Must be non-empty (raises ``ValueError`` else).
    :raises ValueError: if ``salt`` is empty or whitespace-only.
    """
    if not salt or not salt.strip():
        raise ValueError("anonymize_salt_empty")
    payload = f"{user_id!s}|{salt}".encode()
    digest = hashlib.sha256(payload).digest()
    # Take the first 16 bytes for a UUID. Setting version/variant bits is
    # not strictly required (the UUID is opaque) but we force version=4
    # for cosmetic compatibility with PG uuid type checks if any tooling
    # ever inspects the version nibble.
    raw = bytearray(digest[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40  # version 4
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant
    return UUID(bytes=bytes(raw))


def get_anonymize_salt() -> str:
    """Read ``RGPD_ANONYMIZE_SALT`` from env, fail-fast if missing.

    Called from ``delete_account`` at runtime (not at import) so the test
    suite can patch ``os.environ`` per-test. Production services must
    ``require_env("RGPD_ANONYMIZE_SALT")`` at lifespan.
    """
    salt = os.environ.get("RGPD_ANONYMIZE_SALT", "")
    if not salt or not salt.strip():
        raise RuntimeError(
            "RGPD_ANONYMIZE_SALT is not set. Refusing to anonymize without a "
            "salt — this would make per-user UUIDs identical to a public SHA-256 "
            "of the raw user_id and leak the mapping."
        )
    return salt
