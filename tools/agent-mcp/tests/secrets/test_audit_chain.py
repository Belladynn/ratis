"""TDD coverage for `agent_mcp.secrets.audit_chain`.

Strategy
--------
* Use `tmp_path` for log directory.
* Mock the Keychain via the `runner` injection point (same pattern as
  `tests/conftest.py::fake_security_runner`).
* Verify JSONL structure, chaining invariants, HMAC verification, and
  bootstrap key-creation.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agent_mcp.keychain import Keychain
from agent_mcp.secrets.audit_chain import SecretsAuditChain

# ----- helpers ---------------------------------------------------------------

_FAKE_AUDIT_KEY = "deadbeef" * 8  # 64-hex-char fake signing key


def _make_keychain_with_key(key_hex: str = _FAKE_AUDIT_KEY) -> tuple[Keychain, dict[str, Any]]:
    """Return a Keychain backed by an in-memory store pre-seeded with the audit key."""
    store: dict[str, Any] = {
        "store": {"ratis-provider-admin/audit-signing": key_hex},
        "calls": [],
    }

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        store["calls"].append(argv)
        account = None
        for i, tok in enumerate(argv):
            if tok == "-a":
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


def _make_keychain_empty() -> tuple[Keychain, dict[str, Any]]:
    """Return a Keychain with NO audit-signing key stored (bootstrap scenario)."""
    store: dict[str, Any] = {"store": {}, "calls": []}

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        store["calls"].append(argv)
        account = None
        for i, tok in enumerate(argv):
            if tok == "-a":
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


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    d = tmp_path / "audit"
    d.mkdir()
    return d


@pytest.fixture
def chain(log_dir: Path) -> SecretsAuditChain:
    kc, _ = _make_keychain_with_key()
    return SecretsAuditChain(log_dir=log_dir, keychain=kc)


# ----- tests -----------------------------------------------------------------


class TestAppend:
    def test_append_creates_jsonl_file(self, chain: SecretsAuditChain, log_dir: Path) -> None:
        chain.append(action="generate", name="my-secret", principal="agent")
        files = list(log_dir.glob("secrets-*.jsonl"))
        assert len(files) == 1

    def test_appended_line_is_valid_json(self, chain: SecretsAuditChain, log_dir: Path) -> None:
        chain.append(action="generate", name="my-secret", principal="agent")
        lines = next(iter(log_dir.glob("secrets-*.jsonl"))).read_text().strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["action"] == "generate"
        assert parsed["name"] == "my-secret"
        assert parsed["principal"] == "agent"
        assert "seq" in parsed
        assert "ts" in parsed
        assert "prev_hash" in parsed
        assert "hmac" in parsed

    def test_first_entry_has_zero_prev_hash(self, chain: SecretsAuditChain, log_dir: Path) -> None:
        chain.append(action="list", name="", principal="agent")
        line = next(iter(log_dir.glob("secrets-*.jsonl"))).read_text().strip()
        parsed = json.loads(line)
        assert parsed["prev_hash"] == "0" * 64

    def test_append_increments_seq(self, chain: SecretsAuditChain, log_dir: Path) -> None:
        chain.append(action="generate", name="s1", principal="agent")
        chain.append(action="get", name="s1", principal="agent")
        chain.append(action="list", name="", principal="agent")
        lines = next(iter(log_dir.glob("secrets-*.jsonl"))).read_text().strip().splitlines()
        seqs = [json.loads(raw)["seq"] for raw in lines]
        assert seqs == [1, 2, 3]


class TestChaining:
    def test_prev_hash_links_to_previous_raw(self, chain: SecretsAuditChain, log_dir: Path) -> None:
        chain.append(action="generate", name="s", principal="agent")
        chain.append(action="get", name="s", principal="agent")
        chain.append(action="delete", name="s", principal="agent")

        lines = next(iter(log_dir.glob("secrets-*.jsonl"))).read_text().strip().splitlines()
        assert len(lines) == 3

        # prev_hash[1] == sha256(lines[0])
        h1 = hashlib.sha256(lines[0].encode()).hexdigest()
        assert json.loads(lines[1])["prev_hash"] == h1

        # prev_hash[2] == sha256(lines[1])
        h2 = hashlib.sha256(lines[1].encode()).hexdigest()
        assert json.loads(lines[2])["prev_hash"] == h2

    def test_verify_chain_ok_on_intact_chain(self, chain: SecretsAuditChain) -> None:
        chain.append(action="generate", name="s", principal="agent")
        chain.append(action="get", name="s", principal="agent")
        result = chain.verify_chain()
        assert result["ok"] is True
        assert result["checked"] == 2
        assert result["errors"] == []

    def test_verify_chain_detects_tampered_line(self, chain: SecretsAuditChain, log_dir: Path) -> None:
        chain.append(action="generate", name="s", principal="agent")
        chain.append(action="get", name="s", principal="agent")

        # Tamper: rewrite the file with a modified first line
        f = next(iter(log_dir.glob("secrets-*.jsonl")))
        lines = f.read_text().strip().splitlines()
        first = json.loads(lines[0])
        first["name"] = "TAMPERED"
        lines[0] = json.dumps(first, separators=(",", ":"))
        f.write_text("\n".join(lines) + "\n")

        result = chain.verify_chain()
        assert result["ok"] is False
        assert len(result["errors"]) >= 1


class TestTail:
    def test_tail_returns_last_n(self, chain: SecretsAuditChain) -> None:
        for i in range(5):
            chain.append(action="list", name=f"s{i}", principal="agent")
        tail = chain.tail(3)
        assert len(tail) == 3
        seqs = [e["seq"] for e in tail]
        assert seqs == [3, 4, 5]

    def test_tail_returns_all_if_fewer_than_n(self, chain: SecretsAuditChain) -> None:
        chain.append(action="list", name="s", principal="agent")
        tail = chain.tail(10)
        assert len(tail) == 1

    def test_tail_returns_empty_on_no_log(self, log_dir: Path) -> None:
        kc, _ = _make_keychain_with_key()
        fresh_chain = SecretsAuditChain(log_dir=log_dir, keychain=kc)
        assert fresh_chain.tail(5) == []


class TestBootstrapKey:
    def test_bootstrap_creates_key_when_absent(self, log_dir: Path) -> None:
        kc, store = _make_keychain_empty()
        assert "ratis-provider-admin/audit-signing" not in store["store"]

        chain = SecretsAuditChain(log_dir=log_dir, keychain=kc)
        chain.append(action="generate", name="s", principal="agent")

        # Key should now be in the store
        assert "ratis-provider-admin/audit-signing" in store["store"]

    def test_bootstrap_key_is_64_hex_chars(self, log_dir: Path) -> None:
        kc, store = _make_keychain_empty()
        chain = SecretsAuditChain(log_dir=log_dir, keychain=kc)
        chain.append(action="generate", name="s", principal="agent")
        key = store["store"]["ratis-provider-admin/audit-signing"]
        assert len(key) == 64
        # Valid hex
        int(key, 16)

    def test_existing_key_not_overwritten(self, log_dir: Path) -> None:
        kc, store = _make_keychain_with_key(_FAKE_AUDIT_KEY)
        chain = SecretsAuditChain(log_dir=log_dir, keychain=kc)
        chain.append(action="generate", name="s", principal="agent")
        assert store["store"]["ratis-provider-admin/audit-signing"] == _FAKE_AUDIT_KEY
