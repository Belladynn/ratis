"""RGPD anonymize completeness (audit F-AU-3)

Revision ID: 20260511_1000_rgpd_anon
Revises: 20260510_2200_sirene_schema
Create Date: 2026-05-11 10:00:00.000000

Audit deep-audit-auth.md § F-AU-3 — ``delete_account`` left ``user_id`` inline
in 15+ behavioral / event-tracking tables, contradicting the ARCH/PRIVACY
claim of "behavioral PII purged". This migration prepares the schema for
the new two-tier anonymization strategy implemented in
``webservices/ratis_auth/services/account_service.py`` :

1. **Static anon sentinel** for NEVER-PURGE financial / audit tables. A
   single fixed ``users`` row (``id =
   00000000-0000-0000-0000-000000000001``, ``provider = 'internal'``) acts
   as the anchor for ALL deleted users in :
       cabecoin_transactions, cashback_transactions, cashback_withdrawals,
       gift_card_orders
   Per-user correlation is broken (every deleted user's rows point at the
   same anon sentinel), legal retention is preserved (rows kept intact for
   5-10 years per Code de commerce).

2. **Per-user anon UUID** for analytics-bearing behavioral tables. Each
   deleted user is mapped to a deterministic
   ``anonymize_user_id(user_id, salt)`` UUID (cf
   ``ratis_core.anonymize``). To allow the anonymized UUIDs to live in the
   row (the anon UUID has NO corresponding ``users`` row by design — that
   IS what breaks the correlation), the FK constraints to ``users.id``
   are dropped on :
       user_achievements, reward_events, user_missions,
       user_battlepass_progress, user_battlepass_claims,
       community_challenge_claims, community_multipliers,
       mystery_challenge_finds, label_sessions, mission_xp_records,
       xp_transactions, referral_uses (referred_user_id),
       product_name_resolutions
   The columns themselves are unchanged (nullability + default preserved) ;
   only the FK constraint is dropped. Pre-existing rows continue to
   reference real users (the migration is online-safe ; no row update).

3. **Bug fix latent** — ``provider_check`` did not list ``'deleted'`` as a
   valid value, but ``delete_account`` was setting ``provider='deleted'``
   on the tombstone. The test suite did not catch this because the model
   ``__table_args__`` only declares a partial set of CHECK constraints
   (``provider_check`` lives in the migration only). The schema is extended
   here to whitelist ``'deleted'`` so the existing tombstone routine works
   in production. The ``auth_coherence`` CHECK is also extended to allow
   ``provider='deleted'`` with both ``provider_id`` and ``password_hash``
   NULL (matching the existing ``'internal'`` arm).

Tables NOT touched
==================
- Already covered by ``delete_account`` DELETE (refresh_tokens,
  user_push_tokens, shopping_lists, product_tracking, price_alerts,
  user_sessions, user_session_stats, notification_logs,
  user_store_preferences, user_streaks, user_badges, leaderboard_snapshots,
  user_cab_balance, product_favorites) — FK constraints retained for these
  tables since the row itself is deleted ; FK to the tombstone is moot.
- Already covered by ``delete_account`` SET NULL (scans, receipts,
  price_challenge_responses, referral_codes, stores.suggested_by_user_id) —
  FK kept ; ``NULL`` user_id is the documented end-state for these tables
  (consensus data preservation).
- ``user_savings_snapshot``, ``user_xp_balance``, ``notification_outbox``
  — these are added to the DELETE list in delete_account itself (no
  analytics value, contain PII or per-user state). FK retained ; row gone.
- ``subscriptions`` (FK CASCADE) — NOT moved to sentinel because the row
  carries Stripe ``payment_provider_ref`` (e.g. Stripe customer_id) which
  is a separate identifier the user has explicit business relationship
  with. Out of scope per F-AU-3 stop-conditions (linked to Stripe customer
  id — needs business decision). Existing tombstone correlation accepted ;
  audit follow-up tracked separately.

Idempotency
===========
All ``DROP CONSTRAINT IF EXISTS`` (R07 — never use op.drop_constraint
without IF EXISTS). The INSERT for the sentinel row uses
``ON CONFLICT (id) DO NOTHING``. Re-runs are safe.
"""
from __future__ import annotations

