"""Tests for tools/sentry_webhook.py"""

from __future__ import annotations

import hashlib
import hmac
import os

# Set env var before importing module to avoid fail-fast on missing secret.
os.environ["SENTRY_WEBHOOK_SECRET"] = "test-secret-for-tests"
os.environ.setdefault("NOTION_TOKEN", "test-token")
os.environ.setdefault("NOTION_DATABASE_ID", "test-db-id")

import pytest

from tools.sentry_webhook import (
    SentryIssue,
    create_notion_ticket,
    map_level_to_priority,
    map_slug_to_service,
    parse_issue,
    verify_signature,
)

BODY = b'{"action": "triggered", "data": {}}'
SECRET = "test-secret-for-tests"


def _sign(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


FIXTURE_PAYLOAD = {
    "action": "triggered",
    "data": {
        "issue": {
            "id": "123456789",
            "title": "ZeroDivisionError: division by zero",
            "level": "error",
            "culprit": "services/optimization_service.py in _compute",
            "project": {"slug": "ratis-list-optimiser"},
            "permalink": "https://sentry.io/organizations/ratis/issues/123456789/",
            "count": "42",
            "firstSeen": "2026-04-17T10:00:00Z",
            "lastSeen": "2026-04-17T14:32:00Z",
        }
    },
}


class TestVerifySignature:
    def test_valid_signature_passes(self):
        assert verify_signature(BODY, _sign(BODY)) is True

    def test_invalid_signature_fails(self):
        assert verify_signature(BODY, "sha256=deadbeef") is False

    def test_wrong_body_fails(self):
        assert verify_signature(b"other body", _sign(BODY)) is False

    def test_without_sha256_prefix(self):
        # Some Sentry versions omit the prefix — both formats accepted.
        digest = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
        assert verify_signature(BODY, digest) is True


class TestMapLevelToPriority:
    @pytest.mark.parametrize(
        "level,expected",
        [
            ("fatal", "🔴 P0 bloquant"),
            ("error", "🔴 P0 bloquant"),
            ("warning", "🟠 P1 important"),
            ("info", "🟡 P2 nice-to-have"),
            ("unknown", None),
        ],
    )
    def test_mapping(self, level, expected):
        assert map_level_to_priority(level) == expected


class TestMapSlugToService:
    @pytest.mark.parametrize(
        "slug,expected",
        [
            ("ratis-rewards", "ratis_rewards"),
            ("ratis-auth", "ratis_auth"),
            ("ratis-product-analyser", "ratis_product_analyser"),
            ("ratis-list-optimiser", "ratis_list_optimiser"),
            ("ratis-notifier", "ratis_notifier"),
            ("ratis-core", "ratis_core"),
            ("ratis-batch-purge", "ratis_batch"),
            ("ratis-batch-consensus", "ratis_batch"),
            ("unknown-service", None),
        ],
    )
    def test_mapping(self, slug, expected):
        assert map_slug_to_service(slug) == expected


class TestParseIssue:
    def test_parses_all_fields(self):
        issue = parse_issue(FIXTURE_PAYLOAD)
        assert issue.id == "123456789"
        assert issue.title == "ZeroDivisionError: division by zero"
        assert issue.level == "error"
        assert issue.project_slug == "ratis-list-optimiser"
        assert issue.culprit == "services/optimization_service.py in _compute"
        assert issue.permalink == "https://sentry.io/organizations/ratis/issues/123456789/"
        assert issue.count == "42"
        assert issue.first_seen == "2026-04-17T10:00:00Z"
        assert issue.last_seen == "2026-04-17T14:32:00Z"

    def test_missing_optional_fields_use_defaults(self):
        minimal = {"action": "triggered", "data": {"issue": {"id": "1", "title": "Oops"}}}
        issue = parse_issue(minimal)
        assert issue.level == "error"
        assert issue.culprit == ""
        assert issue.count == "?"


from unittest.mock import patch

from tools.sentry_webhook import find_existing_ticket


class TestFindExistingTicket:
    def test_returns_none_when_not_found(self):
        mock_response = {"results": []}
        with patch("tools.sentry_webhook._notion_request", return_value=mock_response):
            result = find_existing_ticket("123456789")
        assert result is None

    def test_returns_page_id_and_statut_when_found(self):
        mock_response = {
            "results": [
                {
                    "id": "page-uuid-abc",
                    "properties": {"Statut": {"select": {"name": "Backlog"}}},
                }
            ]
        }
        with patch("tools.sentry_webhook._notion_request", return_value=mock_response):
            result = find_existing_ticket("123456789")
        assert result == ("page-uuid-abc", "Backlog")

    def test_returns_backlog_when_statut_missing(self):
        mock_response = {"results": [{"id": "page-uuid-xyz", "properties": {}}]}
        with patch("tools.sentry_webhook._notion_request", return_value=mock_response):
            _page_id, statut = find_existing_ticket("123456789")
        assert statut == "Backlog"

    def test_passes_correct_filter_to_notion(self):
        with patch("tools.sentry_webhook._notion_request", return_value={"results": []}) as mock_req:
            find_existing_ticket("999")
        call_kwargs = mock_req.call_args
        body = call_kwargs[1]["json"]
        assert body["filter"]["title"]["contains"] == "[S-999]"


_ISSUE = SentryIssue(
    id="123456789",
    title="ZeroDivisionError: division by zero",
    level="error",
    project_slug="ratis-list-optimiser",
    culprit="services/optimization_service.py in _compute",
    permalink="https://sentry.io/issues/123456789/",
    count="42",
    first_seen="2026-04-17T10:00:00Z",
    last_seen="2026-04-17T14:32:00Z",
)


class TestCreateNotionTicket:
    def test_calls_notion_post_pages(self):
        with patch("tools.sentry_webhook._notion_request") as mock_req:
            create_notion_ticket(_ISSUE)
        mock_req.assert_called_once()
        method, path = mock_req.call_args[0]
        assert method == "post"
        assert path == "/pages"

    def test_title_contains_sentry_tag(self):
        with patch("tools.sentry_webhook._notion_request") as mock_req:
            create_notion_ticket(_ISSUE)
        body = mock_req.call_args[1]["json"]
        title_content = body["properties"]["Titre"]["title"][0]["text"]["content"]
        assert "[S-123456789]" in title_content
        assert "ZeroDivisionError" in title_content

    def test_type_is_bug(self):
        with patch("tools.sentry_webhook._notion_request") as mock_req:
            create_notion_ticket(_ISSUE)
        props = mock_req.call_args[1]["json"]["properties"]
        assert props["Type"]["select"]["name"] == "Bug"

    def test_statut_is_backlog(self):
        with patch("tools.sentry_webhook._notion_request") as mock_req:
            create_notion_ticket(_ISSUE)
        props = mock_req.call_args[1]["json"]["properties"]
        assert props["Statut"]["select"]["name"] == "Backlog"

    def test_priority_mapped_from_level(self):
        with patch("tools.sentry_webhook._notion_request") as mock_req:
            create_notion_ticket(_ISSUE)
        props = mock_req.call_args[1]["json"]["properties"]
        assert props["Priorité"]["select"]["name"] == "🔴 P0 bloquant"

    def test_service_mapped_from_slug(self):
        with patch("tools.sentry_webhook._notion_request") as mock_req:
            create_notion_ticket(_ISSUE)
        props = mock_req.call_args[1]["json"]["properties"]
        assert props["Service"]["select"]["name"] == "ratis_list_optimiser"

    def test_unknown_service_omitted(self):
        unknown_issue = SentryIssue(
            id="1",
            title="Err",
            level="error",
            project_slug="unknown-service",
            culprit="",
            permalink="",
            count="1",
            first_seen="",
            last_seen="",
        )
        with patch("tools.sentry_webhook._notion_request") as mock_req:
            create_notion_ticket(unknown_issue)
        props = mock_req.call_args[1]["json"]["properties"]
        assert "Service" not in props

    def test_page_body_blocks_included(self):
        with patch("tools.sentry_webhook._notion_request") as mock_req:
            create_notion_ticket(_ISSUE)
        body = mock_req.call_args[1]["json"]
        assert len(body["children"]) > 0


from tools.sentry_webhook import update_notion_ticket


class TestUpdateNotionTicket:
    def test_appends_reappearance_blocks(self):
        with patch("tools.sentry_webhook._notion_request") as mock_req:
            update_notion_ticket("page-id", _ISSUE, current_statut="Backlog")
        # One call: append blocks
        calls = mock_req.call_args_list
        patch_blocks = [c for c in calls if c[0][1].startswith("/blocks/")]
        assert len(patch_blocks) == 1
        children = patch_blocks[0][1]["json"]["children"]
        assert any("Réapparition" in str(b) for b in children)

    def test_no_status_update_when_not_termine(self):
        with patch("tools.sentry_webhook._notion_request") as mock_req:
            update_notion_ticket("page-id", _ISSUE, current_statut="En cours")
        calls = mock_req.call_args_list
        page_patches = [c for c in calls if c[0][1] == "/pages/page-id"]
        assert len(page_patches) == 0

    def test_resets_status_to_en_cours_on_regression(self):
        with patch("tools.sentry_webhook._notion_request") as mock_req:
            update_notion_ticket("page-id", _ISSUE, current_statut="Terminé")
        calls = mock_req.call_args_list
        page_patches = [c for c in calls if c[0][1] == "/pages/page-id"]
        assert len(page_patches) == 1
        new_statut = page_patches[0][1]["json"]["properties"]["Statut"]["select"]["name"]
        assert new_statut == "En cours"

    def test_regression_also_appends_blocks(self):
        with patch("tools.sentry_webhook._notion_request") as mock_req:
            update_notion_ticket("page-id", _ISSUE, current_statut="Terminé")
        calls = mock_req.call_args_list
        block_patches = [c for c in calls if "/blocks/" in c[0][1]]
        assert len(block_patches) == 1


import json as json_lib

from fastapi.testclient import TestClient

from tools.sentry_webhook import app

client = TestClient(app)


def _make_request(payload: dict, secret: str = SECRET) -> object:
    """Helper: sign and POST a webhook payload."""
    import hashlib
    import hmac as hmac_lib

    body = json_lib.dumps(payload).encode()
    sig = hmac_lib.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "sentry-hook-signature": f"sha256={sig}",
            "content-type": "application/json",
        },
    )


