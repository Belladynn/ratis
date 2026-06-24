"""Widen cashback_withdrawals.status CHECK to allow 'abandoned'

Revision ID: 20260511_2200_cbabnd
Revises: 20260511_2000_seedprov
Create Date: 2026-05-11 22:00:00.000000

Purpose
=======
RGPD-driven flow refinement (decision 2026-05-08, tracked in PROD_CHECKLIST.md
§ RGPD Cashback handling at account deletion) — when a user invokes
``DELETE /account`` while ``cashback_withdrawals`` are still ``status='pending'``,
the anonymisation step removes the link to the RIB / contact info needed to
process the payout. The pending withdrawal is therefore unprocessable and
must transition to a tombstone state.

Three options were considered :
  1. Hard-DELETE the row → violates the legal "NEVER PURGE" invariant on
     ``cashback_withdrawals`` (CLAUDE.md § tables + RGPD).
  2. Leave it as ``status='pending'`` forever → poisons the admin queue.
  3. Introduce a new tombstone status ``'abandoned'`` → preserves the row
     for audit + clears it from the admin queue. **Chosen** per
     2026-05-08 decision.

This migration is the schema half of the rollout (Pattern A — widen the
PG CHECK first, then mirror it in the ORM + add the service-side flow
in a follow-up). The seed pipeline (Wave 4) is the first consumer : it
seeds Diane's post-DELETE state with ``status='abandoned'`` directly so
the admin UI screen can be exercised against realistic data.

Idempotency
===========
``DROP CONSTRAINT IF EXISTS`` (R07) before each re-add. Re-runs are safe.

Follow-up work tracked in DECISIONS_PENDING.md
==============================================
Service-side flow (``account_service.delete_account`` to emit an
``account_deletion_absorption`` cashback_transaction + UPDATE the pending
withdrawals to ``'abandoned'``) is NOT in scope here — that flow also
needs a widening of ``cashback_transactions.type`` CHECK to admit the
new type, plus the modal UX confirmation (PROD_CHECKLIST.md). Each lands
in its own PR.
"""
from __future__ import annotations

from alembic import op


# revision identifiers (≤32 chars per R08).
revision = "20260511_2200_cbabnd"
down_revision = "20260511_2000_seedprov"
branch_labels = None
depends_on = None


# Old / new ``status_check`` predicates (mirrored verbatim from PG).
_OLD_STATUS_CHECK = "status IN ('pending', 'processed', 'failed')"
_NEW_STATUS_CHECK = "status IN ('pending', 'processed', 'failed', 'abandoned')"


def upgrade() -> None:
    op.execute("ALTER TABLE cashback_withdrawals DROP CONSTRAINT IF EXISTS status_check")
    op.execute(
        f"ALTER TABLE cashback_withdrawals ADD CONSTRAINT status_check CHECK ({_NEW_STATUS_CHECK})"
    )


def downgrade() -> None:
    # Downgrade is safe only if no rows with status='abandoned' exist
    # (otherwise the narrower CHECK would reject them). In prod no such
    # rows exist before this migration ships — downgrade is therefore a
    # no-op there. In seeded ``ratis_seed`` it would need an UPDATE first ;
    # we don't auto-migrate to avoid silent data drift.
    op.execute("ALTER TABLE cashback_withdrawals DROP CONSTRAINT IF EXISTS status_check")
    op.execute(
        f"ALTER TABLE cashback_withdrawals ADD CONSTRAINT status_check CHECK ({_OLD_STATUS_CHECK})"
    )
