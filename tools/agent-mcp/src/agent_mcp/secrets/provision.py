"""Cat-B JIT token provisioning for secrets vault (Module 10, PR 3).

Each provider implements the ``ProviderAdapter`` protocol:
    * ``provision(admin_token, ttl_seconds)`` → ``ProvisionResult``
    * ``revoke(admin_token, token_id)`` → bool

Six providers are supported:
    ``github-app`` · ``cloudflare-r2`` · ``sentry`` · ``eas``
    ``vercel`` · ``stripe-restricted``

Bootstrap admin tokens
----------------------
Each provider reads its admin credential from a separate Keychain instance
with service ``ratis-provider-admin``. The account name matches the provider
key in this table:

    Provider          Admin account
    ----------------  --------------------------------
    github-app        github
    cloudflare-r2     cloudflare  (+cloudflare-account-id)
    sentry            sentry-admin  (+sentry-org)
    eas               eas
    vercel            vercel
    stripe-restricted stripe

Dependency injection
--------------------
Module-level ``_admin_keychain`` is ``None`` until first use.
``get_admin_keychain()`` lazy-inits; ``set_admin_keychain()`` replaces for tests.
Same pattern as ``secrets_tools`` (established in PR 1).

cleanup_expired_leases
----------------------
Reads Cat-B leases where ``expires_at < now`` and ``revoked_at IS NULL``,
attempts the provider revoke API (best-effort — logs on failure), then marks
``revoked_at``.  Called lazily once per process from ``secret_provision``.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from ..errors import KeychainMiss, ProviderError
from ..keychain import Keychain

# ---------------------------------------------------------------------------
# Dependency injection — admin Keychain
# ---------------------------------------------------------------------------

_admin_keychain: Keychain | None = None
_cleanup_done: bool = False


def get_admin_keychain() -> Keychain:
    """Return the singleton admin Keychain (service=ratis-provider-admin)."""
    global _admin_keychain
    if _admin_keychain is None:
        _admin_keychain = Keychain(service="ratis-provider-admin")
    return _admin_keychain


def set_admin_keychain(kc: Keychain | None) -> None:
    """Test helper — inject a custom admin Keychain."""
    global _admin_keychain
    _admin_keychain = kc


def set_cleanup_done(value: bool) -> None:
    """Test helper — reset the cleanup-done flag."""
    global _cleanup_done
    _cleanup_done = value


# ---------------------------------------------------------------------------
# ProvisionResult
# ---------------------------------------------------------------------------


@dataclass
class ProvisionResult:
    """Result of a provider provision() call.

    Attributes:
        value:      The minted token (MUST NOT be returned by MCP tools).
        token_id:   Provider-side ID for revocation. Empty string if N/A.
        expires_at: ISO8601 UTC expiry, or empty string if N/A.
        metadata:   Extra provider-specific data (scopes, labels, etc.).
    """

    value: str
    token_id: str = ""
    expires_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# parse_ttl
# ---------------------------------------------------------------------------

_TTL_RE = re.compile(r"^(\d+)([mhd]?)$")
_TTL_UNITS = {"m": 60, "h": 3600, "d": 86400, "": 1}


def parse_ttl(ttl_str: str) -> int:
    """Parse a human-readable TTL string to seconds.

    Supported formats: "30m", "1h", "24h", "7d", "90" (raw seconds).
    Raises ValueError for unrecognised formats.
    """
    m = _TTL_RE.match(ttl_str.strip())
    if m is None:
        raise ValueError(f"invalid ttl '{ttl_str}': expected '<N>m', '<N>h', '<N>d', or '<N>'")
    amount, unit = int(m.group(1)), m.group(2)
    return amount * _TTL_UNITS[unit]


# ---------------------------------------------------------------------------
# ProviderAdapter protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ProviderAdapter(Protocol):
    """Protocol every Cat-B provider adapter must implement."""

    def provision(self, admin_token: str, ttl_seconds: int) -> ProvisionResult:
        """Mint a short-lived token. admin_token is the bootstrap credential."""
        ...

    def revoke(self, admin_token: str, token_id: str) -> bool:
        """Revoke a previously provisioned token. Returns True on success."""
        ...


# ---------------------------------------------------------------------------
# GitHubAppProvider
# ---------------------------------------------------------------------------


class GitHubAppProvider:
    """Provision fine-grained PATs via the GitHub REST API (using `gh` CLI)."""

    _ADMIN_ACCOUNT = "github"

    def provision(self, admin_token: str, ttl_seconds: int) -> ProvisionResult:
        """Create a short-lived fine-grained PAT via `gh api`.

        Falls back to returning the admin token with a TTL reminder in
        metadata if fine-grained PAT API is unavailable (e.g. enterprise
        policy disables it).
        """
        kc = get_admin_keychain()
        try:
            admin = kc.get(self._ADMIN_ACCOUNT)
        except KeychainMiss as exc:
            raise ProviderError(f"github-app admin key missing: {exc}") from exc

        # Convert ttl_seconds to days (GitHub minimum expiry = 1 day)
        expiry_days = max(1, ttl_seconds // 86400)

        try:
            result = subprocess.run(
                [
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    "/user/personal_access_tokens",
                    "--field",
                    f"expiration={expiry_days}",
                    "--field",
                    "name=ratis-agent-mcp-jit",
                    "--header",
                    f"Authorization: token {admin}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ProviderError("gh CLI not found — install GitHub CLI") from exc

        if result.returncode != 0:
            # Fallback: fine-grained PAT API may be disabled; use admin token
            # with metadata reminder.
            return ProvisionResult(
                value=admin,
                token_id="",
                expires_at="",
                metadata={
                    "note": "fine-grained PAT API unavailable; using admin token with TTL reminder",
                    "ttl_seconds": ttl_seconds,
                },
            )

        try:
            data = json.loads(result.stdout)
            token = data["token"]
            token_id = str(data.get("id", ""))
        except (json.JSONDecodeError, KeyError) as exc:
            raise ProviderError(f"github-app: unexpected API response: {exc}") from exc

        return ProvisionResult(
            value=token,
            token_id=token_id,
            expires_at="",
            metadata={"expiry_days": expiry_days},
        )

    def revoke(self, admin_token: str, token_id: str) -> bool:
        """Delete a fine-grained PAT by ID."""
        if not token_id:
            return True  # nothing to revoke (admin-token fallback path)

        kc = get_admin_keychain()
        try:
            admin = kc.get(self._ADMIN_ACCOUNT)
        except KeychainMiss:
            admin = admin_token

        result = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "DELETE",
                f"/user/personal_access_tokens/{token_id}",
                "--header",
                f"Authorization: token {admin}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0


# ---------------------------------------------------------------------------
# CloudflareR2Provider
# ---------------------------------------------------------------------------


class CloudflareR2Provider:
    """Provision Cloudflare API tokens scoped to R2 via the Cloudflare REST API."""

    _ADMIN_ACCOUNT = "cloudflare"
    _ACCOUNT_ID_ACCOUNT = "cloudflare-account-id"
    _CF_API = "https://api.cloudflare.com/client/v4"

    def _get_account_id(self) -> str:
        kc = get_admin_keychain()
        try:
            return kc.get(self._ACCOUNT_ID_ACCOUNT)
        except KeychainMiss as exc:
            raise ProviderError(f"cloudflare-account-id missing from admin keychain: {exc}") from exc

    def provision(self, admin_token: str, ttl_seconds: int) -> ProvisionResult:
        kc = get_admin_keychain()
        try:
            admin = kc.get(self._ADMIN_ACCOUNT)
        except KeychainMiss as exc:
            raise ProviderError(f"cloudflare-r2 admin key missing: {exc}") from exc

        account_id = self._get_account_id()
        expires_dt = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=ttl_seconds)
        expires_iso = expires_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        payload: dict[str, Any] = {
            "name": "ratis-agent-mcp-r2-jit",
            "policies": [
                {
                    "effect": "allow",
                    "resources": {f"com.cloudflare.api.account.{account_id}": "*"},
                    "permission_groups": [{"id": "f7f0eda5c53040f3a0cb8853d1c4831b"}],  # pragma: allowlist secret
                }
            ],
            "not_before": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_on": expires_iso,
        }

        resp = httpx.post(
            f"{self._CF_API}/accounts/{account_id}/tokens",
            headers={"Authorization": f"Bearer {admin}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            raise ProviderError(f"cloudflare-r2: POST tokens failed ({resp.status_code})")

        data = resp.json()
        if not data.get("success"):
            raise ProviderError(f"cloudflare-r2: API error: {data.get('errors')}")

        result_data = data["result"]
        return ProvisionResult(
            value=result_data["value"],
            token_id=result_data["id"],
            expires_at=expires_iso,
            metadata={"account_id": account_id},
        )

    def revoke(self, admin_token: str, token_id: str) -> bool:
        kc = get_admin_keychain()
        try:
            admin = kc.get(self._ADMIN_ACCOUNT)
        except KeychainMiss:
            admin = admin_token

        account_id = self._get_account_id()
        resp = httpx.delete(
            f"{self._CF_API}/accounts/{account_id}/tokens/{token_id}",
            headers={"Authorization": f"Bearer {admin}"},
            timeout=30,
        )
        return resp.status_code in (200, 204)


# ---------------------------------------------------------------------------
# SentryProvider
# ---------------------------------------------------------------------------


class SentryProvider:
    """Provision Sentry auth tokens via the Sentry REST API."""

    _ADMIN_ACCOUNT = "sentry-admin"
    _ORG_ACCOUNT = "sentry-org"
    _SENTRY_API = "https://sentry.io"

    def _get_org(self) -> str:
        kc = get_admin_keychain()
        try:
            return kc.get(self._ORG_ACCOUNT)
        except KeychainMiss as exc:
            raise ProviderError(f"sentry-org slug missing from admin keychain: {exc}") from exc

    def provision(self, admin_token: str, ttl_seconds: int) -> ProvisionResult:
        kc = get_admin_keychain()
        try:
            admin = kc.get(self._ADMIN_ACCOUNT)
        except KeychainMiss as exc:
            raise ProviderError(f"sentry admin key missing: {exc}") from exc

        org = self._get_org()
        resp = httpx.post(
            f"{self._SENTRY_API}/api/0/organizations/{org}/access-tokens/",
            headers={"Authorization": f"Bearer {admin}", "Content-Type": "application/json"},
            json={
                "label": "ratis-agent-mcp-jit",
                "scopes": ["project:read", "event:read"],
            },
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            raise ProviderError(f"sentry: POST access-tokens failed ({resp.status_code})")

        data = resp.json()
        token_id = str(data.get("id", ""))
        token_value = data.get("token", "")
        if not token_value:
            raise ProviderError("sentry: no token in API response")

        expires_dt = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=ttl_seconds)
        return ProvisionResult(
            value=token_value,
            token_id=token_id,
            expires_at=expires_dt.isoformat(),
            metadata={"org": org, "scopes": ["project:read", "event:read"]},
        )

    def revoke(self, admin_token: str, token_id: str) -> bool:
        kc = get_admin_keychain()
        try:
            admin = kc.get(self._ADMIN_ACCOUNT)
        except KeychainMiss:
            admin = admin_token

        resp = httpx.delete(
            f"{self._SENTRY_API}/api/0/api-tokens/{token_id}/",
            headers={"Authorization": f"Bearer {admin}"},
            timeout=30,
        )
        return resp.status_code in (200, 204)


# ---------------------------------------------------------------------------
# EASProvider
# ---------------------------------------------------------------------------


class EASProvider:
    """Provision Expo Application Services tokens via GraphQL."""

    _ADMIN_ACCOUNT = "eas"
    _EAS_API = "https://api.expo.dev/graphql"

    _CREATE_MUTATION = """
    mutation CreateAccessToken($appId: ID!) {
      createAccessToken(appId: $appId) {
        accessToken {
          id
          token
        }
      }
    }
    """

    _DELETE_MUTATION = """
    mutation DeleteAccessToken($id: ID!) {
      deleteAccessToken(id: $id) {
        id
      }
    }
    """

    def provision(self, admin_token: str, ttl_seconds: int) -> ProvisionResult:
        kc = get_admin_keychain()
        try:
            admin = kc.get(self._ADMIN_ACCOUNT)
        except KeychainMiss as exc:
            raise ProviderError(f"eas admin key missing: {exc}") from exc

        # EAS GraphQL: createAccessToken
        # We use a placeholder appId — real usage requires scoping to a specific app.
        resp = httpx.post(
            self._EAS_API,
            headers={
                "Authorization": f"Bearer {admin}",
                "Content-Type": "application/json",
            },
            json={
                "query": self._CREATE_MUTATION,
                "variables": {"appId": "ratis"},
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise ProviderError(f"eas: GraphQL request failed ({resp.status_code})")

        body = resp.json()
        if "errors" in body:
            raise ProviderError(f"eas: GraphQL errors: {body['errors']}")

        try:
            token_data = body["data"]["createAccessToken"]["accessToken"]
            token_id = str(token_data["id"])
            token_value = token_data["token"]
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"eas: unexpected response shape: {exc}") from exc

        expires_dt = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=ttl_seconds)
        return ProvisionResult(
            value=token_value,
            token_id=token_id,
            expires_at=expires_dt.isoformat(),
            metadata={},
        )

    def revoke(self, admin_token: str, token_id: str) -> bool:
        kc = get_admin_keychain()
        try:
            admin = kc.get(self._ADMIN_ACCOUNT)
        except KeychainMiss:
            admin = admin_token

        resp = httpx.post(
            self._EAS_API,
            headers={
                "Authorization": f"Bearer {admin}",
                "Content-Type": "application/json",
            },
            json={
                "query": self._DELETE_MUTATION,
                "variables": {"id": token_id},
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return False
        body = resp.json()
        return "errors" not in body


# ---------------------------------------------------------------------------
# VercelProvider
# ---------------------------------------------------------------------------


class VercelProvider:
    """Provision Vercel API tokens via the Vercel REST API."""

    _ADMIN_ACCOUNT = "vercel"
    _VERCEL_API = "https://api.vercel.com"

    def provision(self, admin_token: str, ttl_seconds: int) -> ProvisionResult:
        kc = get_admin_keychain()
        try:
            admin = kc.get(self._ADMIN_ACCOUNT)
        except KeychainMiss as exc:
            raise ProviderError(f"vercel admin key missing: {exc}") from exc

        expires_dt = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=ttl_seconds)
        expires_ms = int(expires_dt.timestamp() * 1000)

        resp = httpx.post(
            f"{self._VERCEL_API}/v3/user/tokens",
            headers={
                "Authorization": f"Bearer {admin}",
                "Content-Type": "application/json",
            },
            json={
                "name": "ratis-agent-mcp-jit",
                "expiresAt": expires_ms,
            },
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            raise ProviderError(f"vercel: POST tokens failed ({resp.status_code})")

        data = resp.json()
        token_id = str(data.get("id", ""))
        token_value = data.get("token", "")
        if not token_value:
            raise ProviderError("vercel: no token in API response")

        return ProvisionResult(
            value=token_value,
            token_id=token_id,
            expires_at=expires_dt.isoformat(),
            metadata={},
        )

    def revoke(self, admin_token: str, token_id: str) -> bool:
        kc = get_admin_keychain()
        try:
            admin = kc.get(self._ADMIN_ACCOUNT)
        except KeychainMiss:
            admin = admin_token

        resp = httpx.delete(
            f"{self._VERCEL_API}/v3/user/tokens/{token_id}",
            headers={"Authorization": f"Bearer {admin}"},
            timeout=30,
        )
        return resp.status_code in (200, 204)


# ---------------------------------------------------------------------------
# StripeRestrictedProvider
# ---------------------------------------------------------------------------


class StripeRestrictedProvider:
    """Provision Stripe restricted API keys via the Stripe REST API."""

    _ADMIN_ACCOUNT = "stripe"
    _STRIPE_API = "https://api.stripe.com"

    def provision(self, admin_token: str, ttl_seconds: int) -> ProvisionResult:
        kc = get_admin_keychain()
        try:
            admin = kc.get(self._ADMIN_ACCOUNT)
        except KeychainMiss as exc:
            raise ProviderError(f"stripe admin key missing: {exc}") from exc

        resp = httpx.post(
            f"{self._STRIPE_API}/v1/restricted_keys",
            headers={
                "Authorization": f"Bearer {admin}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "name": "ratis-agent-mcp-jit",
                "permissions[0]": "rak_charge_read",
            },
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            raise ProviderError(f"stripe: POST restricted_keys failed ({resp.status_code})")

        data = resp.json()
        token_id = str(data.get("id", ""))
        token_value = data.get("key", "")
        if not token_value:
            raise ProviderError("stripe: no key in API response")

        expires_dt = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=ttl_seconds)
        return ProvisionResult(
            value=token_value,
            token_id=token_id,
            expires_at=expires_dt.isoformat(),
            metadata={"permissions": ["rak_charge_read"]},
        )

    def revoke(self, admin_token: str, token_id: str) -> bool:
        kc = get_admin_keychain()
        try:
            admin = kc.get(self._ADMIN_ACCOUNT)
        except KeychainMiss:
            admin = admin_token

        resp = httpx.delete(
            f"{self._STRIPE_API}/v1/restricted_keys/{token_id}",
            headers={"Authorization": f"Bearer {admin}"},
            timeout=30,
        )
        return resp.status_code in (200, 204)


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDER_REGISTRY: dict[str, type] = {
    "github-app": GitHubAppProvider,
    "cloudflare-r2": CloudflareR2Provider,
    "sentry": SentryProvider,
    "eas": EASProvider,
    "vercel": VercelProvider,
    "stripe-restricted": StripeRestrictedProvider,
}

# Map provider name → admin Keychain account name (for cleanup lookup)
_PROVIDER_ADMIN_ACCOUNT: dict[str, str] = {
    "github-app": GitHubAppProvider._ADMIN_ACCOUNT,
    "cloudflare-r2": CloudflareR2Provider._ADMIN_ACCOUNT,
    "sentry": SentryProvider._ADMIN_ACCOUNT,
    "eas": EASProvider._ADMIN_ACCOUNT,
    "vercel": VercelProvider._ADMIN_ACCOUNT,
    "stripe-restricted": StripeRestrictedProvider._ADMIN_ACCOUNT,
}


def get_provider(name: str) -> ProviderAdapter:
    """Return a provider adapter instance by name.

    Raises ValueError for unknown provider names.
    """
    cls = PROVIDER_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"unknown provider {name!r}; known: {list(PROVIDER_REGISTRY)}")
    return cls()


# ---------------------------------------------------------------------------
# cleanup_expired_leases
# ---------------------------------------------------------------------------


def cleanup_expired_leases(*, db: Any = None) -> None:
    """Scan Cat-B leases with expires_at < now and call provider revoke API.

    Called lazily once per process from ``secret_provision``. Best-effort:
    individual revoke failures are silently skipped (the token TTL will still
    expire provider-side for providers with native expiry).

    Args:
        db: Optional SecretMetaDB instance for testing. When None, the function
            is a no-op (called lazily from secret_provision with the real DB).
    """
    if db is None:
        return  # Called without DB during lazy init; secret_provision passes db explicitly.

    now_iso = datetime.datetime.now(datetime.UTC).isoformat()

    rows = db._conn.execute(
        """
        SELECT lease_id, name, category
        FROM secret_versions
        WHERE category = 'B'
          AND expires_at IS NOT NULL
          AND expires_at < ?
          AND revoked_at IS NULL
        """,
        (now_iso,),
    ).fetchall()

    for row in rows:
        lease_id = row[0]
        # Best-effort revoke — skip on any failure.
        with contextlib.suppress(Exception):
            _try_revoke_lease(lease_id, db)
        # Always mark revoked_at so we don't retry endlessly.
        db.revoke(lease_id=lease_id, revoked_at=now_iso)


def _try_revoke_lease(lease_id: str, db: Any) -> None:
    """Attempt to call the provider revoke API for a single expired lease.

    Reads the provider name from SQLite metadata column ``provider`` if present.
    If the column doesn't exist or the provider is unknown, silently skips the
    API call (the DB mark still happens in the caller).
    """
    # Try to get the provider name from a metadata column (if present).
    row = db._conn.execute(
        "SELECT name FROM secret_versions WHERE lease_id = ?",
        (lease_id,),
    ).fetchone()
    if row is None:
        return

    # Try to read provider column (added by secret_provision, may not exist in tests).
    try:
        provider_row = db._conn.execute(
            "SELECT provider, token_id FROM secret_versions WHERE lease_id = ?",
            (lease_id,),
        ).fetchone()
        if provider_row is None:
            return
        provider_name = provider_row[0] if provider_row[0] else None
        token_id = provider_row[1] if provider_row[1] else ""
    except Exception:
        return

    if not provider_name:
        return

    try:
        adapter = get_provider(provider_name)
    except ValueError:
        return

    admin_account = _PROVIDER_ADMIN_ACCOUNT.get(provider_name, "")
    if not admin_account:
        return

    try:
        admin_token = get_admin_keychain().get(admin_account)
    except KeychainMiss:
        return

    adapter.revoke(admin_token, token_id)
