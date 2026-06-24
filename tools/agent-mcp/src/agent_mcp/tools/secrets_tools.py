"""Secrets vault tools — Module 10 (PR 1 + PR 2 + PR 3 + PR 4 + PR 6 + PR 7 + PR 8).

Exposes 12 MCP tools for managing Cat-A/B/C secrets:

* ``secret_generate(name, description, length, category, format)``  — admin scope
* ``secret_get(name)``                                               — admin scope
* ``secret_list()``                                                  — ops scope
* ``secret_delete(name)``                                            — admin scope
* ``secret_inject(name, targets, ...)``                              — admin scope
* ``secret_provision(name, provider, ttl, scopes)``                  — admin scope (PR 3)
* ``secret_revoke(lease_id)``                                        — admin scope (PR 3)
* ``secret_renew(lease_id, extend_ttl)``                             — admin scope (PR 4)
* ``secret_audit_expiry(threshold_days)``                            — ops scope (PR 6)
* ``secret_import(name, value, category, expires_at, ...)``          — admin scope (PR 6)
* ``secret_rotate(name, window_minutes, format)``                    — admin scope (PR 7)
* ``secret_rollback(name, version)``                                 — admin scope (PR 7)

Storage model (PR 1 / PR 7)
---------------------------
* **Values** live exclusively in macOS Keychain under service
  ``ratis-agent-mcp``.  From PR 7 onwards, the account key is
  ``secret/<name>/v<N>`` (versioned).  v1 is ALSO stored under legacy key
  ``secret/<name>`` for backwards compatibility with PR 1 callers.
* **Metadata** (lease, version, category, timestamps) lives in the SQLite DB
  at ``~/.local/state/ratis-agent-mcp/ratis_secrets_meta.db`` (via
  ``config.secrets_meta_db_file()``).
* **Audit** is written to a monthly HMAC-chained JSONL file under
  ``~/.local/state/ratis-agent-mcp/audit/secrets-YYYY-MM.jsonl`` (via
  ``config.secrets_audit_dir()``).

Dependency injection
--------------------
Module-level ``_keychain``, ``_meta_db``, and ``_audit_dir`` are ``None``
until first use. ``_get_*()``) lazy-inits from config; ``set_*()`` replaces
for tests. This is the established pattern used by ``db_tools``, ``r2_tools``,
etc.

Design decisions (PR 1 scope)
------------------------------
* ``secret_generate`` on a name that already exists in SQLite creates a new
  version row (version+1) and updates the Keychain value. The old SQLite row
  is NOT revoked here — it is considered superseded. Callers that held the old
  lease_id can still inspect it in the DB but it no longer matches the live
  Keychain value.
* ``secret_delete`` hard-deletes all SQLite rows and the Keychain entry —
  there is no soft-delete or tombstone in PR 1.

Design decisions (PR 7 scope)
------------------------------
* ``secret_rotate(name, window_minutes)`` creates v+1 with a new value,
  stores it under ``secret/<name>/v<N+1>`` in Keychain.  The old version
  stays alive (not revoked) for ``window_minutes`` — its
  ``rotation_window_expires_at`` is set so ``cleanup_rotation_windows()``
  can eventually revoke it.
* ``secret_rollback(name, version)`` reactivates a prior version by clearing
  its ``revoked_at``, and revokes the current active version.  Returns a
  ``warning`` key if the Keychain entry for the requested version is absent.
* ``_read_from_keychain(name, version)`` tries versioned key first
  (``secret/<name>/v<N>``), then falls back to the legacy key
  (``secret/<name>``) only when version == 1.  This preserves PR 1
  compatibility without adding ambiguity for newer versions.

Design decisions (PR 3 scope)
------------------------------
* ``secret_provision`` mints a short-lived provider token (Cat B). The token
  value is stored in Keychain but NEVER returned by the tool. The tool returns
  {name, lease_id, provider, expires_at, metadata}.
* ``secret_revoke`` explicitly revokes a lease: calls the provider revoke API,
  marks revoked_at in SQLite, removes from Keychain.
* ``cleanup_expired_leases`` is called lazily (once per process) at the first
  ``secret_provision`` call. It scans for expired Cat-B leases and revokes them.
"""

