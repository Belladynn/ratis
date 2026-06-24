#!/usr/bin/env python3
"""Check admin token expiry and send n8n alert if < 60 days.

Intended to be called daily via a systemd timer on the Mac mini.
Exit 0 on success (including when an alert is sent).
Exit 1 on unexpected runtime errors.

Environment variables
---------------------
SECRETS_EXPIRY_THRESHOLD_DAYS   int, default 60
SECRETS_EXPIRY_N8N_WEBHOOK_URL  required to send alerts
SECRETS_EXPIRY_DRY_RUN          "1" to skip HTTP POST (still reads DB)
RATIS_SECRETS_DB_PATH           override DB path (test isolation)
"""

from __future__ import annotations

import datetime
import os
import socket
import sys

# Make the src/ package importable when run as a standalone script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

import httpx
from agent_mcp.config import secrets_meta_db_file
from agent_mcp.secrets.meta_db import SecretMetaDB

EXPIRY_THRESHOLD_DAYS = int(os.environ.get("SECRETS_EXPIRY_THRESHOLD_DAYS", "60"))
N8N_WEBHOOK_URL = os.environ.get("SECRETS_EXPIRY_N8N_WEBHOOK_URL", "")
DRY_RUN = os.environ.get("SECRETS_EXPIRY_DRY_RUN", "0") == "1"

# Minimum interval between alerts for the same provider (in hours).
_ALERT_COOLDOWN_HOURS = 24


def _should_alert(last_alerted_at: str | None) -> bool:
    """Return True if the token has never been alerted or the cooldown has passed."""
    if last_alerted_at is None:
        return True
    try:
        last = datetime.datetime.fromisoformat(last_alerted_at)
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=_ALERT_COOLDOWN_HOURS)
        return last < cutoff
    except ValueError:
        # Unparseable date — treat as "never alerted" to be safe.
        return True


def _days_remaining(expires_at: str) -> int:
    """Return the number of full days remaining until expiry (may be negative)."""
    exp = datetime.datetime.fromisoformat(expires_at)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=datetime.UTC)
    delta = exp - datetime.datetime.now(datetime.UTC)
    return int(delta.total_seconds() // 86400)


def run() -> int:
    """Main logic. Returns exit code (0 = success, 1 = unexpected error)."""
    try:
        db = SecretMetaDB(secrets_meta_db_file())
        expiring = db.get_expiring_soon(days=EXPIRY_THRESHOLD_DAYS)

        hostname = socket.gethostname()

        for row in expiring:
            provider = row["provider"]
            expires_at = row["expires_at"]
            last_alerted_at = row.get("last_alerted_at")

            if not _should_alert(last_alerted_at):
                print(f"[check_expiry] skip {provider}: alerted recently ({last_alerted_at})")
                continue

            days = _days_remaining(expires_at)
            payload = {
                "type": "admin_token_expiry",
                "provider": provider,
                "expires_at": expires_at,
                "days_remaining": days,
                "hostname": hostname,
            }

            if DRY_RUN:
                print(f"[check_expiry][dry-run] would alert {provider}: {days}d remaining")
                continue

            if not N8N_WEBHOOK_URL:
                print(
                    f"[check_expiry] WARNING: {provider} expiring in {days}d but "
                    "SECRETS_EXPIRY_N8N_WEBHOOK_URL not set",
                    file=sys.stderr,
                )
                continue

            try:
                resp = httpx.post(N8N_WEBHOOK_URL, json=payload, timeout=10)
                resp.raise_for_status()
                now_str = datetime.datetime.now(datetime.UTC).isoformat()
                db.mark_alerted(provider=provider, alerted_at=now_str)
                print(f"[check_expiry] alert sent for {provider} ({days}d remaining)")
            except Exception as exc:
                print(f"[check_expiry] ERROR sending alert for {provider}: {exc}", file=sys.stderr)

        return 0

    except Exception as exc:
        print(f"[check_expiry] FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run())