class TestWebhookEndpoint:
    def test_invalid_signature_returns_401(self):
        body = json_lib.dumps(FIXTURE_PAYLOAD).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "sentry-hook-signature": "sha256=invalid",
                "content-type": "application/json",
            },
        )
        assert resp.status_code == 401

    def test_non_triggered_action_ignored(self):
        payload = {**FIXTURE_PAYLOAD, "action": "resolved"}
        resp = _make_request(payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_new_issue_creates_ticket(self):
        with patch("tools.sentry_webhook.find_existing_ticket", return_value=None):
            with patch("tools.sentry_webhook.create_notion_ticket") as mock_create:
                resp = _make_request(FIXTURE_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"
        mock_create.assert_called_once()
        created_issue = mock_create.call_args[0][0]
        assert created_issue.id == "123456789"

    def test_existing_issue_updates_ticket(self):
        with patch("tools.sentry_webhook.find_existing_ticket", return_value=("page-id", "Backlog")):
            with patch("tools.sentry_webhook.update_notion_ticket") as mock_update:
                resp = _make_request(FIXTURE_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        mock_update.assert_called_once_with("page-id", mock_update.call_args[0][1], "Backlog")

    def test_regression_flagged_in_response(self):
        with patch("tools.sentry_webhook.find_existing_ticket", return_value=("page-id", "Terminé")):
            with patch("tools.sentry_webhook.update_notion_ticket"):
                resp = _make_request(FIXTURE_PAYLOAD)
        assert resp.json()["regression"] is True

    def test_notion_error_returns_500(self):
        import httpx as httpx_lib

        err = httpx_lib.HTTPStatusError("err", request=None, response=None)
        with patch("tools.sentry_webhook.find_existing_ticket", side_effect=err):
            resp = _make_request(FIXTURE_PAYLOAD)
        assert resp.status_code == 500
