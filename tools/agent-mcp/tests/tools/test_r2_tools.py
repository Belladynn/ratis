"""TDD coverage for `agent_mcp.tools.r2_tools`.

Strategy
--------
* The 3 R2 tools wrap `boto3` calls against Cloudflare's S3-compatible API.
* We use `moto`'s `mock_aws` decorator/context to stub the entire S3 backend
  in-memory — no network at all, deterministic. The R2 wrapper code itself
  builds a `boto3` S3 client with a custom endpoint URL ; under `mock_aws`
  the moto patch intercepts before the endpoint is even read, so we don't
  need a real R2 endpoint to test against.
* `Keychain.get` is monkeypatched to return fake AWS-shaped credentials.
* Audit assertions go through the `Dispatcher` so we cover the full
  registration + dispatch + audit pipeline (the same code path Claude will
  exercise at runtime).

Presigned-URL audit redaction (security-critical)
-------------------------------------------------
`r2_get_object_url` returns a presigned URL that EMBEDS the access_key_id
in the query string (`X-Amz-Credential=...`). The URL is the WHOLE POINT
of the tool — admin can hand it to the user briefly. But it MUST NOT land
in the audit log : audit lines store the redacted args + status only,
NOT the return value. Test asserts no `X-Amz-` and no fake access-key-id
substring in the audit JSONL after a successful call.

Bucket override
---------------
Default bucket is `ratis-receipts-prod` (production R2 bucket referenced by
`R2_BUCKET_NAME` in `.env.example`). Override via `RATIS_R2_BUCKET`
env var — tests exercise both paths.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from agent_mcp import keychain as keychain_mod
from agent_mcp.audit import AuditLog
from agent_mcp.auth import AuthGate
from agent_mcp.errors import KeychainMiss, ProviderError
from agent_mcp.server import Dispatcher
from agent_mcp.tools import r2_tools
from moto import mock_aws

FAKE_ACCESS_KEY = "AKIA_FAKE_DO_NOT_LEAK"  # pragma: allowlist secret
FAKE_SECRET_KEY = "FAKE_SECRET_DO_NOT_LEAK"  # pragma: allowlist secret
FAKE_ENDPOINT_URL = "https://fakeaccount.r2.cloudflarestorage.com"
FAKE_BUCKET = "ratis-receipts-prod"


# -- shared fixtures ------------------------------------------------------


@pytest.fixture
def fake_credentials(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Patch `Keychain.get` so the R2 tools see fake credentials.

    Returns the dict so tests can reference the exact strings (e.g. for
    leak assertions).
    """
    creds = {
        "r2-access-key-id": FAKE_ACCESS_KEY,
        "r2-secret-access-key": FAKE_SECRET_KEY,
        "r2-endpoint-url": FAKE_ENDPOINT_URL,
    }

    def _fake_get(self: keychain_mod.Keychain, account: str) -> str:
        if account not in creds:
            raise KeychainMiss(f"unexpected keychain account {account!r}")
        return creds[account]

    monkeypatch.setattr(keychain_mod.Keychain, "get", _fake_get)
    return creds


