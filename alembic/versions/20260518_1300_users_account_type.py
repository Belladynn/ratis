"""Phase 2 — collapse users.provider/provider_id into users.account_type.

Revision ID: 20260518_1300_acct_type
Revises: 20260518_1200_user_identities
Create Date: 2026-05-18 13:00:00.000000

The real OAuth identity now lives in ``user_identities`` (migration
``20260518_1200_user_identities``). ``users.provider`` is renamed to
``account_type`` and narrowed to account *states* (oauth|internal|
deleted|dev). ``users.provider_id``, the ``users_provider_provider_id_key``
UNIQUE constraint and the ``auth_coherence`` CHECK are dropped — all three
are redundant once identities are externalised.

The ``users_email_key`` UNIQUE constraint on ``users.email`` is also
dropped : with the account key now in ``user_identities.(provider,
provider_id)``, ``email`` is a purely informative contact field and two
accounts (one Apple, one Google) may legitimately share an email
(spec §4.2). ``email`` stays ``NOT NULL`` — only the UNIQUE goes.
"""
from __future__ import annotations

from alembic import op

revision = "20260518_1300_acct_type"
down_revision = "20260518_1200_user_identities"
branch_labels = None
depends_on = None

_NEW_CHECK = "account_type IN ('oauth', 'internal', 'deleted', 'dev')"
_OLD_PROVIDER_CHECK = "provider IN ('google', 'apple', 'internal', 'deleted', 'dev')"
_OLD_AUTH_COHERENCE = (
    "(provider IN ('google', 'apple') AND provider_id IS NOT NULL AND password_hash IS NULL) OR "
    "(provider IN ('internal', 'deleted', 'dev') AND provider_id IS NULL AND password_hash IS NULL)"
)


def upgrade() -> None:
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS auth_coherence")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_provider_provider_id_key")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS provider_check")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key")
    op.execute("ALTER TABLE users RENAME COLUMN provider TO account_type")
    op.execute("UPDATE users SET account_type = 'oauth' WHERE account_type IN ('google', 'apple')")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS provider_id")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT account_type_check CHECK ({_NEW_CHECK})")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS account_type_check")
    op.execute("ALTER TABLE users ADD COLUMN provider_id text")
    op.execute("ALTER TABLE users RENAME COLUMN account_type TO provider")
    op.execute(
        "UPDATE users u SET provider = i.provider, provider_id = i.provider_id "
        "FROM user_identities i WHERE i.user_id = u.id AND u.provider = 'oauth'"
    )
    op.execute(f"ALTER TABLE users ADD CONSTRAINT provider_check CHECK ({_OLD_PROVIDER_CHECK})")
    op.execute(f"ALTER TABLE users ADD CONSTRAINT auth_coherence CHECK ({_OLD_AUTH_COHERENCE})")
    op.execute("ALTER TABLE users ADD CONSTRAINT users_provider_provider_id_key UNIQUE (provider, provider_id)")
    op.execute("ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email)")
