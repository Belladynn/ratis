"""Local conftest for tests/worker — hermetic, no DB, no FastAPI app boot.

These tests cover pure-function pipeline routing logic. We deliberately
avoid the package-level ``tests/conftest.py`` (which spins up the DB,
FastAPI client, etc.) by passing ``--confcutdir=tests/worker`` so pytest
does not collect parent conftests.
"""

import os

# Minimal env so any incidental imports of ratis_core don't blow up.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://ratis:ratis@localhost:5432/ratis_test")
# RS256 — worker tests are hermetic pure-function tests; they never
# decode a token, but ratis_core.jwt may be imported transitively. Point
# at an ephemeral public key so any import-time read does not KeyError.
import tempfile as _tempfile
from pathlib import Path as _Path

from ratis_core.testing import generate_test_jwt_keypair as _gen_keypair

_jwt_key_dir = _Path(_tempfile.mkdtemp(prefix="ratis-jwt-keys-"))
_private_pem, _public_pem = _gen_keypair()
(_jwt_key_dir / "jwt_public.pem").write_text(_public_pem)
os.environ.setdefault("JWT_PUBLIC_KEY_PATH", str(_jwt_key_dir / "jwt_public.pem"))
os.environ.setdefault("JWT_AUDIENCE", "ratis")