from __future__ import annotations

import base64
import contextlib
import datetime
import secrets as _secrets_mod
import string
import uuid
from pathlib import Path
from typing import Any

from ..config import secrets_audit_dir, secrets_meta_db_file, state_dir
from ..errors import KeychainMiss
from ..keychain import Keychain
from ..secrets.audit_chain import SecretsAuditChain
from ..secrets.inject import (
    _inject_docker_compose_env,
    _inject_env_file,
    _inject_gh_actions,
    _inject_n8n,
)
from ..secrets.meta_db import SecretMetaDB
from ..secrets.provision import (
    cleanup_expired_leases as _cleanup_expired_leases,
)
from ..secrets.provision import (
    get_provider,
    parse_ttl,
)
from ..server import TOOLS_REGISTRY, register_tool

# ---------------------------------------------------------------------------
# Dependency injection points (module-level, replaced by set_*() in tests)
# ---------------------------------------------------------------------------

_keychain: Keychain | None = None
_meta_db: SecretMetaDB | None = None
_audit_dir: Path | None = None  # injected by tests; None = use config default


def _get_keychain() -> Keychain:
    global _keychain
    if _keychain is None:
        _keychain = Keychain()
    return _keychain


def _get_meta_db() -> SecretMetaDB:
    global _meta_db
    if _meta_db is None:
        _meta_db = SecretMetaDB(secrets_meta_db_file())
    return _meta_db


def _get_audit_dir() -> Path:
    if _audit_dir is not None:
        return _audit_dir
    return secrets_audit_dir()


def _get_audit_chain() -> SecretsAuditChain:
    """Return a fresh SecretsAuditChain for the current call.

    A new instance is created per call to avoid long-lived state. The chain
    is lightweight (no persistent connection), so this is acceptable.
    """
    return SecretsAuditChain(log_dir=_get_audit_dir(), keychain=_get_keychain())


def set_keychain(kc: Keychain | None) -> None:
    """Test helper — inject a Keychain double."""
    global _keychain
    _keychain = kc


def set_meta_db(db: SecretMetaDB | None) -> None:
    """Test helper — inject a SecretMetaDB double."""
    global _meta_db
    _meta_db = db


def set_audit_dir(path: Path | None) -> None:
    """Test helper — inject a custom audit directory."""
    global _audit_dir
    _audit_dir = path


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

_KEYCHAIN_ACCOUNT_PREFIX = "secret/"


def _keychain_account(name: str) -> str:
    """Legacy (PR 1) account key: ``secret/<name>``.

    Still used by ``secret_delete`` (cleans up both patterns) and as
    a v1 fallback in ``_read_from_keychain``.
    """
    return f"{_KEYCHAIN_ACCOUNT_PREFIX}{name}"


def _versioned_keychain_account(name: str, version: int) -> str:
    """Versioned account key (PR 7): ``secret/<name>/v<N>``."""
    return f"{_KEYCHAIN_ACCOUNT_PREFIX}{name}/v{version}"


def _read_from_keychain(name: str, version: int) -> str | None:
    """Read a secret value from Keychain, trying versioned key first.

    Resolution order:
    1. ``secret/<name>/v<version>`` — new pattern (PR 7+).
    2. ``secret/<name>`` — legacy pattern (PR 1), only tried when version == 1.

    Returns the value string, or ``None`` if not found under either key.
    """
    kc = _get_keychain()
    try:
        return kc.get(_versioned_keychain_account(name, version))
    except KeychainMiss:
        pass
    if version == 1:
        try:
            return kc.get(_keychain_account(name))
        except KeychainMiss:
            pass
    return None


_SUPPORTED_FORMATS = ("urlsafe", "hex", "base64", "alphanumeric", "numeric", "uuid")


