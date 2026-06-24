"""Context manager for short-lived secret access (Module 10, PR 4).

Provides ``secret_with()`` — a Python context manager that yields the secret
value for the duration of the ``with`` block, then automatically revokes / marks
the lease on exit (even if an exception is raised inside the block).

Two paths:
* **Cat-A** (``provider=None``): reads an existing secret from the Keychain via
  ``secrets_tools.secret_get()``.  On exit marks ``revoked_at`` in SQLite so the
  lease is recorded as consumed.  The Keychain entry is NOT removed — Cat-A
  secrets are long-lived; only the lease record is closed.
* **Cat-B** (``provider`` given): calls ``secret_provision()`` to mint a JIT token,
  yields the provisioned value, then calls ``secret_revoke()`` on exit.

The module delegates all storage to ``secrets_tools`` (Keychain + SQLite), so the
same dependency injection points (``set_keychain``, ``set_meta_db``, etc.) used by
the test suite work here too.

Usage::

    from agent_mcp.secrets.lease import secret_with

    with secret_with("github", provider="github-app", ttl="30m") as token:
        subprocess.run(["gh", "pr", "merge", "--token", token])
    # Token is revoked at this point.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from contextlib import contextmanager

from ..errors import KeychainMiss
from ..secrets.provision import get_provider, parse_ttl


@contextmanager
def secret_with(
    name: str,
    *,
    provider: str | None = None,
    ttl: str = "30m",
    scopes: list[str] | None = None,
    principal: str = "agent",
) -> Generator[str, None, None]:
    """Context manager — yields the secret value, auto-revokes on exit.

    Parameters
    ----------
    name:
        Logical secret name (used as the Keychain account suffix ``secret/<name>``).
    provider:
        When set, mints a short-lived Cat-B token via the named provider adapter
        (e.g. ``"github-app"``, ``"sentry"``).  When ``None``, reads the existing
        Cat-A value from the Keychain.
    ttl:
        Duration string for Cat-B tokens — ``"30m"``, ``"1h"``, ``"24h"``.
        Ignored for Cat-A.
    scopes:
        Provider-specific scopes (optional; ignored for Cat-A).
    principal:
        Audit principal tag (default ``"agent"``).

    Yields
    ------
    str
        The secret value (raw, as stored in Keychain).

    On enter:
        - Cat-B: calls ``secret_provision()`` → stores lease in SQLite + Keychain.
        - Cat-A: reads existing Keychain value via ``secret_get()``.
    On exit (always, even on exception):
        - Cat-B: calls ``secret_revoke(lease_id)`` → provider API + SQLite mark.
        - Cat-A: marks ``revoked_at`` in SQLite (Keychain entry preserved).
    """
    # Lazy import to avoid circular dependency (secrets_tools imports from here
    # indirectly via registration).
    from ..tools.secrets_tools import (
        _get_meta_db,
        _now_iso,
    )
    from ..tools.secrets_tools import secret_get as _secret_get

    if provider is not None:
        # ---- Cat-B path: provision a JIT token ----------------------------
        ttl_seconds = parse_ttl(ttl)
        adapter = get_provider(provider)

        # Provision via adapter (reads admin token from Keychain internally).
        result = adapter.provision("", ttl_seconds=ttl_seconds)

        # Store in Keychain + SQLite via the module-level helpers.
        import secrets as _secrets_mod

        kc_account = f"secret/{name}"
        # Store the provisioned token value in Keychain.
        from ..tools.secrets_tools import _get_audit_chain, _get_keychain

        _get_keychain().set(kc_account, result.value)

        db = _get_meta_db()
        existing = db.get_active(name)
        version = (existing["version"] + 1) if existing else 1
        lease_id = _secrets_mod.token_urlsafe(24)
        issued_at = _now_iso()

        db.insert_provision(
            name=name,
            version=version,
            lease_id=lease_id,
            issued_at=issued_at,
            expires_at=result.expires_at if result.expires_at else None,
            description=f"JIT token via {provider} (context manager)",
            provider=provider,
            token_id=result.token_id,
        )

        _get_audit_chain().append(action="generate", name=name, principal=principal)

        value = result.value
        try:
            yield value
        finally:
            # Revoke: call provider API + mark SQLite + remove from Keychain.
            with contextlib.suppress(Exception):
                row = db.get_by_lease_id(lease_id)
                if row:
                    token_id = row.get("token_id") or ""
                    with contextlib.suppress(Exception):
                        adapter.revoke("", token_id)
            with contextlib.suppress(Exception):
                db.revoke(lease_id=lease_id, revoked_at=_now_iso())
            with contextlib.suppress(Exception):
                _get_keychain().delete(kc_account)
            with contextlib.suppress(Exception):
                _get_audit_chain().append(action="revoke", name=name, principal=principal)

    else:
        # ---- Cat-A path: read existing Keychain entry --------------------
        meta_result = _secret_get(name)
        if "error" in meta_result:
            raise KeychainMiss(f"secret '{name}' not found in vault")

        value = meta_result["value"]
        lease_id = meta_result["lease_id"]
        db = _get_meta_db()

        try:
            yield value
        finally:
            # Mark lease as consumed — Keychain entry is preserved.
            with contextlib.suppress(Exception):
                db.revoke(lease_id=lease_id, revoked_at=_now_iso())
            with contextlib.suppress(Exception):
                from ..tools.secrets_tools import _get_audit_chain

                _get_audit_chain().append(action="revoke", name=name, principal=principal)