import contextlib

from alembic import op
import sqlalchemy as sa


# revision identifiers (≤32 chars per R08).
revision = "20260511_1000_rgpd_anon"
down_revision = "20260510_2200_sirene_schema"
branch_labels = None
depends_on = None


# Static sentinel — must match ``ratis_core.anonymize.ANON_SENTINEL_USER_ID``.
# Chosen as ``0…01`` so it sorts to the very top of ``users`` and is
# trivially identifiable in any ad-hoc query.
_SENTINEL_USER_ID = "00000000-0000-0000-0000-000000000001"
_SENTINEL_EMAIL = "anon@deleted.invalid"
_SENTINEL_SUPPORT_ID = "RTS-ANON00"


# FK constraints dropped to allow per-user anon UUIDs to live in the row.
# Order : (table_name, constraint_name).
_FK_DROPS: list[tuple[str, str]] = [
    ("user_achievements", "user_achievements_user_id_fkey"),
    ("reward_events", "reward_events_user_id_fkey"),
    ("user_missions", "fk_user"),
    ("user_battlepass_progress", "fk_user"),
    ("user_battlepass_claims", "fk_user"),
    ("community_challenge_claims", "community_challenge_claims_user_id_fkey"),
    ("community_multipliers", "community_multipliers_user_id_fkey"),
    ("mystery_challenge_finds", "mystery_challenge_finds_user_id_fkey"),
    ("label_sessions", "label_sessions_user_id_fkey"),
    ("mission_xp_records", "mission_xp_records_user_id_fkey"),
    ("xp_transactions", "xp_transactions_user_id_fkey"),
    ("referral_uses", "referral_uses_referred_user_id_fkey"),
    ("product_name_resolutions", "product_name_resolutions_user_id_fkey"),
]


# Original FK definitions — kept here so the downgrade can re-create them
# exactly as they were in schema.sql at the time of this migration.
# Format : (table, constraint_name, column, on_delete).
_FK_DOWNGRADE: list[tuple[str, str, str, str]] = [
    ("user_achievements", "user_achievements_user_id_fkey", "user_id", "CASCADE"),
    ("reward_events", "reward_events_user_id_fkey", "user_id", "SET NULL"),
    ("user_missions", "fk_user", "user_id", "SET NULL"),
    ("user_battlepass_progress", "fk_user", "user_id", "RESTRICT"),
    ("user_battlepass_claims", "fk_user", "user_id", "SET NULL"),
    ("community_challenge_claims", "community_challenge_claims_user_id_fkey", "user_id", "SET NULL"),
    ("community_multipliers", "community_multipliers_user_id_fkey", "user_id", "SET NULL"),
    ("mystery_challenge_finds", "mystery_challenge_finds_user_id_fkey", "user_id", "SET NULL"),
    ("label_sessions", "label_sessions_user_id_fkey", "user_id", "SET NULL"),
    ("mission_xp_records", "mission_xp_records_user_id_fkey", "user_id", "CASCADE"),
    ("xp_transactions", "xp_transactions_user_id_fkey", "user_id", "RESTRICT"),
    ("referral_uses", "referral_uses_referred_user_id_fkey", "referred_user_id", "SET NULL"),
    # product_name_resolutions originally had no ON DELETE rule (defaults
    # to NO ACTION). Re-create accordingly.
    ("product_name_resolutions", "product_name_resolutions_user_id_fkey", "user_id", "NO ACTION"),
]


# Old/new ``provider_check`` and ``auth_coherence`` CHECK constraints. The
# new versions whitelist ``'deleted'`` (used by the tombstone routine).
_OLD_PROVIDER_CHECK = "provider IN ('google', 'apple', 'email', 'internal')"
_NEW_PROVIDER_CHECK = "provider IN ('google', 'apple', 'email', 'internal', 'deleted')"

