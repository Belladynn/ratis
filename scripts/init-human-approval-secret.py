#!/usr/bin/env python3
"""Install the ``HUMAN_APPROVAL_SECRET`` (HSP3 M2) into ``app_settings``.

The human-gate of the db-write-pipeline verifies each approve/reject
decision against an argon2id hash stored in
``app_settings.human_approval.argon2_hash``. The plaintext secret never
touches the database — only its hash. The seed installed by the HSP3
migration leaves the section dormant
(``{secret_set: false, argon2_hash: null}``) ; this script is the ops
ceremony that *arms* it.

It is a human act : the operator runs it once in prod with the chosen
secret. Re-running rotates the hash (idempotent — last run wins).

The plaintext secret is read from :
    1. env ``HUMAN_APPROVAL_SECRET_PLAINTEXT`` (for scripted runs), or
    2. stdin via ``getpass`` (interactive — never echoed to the terminal).

Usage :
    HUMAN_APPROVAL_SECRET_PLAINTEXT=<secret> \\
        DATABASE_URL=postgresql+psycopg://... \\
        uv run --package ratis_product_analyser \\
        python scripts/init-human-approval-secret.py

    # or interactive (prompts on stdin, no echo) :
    DATABASE_URL=postgresql+psycopg://... \\
        uv run --package ratis_product_analyser \\
        python scripts/init-human-approval-secret.py

Exit codes :
    0  — secret installed (hash posted to app_settings).
    1  — error (secret too short, section absent, DATABASE_URL unset, ...).
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parent.parent

# ``hash_secret`` lives in the PA service (argon2id helper). PA uses flat
# imports (``from admin_ui.human_secret import ...``) — its package root is
# the service dir on sys.path. Mirror that so we reuse the *same* hasher the
# runtime verifies against (no duplicated argon2 config — R18).
PA_ROOT = REPO_ROOT / "webservices" / "ratis_product_analyser"
sys.path.insert(0, str(PA_ROOT))

from admin_ui.human_secret import hash_secret

# argon2id needs a non-trivial secret. Mirror the floor enforced elsewhere
# for operator-typed secrets.
_MIN_SECRET_LENGTH = 16


def _read_secret() -> str:
    """Read the plaintext secret from env, falling back to a no-echo prompt."""
    secret = os.environ.get("HUMAN_APPROVAL_SECRET_PLAINTEXT")
    if secret:
        return secret
    return getpass.getpass("HUMAN_APPROVAL_SECRET (input hidden): ")


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set — cannot connect.", file=sys.stderr)
        return 1

    secret = _read_secret()
    if len(secret) < _MIN_SECRET_LENGTH:
        print(
            f"ERROR: secret too short — need at least {_MIN_SECRET_LENGTH} characters.",
            file=sys.stderr,
        )
        return 1

    argon2_hash = hash_secret(secret)

    # Engine import is deferred so a too-short secret fails before touching
    # any DB machinery.
    from ratis_core.database import make_engine

    engine = make_engine(db_url)
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE app_settings SET data = jsonb_set("
                    "jsonb_set(data, '{secret_set}', 'true'::jsonb), "
                    "'{argon2_hash}', to_jsonb(cast(:hash AS text))), "
                    "updated_at = now() WHERE section = 'human_approval'"
                ),
                {"hash": argon2_hash},
            )
            if result.rowcount != 1:
                print(
                    "ERROR: section 'human_approval' absent — applique d'abord la migration HSP3.",
                    file=sys.stderr,
                )
                return 1
    finally:
        engine.dispose()

    print("✅ secret installé (hash argon2id posé).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
