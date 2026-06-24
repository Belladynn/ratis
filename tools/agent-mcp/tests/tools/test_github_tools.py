"""TDD coverage for `agent_mcp.tools.github_tools`.

Strategy
--------
* The 5 GitHub tools are pure Python functions wrapping `httpx.Client` calls
  against `api.github.com`.
* We inject an `httpx.MockTransport` so no real network is touched and we can
  assert exactly what HTTP request the tool issued (URL, method, headers, body).
* `Keychain.get` is monkeypatched to return a fake token — never the real one.
* Audit assertions go through the `Dispatcher` so we cover the full
  registration + dispatch + audit pipeline (the same code path Claude will
  exercise at runtime).

Token-leak guard (security-critical)
------------------------------------
Several tests assert :
* the fake token never appears in the audit JSONL ;
* the substring ``Bearer`` (i.e. the Authorization header) never leaks either ;
* the cross-tool sweep walks every captured request to verify the fake token
  is only in headers, never in URLs / bodies / arg values.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_mcp import keychain as keychain_mod
from agent_mcp.audit import AuditLog
from agent_mcp.auth import AuthGate
from agent_mcp.errors import KeychainMiss, ProviderError
from agent_mcp.server import Dispatcher
from agent_mcp.tools import github_tools

FAKE_TOKEN = "ghp_unit_test_DO_NOT_LEAK"  # pragma: allowlist secret


# -- shared fixtures ------------------------------------------------------


@pytest.fixture
def fake_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Patch `Keychain.get` so the GitHub tools see a fake token under account 'github'."""

    def _fake_get(self: keychain_mod.Keychain, account: str) -> str:
        assert account == "github", f"unexpected keychain account {account!r}"
        return FAKE_TOKEN

    monkeypatch.setattr(keychain_mod.Keychain, "get", _fake_get)
    return FAKE_TOKEN


@pytest.fixture
def captured_requests() -> list[httpx.Request]:
    """List populated by the mock transport with every outbound request."""
    return []


