"""OAuth-only — drop the 'email' arm from users CHECK constraints.

Revision ID: 20260517_1200_oauth_only
Revises: 20260515_1400_xp_ref_chk
Create Date: 2026-05-17 12:00:00.000000

Phase 1 of the OAuth-only auth refactor (audit 2026-05-17, finding C1).
Email/password auth is decommissioned : the 'email' provider is removed
from ``provider_check`` and its arm dropped from ``auth_coherence``.

No data migration needed — production has zero ``provider='email'`` rows
(pre-launch, no real email signup ever happened). ``users.password_hash``
is kept (nullable) for rollback safety ; a physical column drop is deferred
to a later migration.
"""
from __future__ import annotations

from alembic import op


# revision identifiers (≤32 chars per R08).
revision = "20260517_1200_oauth_only"
down_revision = "20260515_1400_xp_ref_chk"
branch_labels = None
depends_on = None


_OLD_PROVIDER = "provider IN ('google', 'apple', 'email', 'internal', 'deleted', 'dev')"
_NEW_PROVIDER = "provider IN ('google', 'apple', 'internal', 'deleted', 'dev')"

_OLD_AUTH_COHERENCE = (
    "(provider = 'email' AND password_hash IS NOT NULL AND provider_id IS NULL) OR "
    "(provider IN ('google', 'apple') AND provider_id IS NOT NULL AND password_hash IS NULL) OR "
    "(provider IN ('internal', 'deleted', 'dev') AND provider_id IS NULL AND password_hash IS NULL)"
)
_NEW_AUTH_COHERENCE = (
    "(provider IN ('google', 'apple') AND provider_id IS NOT NULL AND password_hash IS NULL) OR "
    "(provider IN ('internal', 'deleted', 'dev') AND provider_id IS NULL AND password_hash IS NULL)"
)


def upgrade() -> None:
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS provider_check")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT provider_check CHECK ({_NEW_PROVIDER})")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS auth_coherence")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT auth_coherence CHECK ({_NEW_AUTH_COHERENCE})")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS auth_coherence")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT auth_coherence CHECK ({_OLD_AUTH_COHERENCE})")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS provider_check")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT provider_check CHECK ({_OLD_PROVIDER})")
