"""NRC bloc D — seed RTS-ADMIN0 technical user for admin actions.

Revision ID: 20260501_2000_nrcD
Revises: 20260501_1700_nrcC
Create Date: 2026-05-01 20:00:00

The Bloc D admin endpoints (``POST /admin/name-resolutions/resolve`` and
``/reject-challenges``) write rows in the ``product_name_resolutions``
ledger. The schema requires a real ``users.id`` foreign key on every row,
but the action represents an admin operator — not any specific user.

We seed a single technical row :

- ``email``        : ``admin@ratis.internal`` (non-PII, never used for auth)
- ``support_id``   : ``RTS-ADMIN0`` (sentinel — out-of-band of the real
                     RTS-XXXXXX keyspace which uses base-32 ``[A-HJ-NP-Z2-9]``
                     so ``ADMIN0`` cannot collide with a real-user id)
- ``provider``     : ``internal`` (extends the legacy CHECK enum which
                     only allowed ``google|apple|email``)
- ``display_name`` : ``ratis admin (system)``

The CHECK constraint ``provider_check`` is extended to accept
``internal`` alongside the existing OAuth providers. Downgrade reverses
the constraint and removes the seed row.

This row is referenced by the Bloc D admin endpoints when calling
``record_resolution(... user_id=<this id>, match_method='manual_admin')``.
The look-up convention is ``support_id = 'RTS-ADMIN0'`` (a stable handle
that survives backfills and never appears in OAuth flows).
"""
from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260501_2000_nrcD"
down_revision = "20260501_1700_nrcC"
branch_labels = None
depends_on = None


# Stable UUID for the technical admin row — chosen ahead of time so any
# future cross-environment reference can hardcode it. The value has no
# semantic meaning ; it just must never collide with a real user id (the
# UUID4 namespace makes that astronomically unlikely).
_ADMIN_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000ad0001")
_ADMIN_SUPPORT_ID = "RTS-ADMIN0"
_ADMIN_EMAIL = "admin@ratis.internal"

_OLD_PROVIDER = "provider IN ('google', 'apple', 'email')"
_NEW_PROVIDER = "provider IN ('google', 'apple', 'email', 'internal')"

# ``auth_coherence`` originally required either an OAuth provider_id or an
# email-flow password_hash. The technical admin row carries neither — it
# is purely a ledger anchor with no auth surface. We extend the constraint
# to whitelist ``provider='internal'`` with both fields NULL.
_OLD_AUTH_COHERENCE = (
    "(provider = 'email' AND password_hash IS NOT NULL AND provider_id IS NULL) OR "
    "(provider != 'email' AND provider_id IS NOT NULL AND password_hash IS NULL)"
)
_NEW_AUTH_COHERENCE = (
    "(provider = 'email' AND password_hash IS NOT NULL AND provider_id IS NULL) OR "
    "(provider IN ('google', 'apple') AND provider_id IS NOT NULL AND password_hash IS NULL) OR "
    "(provider = 'internal' AND provider_id IS NULL AND password_hash IS NULL)"
)


def upgrade() -> None:
    # ----- Step 1 : extend the provider CHECK enum -----
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS provider_check")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT provider_check CHECK ({_NEW_PROVIDER})")

    # ----- Step 1b : extend auth_coherence to whitelist provider='internal' -----
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS auth_coherence")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT auth_coherence CHECK ({_NEW_AUTH_COHERENCE})")

    # ----- Step 2 : seed the technical admin row -----
    # ON CONFLICT DO NOTHING : idempotent re-runs are safe (e.g. when the
    # migration is replayed against an env where the row was inserted by
    # a previous run that failed half-way through).
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO users
                (id, email, support_id, provider, display_name, is_deleted)
            VALUES
                (:id, :email, :sid, 'internal', 'ratis admin (system)', false)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": str(_ADMIN_USER_ID),
            "email": _ADMIN_EMAIL,
            "sid": _ADMIN_SUPPORT_ID,
        },
    )


def downgrade() -> None:
    # Drop the seed row first — must happen BEFORE shrinking the CHECKs,
    # otherwise the row violates the old enum and the constraint fails
    # to re-attach.
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM users WHERE id = :id"),
        {"id": str(_ADMIN_USER_ID)},
    )
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS auth_coherence")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT auth_coherence CHECK ({_OLD_AUTH_COHERENCE})")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS provider_check")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT provider_check CHECK ({_OLD_PROVIDER})")