def _generate_token(format: str, length: int) -> str:
    """Generate a random token in the requested format.

    Args:
        format: one of ``urlsafe`` · ``hex`` · ``base64`` · ``alphanumeric`` ·
            ``numeric`` · ``uuid``.
        length: meaning depends on format:
            - ``urlsafe`` / ``hex`` / ``base64``: number of *random bytes*
              (output string will be longer than ``length``).
            - ``alphanumeric`` / ``numeric``: exact number of output characters.
            - ``uuid``: ignored — output is always 36 characters
              (``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx``).

    Raises:
        ValueError: if ``format`` is not one of the supported values.
    """
    if format == "urlsafe":
        return _secrets_mod.token_urlsafe(length)
    if format == "hex":
        return _secrets_mod.token_hex(length)
    if format == "base64":
        return base64.b64encode(_secrets_mod.token_bytes(length)).decode()
    if format == "alphanumeric":
        charset = string.ascii_letters + string.digits
        return "".join(_secrets_mod.choice(charset) for _ in range(length))
    if format == "numeric":
        return "".join(_secrets_mod.choice(string.digits) for _ in range(length))
    if format == "uuid":
        return str(uuid.uuid4())
    raise ValueError(f"unsupported format '{format}'; supported: {', '.join(_SUPPORTED_FORMATS)}")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def secret_generate(
    name: str,
    description: str = "",
    length: int = 32,
    category: str = "A",
    format: str = "urlsafe",
) -> dict[str, Any]:
    """Generate and store a new Cat-A secret. Scope: admin.

    Args:
        name: logical secret name.
        description: human-readable description.
        length: meaning depends on ``format`` — see ``_generate_token`` docstring.
        category: "A" (default), "B", or "C".
        format: token format — one of ``urlsafe`` (default, backward-compat) · ``hex`` ·
            ``base64`` · ``alphanumeric`` · ``numeric`` · ``uuid``.
            For ``uuid`` the ``length`` parameter is ignored.

    Returns: {name, lease_id, version, issued_at, expires_at, keychain_account, format}
    NEVER returns the secret value.
    """
    kc = _get_keychain()
    db = _get_meta_db()

    # Determine next version number.
    existing = db.get_active(name)
    version = (existing["version"] + 1) if existing else 1

    # Generate the random secret value.
    try:
        value = _generate_token(format, length)
    except ValueError as exc:
        return {"error": "unsupported_format", "name": name, "detail": str(exc)}

    # Persist to Keychain first (reversible — if DB write fails, the Keychain
    # entry is orphaned but harmless; rerunning generate will overwrite it).
    # PR 7: store under versioned key; also store under legacy key for v1 only
    # to keep PR 1 callers working (secret_get falls back to legacy for v1).
    account = _versioned_keychain_account(name, version)
    kc.set(account, value)
    if version == 1:
        kc.set(_keychain_account(name), value)

    # Generate a unique lease_id.
    lease_id = _secrets_mod.token_urlsafe(24)
    issued_at = _now_iso()

    # Persist metadata to SQLite.
    db.insert_version(
        name=name,
        category=category,
        version=version,
        lease_id=lease_id,
        issued_at=issued_at,
        expires_at=None,
        description=description,
    )

    # Write audit.
    _get_audit_chain().append(action="generate", name=name, principal="agent")

    return {
        "name": name,
        "lease_id": lease_id,
        "version": version,
        "issued_at": issued_at,
        "expires_at": None,
        "keychain_account": account,  # versioned: secret/<name>/v<N>
        "format": format,
    }


def secret_get(name: str) -> dict[str, Any]:
    """Get secret value + metadata for an active secret. Scope: admin.

    Returns: {name, value, lease_id, version, issued_at, expires_at, category}
    The value is read from Keychain at call time.

    Key resolution order (PR 7):
    1. ``secret/<name>/v<version>`` — versioned key.
    2. ``secret/<name>`` — legacy key (only tried when version == 1).
    """
    db = _get_meta_db()

    meta = db.get_active(name)
    if meta is None:
        return {"error": "not_found", "name": name}

    value = _read_from_keychain(name, meta["version"])
    if value is None:
        return {"error": "not_found", "name": name}

    _get_audit_chain().append(action="get", name=name, principal="agent")

    return {
        "name": name,
        "value": value,
        "lease_id": meta["lease_id"],
        "version": meta["version"],
        "issued_at": meta["issued_at"],
        "expires_at": meta["expires_at"],
        "category": meta["category"],
    }