@pytest.fixture
def mocked_s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Spin up moto's in-memory S3 backend for the duration of the test.

    Yields a real boto3 S3 client wired against moto so the test can
    pre-populate the bucket with fixtures.

    moto v5's `mock_aws` only intercepts requests to standard AWS
    endpoints — calls with a custom `endpoint_url` like Cloudflare R2's
    `https://*.r2.cloudflarestorage.com` would attempt real network and
    SSL-fail. We therefore monkeypatch `r2_tools._build_client` to drop
    the custom endpoint AND use SigV4 against the moto-stubbed AWS region.
    The endpoint-from-Keychain wiring is exercised at unit level by the
    `test_missing_endpoint_url_raises_keychain_miss` test ; cross-cutting
    behaviour is identical from boto's POV.
    """
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=FAKE_BUCKET)

        # Patch the wrapper's `_build_client` so it returns a moto-friendly
        # client (no custom endpoint). The wrapper still pulls credentials
        # from the keychain via `_fetch_credentials` — that path remains
        # under test. SigV4 is preserved so presigned URLs carry
        # `X-Amz-Signature` (matching the production wire format).
        from botocore.client import Config as _BotoConfig

        def _moto_friendly_build_client() -> Any:
            access_key, secret_key, _endpoint = r2_tools._fetch_credentials()
            return boto3.client(
                "s3",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name="us-east-1",
                config=_BotoConfig(signature_version="s3v4"),
            )

        monkeypatch.setattr(r2_tools, "_build_client", _moto_friendly_build_client)
        yield client


@pytest.fixture
def populated_bucket(mocked_s3: Any) -> Any:
    """Seed the bucket with a few keys for list/get/delete tests."""
    mocked_s3.put_object(Bucket=FAKE_BUCKET, Key="receipts/abc.jpg", Body=b"\xff\xd8jpgcontent")
    mocked_s3.put_object(Bucket=FAKE_BUCKET, Key="receipts/def.jpg", Body=b"\xff\xd8more")
    mocked_s3.put_object(Bucket=FAKE_BUCKET, Key="labels/xyz.png", Body=b"\x89PNGcontent")
    return mocked_s3


@pytest.fixture
def dispatcher(tmp_path: Path) -> Dispatcher:
    """Dispatcher backed by a temp audit log + admin/ops tokens."""
    auth = AuthGate(admin_token="ADMIN_TOK", ops_token="OPS_TOK")
    audit = AuditLog(tmp_path / "audit.log")
    r2_tools.register_all()
    return Dispatcher(auth=auth, audit=audit)


def _audit_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# -- happy paths : r2_list_objects ---------------------------------------


def test_list_objects_happy_path(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
) -> None:
    """`r2_list_objects` returns key/size/last_modified for each object."""
    result = r2_tools.r2_list_objects()

    assert isinstance(result, list)
    assert len(result) == 3
    keys = {item["key"] for item in result}
    assert keys == {"receipts/abc.jpg", "receipts/def.jpg", "labels/xyz.png"}
    for item in result:
        assert "key" in item
        assert "size" in item
        assert "last_modified" in item
        assert isinstance(item["size"], int)
        # last_modified serialised as ISO string for JSON-friendliness.
        assert isinstance(item["last_modified"], str)


def test_list_objects_with_prefix(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
) -> None:
    """Prefix filter scopes the listing."""
    result = r2_tools.r2_list_objects(prefix="receipts/")

    assert len(result) == 2
    assert all(item["key"].startswith("receipts/") for item in result)


def test_list_objects_default_limit(
    fake_credentials: dict[str, str],
    mocked_s3: Any,
) -> None:
    """Default limit is 50 (per ARCH § Module 6)."""
    # Seed 60 keys.
    for i in range(60):
        mocked_s3.put_object(Bucket=FAKE_BUCKET, Key=f"k/{i:03d}", Body=b"x")

    result = r2_tools.r2_list_objects(prefix="k/")

    assert len(result) == 50


def test_list_objects_clamps_limit_to_1000(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
) -> None:
    """R2/S3 API caps `limit` at 1000 — tool clamps defensively."""
    result = r2_tools.r2_list_objects(limit=5000)

    # We have 3 keys — the clamp doesn't show up here ; just verify call doesn't blow up.
    assert len(result) == 3


def test_list_objects_clamps_limit_to_1(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
) -> None:
    """Limit floor at 1."""
    result = r2_tools.r2_list_objects(limit=0)

    assert len(result) == 1


def test_list_objects_custom_limit(
    fake_credentials: dict[str, str],
    mocked_s3: Any,
) -> None:
    """Custom limit honored."""
    for i in range(20):
        mocked_s3.put_object(Bucket=FAKE_BUCKET, Key=f"k/{i:03d}", Body=b"x")

    result = r2_tools.r2_list_objects(prefix="k/", limit=5)

    assert len(result) == 5


def test_list_objects_empty_bucket(
    fake_credentials: dict[str, str],
    mocked_s3: Any,
) -> None:
    """Empty bucket → empty list, no crash."""
    result = r2_tools.r2_list_objects()

    assert result == []


# -- happy paths : r2_get_object_url -------------------------------------


def test_get_object_url_happy_path(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
) -> None:
    """`r2_get_object_url` returns a presigned URL + the requested key/ttl."""
    out = r2_tools.r2_get_object_url(key="receipts/abc.jpg", ttl_seconds=600)

    assert isinstance(out, dict)
    assert "url" in out
    assert "key" in out
    assert "ttl_seconds" in out
    assert out["key"] == "receipts/abc.jpg"
    assert out["ttl_seconds"] == 600
    # Presigned URL embeds the bucket and key.
    assert "ratis-receipts-prod" in out["url"]
    assert "receipts/abc.jpg" in out["url"]
    # Standard SigV4 query params.
    assert "X-Amz-Signature" in out["url"]
    assert "X-Amz-Expires=600" in out["url"]


def test_get_object_url_ttl_clamp_to_minimum(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
) -> None:
    """TTL floor at 1 second (boto3 rejects 0)."""
    out = r2_tools.r2_get_object_url(key="receipts/abc.jpg", ttl_seconds=0)

    assert out["ttl_seconds"] == 1
    assert "X-Amz-Expires=1" in out["url"]


def test_get_object_url_ttl_clamp_to_maximum(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
) -> None:
    """TTL ceiling at 7 days (S3 SigV4 spec maximum)."""
    seven_days = 7 * 24 * 3600
    out = r2_tools.r2_get_object_url(key="receipts/abc.jpg", ttl_seconds=10 * 24 * 3600)

    assert out["ttl_seconds"] == seven_days
    assert f"X-Amz-Expires={seven_days}" in out["url"]


def test_get_object_url_default_ttl(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
) -> None:
    """Default TTL is 600s (per ARCH § Module 6)."""
    out = r2_tools.r2_get_object_url(key="receipts/abc.jpg")

    assert out["ttl_seconds"] == 600


# -- happy paths : r2_delete_object --------------------------------------


def test_delete_object_happy_path(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
) -> None:
    """`r2_delete_object` removes the object from the bucket."""
    out = r2_tools.r2_delete_object(key="receipts/abc.jpg")

    assert isinstance(out, dict)
    assert out["key"] == "receipts/abc.jpg"
    assert out["deleted"] is True

    # Verify on the moto client.
    remaining = populated_bucket.list_objects_v2(Bucket=FAKE_BUCKET).get("Contents", [])
    keys = {obj["Key"] for obj in remaining}
    assert "receipts/abc.jpg" not in keys


def test_delete_object_idempotent_on_missing(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
) -> None:
    """Deleting a non-existent key is a no-op (S3 semantics) — no raise."""
    out = r2_tools.r2_delete_object(key="does/not/exist.jpg")

    assert out["key"] == "does/not/exist.jpg"
    assert out["deleted"] is True


# -- bucket override ------------------------------------------------------


def test_bucket_override_via_env_var(
    fake_credentials: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mocked_s3: Any,
) -> None:
    """`RATIS_R2_BUCKET` env var overrides the default `ratis-receipts-prod`."""
    monkeypatch.setenv("RATIS_R2_BUCKET", "my-other-bucket")
    mocked_s3.create_bucket(Bucket="my-other-bucket")
    mocked_s3.put_object(Bucket="my-other-bucket", Key="from/override.txt", Body=b"hi")

    result = r2_tools.r2_list_objects()

    keys = {item["key"] for item in result}
    assert keys == {"from/override.txt"}


def test_default_bucket_is_ratis_receipts(
    fake_credentials: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    populated_bucket: Any,
) -> None:
    """Without env override, default bucket is `ratis-receipts-prod`."""
    monkeypatch.delenv("RATIS_R2_BUCKET", raising=False)

    result = r2_tools.r2_list_objects()

    # We populated `ratis-receipts-prod` in `populated_bucket`, so seeing 3 items
    # confirms the default bucket name resolution.
    assert len(result) == 3


# -- error paths ----------------------------------------------------------


def test_missing_access_key_raises_keychain_miss(
    monkeypatch: pytest.MonkeyPatch,
    mocked_s3: Any,
) -> None:
    """When `r2-access-key-id` keychain entry is absent, surface KeychainMiss."""

    def _missing(self: keychain_mod.Keychain, account: str) -> str:
        if account == "r2-access-key-id":
            raise KeychainMiss("not found")
        return "irrelevant"

    monkeypatch.setattr(keychain_mod.Keychain, "get", _missing)

    with pytest.raises(KeychainMiss, match="r2-access-key-id"):
        r2_tools.r2_list_objects()


def test_missing_secret_key_raises_keychain_miss(
    monkeypatch: pytest.MonkeyPatch,
    mocked_s3: Any,
) -> None:
    """When `r2-secret-access-key` is absent, surface KeychainMiss."""

    def _missing(self: keychain_mod.Keychain, account: str) -> str:
        if account == "r2-secret-access-key":
            raise KeychainMiss("not found")
        return "irrelevant"

    monkeypatch.setattr(keychain_mod.Keychain, "get", _missing)

    with pytest.raises(KeychainMiss, match="r2-secret-access-key"):
        r2_tools.r2_list_objects()


def test_missing_endpoint_url_raises_keychain_miss(
    monkeypatch: pytest.MonkeyPatch,
    mocked_s3: Any,
) -> None:
    """When `r2-endpoint-url` is absent, surface KeychainMiss."""

    def _missing(self: keychain_mod.Keychain, account: str) -> str:
        if account == "r2-endpoint-url":
            raise KeychainMiss("not found")
        return "irrelevant"

    monkeypatch.setattr(keychain_mod.Keychain, "get", _missing)

    with pytest.raises(KeychainMiss, match="r2-endpoint-url"):
        r2_tools.r2_list_objects()


def test_provider_error_on_missing_bucket(
    fake_credentials: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mocked_s3: Any,
) -> None:
    """A bucket that doesn't exist surfaces as `ProviderError`."""
    monkeypatch.setenv("RATIS_R2_BUCKET", "no-such-bucket-anywhere")

    with pytest.raises(ProviderError):
        r2_tools.r2_list_objects()