@pytest.fixture
def install_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
    captured_requests: list[httpx.Request],
) -> Iterator[dict[str, Any]]:
    """Replace `github_tools._build_client` so it returns a client wired to a
    `httpx.MockTransport`. Tests pre-set `responder` to control the response.

    Returns the dict the test mutates : `{"responder": <callable>}`.
    """
    state: dict[str, Any] = {
        "responder": lambda req: httpx.Response(200, json={}),  # default OK
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return state["responder"](request)

    transport = httpx.MockTransport(_handler)

    real_build = github_tools._build_client

    def fake_build_client(token: str) -> httpx.Client:
        # Mirror the real builder configuration but swap in the mock transport.
        return httpx.Client(
            base_url=github_tools.GITHUB_BASE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            transport=transport,
            timeout=github_tools.HTTP_TIMEOUT_SEC,
        )

    monkeypatch.setattr(github_tools, "_build_client", fake_build_client)
    yield state
    github_tools._build_client = real_build  # type: ignore[assignment]


@pytest.fixture
def github_repo(monkeypatch: pytest.MonkeyPatch) -> str:
    """Force `RATIS_GITHUB_REPO` to a deterministic value for URL assertions."""
    monkeypatch.setenv("RATIS_GITHUB_REPO", "Belladynn/ratis")
    return "Belladynn/ratis"


@pytest.fixture
def dispatcher(tmp_path: Path) -> Dispatcher:
    """Dispatcher backed by a temp audit log + admin/ops tokens."""
    auth = AuthGate(admin_token="ADMIN_TOK", ops_token="OPS_TOK")
    audit = AuditLog(tmp_path / "audit.log")
    github_tools.register_all()
    return Dispatcher(auth=auth, audit=audit)


def _audit_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# -- happy paths ----------------------------------------------------------


def test_list_prs_happy_path(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """`github_list_prs` issues a GET to /pulls and returns trimmed entries."""
    fake_response = [
        {
            "number": 297,
            "title": "feat: agent mcp",
            "state": "open",
            "draft": False,
            "user": {"login": "Belladyn", "id": 42, "avatar_url": "https://x"},
            "html_url": "https://github.com/Belladynn/ratis/pull/297",
            "head": {"ref": "feat/agent-mcp", "sha": "deadbeef"},
            "base": {"ref": "main", "sha": "cafef00d"},
            "labels": [{"name": "infra", "color": "blue"}, {"name": "wip"}],
            "created_at": "2026-05-01T10:00:00Z",
            "updated_at": "2026-05-02T11:00:00Z",
            "body": "huge body that should be dropped from list view",
        },
        {
            "number": 296,
            "title": "docs",
            "state": "open",
            "draft": True,
            "user": {"login": "octocat"},
            "html_url": "https://github.com/Belladynn/ratis/pull/296",
            "head": {"ref": "docs/x"},
            "base": {"ref": "main"},
            "labels": [],
            "created_at": "2026-04-30T10:00:00Z",
            "updated_at": "2026-04-30T12:00:00Z",
        },
    ]
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=fake_response)

    result = github_tools.github_list_prs(state="open", limit=2)

    assert result == [
        {
            "number": 297,
            "title": "feat: agent mcp",
            "state": "open",
            "draft": False,
            "user_login": "Belladyn",
            "html_url": "https://github.com/Belladynn/ratis/pull/297",
            "head_ref": "feat/agent-mcp",
            "base_ref": "main",
            "labels": ["infra", "wip"],
            "created_at": "2026-05-01T10:00:00Z",
            "updated_at": "2026-05-02T11:00:00Z",
        },
        {
            "number": 296,
            "title": "docs",
            "state": "open",
            "draft": True,
            "user_login": "octocat",
            "html_url": "https://github.com/Belladynn/ratis/pull/296",
            "head_ref": "docs/x",
            "base_ref": "main",
            "labels": [],
            "created_at": "2026-04-30T10:00:00Z",
            "updated_at": "2026-04-30T12:00:00Z",
        },
    ]
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "GET"
    assert req.url.path == "/repos/Belladynn/ratis/pulls"
    assert req.url.params["state"] == "open"
    assert req.url.params["per_page"] == "2"
    assert req.headers["Authorization"] == f"Bearer {fake_token}"
    assert req.headers["Accept"] == "application/vnd.github+json"
    assert req.headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_list_prs_default_state(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """Default state is 'open' (per ARCH § Module 3)."""
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=[])

    github_tools.github_list_prs()

    req = captured_requests[0]
    assert req.url.params["state"] == "open"
    assert req.url.params["per_page"] == "20"


def test_get_pr_happy_path(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    fake_response = {
        "number": 297,
        "title": "feat: agent mcp",
        "state": "open",
        "draft": False,
        "merged": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "user": {"login": "Belladyn", "id": 42},
        "html_url": "https://github.com/Belladynn/ratis/pull/297",
        "head": {"ref": "feat/agent-mcp", "sha": "deadbee" + "0" * 33},
        "base": {"ref": "main", "sha": "cafef00d"},
        "labels": [{"name": "infra"}, {"name": "mcp"}],
        "additions": 1200,
        "deletions": 30,
        "changed_files": 14,
        "commits": 8,
        "created_at": "2026-05-01T10:00:00Z",
        "updated_at": "2026-05-02T11:00:00Z",
        "body": "PR description",
        "_links": {"self": {"href": "drop me"}},
        "node_id": "drop me too",
    }
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=fake_response)

    result = github_tools.github_get_pr(pr_number=297)

    assert result == {
        "number": 297,
        "title": "feat: agent mcp",
        "state": "open",
        "draft": False,
        "merged": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "user_login": "Belladyn",
        "html_url": "https://github.com/Belladynn/ratis/pull/297",
        "head_ref": "feat/agent-mcp",
        "base_ref": "main",
        "labels": ["infra", "mcp"],
        "additions": 1200,
        "deletions": 30,
        "changed_files": 14,
        "commits": 8,
        "created_at": "2026-05-01T10:00:00Z",
        "updated_at": "2026-05-02T11:00:00Z",
        "body": "PR description",
    }
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "GET"
    assert req.url.path == "/repos/Belladynn/ratis/pulls/297"


def test_list_check_runs_resolves_head_sha_first(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """`github_list_check_runs` first GETs the PR (for head SHA) then GETs check-runs."""
    head_sha = "abc" + "0" * 37

    def _responder(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/repos/Belladynn/ratis/pulls/297":
            return httpx.Response(200, json={"number": 297, "head": {"sha": head_sha}})
        if req.url.path == f"/repos/Belladynn/ratis/commits/{head_sha}/check-runs":
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "check_runs": [
                        {
                            "id": 1,
                            "name": "lint",
                            "status": "completed",
                            "conclusion": "success",
                            "html_url": "https://github.com/Belladynn/ratis/runs/1",
                            "started_at": "2026-05-02T10:00:00Z",
                            "completed_at": "2026-05-02T10:02:00Z",
                            "output": {"summary": "huge bloat", "annotations_count": 99},
                            "app": {"id": 7, "name": "GitHub Actions"},
                        },
                        {
                            "id": 2,
                            "name": "tests",
                            "status": "completed",
                            "conclusion": "failure",
                            "html_url": "https://github.com/Belladynn/ratis/runs/2",
                            "started_at": "2026-05-02T10:00:00Z",
                            "completed_at": "2026-05-02T10:05:00Z",
                            "output": {"text": "1000 lines of log"},
                        },
                    ],
                },
            )
        return httpx.Response(404, json={"message": "unexpected"})

    install_mock_transport["responder"] = _responder

    result = github_tools.github_list_check_runs(pr_number=297)

    assert result == [
        {
            "name": "lint",
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.com/Belladynn/ratis/runs/1",
            "started_at": "2026-05-02T10:00:00Z",
            "completed_at": "2026-05-02T10:02:00Z",
        },
        {
            "name": "tests",
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/Belladynn/ratis/runs/2",
            "started_at": "2026-05-02T10:00:00Z",
            "completed_at": "2026-05-02T10:05:00Z",
        },
    ]
    # Trimmed runs carry no `id` / `output` / `app`.
    assert all("id" not in r and "output" not in r and "app" not in r for r in result)
    # Two requests : PR fetch + check-runs fetch.
    assert len(captured_requests) == 2
    assert captured_requests[0].url.path == "/repos/Belladynn/ratis/pulls/297"
    assert captured_requests[1].url.path == f"/repos/Belladynn/ratis/commits/{head_sha}/check-runs"


def test_comment_pr_happy_path(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """`github_comment_pr` POSTs to /issues/<n>/comments with body JSON."""
    install_mock_transport["responder"] = lambda req: httpx.Response(201, json={"id": 123, "body": "looks good"})

    result = github_tools.github_comment_pr(pr_number=297, body="looks good")

    assert result == {"id": 123, "body": "looks good"}
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "POST"
    assert req.url.path == "/repos/Belladynn/ratis/issues/297/comments"
    body = json.loads(req.content.decode("utf-8"))
    assert body == {"body": "looks good"}


def test_rerun_failed_checks_happy_path(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """Fetch PR head sha → fetch check-runs → POST rerequest only on failures."""
    head_sha = "abc" + "0" * 37

    def _responder(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/repos/Belladynn/ratis/pulls/297":
            return httpx.Response(200, json={"number": 297, "head": {"sha": head_sha}})
        if req.method == "GET" and req.url.path.endswith("/check-runs"):
            return httpx.Response(
                200,
                json={
                    "total_count": 4,
                    "check_runs": [
                        {"id": 10, "conclusion": "success"},
                        {"id": 11, "conclusion": "failure"},
                        {"id": 12, "conclusion": "failure"},
                        {"id": 13, "conclusion": None, "status": "in_progress"},
                    ],
                },
            )
        if req.method == "POST" and "/check-runs/" in req.url.path and req.url.path.endswith("/rerequest"):
            return httpx.Response(201, json={})
        return httpx.Response(404, json={"message": "unexpected"})

    install_mock_transport["responder"] = _responder

    result = github_tools.github_rerun_failed_checks(pr_number=297)

    assert isinstance(result, dict)
    assert sorted(result["rerequested"]) == [11, 12]
    assert result["total_failed"] == 2

    # Sequence: PR GET, check-runs GET, then 2 POST rerequest (no POSTs for
    # successes / pending).
    methods_paths = [(r.method, r.url.path) for r in captured_requests]
    assert methods_paths[0] == ("GET", "/repos/Belladynn/ratis/pulls/297")
    assert methods_paths[1] == ("GET", f"/repos/Belladynn/ratis/commits/{head_sha}/check-runs")
    posts = [(m, p) for (m, p) in methods_paths if m == "POST"]
    assert len(posts) == 2
    assert all(p.endswith("/rerequest") for _, p in posts)
    rerun_ids = {int(p.rsplit("/check-runs/", 1)[1].split("/")[0]) for _, p in posts}
    assert rerun_ids == {11, 12}


def test_rerun_failed_checks_no_failures(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """Zero failures → empty `rerequested`, total_failed=0, NO POST calls."""
    head_sha = "abc" + "0" * 37

    def _responder(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/repos/Belladynn/ratis/pulls/297":
            return httpx.Response(200, json={"number": 297, "head": {"sha": head_sha}})
        if req.method == "GET" and req.url.path.endswith("/check-runs"):
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "check_runs": [
                        {"id": 10, "conclusion": "success"},
                        {"id": 11, "conclusion": "neutral"},
                    ],
                },
            )
        return httpx.Response(500, json={"message": "should not happen"})

    install_mock_transport["responder"] = _responder

    result = github_tools.github_rerun_failed_checks(pr_number=297)

    assert result == {"rerequested": [], "total_failed": 0}
    # Only the 2 GETs ; no POST.
    assert all(r.method == "GET" for r in captured_requests)
    assert len(captured_requests) == 2


def test_rerun_failed_checks_uses_raw_runs_despite_list_trim(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """`github_rerun_failed_checks` relies on raw runs (`id`) — the trim applied
    to `github_list_check_runs` must NOT strip ids from the rerun code path."""
    head_sha = "abc" + "0" * 37

    def _responder(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/repos/Belladynn/ratis/pulls/297":
            return httpx.Response(200, json={"number": 297, "head": {"sha": head_sha}})
        if req.method == "GET" and req.url.path.endswith("/check-runs"):
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "check_runs": [
                        {
                            "id": 55,
                            "name": "tests",
                            "conclusion": "failure",
                            "output": {"text": "bloat"},
                        },
                        {"id": 56, "name": "lint", "conclusion": "success"},
                    ],
                },
            )
        if req.method == "POST" and req.url.path.endswith("/rerequest"):
            return httpx.Response(201, json={})
        return httpx.Response(404, json={"message": "unexpected"})

    install_mock_transport["responder"] = _responder

    result = github_tools.github_rerun_failed_checks(pr_number=297)

    assert result == {"rerequested": [55], "total_failed": 1}
    posts = [r for r in captured_requests if r.method == "POST"]
    assert len(posts) == 1
    assert posts[0].url.path == "/repos/Belladynn/ratis/check-runs/55/rerequest"


def test_default_repo_when_env_unset(
    fake_token: str,
    monkeypatch: pytest.MonkeyPatch,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """When `RATIS_GITHUB_REPO` is unset, default repo is `Belladynn/ratis` (per ARCH)."""
    monkeypatch.delenv("RATIS_GITHUB_REPO", raising=False)
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=[])

    github_tools.github_list_prs()

    assert "/repos/Belladynn/ratis/pulls" in str(captured_requests[0].url)


# -- error paths ----------------------------------------------------------


def test_missing_token_raises_keychain_miss(
    monkeypatch: pytest.MonkeyPatch,
    github_repo: str,
) -> None:
    """When the keychain entry is absent, the tool surfaces `KeychainMiss`."""

    def _missing(self: keychain_mod.Keychain, account: str) -> str:
        raise KeychainMiss(f"keychain account {account!r} not found")

    monkeypatch.setattr(keychain_mod.Keychain, "get", _missing)

    with pytest.raises(KeychainMiss, match="github"):
        github_tools.github_list_prs()


def test_provider_4xx_raises_provider_error(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
) -> None:
    """GitHub returning 4xx is wrapped in `ProviderError`."""
    install_mock_transport["responder"] = lambda req: httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(ProviderError, match="404"):
        github_tools.github_get_pr(pr_number=99999)


def test_provider_5xx_raises_provider_error(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
) -> None:
    install_mock_transport["responder"] = lambda req: httpx.Response(503, text="upstream down")

    with pytest.raises(ProviderError, match="503"):
        github_tools.github_list_prs()


# -- registration & dispatch (full pipeline) ------------------------------


@pytest.mark.asyncio
async def test_dispatch_list_prs_audits_ok(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    raw_pr = {
        "number": 1,
        "title": "t",
        "state": "open",
        "draft": False,
        "user": {"login": "u"},
        "html_url": "https://x/1",
        "head": {"ref": "feat/x"},
        "base": {"ref": "main"},
        "labels": [],
        "created_at": "2026-05-01T10:00:00Z",
        "updated_at": "2026-05-01T11:00:00Z",
    }
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=[raw_pr])

    outcome = await dispatcher.dispatch(
        tool_name="github_list_prs",
        arguments={"state": "open", "limit": 1},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "ok"
    assert outcome.result == [
        {
            "number": 1,
            "title": "t",
            "state": "open",
            "draft": False,
            "user_login": "u",
            "html_url": "https://x/1",
            "head_ref": "feat/x",
            "base_ref": "main",
            "labels": [],
            "created_at": "2026-05-01T10:00:00Z",
            "updated_at": "2026-05-01T11:00:00Z",
        }
    ]

    lines = _audit_lines(tmp_path / "audit.log")
    assert len(lines) == 1
    assert lines[0]["tool"] == "github_list_prs"
    assert lines[0]["status"] == "ok"
    assert lines[0]["caller"] == "ops"
    # No token in args (the tool reads from Keychain).
    assert "token" not in lines[0]["args_redacted"]
    assert "Authorization" not in lines[0]["args_redacted"]


@pytest.mark.asyncio
async def test_dispatch_rerun_failed_checks_rejects_ops_caller(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
    captured_requests: list[httpx.Request],
) -> None:
    """`github_rerun_failed_checks` is admin-scoped — ops caller is denied."""
    outcome = await dispatcher.dispatch(
        tool_name="github_rerun_failed_checks",
        arguments={"pr_number": 297},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "forbidden_tool"
    # No HTTP traffic — auth blocks before invocation.
    assert captured_requests == []
    lines = _audit_lines(tmp_path / "audit.log")
    assert lines[0]["status"] == "forbidden_tool"
    assert lines[0]["tool"] == "github_rerun_failed_checks"
    assert lines[0]["caller"] == "ops"


@pytest.mark.asyncio
async def test_dispatch_comment_pr_rejects_ops_caller(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
    captured_requests: list[httpx.Request],
) -> None:
    """`github_comment_pr` is admin-scoped — ops caller is denied."""
    outcome = await dispatcher.dispatch(
        tool_name="github_comment_pr",
        arguments={"pr_number": 297, "body": "looks good"},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "forbidden_tool"
    assert captured_requests == []


@pytest.mark.asyncio
async def test_dispatch_comment_pr_admin_succeeds(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    install_mock_transport["responder"] = lambda req: httpx.Response(201, json={"id": 1, "body": "ok"})

    outcome = await dispatcher.dispatch(
        tool_name="github_comment_pr",
        arguments={"pr_number": 297, "body": "ok"},
        presented_token="ADMIN_TOK",
    )
    assert outcome.status == "ok"
    assert outcome.result == {"id": 1, "body": "ok"}


@pytest.mark.asyncio
async def test_dispatch_keychain_miss_audited(
    monkeypatch: pytest.MonkeyPatch,
    github_repo: str,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """Keychain miss surfaces as `keychain_miss` in audit log."""

    def _missing(self: keychain_mod.Keychain, account: str) -> str:
        raise KeychainMiss(f"keychain account {account!r} not found")

    monkeypatch.setattr(keychain_mod.Keychain, "get", _missing)

    outcome = await dispatcher.dispatch(
        tool_name="github_list_prs",
        arguments={},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "keychain_miss"
    lines = _audit_lines(tmp_path / "audit.log")
    assert lines[0]["status"] == "keychain_miss"


# -- token leak guard -----------------------------------------------------


@pytest.mark.asyncio
async def test_token_never_leaks_to_audit_log(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> None:
    """Audit JSONL must NEVER contain the token string under any circumstance."""
    install_mock_transport["responder"] = lambda req: httpx.Response(200, json=[{"number": 1}])

    await dispatcher.dispatch(
        tool_name="github_list_prs",
        arguments={"state": "open"},
        presented_token="OPS_TOK",
    )
    await dispatcher.dispatch(
        tool_name="github_get_pr",
        arguments={"pr_number": 1},
        presented_token="OPS_TOK",
    )

    raw = (tmp_path / "audit.log").read_text()
    assert FAKE_TOKEN not in raw, "Token leaked into audit log!"
    assert "Bearer" not in raw, "Authorization header leaked into audit log!"


def test_no_token_in_any_request_url_or_body(
    fake_token: str,
    github_repo: str,
    install_mock_transport: dict[str, Any],
    captured_requests: list[httpx.Request],
) -> None:
    """Cross-tool sweep — call every tool, assert token only in headers, never URL/body."""
    head_sha = "abc" + "0" * 37

    def _responder(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if req.method == "GET" and req.url.path.endswith("/pulls/297"):
            return httpx.Response(200, json={"number": 297, "head": {"sha": head_sha}})
        if req.method == "GET" and req.url.path.endswith("/check-runs"):
            return httpx.Response(200, json={"check_runs": [{"id": 1, "conclusion": "failure"}]})
        if req.method == "POST" and req.url.path.endswith("/rerequest"):
            return httpx.Response(201, json={})
        if req.method == "POST" and req.url.path.endswith("/comments"):
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(200, json={})

    install_mock_transport["responder"] = _responder

    github_tools.github_list_prs()
    github_tools.github_get_pr(pr_number=297)
    github_tools.github_list_check_runs(pr_number=297)
    github_tools.github_comment_pr(pr_number=297, body="ok")
    github_tools.github_rerun_failed_checks(pr_number=297)

    for req in captured_requests:
        # URL never carries the token.
        assert FAKE_TOKEN not in str(req.url), f"token leaked in URL: {req.url}"
        # Body never carries the token.
        body = req.content.decode("utf-8") if req.content else ""
        assert FAKE_TOKEN not in body, f"token leaked in body: {body!r}"
        # Header carries the token (correct path).
        assert req.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"


# -- registration metadata -------------------------------------------------


def test_all_tools_registered_with_correct_scopes() -> None:
    """`register_all()` puts the 5 tools into the global registry with right scopes."""
    github_tools.register_all()
    from agent_mcp.server import TOOLS_REGISTRY

    expected = {
        "github_list_prs": "ops",
        "github_get_pr": "ops",
        "github_list_check_runs": "ops",
        "github_rerun_failed_checks": "admin",
        "github_comment_pr": "admin",
    }
    for name, scope in expected.items():
        assert name in TOOLS_REGISTRY, f"missing {name}"
        assert TOOLS_REGISTRY[name].scope == scope, (
            f"{name} declared scope {TOOLS_REGISTRY[name].scope!r}, expected {scope!r}"
        )


def test_register_all_is_idempotent() -> None:
    """Calling `register_all()` twice doesn't raise."""
    github_tools.register_all()
    github_tools.register_all()
    from agent_mcp.server import TOOLS_REGISTRY

    assert "github_list_prs" in TOOLS_REGISTRY


def test_load_builtin_tools_includes_github() -> None:
    """`server.load_builtin_tools()` is the production entry point — must wire GitHub."""
    from agent_mcp.server import TOOLS_REGISTRY, load_builtin_tools

    load_builtin_tools()
    for name in (
        "github_list_prs",
        "github_get_pr",
        "github_list_check_runs",
        "github_rerun_failed_checks",
        "github_comment_pr",
    ):
        assert name in TOOLS_REGISTRY