def secret_list() -> list[dict[str, Any]]:
    """List all managed secrets (metadata only, NEVER values). Scope: ops.

    Returns list of {name, category, version, lease_id, issued_at, expires_at, revoked_at}
    """
    db = _get_meta_db()
    rows = db.list_all()
    _get_audit_chain().append(action="list", name="", principal="agent")
    # Paranoid: strip any stray 'value' key before returning.
    return [{k: v for k, v in row.items() if k != "value"} for row in rows]


def secret_delete(name: str) -> dict[str, Any]:
    """Delete a secret: revoke all leases, remove from Keychain. Scope: admin.

    Removes both the legacy key (``secret/<name>``) and any versioned keys
    (``secret/<name>/v<N>``) found in SQLite before deleting the DB rows.

    Returns: {name, deleted: true, versions_removed: int}
    """
    kc = _get_keychain()
    db = _get_meta_db()

    # Collect all version numbers before deleting rows.
    import contextlib as _cl

    all_rows = db._conn.execute("SELECT version FROM secret_versions WHERE name = ?", (name,)).fetchall()
    versions = [row[0] for row in all_rows]

    count = db.delete_all_versions(name)

    # Remove legacy key (PR 1 pattern) — idempotent.
    with _cl.suppress(Exception):
        kc.delete(_keychain_account(name))

    # Remove all versioned keys (PR 7 pattern) — idempotent.
    for v in versions:
        with _cl.suppress(Exception):
            kc.delete(_versioned_keychain_account(name, v))

    _get_audit_chain().append(action="delete", name=name, principal="agent")

    return {
        "name": name,
        "deleted": True,
        "versions_removed": count,
    }


def secret_inject(
    name: str,
    targets: list[str],
    gh_secret_name: str | None = None,
    env_file_path: str | None = None,
    # Internal injection point for the runtime env-file path (tests override this)
    _runtime_env_file: "Path | None" = None,
) -> dict[str, Any]:
    """Inject a secret into one or more targets. Scope: admin.

    Args:
        name: secret name as stored in Keychain (via secret_generate/import)
        targets: list of target adapters — any of:
            "env-file"           → ~/.local/state/ratis-agent-mcp/secrets.runtime.env
            "gh-actions"         → GitHub Actions secret (requires gh CLI auth)
            "n8n-env"            → n8n environment variable via REST API
            "docker-compose-env" → .env file (path in env_file_path, default .env)
        gh_secret_name: GitHub Actions secret name
            (default: NAME.upper().replace("-","_"))
        env_file_path: path to .env file for "docker-compose-env" target
            (default: ".env")

    Returns:
        {name, injected: {target: "ok" | "error: <reason>"}}
        or {error: "not_found"} if the secret does not exist.
    """
    # Resolve the secret value directly from Keychain (no double-audit).
    db = _get_meta_db()
    meta = db.get_active(name)
    if meta is None:
        return {"error": "not_found", "name": name}
    value = _read_from_keychain(name, meta["version"])
    if value is None:
        return {"error": "not_found", "name": name}

    # Resolve runtime env-file path (injectable for tests).
    runtime_env_file: Path
    if _runtime_env_file is not None:
        runtime_env_file = _runtime_env_file
    else:
        runtime_env_file = state_dir() / "secrets.runtime.env"

    # Resolve docker-compose env-file path.
    dc_env_file = Path(env_file_path) if env_file_path else Path(".env")

    # Resolve GH Actions secret name.
    resolved_gh_name = gh_secret_name or name.upper().replace("-", "_")

    # Retrieve n8n API key from Keychain (lazy — only when target requested).
    _n8n_api_key: str | None = None
    _n8n_key_fetched = False

    def _get_n8n_api_key() -> str | None:
        nonlocal _n8n_api_key, _n8n_key_fetched
        if _n8n_key_fetched:
            return _n8n_api_key
        _n8n_key_fetched = True
        try:
            _n8n_api_key = _get_keychain().get("n8n-api-key")
        except KeychainMiss:
            _n8n_api_key = None
        return _n8n_api_key

    # Dispatch each target — accumulate statuses, never raise.
    injected: dict[str, str] = {}
    for target in targets:
        if target == "env-file":
            injected[target] = _inject_env_file(name.upper().replace("-", "_"), value, runtime_env_file)
        elif target == "gh-actions":
            injected[target] = _inject_gh_actions(resolved_gh_name, value)
        elif target == "n8n-env":
            injected[target] = _inject_n8n(
                name.upper().replace("-", "_"),
                value,
                n8n_api_key=_get_n8n_api_key(),
            )
        elif target == "docker-compose-env":
            injected[target] = _inject_docker_compose_env(name.upper().replace("-", "_"), value, dc_env_file)
        else:
            injected[target] = f"error: unknown target '{target}'"

    # Audit — record which targets succeeded.
    targets_ok = [t for t, s in injected.items() if s == "ok"]
    _get_audit_chain().append(
        action="inject",
        name=name,
        principal="agent",
    )

    return {
        "name": name,
        "injected": injected,
        "targets_ok": targets_ok,
    }


