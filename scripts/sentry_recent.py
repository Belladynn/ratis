"""Sentry recent issues + events — read-only helper.

Fetches the most recent issues (or events of a specific issue) from the
Sentry REST API. Lets the orchestrator triage a freshly-reported alpha bug
without copy-pasting from the Sentry UI.

Usage:
    uv run python scripts/sentry_recent.py            # last 5 issues, 1h
    uv run python scripts/sentry_recent.py --hours 24 # last 24h
    uv run python scripts/sentry_recent.py --limit 10
    uv run python scripts/sentry_recent.py <issue_id> # latest event of issue

Reads tools/.env.local for SENTRY_API_TOKEN, SENTRY_ORG, SENTRY_PROJECT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_LOCAL = ROOT / "tools" / ".env.local"


def load_env() -> tuple[str, str, str]:
    if not ENV_LOCAL.exists():
        sys.exit(
            f"Missing {ENV_LOCAL}. Copy tools/.env.example and fill in SENTRY_API_TOKEN / SENTRY_ORG / SENTRY_PROJECT."
        )
    load_dotenv(ENV_LOCAL)
    token = os.getenv("SENTRY_API_TOKEN", "").strip()
    org = os.getenv("SENTRY_ORG", "").strip()
    project = os.getenv("SENTRY_PROJECT", "").strip()
    if not token or not org or not project:
        sys.exit(f"SENTRY_API_TOKEN / SENTRY_ORG / SENTRY_PROJECT must all be set in {ENV_LOCAL}.")
    return token, org, project


def issues(token: str, org: str, project: str, hours: int, limit: int) -> None:
    url = f"https://sentry.io/api/0/projects/{org}/{project}/issues/"
    r = httpx.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params={"statsPeriod": f"{hours}h", "limit": limit},
        timeout=10.0,
    )
    r.raise_for_status()
    for issue in r.json():
        print(f"[{issue['shortId']}] {issue['title']}")
        print(f"  level={issue['level']}  count={issue['count']}  users={issue['userCount']}")
        print(f"  lastSeen={issue['lastSeen']}")
        print(f"  culprit={issue.get('culprit') or '<none>'}")
        print(f"  permalink={issue['permalink']}")
        print()


def latest_event(token: str, org: str, issue_id: str) -> None:
    url = f"https://sentry.io/api/0/organizations/{org}/issues/{issue_id}/events/latest/"
    r = httpx.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    r.raise_for_status()
    event = r.json()
    print(f"Title:    {event.get('title')}")
    print(f"Message:  {event.get('message')}")
    print(f"Platform: {event.get('platform')}")
    print(f"DateRecv: {event.get('dateReceived')}")
    print("Tags:")
    for tag in event.get("tags", []):
        print(f"  {tag['key']}={tag['value']}")
    print()
    print("=== Context ===")
    contexts = event.get("contexts", {})
    for k in ("os", "device", "app", "runtime"):
        if k in contexts:
            print(f"{k}: {json.dumps(contexts[k], indent=2)}")
    print()
    print("=== Extra (logger.error attachments) ===")
    extra = event.get("extra") or {}
    if extra:
        print(json.dumps(extra, indent=2))
    else:
        print("<none>")
    print()
    print("=== Stack (top 15 frames) ===")
    for entry in event.get("entries", []):
        if entry.get("type") == "exception":
            for value in entry.get("data", {}).get("values", []):
                print(f"-- {value.get('type')}: {value.get('value')} --")
                frames = (value.get("stacktrace") or {}).get("frames", [])
                for f in frames[-15:]:
                    print(f"  {f.get('filename', '?')}:{f.get('lineno', '?')} in {f.get('function', '?')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issue", nargs="?", help="Issue short ID (e.g. RATIS-CLIENT-1)")
    parser.add_argument("--hours", type=int, default=1, help="Lookback window (default 1h)")
    parser.add_argument("--limit", type=int, default=5, help="Max issues (default 5)")
    args = parser.parse_args()

    token, org, project = load_env()
    if args.issue:
        latest_event(token, org, args.issue)
    else:
        issues(token, org, project, args.hours, args.limit)


if __name__ == "__main__":
    main()