# -- registration & dispatch (full pipeline) ------------------------------


@pytest.mark.asyncio
async def test_dispatch_list_objects_audits_ok(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    outcome = await dispatcher.dispatch(
        tool_name="r2_list_objects",
        arguments={"prefix": "receipts/"},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "ok"
    assert isinstance(outcome.result, list)
    assert len(outcome.result) == 2

    lines = _audit_lines(tmp_path / "audit.log")
    tool_lines = [ln for ln in lines if ln["tool"] == "r2_list_objects"]
    assert len(tool_lines) == 1
    assert tool_lines[0]["status"] == "ok"
    assert tool_lines[0]["caller"] == "ops"


@pytest.mark.asyncio
async def test_dispatch_get_object_url_admin_succeeds(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    outcome = await dispatcher.dispatch(
        tool_name="r2_get_object_url",
        arguments={"key": "receipts/abc.jpg", "ttl_seconds": 300},
        presented_token="ADMIN_TOK",
    )
    assert outcome.status == "ok"
    assert "url" in outcome.result
    assert outcome.result["key"] == "receipts/abc.jpg"
    assert outcome.result["ttl_seconds"] == 300


@pytest.mark.asyncio
async def test_dispatch_get_object_url_rejects_ops_caller(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """`r2_get_object_url` is admin-scoped — ops gets `forbidden_tool` ; NO presigned URL generated."""
    outcome = await dispatcher.dispatch(
        tool_name="r2_get_object_url",
        arguments={"key": "receipts/abc.jpg"},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "forbidden_tool"
    assert outcome.result is None  # No URL leaks to the caller.

    lines = _audit_lines(tmp_path / "audit.log")
    tool_lines = [ln for ln in lines if ln["tool"] == "r2_get_object_url"]
    assert tool_lines[0]["status"] == "forbidden_tool"
    assert tool_lines[0]["caller"] == "ops"


@pytest.mark.asyncio
async def test_dispatch_delete_object_admin_succeeds(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    outcome = await dispatcher.dispatch(
        tool_name="r2_delete_object",
        arguments={"key": "receipts/def.jpg"},
        presented_token="ADMIN_TOK",
    )
    assert outcome.status == "ok"
    assert outcome.result["deleted"] is True

    # Verify the object is gone.
    remaining = populated_bucket.list_objects_v2(Bucket=FAKE_BUCKET).get("Contents", [])
    keys = {obj["Key"] for obj in remaining}
    assert "receipts/def.jpg" not in keys


@pytest.mark.asyncio
async def test_dispatch_delete_object_rejects_ops_caller_no_mutation(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """`r2_delete_object` is admin-scoped — ops rejected ; CRITICALLY, object NOT deleted."""
    outcome = await dispatcher.dispatch(
        tool_name="r2_delete_object",
        arguments={"key": "receipts/abc.jpg"},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "forbidden_tool"

    # Object MUST still be present.
    remaining = populated_bucket.list_objects_v2(Bucket=FAKE_BUCKET).get("Contents", [])
    keys = {obj["Key"] for obj in remaining}
    assert "receipts/abc.jpg" in keys

    lines = _audit_lines(tmp_path / "audit.log")
    tool_lines = [ln for ln in lines if ln["tool"] == "r2_delete_object"]
    assert tool_lines[0]["status"] == "forbidden_tool"


@pytest.mark.asyncio
async def test_dispatch_keychain_miss_audited(
    monkeypatch: pytest.MonkeyPatch,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """Keychain miss surfaces as `keychain_miss` in audit log."""

    def _missing(self: keychain_mod.Keychain, account: str) -> str:
        raise KeychainMiss(f"keychain account {account!r} not found")

    monkeypatch.setattr(keychain_mod.Keychain, "get", _missing)

    outcome = await dispatcher.dispatch(
        tool_name="r2_list_objects",
        arguments={},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "keychain_miss"
    lines = _audit_lines(tmp_path / "audit.log")
    tool_lines = [ln for ln in lines if ln["tool"] == "r2_list_objects"]
    assert tool_lines[0]["status"] == "keychain_miss"


# -- token leak guard (3-layer) ------------------------------------------


@pytest.mark.asyncio
async def test_token_never_leaks_to_audit_log(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """Audit JSONL must NEVER contain the AWS keys, the endpoint, or presigned URL."""
    await dispatcher.dispatch(
        tool_name="r2_list_objects",
        arguments={"prefix": "receipts/"},
        presented_token="OPS_TOK",
    )
    await dispatcher.dispatch(
        tool_name="r2_get_object_url",
        arguments={"key": "receipts/abc.jpg", "ttl_seconds": 300},
        presented_token="ADMIN_TOK",
    )
    await dispatcher.dispatch(
        tool_name="r2_delete_object",
        arguments={"key": "receipts/def.jpg"},
        presented_token="ADMIN_TOK",
    )

    raw = (tmp_path / "audit.log").read_text()
    assert FAKE_ACCESS_KEY not in raw, "Access key leaked into audit log!"
    assert FAKE_SECRET_KEY not in raw, "Secret key leaked into audit log!"
    # Presigned URLs embed `X-Amz-Credential` ; they must NOT appear in audit.
    assert "X-Amz-Credential" not in raw, "Presigned URL leaked into audit log!"
    assert "X-Amz-Signature" not in raw, "Presigned URL signature leaked!"


@pytest.mark.asyncio
async def test_presigned_url_redacted_from_audit_but_returned_to_caller(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """Critical : the URL is the WHOLE POINT of the tool — caller must get
    it — but the audit log MUST NOT include it."""
    outcome = await dispatcher.dispatch(
        tool_name="r2_get_object_url",
        arguments={"key": "receipts/abc.jpg"},
        presented_token="ADMIN_TOK",
    )
    assert outcome.status == "ok"
    presigned = outcome.result["url"]
    # Caller gets the URL.
    assert "X-Amz-Signature" in presigned

    # Audit does NOT contain it.
    raw = (tmp_path / "audit.log").read_text()
    assert presigned not in raw
    assert "X-Amz-Signature" not in raw


def test_no_credentials_in_returned_dicts(
    fake_credentials: dict[str, str],
    populated_bucket: Any,
) -> None:
    """Cross-tool sweep — call every tool, assert credentials never appear in returns."""
    list_result = r2_tools.r2_list_objects()
    url_result = r2_tools.r2_get_object_url(key="receipts/abc.jpg")
    delete_result = r2_tools.r2_delete_object(key="receipts/def.jpg")

    for blob in (str(list_result), json.dumps(url_result, default=str), json.dumps(delete_result, default=str)):
        assert FAKE_SECRET_KEY not in blob, f"Secret leaked: {blob[:200]!r}"
    # `url_result["url"]` legitimately includes `X-Amz-Credential` (which
    # contains the access key id) — that's a presigned URL invariant.
    # We separately assert the SECRET key never appears anywhere.
    assert FAKE_SECRET_KEY not in url_result["url"]


# -- registration metadata -------------------------------------------------


def test_all_tools_registered_with_correct_scopes() -> None:
    """`register_all()` puts the 3 tools into the global registry with right scopes.

    Per ARCH § Module 6 :
    * `r2_list_objects` → ops (read-only).
    * `r2_get_object_url` → admin (presigned URL exposes contents).
    * `r2_delete_object` → admin (mutating).
    """
    r2_tools.register_all()
    from agent_mcp.server import TOOLS_REGISTRY

    expected = {
        "r2_list_objects": "ops",
        "r2_get_object_url": "admin",
        "r2_delete_object": "admin",
    }
    for name, scope in expected.items():
        assert name in TOOLS_REGISTRY, f"missing {name}"
        assert TOOLS_REGISTRY[name].scope == scope, (
            f"{name} declared scope {TOOLS_REGISTRY[name].scope!r}, expected {scope!r}"
        )


def test_register_all_is_idempotent() -> None:
    """Calling `register_all()` twice doesn't raise."""
    r2_tools.register_all()
    r2_tools.register_all()
    from agent_mcp.server import TOOLS_REGISTRY

    assert "r2_list_objects" in TOOLS_REGISTRY


def test_load_builtin_tools_includes_r2() -> None:
    """`server.load_builtin_tools()` is the production entry point — must wire R2."""
    from agent_mcp.server import TOOLS_REGISTRY, load_builtin_tools

    load_builtin_tools()
    for name in ("r2_list_objects", "r2_get_object_url", "r2_delete_object"):
        assert name in TOOLS_REGISTRY