# ---------------------------------------------------------------------------
# PR 3 — secret_provision + secret_revoke
# ---------------------------------------------------------------------------

# Lazy-once flag: cleanup_expired_leases() is called at most once per process.
_provision_cleanup_done = False


def secret_provision(
    name: str,
    provider: str,
    ttl: str = "30m",
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    """Mint a short-lived token from a provider (Cat B JIT). Scope: admin.

    Args:
        name: logical name for this lease (stored in Keychain as secret/{name})
        provider: one of "github-app", "cloudflare-r2", "sentry", "eas",
            "vercel", "stripe-restricted"
        ttl: duration string "30m", "1h", "24h" (parsed to seconds)
        scopes: provider-specific scopes (optional, defaults per provider)

    Returns: {name, lease_id, provider, expires_at, metadata}
    NEVER returns the token value.
    """
    global _provision_cleanup_done

    kc = _get_keychain()
    db = _get_meta_db()

    # Lazy cleanup — once per process.
    if not _provision_cleanup_done:
        _provision_cleanup_done = True
        with contextlib.suppress(Exception):
            _cleanup_expired_leases(db=db)

    # Validate provider and parse TTL.
    adapter = get_provider(provider)  # raises ValueError for unknown providers
    ttl_seconds = parse_ttl(ttl)  # raises ValueError for invalid format

    # Read admin token via the admin Keychain (set_admin_keychain injects for tests).
    result = adapter.provision("", ttl_seconds=ttl_seconds)

    # Store token value in Keychain.
    account = _keychain_account(name)
    kc.set(account, result.value)

    # Persist metadata in SQLite.
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
        description=f"JIT token via {provider}",
        provider=provider,
        token_id=result.token_id,
    )

    # Audit.
    _get_audit_chain().append(action="generate", name=name, principal="agent")

    return {
        "name": name,
        "lease_id": lease_id,
        "provider": provider,
        "expires_at": result.expires_at,
        "metadata": result.metadata,
    }


