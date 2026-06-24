"""Tamper-evident JSONL audit chain for the secrets vault (Module 10).

Each line appended to the monthly log file has the shape:

    {"seq": 1, "ts": "...", "action": "generate|get|list|delete|revoke",
     "name": "...", "principal": "agent|cli|test",
     "prev_hash": "<sha256-hex-of-previous-raw-line>",
     "hmac": "<hmac-sha256-hex-of-this-line-without-hmac-field>"}

Chain invariant
---------------
* ``prev_hash`` of line N = SHA256(raw bytes of line N-1).
* ``prev_hash`` of the first line = ``"0" * 64``.
* ``hmac`` = HMAC-SHA256 of the *minimal* JSON serialisation of the line
  (``separators=(",", ":")``), computed WITHOUT the ``hmac`` field, signed
  with the 32-byte key stored in Keychain account
  ``ratis-provider-admin/audit-signing``.

Bootstrap
---------
If the signing key does not exist in the Keychain on first use, a 32-byte
random key is generated, hex-encoded, and stored silently. This makes fresh
installs self-initialising with no operator action needed.

Concurrency
-----------
``fcntl.flock(LOCK_EX)`` around the append (same pattern as
``agent_mcp.audit.AuditLog``). Multiple MCP processes on the same machine
can safely share the file.

File layout
-----------
``<log_dir>/secrets-YYYY-MM.jsonl``  — one file per calendar month (UTC).
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
import hmac as _hmac_mod
import json
import secrets as _secrets_mod
from pathlib import Path
from typing import Any

from ..keychain import Keychain

# The Keychain account that holds the HMAC signing key.
# Service is the same as the global ``KEYCHAIN_SERVICE`` but the account is
# namespaced under ``ratis-provider-admin/`` to separate it from provider
# tokens.
_AUDIT_KEY_ACCOUNT = "ratis-provider-admin/audit-signing"

# Sentinel used as prev_hash for the very first entry in each monthly file.
_GENESIS_HASH = "0" * 64


class SecretsAuditChain:
    """Append-only tamper-evident audit log for secrets operations.

    Parameters
    ----------
    log_dir:
        Directory where monthly JSONL files are written. Created if absent.
    keychain:
        ``Keychain`` instance used to fetch/store the HMAC signing key.
        Must have read+write access (used for bootstrap).
    """

    def __init__(self, log_dir: Path, keychain: Keychain) -> None:
        self._log_dir = log_dir
        self._keychain = keychain
        self._log_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ public

    def append(self, *, action: str, name: str, principal: str) -> None:
        """Append one signed entry to the current month's log file."""
        key = self._get_or_bootstrap_key()
        log_path = self._current_log_path()

        with log_path.open("a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                # Read last line to compute prev_hash and determine seq.
                prev_raw, last_seq = _read_last_line(log_path)
                prev_hash = hashlib.sha256(prev_raw.encode()).hexdigest() if prev_raw else _GENESIS_HASH
                seq = last_seq + 1

                ts = datetime.datetime.now(datetime.UTC).isoformat()
                entry: dict[str, Any] = {
                    "seq": seq,
                    "ts": ts,
                    "action": action,
                    "name": name,
                    "principal": principal,
                    "prev_hash": prev_hash,
                }

                # Compute HMAC over the entry WITHOUT the hmac field.
                body = json.dumps(entry, separators=(",", ":"), ensure_ascii=True)
                hmac_hex = _compute_hmac(body, key)
                entry["hmac"] = hmac_hex

                raw = json.dumps(entry, separators=(",", ":"), ensure_ascii=True)
                fh.write(raw + "\n")
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        """Return the last *n* entries from the current month's log.

        Returns an empty list if the log file does not exist yet.
        """
        log_path = self._current_log_path()
        if not log_path.exists():
            return []
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        tail_lines = lines[-n:] if len(lines) > n else lines
        result = []
        for line in tail_lines:
            line = line.strip()
            if line:
                with contextlib.suppress(json.JSONDecodeError):
                    result.append(json.loads(line))
        return result

    def verify_chain(self, month: str | None = None) -> dict[str, Any]:
        """Verify the integrity of a monthly chain.

        Parameters
        ----------
        month:
            ``"YYYY-MM"`` string. Defaults to the current UTC month.

        Returns
        -------
        dict with keys ``ok`` (bool), ``checked`` (int), ``errors`` (list[str]).
        """
        if month is None:
            month = datetime.datetime.now(datetime.UTC).strftime("%Y-%m")
        log_path = self._log_dir / f"secrets-{month}.jsonl"
        if not log_path.exists():
            return {"ok": True, "checked": 0, "errors": []}

        key = self._get_or_bootstrap_key()
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        errors: list[str] = []
        prev_raw: str = ""

        for idx, raw in enumerate(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"line {idx + 1}: invalid JSON — {exc}")
                continue

            # 1. seq must be idx+1
            expected_seq = idx + 1
            if entry.get("seq") != expected_seq:
                errors.append(f"line {idx + 1}: expected seq={expected_seq}, got {entry.get('seq')!r}")

            # 2. prev_hash must match the previous raw line
            if idx == 0:
                expected_prev = _GENESIS_HASH
            else:
                expected_prev = hashlib.sha256(prev_raw.encode()).hexdigest()
            if entry.get("prev_hash") != expected_prev:
                errors.append(
                    f"line {idx + 1}: prev_hash mismatch "
                    f"(expected {expected_prev[:16]}…, got {str(entry.get('prev_hash', ''))[:16]}…)"
                )

            # 3. HMAC must verify
            stored_hmac = entry.pop("hmac", None)
            if stored_hmac is None:
                errors.append(f"line {idx + 1}: missing hmac field")
            else:
                body = json.dumps(entry, separators=(",", ":"), ensure_ascii=True)
                expected_hmac = _compute_hmac(body, key)
                if not _hmac_mod.compare_digest(stored_hmac, expected_hmac):
                    errors.append(f"line {idx + 1}: HMAC verification failed")
                # Restore the entry for prev_hash tracking.
                entry["hmac"] = stored_hmac

            prev_raw = raw

        return {
            "ok": len(errors) == 0,
            "checked": len(lines),
            "errors": errors,
        }

    # ----------------------------------------------------------------- private

    def _current_log_path(self) -> Path:
        month = datetime.datetime.now(datetime.UTC).strftime("%Y-%m")
        return self._log_dir / f"secrets-{month}.jsonl"

    def _get_or_bootstrap_key(self) -> bytes:
        """Return the HMAC signing key bytes, bootstrapping if absent."""
        from ..errors import KeychainMiss

        try:
            hex_key = self._keychain.get(_AUDIT_KEY_ACCOUNT)
            return bytes.fromhex(hex_key)
        except KeychainMiss:
            # Bootstrap: generate a fresh 32-byte key and persist it.
            new_key = _secrets_mod.token_bytes(32)
            hex_key = new_key.hex()
            self._keychain.set(_AUDIT_KEY_ACCOUNT, hex_key)
            return new_key


# -------------------------------------------------------------------- helpers


def _read_last_line(path: Path) -> tuple[str, int]:
    """Return (last_non_empty_raw_line, last_seq) from *path*.

    Returns ``("", 0)`` if the file is empty or does not exist.
    The file must already be opened/locked by the caller — we read it fresh
    from disk to catch lines written by concurrent processes.
    """
    if not path.exists():
        return "", 0
    text = path.read_text(encoding="utf-8")
    lines = [raw.strip() for raw in text.splitlines() if raw.strip()]
    if not lines:
        return "", 0
    last_raw = lines[-1]
    try:
        last_entry = json.loads(last_raw)
        last_seq = int(last_entry.get("seq", 0))
    except (json.JSONDecodeError, ValueError):
        last_seq = len(lines)
    return last_raw, last_seq


def _compute_hmac(body: str, key: bytes) -> str:
    """Return the HMAC-SHA256 hex digest of *body* using *key*."""
    h = _hmac_mod.new(key, body.encode("utf-8"), digestmod=hashlib.sha256)
    return h.hexdigest()
