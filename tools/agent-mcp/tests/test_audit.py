"""Tests for `agent_mcp.audit.AuditLog` and `redact_args`."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from agent_mcp.audit import AuditLog, redact_args


def _read_lines(path: Path) -> list[dict[str, object]]:
    """Parse a JSONL file into a list of records ; one line = one record."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_write_appends_one_line(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.log")
    log.write(
        caller="admin",
        tool="glitchtip_list_issues",
        args_redacted={"project": "ratis-client", "limit": 10},
        status="ok",
        latency_ms=145,
    )
    records = _read_lines(tmp_path / "audit.log")
    assert len(records) == 1
    rec = records[0]
    assert rec["caller"] == "admin"
    assert rec["tool"] == "glitchtip_list_issues"
    assert rec["args_redacted"] == {"project": "ratis-client", "limit": 10}
    assert rec["status"] == "ok"
    assert rec["latency_ms"] == 145
    assert rec["error"] is None
    # Timestamp present and ISO-8601-ish.
    assert isinstance(rec["ts"], str)
    assert "T" in rec["ts"]


def test_write_preserves_order(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.log")
    for i in range(5):
        log.write(
            caller="ops",
            tool=f"tool_{i}",
            args_redacted={"i": i},
            status="ok",
            latency_ms=i,
        )
    records = _read_lines(tmp_path / "audit.log")
    assert [r["tool"] for r in records] == [f"tool_{i}" for i in range(5)]


def test_log_file_chmod_600(tmp_path: Path) -> None:
    """Newly-created audit log must have permissions 600."""
    path = tmp_path / "audit.log"
    AuditLog(path)
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_concurrent_writes_no_corruption(tmp_path: Path) -> None:
    """Multiple threads append concurrently — every line must parse as JSON.

    Without `flock`, interleaving on multi-line writes (which we do not
    emit, but we want the safety belt anyway) would produce corrupted
    JSON. With our flock+threading.Lock combo, every line stands alone.
    """
    log = AuditLog(tmp_path / "audit.log")
    iterations = 50

    def worker(name: str) -> None:
        for i in range(iterations):
            log.write(
                caller="ops",
                tool=name,
                args_redacted={"name": name, "i": i, "filler": "x" * 200},
                status="ok",
                latency_ms=i,
            )

    threads = [threading.Thread(target=worker, args=(f"t{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    records = _read_lines(tmp_path / "audit.log")
    assert len(records) == 4 * iterations
    # All records parsed cleanly = no byte-level corruption.


def test_redact_top_level_token_keys() -> None:
    args = {
        "project": "ratis",
        "auth_token": "abc",
        "api_key": "xyz",
        "secret_value": "shh",
        "password": "p4ssw0rd",
        "credential": "creds",
    }
    out = redact_args(args)
    assert out["project"] == "ratis"
    for k in ("auth_token", "api_key", "secret_value", "password", "credential"):
        assert out[k] == "<redacted>", f"{k} not redacted"


def test_redact_nested_dict_keys() -> None:
    args = {
        "envelope": {
            "headers": {"authorization": "Bearer xxx"},
            "body": "ok",
        }
    }
    out = redact_args(args)
    # `authorization` matches `auth` substring.
    assert out["envelope"]["headers"]["authorization"] == "<redacted>"
    assert out["envelope"]["body"] == "ok"


def test_redact_does_not_mutate_input() -> None:
    original = {"token": "secret", "x": 1}
    redact_args(original)
    assert original == {"token": "secret", "x": 1}


def test_redact_non_dict_input() -> None:
    out = redact_args("not a dict")  # type: ignore[arg-type]
    assert out == {"__non_dict_args__": "<redacted>"}


def test_write_unserializable_args_does_not_crash(tmp_path: Path) -> None:
    """Object that's not JSON-serialisable should not lose the audit line."""
    log = AuditLog(tmp_path / "audit.log")
    # bytes are not JSON-serialisable by default.
    log.write(
        caller="ops",
        tool="weird_tool",
        args_redacted={"blob": b"\x00\x01"},
        status="ok",
        latency_ms=1,
    )
    records = _read_lines(tmp_path / "audit.log")
    assert len(records) == 1
    rec = records[0]
    # Either we serialised via default=str (bytes -> str repr) OR fell back
    # to the marker. Either way the line exists and parses.
    assert "args_redacted" in rec


def test_log_lines_end_with_newline(tmp_path: Path) -> None:
    """JSONL invariant — no missing newline before EOF."""
    log = AuditLog(tmp_path / "audit.log")
    log.write(caller="admin", tool="x", args_redacted={}, status="ok", latency_ms=0)
    raw = (tmp_path / "audit.log").read_bytes()
    assert raw.endswith(b"\n")


# ---------------------------------------------------------------------------
# Audit L4 — long-text truncation in `db_propose_write` args
# ---------------------------------------------------------------------------
# `redact_args` masks token-shaped keys but does nothing for free-form text
# args used by the db-write pipeline. `client_message` and `investigation`
# can be hundreds of chars of PII / sensitive text. We truncate them in the
# audit log to 100 chars + "...[truncated N more chars]" marker. The full
# payload remains in `db_write_approvals.payload` for human review.


def test_redact_truncates_long_client_message() -> None:
    """`client_message` longer than the cap must be truncated with a marker."""
    long_msg = "x" * 250
    args = {"procedure": "support_credit_cab", "client_message": long_msg}
    out = redact_args(args)
    assert out["procedure"] == "support_credit_cab"
    truncated = out["client_message"]
    assert truncated.startswith("x" * 100)
    assert "...[truncated 150 more chars]" in truncated
    # The full body must NOT survive in the redacted payload.
    assert len(truncated) < len(long_msg)


def test_redact_truncates_long_investigation() -> None:
    """Same rule for `investigation`."""
    long_inv = "a" * 500
    out = redact_args({"investigation": long_inv})
    truncated = out["investigation"]
    assert truncated.startswith("a" * 100)
    assert "...[truncated 400 more chars]" in truncated


def test_redact_preserves_short_client_message() -> None:
    """Below the cap, the value passes through unchanged."""
    short = "ok"
    out = redact_args({"client_message": short})
    assert out["client_message"] == short


def test_redact_truncation_boundary_exactly_100() -> None:
    """At exactly the cap, no truncation marker is added."""
    msg = "x" * 100
    out = redact_args({"client_message": msg})
    assert out["client_message"] == msg
    assert "truncated" not in out["client_message"]


def test_redact_truncates_nested_client_message() -> None:
    """Truncation must apply through one level of nesting too."""
    args = {"payload": {"client_message": "y" * 200}}
    out = redact_args(args)
    nested = out["payload"]["client_message"]
    assert nested.startswith("y" * 100)
    assert "...[truncated 100 more chars]" in nested
