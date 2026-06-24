#!/usr/bin/env python3
"""GitHub watcher — poll Belladynn/ratis, push deltas to Telegram.

Run via `hermes cron --no-agent --script github-watch.sh --deliver telegram`.
Stdout = the new events since last run (empty = silent, no Telegram spam).

Detects, since the last run:
  - PRs newly OPENED
  - PRs newly MERGED
  - CI runs that FAILED on push/pull_request (NOT scheduled batch crons —
    those fail daily and are noise).

State (seen IDs + last cursor) in /opt/data/state/github-watch-state.json,
so each event is reported exactly once. First run primes state silently.

Zero new attack surface: no public ingress, reuses DIGEST_GITHUB_TOKEN.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env(Path("/opt/data/.env"))

TOKEN = os.environ.get("DIGEST_GITHUB_TOKEN", "")
REPO = os.environ.get("DIGEST_REPO", "Belladynn/ratis")
STATE = Path(os.environ.get(
    "GITHUB_WATCH_STATE", "/opt/data/state/github-watch-state.json"))
# Failed-run events worth alerting on (code CI), NOT scheduled batch crons.
ALERT_EVENTS = {"push", "pull_request", "merge_group"}


def _api(path: str) -> list | dict:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        return {"_error": str(e)}


def _load_state() -> dict:
    if STATE.is_file():
        try:
            return json.loads(STATE.read_text())
        except json.JSONDecodeError:
            pass
    return {"seen_open": [], "seen_merged": [], "seen_failed_runs": [], "primed": False}


def main() -> None:
    if not TOKEN:
        return  # silent — misconfigured, don't spam
    st = _load_state()
    seen_open = set(st.get("seen_open", []))
    seen_merged = set(st.get("seen_merged", []))
    seen_failed = set(st.get("seen_failed_runs", []))
    primed = st.get("primed", False)

    lines: list[str] = []

    # --- PRs (open + recently updated, captures merges) ---
    prs = _api(f"/repos/{REPO}/pulls?state=all&sort=updated&direction=desc&per_page=30")
    new_open, new_merged = [], []
    if isinstance(prs, list):
        for pr in prs:
            num = pr.get("number")
            if pr.get("state") == "open" and num not in seen_open:
                new_open.append(pr)
                seen_open.add(num)
            if pr.get("merged_at") and num not in seen_merged:
                new_merged.append(pr)
                seen_merged.add(num)
                seen_open.discard(num)

    # --- Failed CI runs (code events only, not scheduled batch) ---
    runs = _api(f"/repos/{REPO}/actions/runs?status=failure&per_page=30")
    new_failed = []
    if isinstance(runs, dict) and isinstance(runs.get("workflow_runs"), list):
        for run in runs["workflow_runs"]:
            rid = run.get("id")
            if (run.get("event") in ALERT_EVENTS
                    and run.get("conclusion") == "failure"
                    and rid not in seen_failed):
                new_failed.append(run)
                seen_failed.add(rid)

    # First run: prime state, stay silent (avoid dumping all history).
    if not primed:
        st.update({
            "seen_open": sorted(seen_open), "seen_merged": sorted(seen_merged),
            "seen_failed_runs": sorted(seen_failed)[-100:], "primed": True,
        })
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st))
        return

    # Build the message from genuine deltas.
    if new_merged:
        lines.append("✅ *PR mergées*")
        for pr in new_merged:
            lines.append(f"  #{pr['number']} {pr['title'][:60]}")
    if new_open:
        lines.append("🟪 *PR ouvertes*")
        for pr in new_open:
            draft = " [draft]" if pr.get("draft") else ""
            lines.append(f"  #{pr['number']} {pr['title'][:60]}{draft}")
    if new_failed:
        lines.append("🔴 *CI échouée (code)*")
        for run in new_failed:
            wf = run.get("name", "?")[:30]
            br = run.get("head_branch", "?")
            lines.append(f"  {wf} · {br} · {run.get('html_url', '')}")

    # Persist state (cap lists to avoid unbounded growth).
    st.update({
        "seen_open": sorted(seen_open)[-200:],
        "seen_merged": sorted(seen_merged)[-200:],
        "seen_failed_runs": sorted(seen_failed)[-100:],
        "primed": True,
        "last_run": datetime.now(UTC).isoformat(),
    })
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st))

    if lines:
        print("🐙 *GitHub* — " + REPO + "\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
