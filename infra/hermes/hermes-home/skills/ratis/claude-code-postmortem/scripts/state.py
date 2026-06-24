"""State file management for the claude-code-postmortem skill.

Two things live here:

1. **mtime cache** — per-session-file last-seen mtime. A session is only re-
   analyzed if its mtime moved AND the session is considered "finished"
   (cf. ``needs_analysis``). Avoids re-paying Codex tokens for the same
   conversation.
2. **rate-limit counters** — per-hour analysis count, used by ``should_run_now``
   to throttle (3/h normal, 10/h during 02h-06h off-peak).

State is persisted as JSON in ``~/.hermes/state/claude-postmortem-state.json``
(path overridable via ``HERMES_POSTMORTEM_STATE_PATH``). Atomic write with
``os.replace`` so a crash mid-write does not corrupt the file.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_STATE_PATH = Path(
    os.environ.get(
        "HERMES_POSTMORTEM_STATE_PATH",
        str(Path.home() / ".hermes" / "state" / "claude-postmortem-state.json"),
    )
)

# A session is considered "finished" if its file hasn't been touched in this
# many seconds. 15 minutes mirrors the spec.
SESSION_QUIET_SECONDS = int(os.environ.get("HERMES_POSTMORTEM_QUIET_SECONDS", "900"))


@dataclass
class PostmortemState:
    """In-memory representation of the persistent state file."""

    session_mtimes: dict[str, float] = field(default_factory=dict)
    hourly_counts: dict[str, int] = field(default_factory=dict)  # "YYYY-MM-DD-HH" → count
    path: Path = DEFAULT_STATE_PATH

    # ----- persistence ---------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "PostmortemState":
        p = path or DEFAULT_STATE_PATH
        if not p.exists():
            return cls(path=p)
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            # Corrupted state — start fresh rather than crash. The worst case
            # is one wasted analysis re-run.
            return cls(path=p)
        return cls(
            session_mtimes=dict(data.get("session_mtimes", {})),
            hourly_counts=dict(data.get("hourly_counts", {})),
            path=p,
        )

    def save(self) -> None:
        """Atomic write — temp file then ``os.replace``."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "session_mtimes": self.session_mtimes,
            "hourly_counts": self.hourly_counts,
        }
        # Drop old hourly buckets to keep the file small (only keep last 48h).
        self._prune_hourly_counts()
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=str(self.path.parent),
            prefix=".claude-postmortem-state-",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            json.dump(payload, tmp, indent=2, sort_keys=True)
            tmp_name = tmp.name
        os.replace(tmp_name, self.path)

    def _prune_hourly_counts(self, keep_hours: int = 48) -> None:
        if len(self.hourly_counts) <= keep_hours:
            return
        # Keys are sortable lexicographically (YYYY-MM-DD-HH).
        sorted_keys = sorted(self.hourly_counts.keys())
        for k in sorted_keys[:-keep_hours]:
            self.hourly_counts.pop(k, None)

    # ----- analysis-needs decisions -------------------------------------

    def needs_analysis(self, session_path: Path, now: float | None = None) -> bool:
        """Should this session be analyzed right now?

        Conditions:
        - mtime has changed since last recorded analysis (or never analyzed)
        - session has been quiet for ``SESSION_QUIET_SECONDS`` (assume done)
        """
        now = now or time.time()
        try:
            mtime = session_path.stat().st_mtime
        except FileNotFoundError:
            return False

        last_seen = self.session_mtimes.get(str(session_path))
        if last_seen is not None and abs(mtime - last_seen) < 1e-3:
            # Already analyzed this exact version.
            return False

        if (now - mtime) < SESSION_QUIET_SECONDS:
            # Session still active — wait for it to settle.
            return False

        return True

    def mark_analyzed(self, session_path: Path) -> None:
        try:
            mtime = session_path.stat().st_mtime
        except FileNotFoundError:
            return
        self.session_mtimes[str(session_path)] = mtime

    # ----- rate-limit ----------------------------------------------------

    @staticmethod
    def _bucket(now: float | None = None) -> str:
        t = time.gmtime(now or time.time())
        return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}-{t.tm_hour:02d}"

    def analyses_this_hour(self, now: float | None = None) -> int:
        return self.hourly_counts.get(self._bucket(now), 0)

    def should_run_now(self, queue_size: int, now: float | None = None) -> bool:
        """Throttle: 3/h normally, 10/h during 02h-06h local off-peak."""
        if queue_size <= 0:
            return False
        local = time.localtime(now or time.time())
        cap = 10 if 2 <= local.tm_hour < 6 else 3
        return self.analyses_this_hour(now) < cap

    def increment_analysis_count(self, now: float | None = None) -> None:
        bucket = self._bucket(now)
        self.hourly_counts[bucket] = self.hourly_counts.get(bucket, 0) + 1


__all__ = [
    "DEFAULT_STATE_PATH",
    "SESSION_QUIET_SECONDS",
    "PostmortemState",
]