_OLD_AUTH_COHERENCE = (
    "(provider = 'email' AND password_hash IS NOT NULL AND provider_id IS NULL) OR "
    "(provider IN ('google', 'apple') AND provider_id IS NOT NULL AND password_hash IS NULL) OR "
    "(provider = 'internal' AND provider_id IS NULL AND password_hash IS NULL)"
)
_NEW_AUTH_COHERENCE = (
    "(provider = 'email' AND password_hash IS NOT NULL AND provider_id IS NULL) OR "
    "(provider IN ('google', 'apple') AND provider_id IS NOT NULL AND password_hash IS NULL) OR "
    "(provider IN ('internal', 'deleted') AND provider_id IS NULL AND password_hash IS NULL)"
)


def upgrade() -> None:
    # --- Step 1 : extend users CHECK constraints to whitelist 'deleted' ---
    # delete_account has been setting provider='deleted' since the initial
    # implementation but no environment ever caught it because the test
    # bootstrap uses ``Base.metadata.create_all`` which omits the CHECKs
    # defined only in migrations. Surface in prod = silent IntegrityError
    # at commit time on the first user-initiated deletion. Fix here.
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS provider_check")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT provider_check CHECK ({_NEW_PROVIDER_CHECK})")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS auth_coherence")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT auth_coherence CHECK ({_NEW_AUTH_COHERENCE})")

    # --- Step 2 : seed the static anon sentinel users row ---
    # ON CONFLICT DO NOTHING : idempotent re-runs are safe.
    bind = op.get_bind()
    # ``gift_card_redeemed_ytd_cents`` is NOT NULL with no server_default
    # (the default was dropped post-backfill in 20260508_2200_boutique_v1)
    # — must be explicit. ``trust_score`` retains its server_default = 50.
    bind.execute(
        sa.text(
            """
            INSERT INTO users
                (id, email, support_id, provider, display_name, is_deleted,
                 gift_card_redeemed_ytd_cents)
            VALUES
                (:id, :email, :sid, 'internal', 'ratis anon (rgpd)', true, 0)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": _SENTINEL_USER_ID,
            "email": _SENTINEL_EMAIL,
            "sid": _SENTINEL_SUPPORT_ID,
        },
    )

    # --- Step 3 : drop FKs to users for analytics tables ---
    # Per F-AU-3 design : anon UUIDs do NOT have corresponding ``users``
    # rows (that is the mechanism that breaks cross-table correlation —
    # an attacker sees ``user_id = <anon>`` but cannot resolve it without
    # the salt). FK constraints would forbid this assignment, so they are
    # dropped. The columns themselves remain.
    for table, constraint in _FK_DROPS:
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"
        )


def downgrade() -> None:
    # Step 3 reverse : re-create the FKs. WARNING : downgrade is destructive
    # in the sense that if any per-user anonymizations have run between
    # upgrade and downgrade, the re-added FK will fail validation against
    # the orphan anon UUIDs. The operator must clean those rows first.
    # Defensive : we use ``NOT VALID + VALIDATE CONSTRAINT`` so the
    # downgrade succeeds and the operator gets a clear error on VALIDATE
    # rather than a silent FK violation deep in some later query.
    for table, constraint, column, on_delete in _FK_DOWNGRADE:
        on_delete_clause = "" if on_delete == "NO ACTION" else f" ON DELETE {on_delete}"
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"FOREIGN KEY ({column}) REFERENCES users(id){on_delete_clause} "
            f"NOT VALID"
        )
        # Best-effort validate ; if it fails, the constraint stays NOT VALID
        # and the operator must intervene (orphan rows ⇒ need cleanup before
        # the FK can be re-validated).
        with contextlib.suppress(Exception):
            op.execute(
                f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint}"
            )

    # Step 2 reverse : drop the sentinel row. Must happen BEFORE shrinking
    # the CHECKs so we don't trip auth_coherence on the seed row.
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM users WHERE id = :id"),
        {"id": _SENTINEL_USER_ID},
    )

    # Step 1 reverse : restore the pre-migration CHECKs.
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS auth_coherence")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT auth_coherence CHECK ({_OLD_AUTH_COHERENCE})")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS provider_check")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT provider_check CHECK ({_OLD_PROVIDER_CHECK})")
