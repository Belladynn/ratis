"""Sentry → Notion webhook server.

Receives Sentry Issue Alert webhooks and creates/updates tickets
in the Notion 📋 Backlog database.

Run:
    uv run uvicorn tools.sentry_webhook:app --port 8099 --reload

Expose via ngrok:
    ngrok http 8099

Env vars (tools/.env.local):
    SENTRY_WEBHOOK_SECRET  — HMAC secret configured in Sentry Alert webhook
    NOTION_TOKEN           — Notion integration token (secret_...)
    NOTION_DATABASE_ID     — Target Notion database ID
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

# Load tools/.env.local if present (no-op in tests where env is pre-set).
load_dotenv(Path(__file__).parent / ".env.local")

logger = logging.getLogger(__name__)
app = FastAPI(title="Sentry → Notion Webhook")

# Fail-fast at import time — surfaced immediately on misconfigured deploy.
SENTRY_WEBHOOK_SECRET: str = os.environ["SENTRY_WEBHOOK_SECRET"]
NOTION_TOKEN: str = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID: str = os.environ["NOTION_DATABASE_ID"]

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


def verify_signature(body: bytes, signature_header: str) -> bool:
    """Verify Sentry HMAC-SHA256 webhook signature.

    Accepts both 'sha256=<hex>' and bare '<hex>' formats.
    """
    received = signature_header.removeprefix("sha256=")
    expected = hmac.new(
        SENTRY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, received)


# ---------------------------------------------------------------------------
# Mapping tables
# ---------------------------------------------------------------------------

_LEVEL_TO_PRIORITY: dict[str, str] = {
    "fatal": "🔴 P0 bloquant",
    "error": "🔴 P0 bloquant",
    "warning": "🟠 P1 important",
    "info": "🟡 P2 nice-to-have",
}

_SLUG_TO_SERVICE: dict[str, str] = {
    "ratis-rewards": "ratis_rewards",
    "ratis-auth": "ratis_auth",
    "ratis-product-analyser": "ratis_product_analyser",
    "ratis-list-optimiser": "ratis_list_optimiser",
    "ratis-notifier": "ratis_notifier",
    "ratis-core": "ratis_core",
}


def map_level_to_priority(level: str) -> str | None:
    return _LEVEL_TO_PRIORITY.get(level)


def map_slug_to_service(slug: str) -> str | None:
    if slug.startswith("ratis-batch"):
        return "ratis_batch"
    return _SLUG_TO_SERVICE.get(slug)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class SentryIssue:
    id: str
    title: str
    level: str
    project_slug: str
    culprit: str
    permalink: str
    count: str
    first_seen: str
    last_seen: str


def parse_issue(payload: dict) -> SentryIssue:
    """Extract SentryIssue from a Sentry Issue Alert webhook payload."""
    issue = payload["data"]["issue"]
    return SentryIssue(
        id=str(issue["id"]),
        title=issue["title"],
        level=issue.get("level", "error"),
        project_slug=issue.get("project", {}).get("slug", ""),
        culprit=issue.get("culprit", ""),
        permalink=issue.get("permalink", ""),
        count=str(issue.get("count", "?")),
        first_seen=issue.get("firstSeen", ""),
        last_seen=issue.get("lastSeen", ""),
    )


# ---------------------------------------------------------------------------
# Notion API client
# ---------------------------------------------------------------------------


def _notion_request(method: str, path: str, **kwargs) -> dict:
    """Low-level Notion API call. Raises httpx.HTTPStatusError on non-2xx."""
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=10) as client:
        resp = getattr(client, method)(f"{NOTION_API}{path}", headers=headers, **kwargs)
    resp.raise_for_status()
    return resp.json()


def find_existing_ticket(issue_id: str) -> tuple[str, str] | None:
    """Search Notion for a ticket tagged [S-{issue_id}].

    Returns (page_id, current_statut) or None if not found.
    """
    result = _notion_request(
        "post",
        f"/databases/{NOTION_DATABASE_ID}/query",
        json={
            "filter": {
                "property": "Titre",
                "title": {"contains": f"[S-{issue_id}]"},
            },
            "page_size": 1,
        },
    )
    pages = result.get("results", [])
    if not pages:
        return None
    page = pages[0]
    statut = (page.get("properties", {}).get("Statut", {}).get("select") or {}).get("name", "Backlog")
    return page["id"], statut


# ---------------------------------------------------------------------------
# Page content builders
# ---------------------------------------------------------------------------


def _text_block(block_type: str, content: str) -> dict:
    """Helper: build a simple Notion block with a single text run."""
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": [{"type": "text", "text": {"content": content}}]},
    }


def _build_page_blocks(issue: SentryIssue) -> list[dict]:
    """Build the initial page body for a new Sentry ticket."""
    now = datetime.now(UTC).isoformat()
    return [
        _text_block("heading_2", "Infos Sentry"),
        _text_block("bulleted_list_item", f"ID issue : S-{issue.id}"),
        _text_block("bulleted_list_item", f"Niveau : {issue.level}"),
        _text_block("bulleted_list_item", f"Service : {issue.project_slug}"),
        _text_block("bulleted_list_item", f"Culprit : {issue.culprit}"),
        _text_block("bulleted_list_item", f"Première occurrence : {issue.first_seen}"),
        _text_block("bulleted_list_item", f"Dernière occurrence : {issue.last_seen}"),
        _text_block("bulleted_list_item", f"Occurrences : {issue.count}"),
        {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [
                    {"type": "text", "text": {"content": "Lien Sentry : "}},
                    {
                        "type": "text",
                        "text": {
                            "content": issue.permalink,
                            "link": {"url": issue.permalink},
                        },
                    },
                ]
            },
        },
        _text_block("paragraph", f"Créé automatiquement par sentry_webhook.py le {now}"),
    ]


def _build_reappearance_blocks(issue: SentryIssue) -> list[dict]:
    """Build blocks appended on each reappearance."""
    now = datetime.now(UTC).isoformat()
    return [
        {"object": "block", "type": "divider", "divider": {}},
        _text_block("heading_2", f"Réapparition — {now}"),
        _text_block("bulleted_list_item", f"Occurrences : {issue.count}"),
        _text_block("bulleted_list_item", f"Dernière vue : {issue.last_seen}"),
    ]


# ---------------------------------------------------------------------------
# Notion write operations
# ---------------------------------------------------------------------------


def create_notion_ticket(issue: SentryIssue) -> None:
    """Create a new Bug ticket in the Notion Backlog."""
    service = map_slug_to_service(issue.project_slug)
    priority = map_level_to_priority(issue.level)
    title = f"[Bug][{issue.project_slug}] {issue.title} [S-{issue.id}]"

    properties: dict = {
        "Titre": {"title": [{"text": {"content": title}}]},
        "Type": {"select": {"name": "Bug"}},
        "Statut": {"select": {"name": "Backlog"}},
    }
    if priority:
        properties["Priorité"] = {"select": {"name": priority}}
    if service:
        properties["Service"] = {"select": {"name": service}}

    _notion_request(
        "post",
        "/pages",
        json={
            "parent": {"database_id": NOTION_DATABASE_ID},
            "properties": properties,
            "children": _build_page_blocks(issue),
        },
    )
    logger.info("Created Notion ticket for Sentry issue S-%s", issue.id)


def update_notion_ticket(page_id: str, issue: SentryIssue, current_statut: str) -> None:
    """Update an existing ticket on Sentry issue reappearance.

    - Appends a dated "Réapparition" section to the page body.
    - If the ticket was Terminé (regression), resets Statut to "En cours".
    """
    if current_statut == "Terminé":
        _notion_request(
            "patch",
            f"/pages/{page_id}",
            json={"properties": {"Statut": {"select": {"name": "En cours"}}}},
        )
        logger.info("Regression for S-%s — reset Statut to 'En cours'", issue.id)

    _notion_request(
        "patch",
        f"/blocks/{page_id}/children",
        json={"children": _build_reappearance_blocks(issue)},
    )
    logger.info("Updated Notion ticket for Sentry issue S-%s", issue.id)


# ---------------------------------------------------------------------------
# FastAPI endpoint
# ---------------------------------------------------------------------------


@app.post("/webhook")
async def receive_webhook(
    request: Request,
    sentry_hook_signature: str = Header(...),
) -> JSONResponse:
    """Receive a Sentry Issue Alert webhook.

    Verifies HMAC-SHA256 signature, parses the payload, and creates
    or updates a ticket in the Notion 📋 Backlog database.
    """
    body = await request.body()

    if not verify_signature(body, sentry_hook_signature):
        raise HTTPException(status_code=401, detail="invalid_signature")

    payload = json.loads(body)
    action = payload.get("action")

    if action != "triggered":
        return JSONResponse({"status": "ignored", "action": action})

    try:
        issue = parse_issue(payload)
    except (KeyError, TypeError) as exc:
        logger.warning("sentry_webhook: malformed payload: %s", exc)
        raise HTTPException(status_code=422, detail="malformed_payload")

    try:
        existing = find_existing_ticket(issue.id)

        if existing is None:
            create_notion_ticket(issue)
            return JSONResponse({"status": "created", "issue_id": issue.id})

        page_id, current_statut = existing
        update_notion_ticket(page_id, issue, current_statut)
        return JSONResponse(
            {
                "status": "updated",
                "issue_id": issue.id,
                "regression": current_statut == "Terminé",
            }
        )

    except Exception as exc:
        logger.exception("sentry_webhook: unhandled error for payload action=%s: %s", action, exc)
        raise HTTPException(status_code=500, detail="internal_error")