def secret_renew(lease_id: str, extend_ttl: str = "30m") -> dict[str, Any]:
    """Renew a lease by extending its expires_at. Scope: admin.

    Only applicable when the lease exists in SQLite.  For Cat-A secrets the
    ``expires_at`` column is updated directly.  For Cat-B secrets the current
    implementation updates the SQLite record only (re-provisioning from the
    provider is deferred to a future PR).

    Args:
        lease_id:    The lease identifier returned by ``secret_generate`` /
                     ``secret_provision``.
        extend_ttl:  How much time to add from *now* — ``"30m"``, ``"1h"``, etc.
                     Parsed by ``parse_ttl()``.

    Returns:
        ``{lease_id, new_expires_at, renewed: bool}``
        or ``{lease_id, renewed: false}`` if the lease is not found.
    """
    db = _get_meta_db()

    row = db.get_by_lease_id(lease_id)
    if row is None:
        return {"lease_id": lease_id, "renewed": False}

    ttl_seconds = parse_ttl(extend_ttl)
    new_expires_at = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=ttl_seconds)).isoformat()

    db._conn.execute(
        "UPDATE secret_versions SET expires_at = ? WHERE lease_id = ?",
        (new_expires_at, lease_id),
    )
    db._conn.commit()

    _get_audit_chain().append(action="renew", name=row["name"], principal="agent")

    return {
        "lease_id": lease_id,
        "new_expires_at": new_expires_at,
        "renewed": True,
    }


def secret_revoke(lease_id: str) -> dict[str, Any]:
    """Explicitly revoke a lease before TTL. Scope: admin.

    Calls the provider revoke API if applicable, marks revoked_at in SQLite,
    removes from Keychain.

    Returns: {lease_id, revoked: true, provider}
    """
    kc = _get_keychain()
    db = _get_meta_db()

    row = db.get_by_lease_id(lease_id)
    if row is None:
        return {"error": "not_found", "lease_id": lease_id}

    provider_name: str = row.get("provider") or ""
    token_id: str = row.get("token_id") or ""
    secret_name: str = row["name"]

    # Attempt provider revoke if we have the info (best-effort — always proceed to DB mark).
    if provider_name:
        with contextlib.suppress(Exception):
            adapter = get_provider(provider_name)
            # Admin token is read lazily inside the adapter.
            adapter.revoke("", token_id)

    # Mark revoked in SQLite.
    db.revoke(lease_id=lease_id, revoked_at=_now_iso())

    # Remove from Keychain — both legacy and versioned keys (idempotent, best-effort).
    version: int = row.get("version") or 1
    with contextlib.suppress(Exception):
        kc.delete(_keychain_account(secret_name))
    with contextlib.suppress(Exception):
        kc.delete(_versioned_keychain_account(secret_name, version))

    # Audit.
    _get_audit_chain().append(action="revoke", name=secret_name, principal="agent")

    return {
        "lease_id": lease_id,
        "revoked": True,
        "provider": provider_name,
    }


# ---------------------------------------------------------------------------
# PR 6 — secret_audit_expiry + secret_import
# ---------------------------------------------------------------------------


