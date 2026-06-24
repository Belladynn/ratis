"""Append-only JSONL audit log (DA-48).

Every tool dispatch produces exactly one line with the canonical schema :

```
{"ts": "...", "caller": "admin|ops", "tool": "...", "args_redacted": {...},
 "status": "ok|forbidden_tool|keychain_miss|provider_error|audit_error",
 "latency_ms": 145, "error": null}
```

Concurrency model :
    Multiple Claude Code SAs can be dispatched in parallel inside the same
    user account, each spawning its own `agent-mcp serve` subprocess. They
    all append to the same `audit.log`. We use `fcntl.flock(LOCK_EX)` around
    the append to guarantee no interleaved bytes — POSIX `O_APPEND` is
    atomic only for writes ≤ PIPE_BUF (typically 4 KiB) and our lines can
    grow with future arg dicts, so we do not rely on it.

Redaction :
    Args are scrubbed by `redact_args()` before serialization. The Foundation
    rule is conservative — any key whose name contains `token`, `key`,
    `secret`, `password`, `auth`, or `credential` is replaced with the
    sentinel `"<redacted>"`. Modules can extend this with their own per-arg
    rules in chunks 2+, but they MUST never disable the base sweep.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Literal

from .errors import AuditError

AuditStatus = Literal[
    "ok",
    "forbidden_tool",
    "keychain_miss",
    "provider_error",
    "audit_error",
    "tool_not_registered",
    "token_rotated",
    "live_mode_used",
]
"""Closed set of values accepted in the `status` field of audit lines.

* `live_mode_used` is a one-shot warning emitted by `stripe_tools` when a
  `sk_live_` key is detected. Non-blocking — exists purely to make V1 mode
  visible to operators tailing the log (DA-48 + ARCH § Module 5).
"""


_REDACT_KEY_RE = re.compile(
    r"(token|key|secret|password|auth|credential)",
    re.IGNORECASE,
)
"""Substring match against arg keys ; conservative on purpose."""

REDACTED_PLACEHOLDER = "<redacted>"

# Audit L4 — free-form text args from `db_propose_write` can contain hundreds
# of chars of PII / client message. The audit log (tailed to Loki) must not
# carry the full body — keep a 100-char prefix with a clear truncation marker.
# The full payload remains intact in `db_write_approvals.payload` for human
# review of the proposition itself.
_TRUNCATED_KEYS: frozenset[str] = frozenset({"client_message", "investigation"})
_TRUNCATE_AT = 100


def _truncate_long_text(key: str, value: Any) -> Any:
    """Truncate string values for the L4-listed keys ; pass-through otherwise."""
    if key not in _TRUNCATED_KEYS or not isinstance(value, str):
        return value
    if len(value) <= _TRUNCATE_AT:
        return value
    remaining = len(value) - _TRUNCATE_AT
    return f"{value[:_TRUNCATE_AT]}...[truncated {remaining} more chars]"


def redact_args(args: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of `args` with sensitive values masked.

    Recurses one level into nested dicts (sufficient for our tool schemas —
    we do not pass deep trees in V0). Lists / tuples are scanned but their
    string elements are not redacted (we only key off arg-name semantics for
    now). If a module ever needs value-shape redaction it can apply its own
    pre-pass before calling `AuditLog.write()`.

    Audit L4 — `client_message` and `investigation` (free-form text from
    `db_propose_write`) are additionally truncated to `_TRUNCATE_AT` chars
    plus a `...[truncated N more chars]` marker, both at the top level and
    one level deep.
    """
    if not isinstance(args, dict):
        return {"__non_dict_args__": REDACTED_PLACEHOLDER}

    redacted: dict[str, Any] = {}
    for key, value in args.items():
        if _REDACT_KEY_RE.search(key):
            redacted[key] = REDACTED_PLACEHOLDER
        elif isinstance(value, dict):
            redacted[key] = redact_args(value)
        else:
            redacted[key] = _truncate_long_text(key, value)
    return redacted


class AuditLog:
    """Thread-safe + multi-process-safe append-only JSONL writer.

    `threading.Lock` covers in-process concurrency (asyncio tool dispatch
    can be re-entrant), `fcntl.flock` covers cross-process concurrency
    (parallel SAs).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # `touch` first to ensure the file exists, then chmod separately —
        # `Path.touch(mode=...)` only applies the mode on creation, but if
        # the file already exists with looser perms we want to tighten it.
        if not self.path.exists():
            self.path.touch(mode=0o600)
        else:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                # On odd filesystems chmod can fail ; we proceed but warn.
                print(
                    f"agent-mcp: warning — could not chmod 600 {self.path}",
                    file=sys.stderr,
                )
        self._tlock = threading.Lock()

    def write(
        self,
        *,
        caller: str,
        tool: str,
        args_redacted: dict[str, Any],
        status: AuditStatus,
        latency_ms: int,
        error: str | None = None,
    ) -> None:
        """Append a single audit line.

        The line is serialized to a single string ending with `\n`, then
        written under both an in-process lock and an exclusive flock. We do
        NOT pretty-print — keep one record per line (JSONL invariant).

        On failure (disk full, permission flipped), `AuditError` is raised
        AFTER an attempt to dump the line to stderr so the operator at least
        sees the call happened.
        """
        record = {
            "ts": _dt.datetime.now(tz=_dt.UTC).isoformat(),
            "caller": caller,
            "tool": tool,
            "args_redacted": args_redacted,
            "status": status,
            "latency_ms": latency_ms,
            "error": error,
        }
        try:
            line = json.dumps(record, ensure_ascii=False) + "\n"
        except (TypeError, ValueError) as exc:
            # Unserializable args — fall back to a stringified copy so we
            # never silently drop an audit line.
            record["args_redacted"] = {"__unserializable__": str(args_redacted)[:200]}
            record["error"] = f"audit_serialize_error: {exc}"
            line = json.dumps(record, ensure_ascii=False, default=str) + "\n"

        with self._tlock:
            try:
                # `O_APPEND` + flock = sequential, no interleave even with
                # multiple concurrent processes pointing at the same file.
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    try:
                        os.write(fd, line.encode("utf-8"))
                    finally:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
            except OSError as exc:
                # Last-resort surface so the operator notices in stderr —
                # better partial visibility than silent drop.
                print(f"agent-mcp: AUDIT WRITE FAILED: {line.rstrip()}", file=sys.stderr)
                raise AuditError(str(exc)) from exc
