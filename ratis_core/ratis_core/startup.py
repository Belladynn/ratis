"""
Fail-fast helpers for service startup.

Each service should call require_env() in its lifespan before accepting
any traffic. Missing env vars raise RuntimeError immediately so the
container restarts visibly rather than failing silently at runtime.

Usage:
    from ratis_core.startup import require_env

    @asynccontextmanager
    async def lifespan(app):
        require_env("INTERNAL_API_KEY", "DATABASE_URL")
        ...
        yield
"""

import os


def require_env(*names: str) -> None:
    """
    Raise RuntimeError if any of the given environment variables is absent or empty.

    Call once at service startup (lifespan) to fail fast before accepting traffic.
    """
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)} — aborting")


def require_env_min_length(name: str, min_length: int) -> str:
    """Read env var ``name``, fail fast if shorter than ``min_length``.

    M1 fix (audit sécurité 2026-05-03) — defense against brute force on
    short / weak admin secrets. Apply at service lifespan startup so a
    misconfigured deploy crashes visibly rather than serving with a
    rainbow-table-vulnerable key.

    Returns the validated value on success so the caller may keep it
    inline (``key = require_env_min_length("ADMIN_API_KEY", 32)``).
    Raises ``RuntimeError`` with a clear message if the var is unset,
    empty, or shorter than the threshold.
    """
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name!r} — aborting")
    if len(value) < min_length:
        raise RuntimeError(
            f"Env var {name!r} too short: {len(value)} chars, "
            f"min {min_length} required (defense against brute force) — aborting"
        )
    return value