def secret_audit_expiry(threshold_days: int = 60) -> dict[str, Any]:
    """Audit admin token expiry dates. Scope: ops.

    Returns list of tokens expiring within threshold_days,
    and the full list of known admin token expiry entries.
    NEVER returns token values.

    Returns:
        {
            "threshold_days": int,
            "expiring_soon": [{"provider": str, "expires_at": str, "days_remaining": int}, ...],
            "all": [{"provider": str, "expires_at": str|None, "last_alerted_at": str|None, "notes": str}, ...]
        }
    """
    db = _get_meta_db()
    all_entries = db.list_admin_expiry()
    expiring_rows = db.get_expiring_soon(days=threshold_days)

    now = datetime.datetime.now(datetime.UTC)

    expiring_soon = []
    for row in expiring_rows:
        expires_at = row["expires_at"]
        try:
            exp = datetime.datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=datetime.UTC)
            days_remaining = int((exp - now).total_seconds() // 86400)
        except (ValueError, TypeError):
            days_remaining = -1
        expiring_soon.append(
            {
                "provider": row["provider"],
                "expires_at": expires_at,
                "days_remaining": days_remaining,
            }
        )

    return {
        "threshold_days": threshold_days,
        "expiring_soon": expiring_soon,
        "all": [dict(row.items()) for row in all_entries],
    }


def secret_import(
    name: str,
    value: str,
    category: str = "C",
    expires_at: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Import an externally-managed secret (Cat-C) into the vault. Scope: admin.

    Stores the value in Keychain and records metadata in SQLite with the
    provided ``expires_at``. Useful for long-lived bootstrap tokens whose
    expiry must be tracked for the cron alert.

    Args:
        name:        Logical secret name (e.g. "stripe-live-key").
        value:       The secret value — stored in Keychain, NEVER returned.
        category:    "C" (default) for externally-managed secrets.
        expires_at:  ISO8601 UTC expiry date, or None if unknown.
        description: Human-readable description.

    Returns:
        {name, lease_id, version, issued_at, expires_at, category, keychain_account}
        NEVER includes the value.
    """
    kc = _get_keychain()
    db = _get_meta_db()

    if not value:
        return {"error": "value_required", "name": name}

    # Determine next version.
    existing = db.get_active(name)
    version = (existing["version"] + 1) if existing else 1

    # Store value in Keychain.
    account = _keychain_account(name)
    kc.set(account, value)

    # Generate lease and persist metadata.
    lease_id = _secrets_mod.token_urlsafe(24)
    issued_at = _now_iso()

    db.insert_version(
        name=name,
        category=category,
        version=version,
        lease_id=lease_id,
        issued_at=issued_at,
        expires_at=expires_at,
        description=description,
    )

    # Audit.
    _get_audit_chain().append(action="import", name=name, principal="agent")

    return {
        "name": name,
        "lease_id": lease_id,
        "version": version,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "category": category,
        "keychain_account": account,
    }


# ---------------------------------------------------------------------------
# PR 7 — secret_rotate + secret_rollback + cleanup_rotation_windows
# ---------------------------------------------------------------------------

# Lazy-once flag: cleanup_rotation_windows() is called at most once per process
# during the first rotate call.
_rotation_cleanup_done = False


def cleanup_rotation_windows() -> int:
    """Revoke old versions whose rotation grace window has expired.

    Called lazily (once per process) at the start of ``secret_rotate``.
    Can also be called explicitly for testing or maintenance.

    Scans ``secret_versions`` for rows where
    ``rotation_window_expires_at < now`` and ``revoked_at IS NULL``.
    For each, marks ``revoked_at = now`` in SQLite and removes the Keychain
    entry (best-effort — the Keychain entry may have been deleted already).

    Returns the number of versions revoked.
    """
    db = _get_meta_db()
    kc = _get_keychain()
    now = _now_iso()

    expired = db.list_expired_rotation_windows(now_iso=now)
    for row in expired:
        # Revoke in SQLite.
        db.revoke(lease_id=row["lease_id"], revoked_at=now)
        # Remove versioned Keychain entry (best-effort).
        with contextlib.suppress(Exception):
            kc.delete(_versioned_keychain_account(row["name"], row["version"]))
        # Also try legacy key for v1 (best-effort).
        if row["version"] == 1:
            with contextlib.suppress(Exception):
                kc.delete(_keychain_account(row["name"]))

    return len(expired)


def secret_rotate(
    name: str,
    window_minutes: int = 60,
    format: str = "urlsafe",
) -> dict[str, Any]:
    """Rotate a secret: create version N+1, keep N alive for window_minutes. Scope: admin.

    After ``window_minutes``, the old version N is revoked by
    ``cleanup_rotation_windows()`` (called lazily at the next rotate).

    Both versions are accessible during the window via ``secret_get`` (returns
    latest active version, i.e. N+1).

    Args:
        name: secret name.
        window_minutes: grace window before old version is revoked (default 60).
        format: token format for the new version — one of ``urlsafe`` (default) ·
            ``hex`` · ``base64`` · ``alphanumeric`` · ``numeric`` · ``uuid``.
            For ``uuid`` the generated length is fixed (36 chars).

    Returns: {name, old_version, new_version, new_lease_id, window_expires_at}
    NEVER returns values.
    """
    global _rotation_cleanup_done

    kc = _get_keychain()
    db = _get_meta_db()

    # Lazy cleanup of expired rotation windows — once per process.
    if not _rotation_cleanup_done:
        _rotation_cleanup_done = True
        with contextlib.suppress(Exception):
            cleanup_rotation_windows()

    # Resolve current active version.
    old_meta = db.get_active(name)
    if old_meta is None:
        return {"error": "not_found", "name": name}

    old_version: int = old_meta["version"]
    old_lease_id: str = old_meta["lease_id"]
    new_version: int = old_version + 1

    # Generate new secret value.
    try:
        new_value = _generate_token(format, 32)
    except ValueError as exc:
        return {"error": "unsupported_format", "name": name, "detail": str(exc)}

    # Store under versioned key in Keychain.
    kc.set(_versioned_keychain_account(name, new_version), new_value)

    # Compute grace window expiry.
    window_expires_at = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=window_minutes)).isoformat()

    # Insert new version in SQLite (category carries over from old).
    new_lease_id = _secrets_mod.token_urlsafe(24)
    issued_at = _now_iso()
    db.insert_version(
        name=name,
        category=old_meta.get("category", "A"),
        version=new_version,
        lease_id=new_lease_id,
        issued_at=issued_at,
        expires_at=None,
        description=old_meta.get("description", ""),
    )

    # Record grace window on old version so cleanup can find it later.
    db.set_rotation_window(old_lease_id, window_expires_at)

    # Audit.
    _get_audit_chain().append(action="rotate", name=name, principal="agent")

    return {
        "name": name,
        "old_version": old_version,
        "new_version": new_version,
        "new_lease_id": new_lease_id,
        "window_expires_at": window_expires_at,
    }


def secret_rollback(name: str, version: int) -> dict[str, Any]:
    """Roll back to a previous version of a secret. Scope: admin.

    Revokes the current active version (if any) and reactivates the
    specified version by clearing its ``revoked_at``.

    Returns: {name, rolled_back_to_version, lease_id, warning: str | None}
    NEVER returns values.
    """
    kc = _get_keychain()
    db = _get_meta_db()

    # Validate that the requested version exists for this name.
    target_row = db.get_by_name_version(name, version)
    if target_row is None:
        return {"error": "not_found", "name": name, "version": version}

    # Revoke the current active version (if any and different from target).
    current = db.get_active(name)
    if current is not None and current["version"] != version:
        db.revoke(lease_id=current["lease_id"], revoked_at=_now_iso())
        # Also remove the current versioned Keychain entry (best-effort).
        with contextlib.suppress(Exception):
            kc.delete(_versioned_keychain_account(name, current["version"]))

    # Reactivate target version in SQLite.
    db.reactivate_version(name, version)

    # Verify the Keychain entry exists for the target version.
    value = _read_from_keychain(name, version)
    warning: str | None = None
    if value is None:
        warning = f"keychain entry missing for {name}/v{version} — re-generate required"

    # Audit.
    _get_audit_chain().append(action="rollback", name=name, principal="agent")

    return {
        "name": name,
        "rolled_back_to_version": version,
        "lease_id": target_row["lease_id"],
        "warning": warning,
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_REGISTERED = False


def register_all() -> None:
    """Register the 12 secrets-vault tools into the module-level registry.

    Idempotent — pairs with ``agent_mcp.server.load_builtin_tools()``.
    """
    global _REGISTERED
    if _REGISTERED and "secret_generate" in TOOLS_REGISTRY:
        return

    if "secret_generate" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(secret_generate)
    if "secret_get" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(secret_get)
    if "secret_list" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(secret_list)
    if "secret_delete" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(secret_delete)
    if "secret_inject" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(secret_inject)
    if "secret_provision" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(secret_provision)
    if "secret_revoke" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(secret_revoke)
    if "secret_renew" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(secret_renew)
    if "secret_audit_expiry" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(secret_audit_expiry)
    if "secret_import" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(secret_import)
    if "secret_rotate" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(secret_rotate)
    if "secret_rollback" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(secret_rollback)

    _REGISTERED = True


def _reset_for_tests() -> None:
    """Test-only — drop the idempotence flags so ``register_all()`` re-runs."""
    global _REGISTERED, _provision_cleanup_done, _rotation_cleanup_done
    _REGISTERED = False
    _provision_cleanup_done = False
    _rotation_cleanup_done = False
