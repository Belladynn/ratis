"""Widen users.provider_check + auth_coherence to whitelist 'dev'

Revision ID: 20260511_2000_seedprov
Revises: 20260511_1900_afpr5
Create Date: 2026-05-11 20:00:00.000000

Purpose
=======
The seed pipeline (`scripts/seed/`) creates 6 personas with
``provider = 'dev'`` to mark them as **seeded-only** rows that must NEVER
appear in production. The semantics chosen :

- ``email``               : password_hash IS NOT NULL, provider_id IS NULL
- ``google`` / ``apple``  : provider_id IS NOT NULL, password_hash IS NULL
- ``internal`` / ``deleted`` / ``dev`` : both NULL (sentinel/tombstone/seed)

This migration extends both CHECK constraints (whitelist + auth_coherence)
to allow ``'dev'`` with both ``password_hash`` and ``provider_id`` NULL —
mirroring the ``'internal'`` arm semantics.

Why DB-level instead of using ``provider='email'`` with a sentinel hash ?
------------------------------------------------------------------------
Cleaner DB semantics. ``provider='dev'`` is greppable in any environment
(``SELECT * FROM users WHERE provider='dev'`` → 0 rows in prod, ~6 rows
in seed). Combined with the ``dev_*@ratis.app`` email sentinel (DA-4) and
the safety guards in ``scripts/seed/main.py`` (DA-5), there are three
independent layers of defense against seed leakage into prod :

1. ``ENVIRONMENT == "production"`` aborts ``main()``.
2. ``DATABASE_URL`` must contain ``_seed`` or ``_dev``.
3. Email pattern ``dev_*@ratis.app`` enforced in code.

The CHECK widening itself is harmless in prod : real production code paths
(OAuth signup, email signup, account deletion) never set ``provider='dev'``
— they go through ``ratis_auth`` services that use ``'google'/'apple'/
'email'/'deleted'/'internal'`` exclusively. The widening only makes the
seeded rows storable. No production state changes.

Idempotency
===========
``DROP CONSTRAINT IF EXISTS`` (R07) before each re-add. Re-runs are safe.
"""
from __future__ import annotations

from alembic import op


# revision identifiers (≤32 chars per R08).
revision = "20260511_2000_seedprov"
down_revision = "20260511_1900_afpr5"
branch_labels = None
depends_on = None


# Old/new ``provider_check`` and ``auth_coherence`` CHECKs.
_OLD_PROVIDER_CHECK = "provider IN ('google', 'apple', 'email', 'internal', 'deleted')"
_NEW_PROVIDER_CHECK = "provider IN ('google', 'apple', 'email', 'internal', 'deleted', 'dev')"

_OLD_AUTH_COHERENCE = (
    "(provider = 'email' AND password_hash IS NOT NULL AND provider_id IS NULL) OR "
    "(provider IN ('google', 'apple') AND provider_id IS NOT NULL AND password_hash IS NULL) OR "
    "(provider IN ('internal', 'deleted') AND provider_id IS NULL AND password_hash IS NULL)"
)
_NEW_AUTH_COHERENCE = (
    "(provider = 'email' AND password_hash IS NOT NULL AND provider_id IS NULL) OR "
    "(provider IN ('google', 'apple') AND provider_id IS NOT NULL AND password_hash IS NULL) OR "
    "(provider IN ('internal', 'deleted', 'dev') AND provider_id IS NULL AND password_hash IS NULL)"
)


def upgrade() -> None:
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS provider_check")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT provider_check CHECK ({_NEW_PROVIDER_CHECK})")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS auth_coherence")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT auth_coherence CHECK ({_NEW_AUTH_COHERENCE})")


def downgrade() -> None:
    # Downgrade is safe only if no rows with ``provider='dev'`` exist (otherwise
    # the narrower CHECK would reject them). The seed scripts only run against
    # ``ratis_seed`` / ``ratis_dev`` DBs — downgrade in prod is a no-op since
    # prod never has ``provider='dev'`` rows.
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS auth_coherence")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT auth_coherence CHECK ({_OLD_AUTH_COHERENCE})")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS provider_check")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT provider_check CHECK ({_OLD_PROVIDER_CHECK})")
