"""Cloudflare R2 (S3-compatible) wrappers — Module 6 of agent-mcp (ARCH § Module 6).

Exposes 3 typed tools to Claude Code agents :

* `r2_list_objects`     (ops)   — `s3:ListObjectsV2` against the receipts bucket.
* `r2_get_object_url`   (admin) — `s3:GetObject` presigned URL (content exposed).
* `r2_delete_object`    (admin) — `s3:DeleteObject` (mutating).

Backend choice (per ARCH § Module 6)
------------------------------------
Cloudflare R2 exposes an S3-compatible API at
``https://<account_id>.r2.cloudflarestorage.com``. We hit it through `boto3`'s
S3 client with SigV4 — same auth + same wire protocol as AWS S3, just a
different endpoint. No `cloudflare`-specific SDK is needed (and none is
maintained for R2 anyway).

Why this matters for receipts debugging
---------------------------------------
Receipts images are stored 48h on R2 (RGPD § ARCH_RATIS), then purged. When
an OCR pipeline goes wrong, the operator may need to :

1. List recent objects (`r2_list_objects`) to find the right key.
2. Generate a short-lived URL (`r2_get_object_url`) to inspect the image
   without exposing R2 credentials to the user.
3. Delete a stale object (`r2_delete_object`) when re-uploading after a fix.

Token discipline (security-critical, DA-43)
-------------------------------------------
* THREE secrets are read fresh from `Keychain` on every call :
  - `r2-access-key-id`     — AWS_ACCESS_KEY_ID equivalent.
  - `r2-secret-access-key` — AWS_SECRET_ACCESS_KEY equivalent.
  - `r2-endpoint-url`      — the full Cloudflare R2 endpoint URL.
* The access-key-id NECESSARILY appears in presigned URLs (SigV4 invariant
  embeds it as `X-Amz-Credential`). The presigned URL is the WHOLE POINT
  of `r2_get_object_url` — caller gets it, but it is NEVER logged to the
  audit JSONL (the dispatcher logs args + status, not the return value).
* The SECRET key is never in argv, never in returned dicts, never in audit
  entries. Cross-tool sweep tests verify this exhaustively.
* `KeychainMiss` propagates verbatim so the dispatcher tags audit
  `keychain_miss` and the operator knows which keychain entry to populate.

Endpoint URL : Keychain (not env)
---------------------------------
We chose to store the R2 endpoint URL in the Keychain alongside the access
key + secret rather than in an env var. Rationale :

* The endpoint URL embeds the Cloudflare account ID — moderately sensitive
  (combined with stolen credentials it identifies the target account).
* Keeping all R2 config in one place (Keychain entries with prefix `r2-`)
  makes setup + rotation simpler : `agent-mcp keychain set r2-*`.
* The set of secrets per-call is small (3 reads × 60s positive cache = at
  most one cold lookup per minute per key).

Bucket : env override
---------------------
Default bucket is `ratis-receipts-prod` (matches the production R2 bucket
referenced by the `R2_BUCKET_NAME` slot in `.env.example`).
Operator can override per-process via `RATIS_R2_BUCKET` env var. The bucket
is NOT in the Keychain because :
* It's not a secret.
* Bucket names appear in audit logs (legitimately — debugging requires
  knowing which bucket a tool touched).

TTL clamping
------------
Presigned URL TTLs are clamped to `[1, 7*24*3600]` (S3 SigV4 spec maximum
is 7 days). Sub-second TTLs are silently floored to 1s, multi-week TTLs
to 7 days. Defensive : keeps agents from accidentally generating a URL
that lasts a month.

Limit clamping
--------------
`list_objects` `limit` is clamped to `[1, 1000]` (S3 list-objects-v2 caps
`MaxKeys` at 1000 per page). Pagination is deliberately NOT exposed — V0
debugging never needs more than one page, and a non-paginated interface
keeps the tool footprint small.

Scopes (DA-44)
--------------
* `r2_list_objects` is `ops` — listing object metadata is read-only and
  non-sensitive (keys are not PII, sizes / timestamps are debug-only).
* `r2_get_object_url` is `admin` — the URL exposes file contents (receipt
  images contain receipts data, RGPD-class info). Admin token required.
* `r2_delete_object` is `admin` — mutating. Auth gate enforces BEFORE the
  tool body runs (so an ops caller can't even spawn the boto3 call).

References
----------
* ARCH_agent_mcp.md § Module 6 (signatures + scopes)
* DA-43 (Keychain), DA-44 (scopes), DA-48 (audit), DA-49 (typed Python tools)
* Cloudflare R2 docs : https://developers.cloudflare.com/r2/api/s3/
* boto3 S3 client : https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from ..errors import KeychainMiss, ProviderError
from ..keychain import Keychain
from ..server import TOOLS_REGISTRY, register_tool

KEYCHAIN_ACCESS_KEY = "r2-access-key-id"
"""Account name in macOS Keychain for the R2 access key id."""

KEYCHAIN_SECRET_KEY = "r2-secret-access-key"  # noqa: S105 — Keychain account name, not a password value.  # pragma: allowlist secret
"""Account name in macOS Keychain for the R2 secret access key."""

KEYCHAIN_ENDPOINT = "r2-endpoint-url"
"""Account name in macOS Keychain for the R2 endpoint URL.

