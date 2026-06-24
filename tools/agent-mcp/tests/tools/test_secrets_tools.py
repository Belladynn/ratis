"""TDD coverage for `agent_mcp.tools.secrets_tools`.

Strategy
--------
* Inject `Keychain` doubles via `set_keychain()` and `SecretMetaDB` doubles
  via `set_meta_db()` — no real macOS security calls, no real DB file.
* Verify tool contracts: NEVER return value from list, generate returns
  metadata without value, get returns value, delete cleans up both stores.
* Audit chain interactions are also tested via an injected chain.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from agent_mcp.keychain import Keychain
from agent_mcp.secrets.meta_db import SecretMetaDB
from agent_mcp.tools import secrets_tools

# ----- helpers ---------------------------------------------------------------

_FAKE_AUDIT_KEY = "cafebabe" * 8  # 64-hex-char fake signing key


def _make_fake_keychain() -> tuple[Keychain, dict[str, Any]]:
    """Return a Keychain backed by an in-memory fake store."""
    store: dict[str, Any] = {
        "store": {},
        "calls": [],
    }

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        store["calls"].append(list(argv))
        account = None
        for i, tok in enumerate(argv):
            if tok == "-a" and i + 1 < len(argv):
                account = argv[i + 1]

        if argv[1] == "find-generic-password":
            if account in store["store"]:
                return subprocess.CompletedProcess(argv, 0, store["store"][account] + "\n", "")
            return subprocess.CompletedProcess(argv, 44, "", "not found")
        if argv[1] == "add-generic-password":
            value = kwargs.get("input")
            if not value:
                return subprocess.CompletedProcess(argv, 1, "", "no stdin")
            store["store"][account] = value
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "delete-generic-password":
            if account in store["store"]:
                del store["store"][account]
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 44, "", "not found")
        return subprocess.CompletedProcess(argv, 1, "", "unhandled")

    kc = Keychain(runner=runner)
    return kc, store


# ----- fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _inject_doubles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Inject fake Keychain + real (tmp) MetaDB + fake audit chain for each test.

    The RATIS_SECRETS_DB_PATH and RATIS_SECRETS_AUDIT_DIR env vars ensure
    any lazy initialisation in the module also stays inside tmp_path.
    """
    kc, _store = _make_fake_keychain()
    # Seed audit-signing key so SecretsAuditChain does not try to write to real Keychain
    _store["store"]["ratis-provider-admin/audit-signing"] = _FAKE_AUDIT_KEY

    db = SecretMetaDB(tmp_path / "secrets.db")
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    monkeypatch.setenv("RATIS_SECRETS_DB_PATH", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("RATIS_SECRETS_AUDIT_DIR", str(audit_dir))

    secrets_tools.set_keychain(kc)
    secrets_tools.set_meta_db(db)
    secrets_tools.set_audit_dir(audit_dir)

    yield

    # Teardown: clear injections
    secrets_tools.set_keychain(None)
    secrets_tools.set_meta_db(None)
    secrets_tools.set_audit_dir(None)


@pytest.fixture
def fake_kc_state(tmp_path: Path) -> dict[str, Any]:
    """Expose the Keychain store for assertions. Must be called AFTER autouse fixture."""
    # Access via the module's current keychain instance
    # Access indirectly via tool calls — no direct Keychain inspection needed.
    return {}


# ----- tests -----------------------------------------------------------------


class TestSecretGenerate:
    def test_generate_returns_metadata_no_value(self) -> None:
        result = secrets_tools.secret_generate(name="my-secret", description="test", length=32)
        assert "lease_id" in result
        assert "name" in result
        assert result["name"] == "my-secret"
        assert "version" in result
        assert "issued_at" in result
        assert "value" not in result
        assert "keychain_account" in result

    def test_generate_stores_in_keychain(self) -> None:
        secrets_tools.secret_generate(name="kc-secret", description="", length=16)
        # Verify by reading back via get
        result = secrets_tools.secret_get("kc-secret")
        assert "value" in result
        assert isinstance(result["value"], str)
        assert len(result["value"]) > 0

    def test_generate_stores_in_meta_db(self) -> None:
        secrets_tools.secret_generate(name="db-secret", description="for db test")
        db = secrets_tools._get_meta_db()
        active = db.get_active("db-secret")
        assert active is not None
        assert active["name"] == "db-secret"

    def test_generate_duplicate_name_overwrites(self) -> None:
        """Second generate on same name must not crash (Keychain.set is idempotent)."""
        r1 = secrets_tools.secret_generate(name="dup-secret", description="first")
        r2 = secrets_tools.secret_generate(name="dup-secret", description="second")
        # Both succeed, second has higher version
        assert r1["name"] == "dup-secret"
        assert r2["name"] == "dup-secret"
        assert r2["version"] > r1["version"]

    def test_generate_respects_length(self) -> None:
        secrets_tools.secret_generate(name="sized-secret", length=64)
        get_result = secrets_tools.secret_get("sized-secret")
        assert len(get_result["value"]) >= 64


class TestSecretGet:
    def test_get_returns_value_and_metadata(self) -> None:
        secrets_tools.secret_generate(name="readable", description="desc")
        result = secrets_tools.secret_get("readable")
        assert "value" in result
        assert "name" in result
        assert result["name"] == "readable"
        assert "lease_id" in result
        assert "version" in result
        assert "category" in result

    def test_get_unknown_name_returns_error(self) -> None:
        result = secrets_tools.secret_get("does-not-exist")
        assert "error" in result

    def test_get_writes_audit(self, tmp_path: Path) -> None:
        audit_dir = tmp_path / "audit"
        # Generate first, then get
        secrets_tools.secret_generate(name="audit-test")
        secrets_tools.secret_get("audit-test")
        # Audit files should exist
        files = list(audit_dir.glob("secrets-*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().strip().splitlines()
        # Should have at least 2 lines: generate + get
        assert len(lines) >= 2


class TestSecretList:
    def test_list_returns_metadata_only(self) -> None:
        secrets_tools.secret_generate(name="s1")
        secrets_tools.secret_generate(name="s2")
        results = secrets_tools.secret_list()
        assert len(results) >= 2
        for row in results:
            assert "value" not in row
            assert "name" in row

    def test_list_empty_when_no_secrets(self) -> None:
        results = secrets_tools.secret_list()
        assert results == []

    def test_list_fields_present(self) -> None:
        secrets_tools.secret_generate(name="listed-secret")
        results = secrets_tools.secret_list()
        assert len(results) >= 1
        row = next(r for r in results if r["name"] == "listed-secret")
        assert "category" in row
        assert "version" in row
        assert "lease_id" in row
        assert "issued_at" in row


class TestSecretDelete:
    def test_delete_returns_success_dict(self) -> None:
        secrets_tools.secret_generate(name="to-delete")
        result = secrets_tools.secret_delete("to-delete")
        assert result["deleted"] is True
        assert result["name"] == "to-delete"
        assert result["versions_removed"] >= 1

    def test_delete_removes_from_meta_db(self) -> None:
        secrets_tools.secret_generate(name="to-delete-db")
        secrets_tools.secret_delete("to-delete-db")
        db = secrets_tools._get_meta_db()
        assert db.get_active("to-delete-db") is None
        names = {r["name"] for r in db.list_all()}
        assert "to-delete-db" not in names

    def test_delete_removes_from_keychain(self) -> None:
        secrets_tools.secret_generate(name="to-delete-kc")
        secrets_tools.secret_delete("to-delete-kc")
        # Trying to get should return an error (not found in keychain)
        result = secrets_tools.secret_get("to-delete-kc")
        assert "error" in result

    def test_delete_writes_audit(self, tmp_path: Path) -> None:
        audit_dir = tmp_path / "audit"
        secrets_tools.secret_generate(name="audit-delete")
        secrets_tools.secret_delete("audit-delete")
        files = list(audit_dir.glob("secrets-*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().strip().splitlines()
        import json

        actions = [json.loads(raw)["action"] for raw in lines]
        assert "delete" in actions


# ---------------------------------------------------------------------------
# TestSecretInject
# ---------------------------------------------------------------------------


class TestSecretInject:
    def test_inject_multiple_targets(self, tmp_path: Path) -> None:
        """All adapters succeed → returns {name, injected: {target: 'ok', ...}}."""
        secrets_tools.secret_generate(name="inject-test")

        env_file = tmp_path / "secrets.env"

        with (
            patch("agent_mcp.tools.secrets_tools._inject_gh_actions", return_value="ok"),
            patch("agent_mcp.tools.secrets_tools._inject_n8n", return_value="ok"),
        ):
            result = secrets_tools.secret_inject(
                name="inject-test",
                targets=["env-file", "gh-actions", "n8n-env", "docker-compose-env"],
                gh_secret_name="INJECT_TEST",
                env_file_path=str(env_file),
                _runtime_env_file=env_file,
            )

        assert result["name"] == "inject-test"
        injected = result["injected"]
        assert injected["env-file"] == "ok"
        assert injected["gh-actions"] == "ok"
        assert injected["n8n-env"] == "ok"
        assert injected["docker-compose-env"] == "ok"

    def test_inject_unknown_secret_returns_not_found(self) -> None:
        result = secrets_tools.secret_inject(name="does-not-exist", targets=["env-file"])
        assert result.get("error") == "not_found"

    def test_inject_partial_failure_returns_both_statuses(self, tmp_path: Path) -> None:
        """One target succeeds, another fails → both statuses in result, no exception."""
        secrets_tools.secret_generate(name="partial-test")

        env_file = tmp_path / "secrets.env"

        with patch(
            "agent_mcp.tools.secrets_tools._inject_gh_actions",
            return_value="error: authentication required",
        ):
            result = secrets_tools.secret_inject(
                name="partial-test",
                targets=["env-file", "gh-actions"],
                gh_secret_name="PARTIAL_TEST",
                _runtime_env_file=env_file,
            )

        assert result["name"] == "partial-test"
        injected = result["injected"]
        assert injected["env-file"] == "ok"
        assert injected["gh-actions"].startswith("error:")

    def test_inject_writes_audit_entry(self, tmp_path: Path) -> None:
        import json

        secrets_tools.secret_generate(name="audit-inject")
        audit_dir = tmp_path / "audit"

        env_file = tmp_path / "secrets.env"
        secrets_tools.secret_inject(
            name="audit-inject",
            targets=["env-file"],
            _runtime_env_file=env_file,
        )

        files = list(audit_dir.glob("secrets-*.jsonl"))
        assert len(files) == 1
        actions = [json.loads(raw)["action"] for raw in files[0].read_text().strip().splitlines()]
        assert "inject" in actions


# ---------------------------------------------------------------------------
# TestSecretProvision (PR 3 — new tools)
# ---------------------------------------------------------------------------


class TestSecretProvision:
    def test_secret_provision_returns_no_value(self) -> None:
        """secret_provision MUST never return the token value."""
        import subprocess as _sub
        from unittest.mock import MagicMock, patch

        from agent_mcp.secrets.provision import set_admin_keychain

        def _runner(argv, **kwargs):
            account = None
            for i, tok in enumerate(argv):
                if tok == "-a" and i + 1 < len(argv):
                    account = argv[i + 1]
            if argv[1] == "find-generic-password":
                store = {"sentry-admin": "sentry_token", "sentry-org": "ratis-hq"}
                if account in store:
                    return _sub.CompletedProcess(argv, 0, store[account] + "\n", "")
                return _sub.CompletedProcess(argv, 44, "", "not found")
            return _sub.CompletedProcess(argv, 0, "", "")

        from agent_mcp.keychain import Keychain as _KC

        admin_kc = _KC(runner=_runner)
        set_admin_keychain(admin_kc)

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": "sentry_tok_999", "token": "sntrys_secret_do_not_return"}

        with patch("httpx.post", return_value=mock_resp):
            result = secrets_tools.secret_provision(
                name="prov-sentry",
                provider="sentry",
                ttl="30m",
            )

        set_admin_keychain(None)
        assert "value" not in result, "secret_provision must NEVER return the token value"
        assert result.get("provider") == "sentry"
        assert "lease_id" in result
        assert "name" in result

    def test_secret_provision_stores_in_meta_db(self) -> None:
        """Provisioned token metadata is persisted in SQLite."""
        import subprocess as _sub
        from unittest.mock import MagicMock, patch

        from agent_mcp.secrets.provision import set_admin_keychain

        def _runner(argv, **kwargs):
            account = None
            for i, tok in enumerate(argv):
                if tok == "-a" and i + 1 < len(argv):
                    account = argv[i + 1]
            store = {"sentry-admin": "sentry_token", "sentry-org": "ratis-hq"}
            if argv[1] == "find-generic-password":
                if account in store:
                    return _sub.CompletedProcess(argv, 0, store[account] + "\n", "")
                return _sub.CompletedProcess(argv, 44, "", "not found")
            if argv[1] == "add-generic-password":
                return _sub.CompletedProcess(argv, 0, "", "")
            return _sub.CompletedProcess(argv, 0, "", "")

        from agent_mcp.keychain import Keychain as _KC

        admin_kc = _KC(runner=_runner)
        set_admin_keychain(admin_kc)

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": "sentry_tok_db_001", "token": "sntrys_db_token"}

        with patch("httpx.post", return_value=mock_resp):
            secrets_tools.secret_provision(
                name="prov-meta-test",
                provider="sentry",
                ttl="1h",
            )

        set_admin_keychain(None)

        # Verify metadata in DB
        db = secrets_tools._get_meta_db()
        meta = db.get_active("prov-meta-test")
        assert meta is not None, "metadata should be persisted after provision"
        assert meta["category"] == "B"

    def test_secret_revoke_marks_revoked(self) -> None:
        """secret_revoke calls provider API and marks revoked_at in DB."""
        import subprocess as _sub
        from unittest.mock import MagicMock, patch

        from agent_mcp.secrets.provision import set_admin_keychain

        def _runner(argv, **kwargs):
            account = None
            for i, tok in enumerate(argv):
                if tok == "-a" and i + 1 < len(argv):
                    account = argv[i + 1]
            store = {"sentry-admin": "sentry_token", "sentry-org": "ratis-hq"}
            if argv[1] == "find-generic-password":
                if account in store:
                    return _sub.CompletedProcess(argv, 0, store[account] + "\n", "")
                return _sub.CompletedProcess(argv, 44, "", "not found")
            if argv[1] == "add-generic-password":
                return _sub.CompletedProcess(argv, 0, "", "")
            if argv[1] == "delete-generic-password":
                return _sub.CompletedProcess(argv, 0, "", "")
            return _sub.CompletedProcess(argv, 0, "", "")

        from agent_mcp.keychain import Keychain as _KC

        admin_kc = _KC(runner=_runner)
        set_admin_keychain(admin_kc)

        # First provision to get a lease
        mock_post = MagicMock()
        mock_post.status_code = 201
        mock_post.json.return_value = {"id": "sentry_tok_rev_001", "token": "sntrys_rev_token"}

        with patch("httpx.post", return_value=mock_post):
            prov_result = secrets_tools.secret_provision(
                name="prov-to-revoke",
                provider="sentry",
                ttl="30m",
            )

        lease_id = prov_result["lease_id"]

        # Now revoke
        mock_delete = MagicMock()
        mock_delete.status_code = 204
        mock_delete.json.return_value = {}

        with patch("httpx.delete", return_value=mock_delete):
            rev_result = secrets_tools.secret_revoke(lease_id=lease_id)

        set_admin_keychain(None)

        assert rev_result.get("revoked") is True
        assert rev_result.get("lease_id") == lease_id

        # DB should have revoked_at set
        db = secrets_tools._get_meta_db()
        row = db._conn.execute(
            "SELECT revoked_at FROM secret_versions WHERE lease_id = ?",
            (lease_id,),
        ).fetchone()
        assert row is not None
        assert row[0] is not None, "revoked_at should be set after secret_revoke"

    def test_secret_revoke_unknown_lease_id(self) -> None:
        """secret_revoke on an unknown lease_id returns {error: not_found}."""
        result = secrets_tools.secret_revoke(lease_id="nonexistent_lease_xyz")
        assert result.get("error") == "not_found"


# ---------------------------------------------------------------------------
# TestSecretImport (PR 6 — Cat-C import)
# ---------------------------------------------------------------------------


class TestSecretImport:
    def test_secret_import_stores_cat_c(self) -> None:
        """secret_import with category=C and expires_at stores entry in meta_db."""
        expires_at = "2027-01-01T00:00:00+00:00"
        result = secrets_tools.secret_import(
            name="stripe-live-key",
            value="sk_live_fakekeyfromtest",
            category="C",
            expires_at=expires_at,
            description="Stripe live key",
        )
        assert result.get("error") is None, f"unexpected error: {result}"
        assert result["name"] == "stripe-live-key"
        assert result["category"] == "C"
        assert result["expires_at"] == expires_at

        # Must be stored in meta_db
        db = secrets_tools._get_meta_db()
        active = db.get_active("stripe-live-key")
        assert active is not None
        assert active["category"] == "C"
        assert active["expires_at"] == expires_at

    def test_secret_import_value_never_returned(self) -> None:
        """secret_import must never include the secret value in its return dict."""
        result = secrets_tools.secret_import(
            name="gh-pat",
            value="ghp_faketoken1234",
            category="C",
            expires_at=None,
            description="GitHub PAT",
        )
        assert "value" not in result

    def test_secret_import_no_expires_at(self) -> None:
        """secret_import without expires_at must still succeed (NULL stored)."""
        result = secrets_tools.secret_import(
            name="some-api-key",
            value="apikey12345",
            category="C",
            expires_at=None,
            description="",
        )
        assert result.get("name") == "some-api-key"

        db = secrets_tools._get_meta_db()
        active = db.get_active("some-api-key")
        assert active is not None
        assert active["expires_at"] is None


# ---------------------------------------------------------------------------
# TestSecretGenerateFormat — format param (new feature)
# ---------------------------------------------------------------------------


class TestSecretGenerateFormat:
    """Test `format` parameter on secret_generate for all 6 supported formats."""

    def test_generate_default_format_urlsafe(self) -> None:
        """Default format=urlsafe succeeds and format is present in result."""
        result = secrets_tools.secret_generate(name="fmt-default")
        assert "error" not in result
        assert result.get("format") == "urlsafe"
        assert "value" not in result

    def test_generate_format_urlsafe(self) -> None:
        result = secrets_tools.secret_generate(name="fmt-urlsafe", format="urlsafe")
        assert "error" not in result
        assert result.get("format") == "urlsafe"
        # Value is never returned
        assert "value" not in result

    def test_generate_format_hex(self) -> None:
        result = secrets_tools.secret_generate(name="fmt-hex", format="hex")
        assert "error" not in result
        assert result.get("format") == "hex"
        assert "value" not in result

    def test_generate_format_base64(self) -> None:
        result = secrets_tools.secret_generate(name="fmt-b64", format="base64")
        assert "error" not in result
        assert result.get("format") == "base64"
        assert "value" not in result

    def test_generate_format_alphanumeric(self) -> None:
        result = secrets_tools.secret_generate(name="fmt-alnum", format="alphanumeric", length=20)
        assert "error" not in result
        assert result.get("format") == "alphanumeric"
        assert "value" not in result

    def test_generate_format_numeric(self) -> None:
        result = secrets_tools.secret_generate(name="fmt-num", format="numeric", length=8)
        assert "error" not in result
        assert result.get("format") == "numeric"
        assert "value" not in result

    def test_generate_format_uuid(self) -> None:
        result = secrets_tools.secret_generate(name="fmt-uuid", format="uuid")
        assert "error" not in result
        assert result.get("format") == "uuid"
        assert "value" not in result

    def test_generate_unknown_format_returns_error_dict(self) -> None:
        """An unknown format must return {error, name, detail} — never raise."""
        result = secrets_tools.secret_generate(name="fmt-bad", format="binary")
        assert "error" in result
        assert result.get("name") == "fmt-bad"
        assert "detail" in result

    def test_generate_format_alphanumeric_stored_value_is_alnum(self) -> None:
        """Value stored in Keychain for alphanumeric format contains only [a-zA-Z0-9]."""
        import string

        secrets_tools.secret_generate(name="fmt-alnum-verify", format="alphanumeric", length=24)
        get_result = secrets_tools.secret_get("fmt-alnum-verify")
        value = get_result["value"]
        assert all(c in string.ascii_letters + string.digits for c in value)
        assert len(value) == 24

    def test_generate_format_numeric_stored_value_is_digits_only(self) -> None:
        """Value stored in Keychain for numeric format contains only digits."""
        secrets_tools.secret_generate(name="fmt-num-verify", format="numeric", length=10)
        get_result = secrets_tools.secret_get("fmt-num-verify")
        value = get_result["value"]
        assert value.isdigit()
        assert len(value) == 10

    def test_generate_format_hex_stored_value_is_hex(self) -> None:
        """Value stored in Keychain for hex format is valid hex string."""
        secrets_tools.secret_generate(name="fmt-hex-verify", format="hex", length=16)
        get_result = secrets_tools.secret_get("fmt-hex-verify")
        value = get_result["value"]
        int(value, 16)  # raises ValueError if not valid hex

    def test_generate_format_uuid_stored_value_is_uuid(self) -> None:
        """Value stored in Keychain for uuid format is a valid UUID (36 chars, correct pattern)."""
        import re

        secrets_tools.secret_generate(name="fmt-uuid-verify", format="uuid")
        get_result = secrets_tools.secret_get("fmt-uuid-verify")
        value = get_result["value"]
        assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value), (
            f"not a UUID: {value!r}"
        )
        assert len(value) == 36

    def test_generate_format_base64_stored_value_is_valid_base64(self) -> None:
        """Value stored in Keychain for base64 format decodes without error."""
        import base64

        secrets_tools.secret_generate(name="fmt-b64-verify", format="base64", length=16)
        get_result = secrets_tools.secret_get("fmt-b64-verify")
        value = get_result["value"]
        base64.b64decode(value)  # raises if invalid


# ---------------------------------------------------------------------------
# TestSecretRotateFormat — format param on secret_rotate (new feature)
# ---------------------------------------------------------------------------


class TestSecretRotateFormat:
    """Test `format` parameter on secret_rotate."""

    def test_rotate_default_format_urlsafe(self) -> None:
        """secret_rotate with no format defaults to urlsafe."""
        secrets_tools.secret_generate(name="rot-fmt-default")
        result = secrets_tools.secret_rotate(name="rot-fmt-default")
        assert "error" not in result
        assert "value" not in result

    def test_rotate_format_hex(self) -> None:
        """secret_rotate with format=hex stores a hex token in Keychain."""
        secrets_tools.secret_generate(name="rot-hex")
        result = secrets_tools.secret_rotate(name="rot-hex", format="hex")
        assert "error" not in result
        assert "value" not in result

        # Verify the new version has a hex value in Keychain
        get_result = secrets_tools.secret_get("rot-hex")
        value = get_result["value"]
        int(value, 16)  # raises ValueError if not valid hex

    def test_rotate_unknown_format_returns_error_dict(self) -> None:
        """An unknown format on secret_rotate must return {error, name} — never raise."""
        secrets_tools.secret_generate(name="rot-bad-fmt")
        result = secrets_tools.secret_rotate(name="rot-bad-fmt", format="binary")
        assert "error" in result
        assert result.get("name") == "rot-bad-fmt"
        assert "detail" in result