Format : ``https://<account_id>.r2.cloudflarestorage.com``. We accept the
full URL (with scheme) rather than just the account id to keep the
configuration explicit — different deployments could in theory use a
custom domain in front of R2.
"""

DEFAULT_BUCKET = "ratis-receipts-prod"
"""Default R2 bucket — matches the production R2 bucket referenced by the
`R2_BUCKET_NAME` slot in `.env.example`."""

BUCKET_ENV = "RATIS_R2_BUCKET"
"""Env var to override `DEFAULT_BUCKET` — non-secret, audit-friendly."""

S3_LIST_LIMIT_MAX = 1000
"""S3 `list_objects_v2` caps `MaxKeys` at 1000 per page."""

PRESIGNED_TTL_MIN = 1
"""Floor for presigned URL TTL — boto3 rejects 0 outright."""

PRESIGNED_TTL_MAX = 7 * 24 * 3600
"""Ceiling for presigned URL TTL — S3 SigV4 spec maximum (7 days)."""

# R2's SigV4 region — boto3 requires SOMETHING valid here. R2 ignores it
# (it has no real notion of regions), but the signing process needs it.
# `auto` is the convention from Cloudflare's own docs.
R2_REGION = "auto"


# ---- internal helpers ---------------------------------------------------


def _fetch_credentials() -> tuple[str, str, str]:
    """Read the three R2 secrets from the macOS Keychain.

    Each `Keychain.get` may raise `KeychainMiss` — we re-raise with the
    account name embedded so the operator sees exactly which Keychain
    entry to populate (the underlying Keychain message can be terse,
    e.g. "not found" without context).

    Returns ``(access_key_id, secret_access_key, endpoint_url)``.
    """
    keychain = Keychain()
    for account in (KEYCHAIN_ACCESS_KEY, KEYCHAIN_SECRET_KEY, KEYCHAIN_ENDPOINT):
        try:
            keychain.get(account)
        except KeychainMiss as exc:
            # Enrich the error with the missing account name.
            raise KeychainMiss(
                f"R2 keychain entry '{account}' is missing — run `agent-mcp keychain set {account}`"
            ) from exc
    # Re-fetch (cached) — split from the loop so static checkers see the bindings.
    access_key = keychain.get(KEYCHAIN_ACCESS_KEY)
    secret_key = keychain.get(KEYCHAIN_SECRET_KEY)
    endpoint = keychain.get(KEYCHAIN_ENDPOINT)
    return access_key, secret_key, endpoint


def _resolve_bucket() -> str:
    """Pick the R2 bucket — env override or default `ratis-receipts-prod`.

    Read at call time (not at import) so a test or operator can flip the
    env var between calls without restarting the MCP process.
    """
    return os.environ.get(BUCKET_ENV, DEFAULT_BUCKET)


def _build_client() -> Any:
    """Construct a boto3 S3 client wired to the R2 endpoint.

    Test injection : `moto`'s `mock_aws` patches `boto3.client` at import
    boundary so this function returns a moto-stubbed client transparently
    inside `with mock_aws(): ...` blocks. We do NOT need a separate test
    seam — moto IS the seam.

    `signature_version="s3v4"` is mandatory : R2 only accepts SigV4
    (SigV2 is unsupported), and boto3 occasionally defaults to V2 for
    custom endpoints depending on the runtime.
    """
    access_key, secret_key, endpoint = _fetch_credentials()
    return boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        endpoint_url=endpoint,
        region_name=R2_REGION,
        config=BotoConfig(signature_version="s3v4"),
    )


def _clamp_limit(limit: int) -> int:
    """Clamp `limit` to `[1, S3_LIST_LIMIT_MAX]`.

    Defensive : agents may pass nonsensical values ; a 0 would be silently
    "no results" from boto and a 5000 would 400 from S3 — better to clamp.
    """
    if limit < 1:
        return 1
    if limit > S3_LIST_LIMIT_MAX:
        return S3_LIST_LIMIT_MAX
    return limit


def _clamp_ttl(ttl_seconds: int) -> int:
    """Clamp presigned URL TTL to `[1, 7 days]`."""
    if ttl_seconds < PRESIGNED_TTL_MIN:
        return PRESIGNED_TTL_MIN
    if ttl_seconds > PRESIGNED_TTL_MAX:
        return PRESIGNED_TTL_MAX
    return ttl_seconds


def _wrap_boto_errors(context: str, exc: Exception) -> ProviderError:
    """Convert a boto3 / botocore exception into a `ProviderError` with a
    stable shape — never includes the secret credentials.

    `ClientError` carries a structured `response["Error"]` dict with
    `Code` + `Message` ; we surface those + the operation context.
    Generic `BotoCoreError` (network failure, invalid signature setup,
    ...) gets a sanitized string. The exception's repr can sometimes
    include the endpoint URL ; that's not a secret per se but we trim
    aggressively for caller-facing messages.
    """
    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {}) if hasattr(exc, "response") else {}
        code = err.get("Code", "UnknownError")
        message = err.get("Message", str(exc))
        return ProviderError(f"r2 {context} failed: {code} — {message}")
    return ProviderError(f"r2 {context} failed: {type(exc).__name__}")


# ---- tool implementations -----------------------------------------------


def r2_list_objects(prefix: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """List objects in the R2 bucket (key, size, last_modified). Read-only. Scope: ops.

    Args :
        prefix : optional key prefix filter (e.g. ``"receipts/"``). Empty
                 string lists everything (up to `limit`).
        limit  : max objects returned. Clamped to ``[1, 1000]``.

    Returns a list of dicts ``{"key": str, "size": int, "last_modified": str}``.
    `last_modified` is serialized as ISO 8601 for JSON-friendliness.
    """
    bucket = _resolve_bucket()
    client = _build_client()

    try:
        response = client.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix,
            MaxKeys=_clamp_limit(limit),
        )
    except (ClientError, BotoCoreError) as exc:
        raise _wrap_boto_errors("list_objects", exc) from exc

    contents = response.get("Contents", [])
    return [
        {
            "key": obj["Key"],
            "size": obj["Size"],
            # `LastModified` is a `datetime` from boto — make it JSON-safe.
            "last_modified": _serialize_last_modified(obj.get("LastModified")),
        }
        for obj in contents
    ]


def _serialize_last_modified(value: Any) -> str:
    """Coerce a boto `LastModified` field to an ISO 8601 string.

    `boto3` returns a `datetime` ; if the field is missing or unparseable
    (extreme edge), fall back to a string repr so the output remains JSON-safe.
    """
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if value is not None else ""


def r2_get_object_url(key: str, ttl_seconds: int = 600) -> dict[str, Any]:
    """Generate a presigned URL valid for ttl_seconds. Read-only on bucket but
    generates a URL that exposes the object contents — handle with care. Scope: admin.

    Args :
        key         : object key in the R2 bucket (e.g. ``"receipts/abc.jpg"``).
        ttl_seconds : presigned URL validity in seconds. Clamped to
                      ``[1, 7*24*3600]`` (S3 SigV4 spec maximum).

    Returns ``{"key": str, "ttl_seconds": int, "url": str}``. The URL embeds
    the access-key-id (SigV4 invariant) so the audit log MUST not include
    it — the dispatcher logs args + status only, NOT the return value.
    """
    bucket = _resolve_bucket()
    clamped_ttl = _clamp_ttl(ttl_seconds)
    client = _build_client()

    try:
        url: str = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=clamped_ttl,
        )
    except (ClientError, BotoCoreError) as exc:
        raise _wrap_boto_errors("get_object_url", exc) from exc

    return {
        "key": key,
        "ttl_seconds": clamped_ttl,
        "url": url,
    }


def r2_delete_object(key: str) -> dict[str, Any]:
    """Delete a specific object. Mutating. Scope: admin.

    Args :
        key : object key in the R2 bucket.

    Returns ``{"key": str, "deleted": True}``. S3 `delete_object` is
    idempotent — deleting a non-existent key is NOT an error and returns
    ``deleted=True`` (matching S3 semantics : the post-condition holds).
    """
    bucket = _resolve_bucket()
    client = _build_client()

    try:
        client.delete_object(Bucket=bucket, Key=key)
    except (ClientError, BotoCoreError) as exc:
        raise _wrap_boto_errors("delete_object", exc) from exc

    return {"key": key, "deleted": True}


# ---- registration -------------------------------------------------------

# Imperative registration — mirrors `glitchtip_tools` / `github_tools` /
# `eas_tools` / `stripe_tools`. The autouse
# `reset_tools_registry` test fixture clears the registry, so we
# re-populate deterministically.

_REGISTERED = False


def register_all() -> None:
    """Register the 3 R2 tools into the module-level registry.

    Per ARCH § Module 6 :
    * `r2_list_objects` (read-only metadata) → ops scope.
    * `r2_get_object_url` (presigned URL exposes contents) → admin scope.
    * `r2_delete_object` (mutating) → admin scope.

    Idempotent — subsequent calls are no-ops, so importing this module from
    multiple places (CLI bootstrap, tests, future docs generators) is safe.
    """
    global _REGISTERED
    if _REGISTERED and "r2_list_objects" in TOOLS_REGISTRY:
        return

    if "r2_list_objects" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(r2_list_objects)
    if "r2_get_object_url" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(r2_get_object_url)
    if "r2_delete_object" not in TOOLS_REGISTRY:
        register_tool(scope="admin")(r2_delete_object)

    _REGISTERED = True


def _reset_for_tests() -> None:
    """Test-only — drop the idempotence flag so `register_all()` re-runs."""
    global _REGISTERED
    _REGISTERED = False
