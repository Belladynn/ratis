"""TDD coverage for `agent_mcp.tools.db_tools`.

Strategy
--------
* `db_query` is a pure function wrapping a `psql` subprocess call.
* `_run_psql` is the single subprocess boundary — tests monkeypatch it to
  return canned CSV, so no real database or `docker`/`ssh` is touched.
* One test does NOT monkeypatch `_run_psql` but monkeypatches `subprocess.run`
  instead, to assert the exact argv built for dev vs prod.
* The dispatch-level test goes through `Dispatcher` to cover registration +
  scope (`ops`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_mcp.audit import AuditLog
from agent_mcp.auth import AuthGate
from agent_mcp.errors import ProviderError
from agent_mcp.server import Dispatcher
from agent_mcp.tools import db_tools

# -- db_query : parsing -----------------------------------------------------


def test_db_query_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """`db_query` parses psql CSV into {columns, rows, rowcount, truncated}."""
    monkeypatch.setattr(
        db_tools,
        "_run_psql",
        lambda env, sql: "id,cab\n1,10\n2,20\n",
    )
    result = db_tools.db_query("SELECT id, cab FROM users", env="dev")
    assert result == {
        "columns": ["id", "cab"],
        "rows": [["1", "10"], ["2", "20"]],
        "rowcount": 2,
        "truncated": False,
    }


def test_db_query_truncates_at_max_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """A result larger than MAX_ROWS is capped and flagged `truncated`."""
    big = "n\n" + "\n".join(str(i) for i in range(db_tools.MAX_ROWS + 50)) + "\n"
    monkeypatch.setattr(db_tools, "_run_psql", lambda env, sql: big)
    result = db_tools.db_query("SELECT n FROM generate_series(1,250) n", env="dev")
    assert result["rowcount"] == db_tools.MAX_ROWS
    assert result["truncated"] is True
    assert len(result["rows"]) == db_tools.MAX_ROWS


def test_db_query_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty psql output yields an empty, non-truncated result."""
    monkeypatch.setattr(db_tools, "_run_psql", lambda env, sql: "")
    result = db_tools.db_query("SELECT 1 WHERE false", env="dev")
    assert result == {"columns": [], "rows": [], "rowcount": 0, "truncated": False}


def test_db_query_unknown_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown `env` raises ProviderError before any subprocess runs."""
    monkeypatch.setattr(db_tools, "_run_psql", lambda env, sql: pytest.fail("must not run"))
    with pytest.raises(ProviderError, match="unknown env"):
        db_tools.db_query("SELECT 1", env="staging")


# -- _run_psql : transport --------------------------------------------------


def _fake_completed(stdout: str = "", stderr: str = "", code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


def test_run_psql_dev_builds_local_docker_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """dev → `docker exec -i ratis-postgres-1 psql ...` (no ssh), SQL via stdin."""
    captured: dict[str, Any] = {}

    def _fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return _fake_completed(stdout="ok\n1\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    db_tools._run_psql("dev", "SELECT 1")

    argv = captured["argv"]
    assert argv[0] == "docker"
    assert argv[1:5] == ["exec", "-i", "ratis-postgres-1", "psql"]
    assert "ssh" not in argv
    assert "ratis_dev" in " ".join(argv)
    assert captured["input"] == "SELECT 1"


def test_run_psql_prod_wraps_in_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    """prod → `ssh ratis-prod <quoted docker exec ... psql ...>`, SQL via stdin."""
    captured: dict[str, Any] = {}

    def _fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return _fake_completed(stdout="ok\n1\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    db_tools._run_psql("prod", "SELECT 1")

    argv = captured["argv"]
    assert argv[0] == "ssh"
    assert argv[1] == "ratis-prod"
    # The remote command is one shell-quoted string carrying docker+psql.
    assert "docker exec -i ratis-postgres-1 psql" in argv[2]
    assert "ratis_prod" in argv[2]
    assert captured["input"] == "SELECT 1"


def test_run_psql_nonzero_exit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-zero psql exit (e.g. a rejected write) becomes a ProviderError."""

    def _fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return _fake_completed(stderr="ERROR:  cannot execute UPDATE in a read-only transaction", code=1)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    with pytest.raises(ProviderError, match="read-only transaction"):
        db_tools._run_psql("dev", "UPDATE users SET cab = 0")


def test_run_psql_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A subprocess timeout becomes a ProviderError."""

    def _fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=db_tools.SUBPROCESS_TIMEOUT_SEC)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    with pytest.raises(ProviderError, match="timed out"):
        db_tools._run_psql("dev", "SELECT pg_sleep(99)")


def test_run_psql_readonly_option_in_conninfo(monkeypatch: pytest.MonkeyPatch) -> None:
    """The libpq conninfo passed to psql forces read-only + a statement timeout."""
    captured: dict[str, Any] = {}

    def _fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        return _fake_completed(stdout="x\n1\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    db_tools._run_psql("dev", "SELECT 1")
    joined = " ".join(captured["argv"])
    assert "default_transaction_read_only=on" in joined
    assert "statement_timeout=" in joined


# -- registration + dispatch -----------------------------------------------


def test_db_query_via_dispatcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`db_query` is registered with scope `ops` and runs through the dispatcher."""
    import asyncio

    monkeypatch.setattr(db_tools, "_run_psql", lambda env, sql: "id\n7\n")
    db_tools._reset_for_tests()
    db_tools.register_all()

    auth = AuthGate(admin_token="ADMIN_TOK", ops_token="OPS_TOK")
    audit = AuditLog(tmp_path / "audit.log")
    disp = Dispatcher(auth=auth, audit=audit)

    outcome = asyncio.run(
        disp.dispatch(
            tool_name="db_query",
            arguments={"sql": "SELECT id FROM x", "env": "dev"},
            presented_token="OPS_TOK",
        )
    )
    assert outcome.status == "ok"
    assert outcome.result == {"columns": ["id"], "rows": [["7"]], "rowcount": 1, "truncated": False}


# -- HSP4 M6 — db_query throttle 60 req/min ---------------------------------


def test_db_query_max_rows_is_1000() -> None:
    """HSP4 M6 — MAX_ROWS relevé de 200 à 1000."""
    assert db_tools.MAX_ROWS == 1000


def test_db_query_throttle_60_per_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    """61ème appel dans une fenêtre de 60s → ProviderError rate limit."""
    monkeypatch.setattr(db_tools, "_run_psql", lambda env, sql: "id\n1\n")
    # Reset le compteur in-memory (helper test-only).
    db_tools._reset_throttle_for_tests()
    # Freeze le temps pour rendre le test déterministe.
    fake_now = [1000.0]
    monkeypatch.setattr(db_tools, "_monotonic_now", lambda: fake_now[0])

    for _i in range(60):
        result = db_tools.db_query("SELECT 1", env="dev")
        assert result["rowcount"] >= 0
        fake_now[0] += 0.1  # 0.1s entre appels — tous dans la même fenêtre 60s.

    with pytest.raises(ProviderError, match="rate limit"):
        db_tools.db_query("SELECT 1", env="dev")


def test_db_query_throttle_resets_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Après 60s, le compteur sliding-window se vide."""
    monkeypatch.setattr(db_tools, "_run_psql", lambda env, sql: "id\n1\n")
    db_tools._reset_throttle_for_tests()
    fake_now = [2000.0]
    monkeypatch.setattr(db_tools, "_monotonic_now", lambda: fake_now[0])

    for _ in range(60):
        db_tools.db_query("SELECT 1", env="dev")
        fake_now[0] += 0.1

    # 61ème dans la même fenêtre — KO.
    with pytest.raises(ProviderError, match="rate limit"):
        db_tools.db_query("SELECT 1", env="dev")

    # Avance > 60s — fenêtre se vide, on peut re-tirer.
    fake_now[0] += 70.0
    result = db_tools.db_query("SELECT 1", env="dev")
    assert result["rowcount"] >= 0


# -- db_propose_write -------------------------------------------------------


def test_db_propose_write_packages_and_posts(monkeypatch):
    """db_propose_write packages a signed proposal with agent_id + proposed_at, POSTs, returns verdict."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://n8n.example/webhook/db-write")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s3cr3t")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-code-main")
    captured = {}

    def _fake_post(url, body, headers):
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        return 200, {"status": "pending_human_approval", "submission_id": "echoed"}

    monkeypatch.setattr(db_tools, "_post_proposal", _fake_post)
    result = db_tools.db_propose_write(
        procedure="support_credit_cab",
        args={"p_user_id": "00000000-0000-4000-8000-000000000000", "p_amount": 100},
        rationale="ticket #1",
    )
    assert result["status"] == "pending_human_approval"
    assert result["submission_id"]

    import json as _j

    sent = _j.loads(captured["body"])
    assert sent["procedure"] == "support_credit_cab"
    assert sent["agent_id"] == "claude-code-main"
    assert "proposed_at" in sent
    # HSP4 M4 — ces champs n'existent PLUS dans le payload.
    assert "mode" not in sent
    assert "new_procedure_sql" not in sent
    assert "checks" not in sent
    assert "break_glass" not in sent
    assert "X-Ratis-Signature" in captured["headers"]


def test_db_propose_write_does_not_accept_mode_kwarg(monkeypatch):
    """HSP4 M4 — `mode`, `new_procedure_sql`, `checks`, `break_glass` retirés."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    with pytest.raises(TypeError, match="mode"):
        db_tools.db_propose_write(  # type: ignore[call-arg]
            mode="existing",
            procedure="p",
            args={},
            rationale="r",
        )


def test_db_propose_write_does_not_accept_break_glass_kwarg(monkeypatch):
    """HSP4 M4 — `break_glass` retiré : passer ce kwarg lève TypeError."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    with pytest.raises(TypeError, match="break_glass"):
        db_tools.db_propose_write(  # type: ignore[call-arg]
            procedure="p",
            args={},
            rationale="r",
            break_glass=True,
        )


def test_db_propose_write_does_not_accept_checks_kwarg(monkeypatch):
    """HSP4 M4 — `checks` retiré : passer ce kwarg lève TypeError."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    with pytest.raises(TypeError, match="checks"):
        db_tools.db_propose_write(  # type: ignore[call-arg]
            procedure="p",
            args={},
            rationale="r",
            checks=[],
        )


def test_db_propose_write_does_not_accept_new_procedure_sql_kwarg(monkeypatch):
    """HSP4 M4 — `new_procedure_sql` retiré : passer ce kwarg lève TypeError."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    with pytest.raises(TypeError, match="new_procedure_sql"):
        db_tools.db_propose_write(  # type: ignore[call-arg]
            procedure="p",
            args={},
            rationale="r",
            new_procedure_sql="CREATE ...",
        )


def test_db_propose_write_missing_env_raises(monkeypatch):
    monkeypatch.delenv("N8N_DB_PIPELINE_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", raising=False)
    with pytest.raises(ProviderError, match="N8N_DB_PIPELINE"):
        db_tools.db_propose_write(procedure="x", args={}, rationale="r")


def test_db_propose_write_returns_pending(monkeypatch):
    """A pipeline 'pending_human_approval' response flows back to the agent."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-code-main")
    monkeypatch.setattr(
        db_tools,
        "_post_proposal",
        lambda url, body, headers: (200, {"status": "pending_human_approval", "submission_id": "echoed"}),
    )
    result = db_tools.db_propose_write(
        procedure="support_credit_cab",
        args={"p_user_id": "x", "p_amount": 1},
        rationale="t",
    )
    assert result["status"] == "pending_human_approval"
    assert result["submission_id"]
    assert "stage" not in result
    assert "feedback" not in result


def test_db_propose_write_returns_rejected_with_feedback(monkeypatch):
    """A 'rejected' response carries stage + structured feedback to the agent."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-code-main")
    feedback = [
        {"pass": "intent", "verdict": "ok"},
        {"pass": "magic_case", "verdict": "not_ok", "reason": "coincidence of the test case"},
    ]
    monkeypatch.setattr(
        db_tools,
        "_post_proposal",
        lambda url, body, headers: (200, {"status": "rejected", "stage": "llm_review", "feedback": feedback}),
    )
    result = db_tools.db_propose_write(procedure="p", args={}, rationale="t")
    assert result["status"] == "rejected"
    assert result["stage"] == "llm_review"
    assert result["feedback"] == feedback


def test_db_propose_write_http_error_not_retried(monkeypatch):
    """A non-2xx pipeline response is a hard error — reached n8n, so not retried."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-code-main")
    calls = []

    def _fake_post(url, body, headers):
        calls.append(1)
        return 401, {"status": "rejected", "reason": "invalid_signature"}

    monkeypatch.setattr(db_tools, "_post_proposal", _fake_post)
    with pytest.raises(ProviderError, match="HTTP 401"):
        db_tools.db_propose_write(procedure="p", args={}, rationale="t")
    assert len(calls) == 1


def test_db_propose_write_retries_then_alerts(monkeypatch):
    """An unreachable pipeline is retried PIPELINE_MAX_TRIES times, then Discord-alerted."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-code-main")
    attempts = []

    def _fake_post(url, body, headers):
        attempts.append(1)
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(db_tools, "_post_proposal", _fake_post)
    alerts = []
    monkeypatch.setattr(db_tools, "_send_discord_alert", lambda msg: alerts.append(msg))
    with pytest.raises(ProviderError, match="unreachable"):
        db_tools.db_propose_write(procedure="p", args={}, rationale="t")
    assert len(attempts) == db_tools.PIPELINE_MAX_TRIES
    assert len(alerts) == 1


def test_db_propose_write_malformed_response_raises(monkeypatch):
    """A 2xx response with a non-dict / status-less body is a hard error."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-code-main")
    monkeypatch.setattr(db_tools, "_post_proposal", lambda url, body, headers: (200, None))
    with pytest.raises(ProviderError, match="malformed"):
        db_tools.db_propose_write(procedure="p", args={}, rationale="t")


def test_db_propose_write_packages_support_context(monkeypatch):
    """client_message + investigation sont packagés dans le payload POSTé."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-code-main")
    captured = {}

    def _fake_post(url, body, headers):
        captured["body"] = body
        return 200, {"status": "pending_human_approval", "submission_id": "echoed"}

    monkeypatch.setattr(db_tools, "_post_proposal", _fake_post)
    db_tools.db_propose_write(
        procedure="support_credit_cab",
        args={"p_user_id": "x", "p_amount": 1},
        rationale="ticket #1",
        client_message="Bonjour, je n'ai pas reçu mes CAB du scan d'hier.",
        investigation="Scan accepté mais reward_event jamais émis — bug pipeline RW.",
    )
    import json as _j

    sent = _j.loads(captured["body"])
    assert sent["client_message"] == "Bonjour, je n'ai pas reçu mes CAB du scan d'hier."
    assert sent["investigation"] == ("Scan accepté mais reward_event jamais émis — bug pipeline RW.")


def test_db_propose_write_support_context_defaults_empty(monkeypatch):
    """Sans client_message / investigation, le payload porte des chaînes vides."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-code-main")
    captured = {}

    def _fake_post(url, body, headers):
        captured["body"] = body
        return 200, {"status": "pending_human_approval", "submission_id": "echoed"}

    monkeypatch.setattr(db_tools, "_post_proposal", _fake_post)
    db_tools.db_propose_write(
        procedure="p",
        args={},
        rationale="r",
    )
    import json as _j

    sent = _j.loads(captured["body"])
    assert sent["client_message"] == ""
    assert sent["investigation"] == ""


# -- HSP4 M2 — agent_id + proposed_at ---------------------------------------


def test_db_propose_write_uses_default_agent_id_when_env_unset(monkeypatch):
    """RATIS_AGENT_ID absent → default compilé `claude-code-main`."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.delenv("RATIS_AGENT_ID", raising=False)
    captured = {}

    def _fake_post(url, body, headers):
        captured["body"] = body
        return 200, {"status": "pending_human_approval", "submission_id": "x"}

    monkeypatch.setattr(db_tools, "_post_proposal", _fake_post)
    db_tools.db_propose_write(procedure="p", args={}, rationale="r")
    import json as _j

    sent = _j.loads(captured["body"])
    assert sent["agent_id"] == "claude-code-main"


def test_db_propose_write_uses_custom_agent_id_from_env(monkeypatch):
    """RATIS_AGENT_ID positionné → utilisé dans le payload."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-batch-reconciliation")
    captured = {}

    def _fake_post(url, body, headers):
        captured["body"] = body
        return 200, {"status": "pending_human_approval", "submission_id": "x"}

    monkeypatch.setattr(db_tools, "_post_proposal", _fake_post)
    db_tools.db_propose_write(procedure="p", args={}, rationale="r")
    import json as _j

    sent = _j.loads(captured["body"])
    assert sent["agent_id"] == "claude-batch-reconciliation"


def test_db_propose_write_rejects_invalid_agent_id(monkeypatch):
    """Regex ^[a-z][a-z0-9_-]{2,31}$ — majuscules / start digit / trop court / trop long rejected."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    for bad in ("Claude-Main", "1invalid", "ab", "x" * 33, ""):
        monkeypatch.setenv("RATIS_AGENT_ID", bad)
        with pytest.raises(ProviderError, match="agent_id"):
            db_tools.db_propose_write(procedure="p", args={}, rationale="r")


def test_db_propose_write_proposed_at_is_utc_iso_8601(monkeypatch):
    """proposed_at est un timestamp UTC ISO-8601 parsable."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-code-main")
    captured = {}

    def _fake_post(url, body, headers):
        captured["body"] = body
        return 200, {"status": "pending_human_approval", "submission_id": "x"}

    monkeypatch.setattr(db_tools, "_post_proposal", _fake_post)
    db_tools.db_propose_write(procedure="p", args={}, rationale="r")
    import json as _j

    sent = _j.loads(captured["body"])
    # ISO-8601 UTC : YYYY-MM-DDTHH:MM:SS(.ffffff)?+00:00 ou Z.
    ts = sent["proposed_at"]
    assert ts.endswith("+00:00") or ts.endswith("Z"), f"proposed_at non-UTC : {ts!r}"
    # Parsable par datetime.fromisoformat.
    from datetime import datetime

    datetime.fromisoformat(ts.replace("Z", "+00:00"))


# -- HSP4 M6 — payload cap 32 kB --------------------------------------------


def test_db_propose_write_payload_too_large_raises(monkeypatch):
    """Un payload sérialisé > 32 kB lève ProviderError avant tout POST."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-code-main")
    # Garde-fou : un POST ne doit JAMAIS être appelé si le payload est trop gros.
    monkeypatch.setattr(
        db_tools,
        "_post_proposal",
        lambda url, body, headers: pytest.fail("payload too large must not POST"),
    )
    big_investigation = "x" * (33 * 1024)  # > 32 kB
    with pytest.raises(ProviderError, match="payload"):
        db_tools.db_propose_write(
            procedure="p",
            args={},
            rationale="r",
            investigation=big_investigation,
        )


# -- L2 — db_propose_write throttle 10 req/min ------------------------------


def test_db_propose_write_throttle_10_per_minute(monkeypatch):
    """11ème proposition dans une fenêtre de 60s → ProviderError rate limit."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-code-main")
    monkeypatch.setattr(
        db_tools,
        "_post_proposal",
        lambda url, body, headers: (200, {"status": "pending_human_approval", "submission_id": "x"}),
    )
    db_tools._reset_propose_write_throttle_for_tests()
    fake_now = [3000.0]
    monkeypatch.setattr(db_tools, "_monotonic_now", lambda: fake_now[0])

    for _ in range(10):
        result = db_tools.db_propose_write(procedure="p", args={}, rationale="r")
        assert result["status"] == "pending_human_approval"
        fake_now[0] += 0.1  # 0.1s entre appels — tous dans la même fenêtre 60s.

    with pytest.raises(ProviderError, match="rate limit"):
        db_tools.db_propose_write(procedure="p", args={}, rationale="r")


def test_db_propose_write_throttle_independent_from_db_query(monkeypatch):
    """Les deques db_query (60/min) et db_propose_write (10/min) sont séparés."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-code-main")
    monkeypatch.setattr(db_tools, "_run_psql", lambda env, sql: "id\n1\n")
    monkeypatch.setattr(
        db_tools,
        "_post_proposal",
        lambda url, body, headers: (200, {"status": "pending_human_approval", "submission_id": "x"}),
    )
    db_tools._reset_throttle_for_tests()
    db_tools._reset_propose_write_throttle_for_tests()
    fake_now = [4000.0]
    monkeypatch.setattr(db_tools, "_monotonic_now", lambda: fake_now[0])

    # 59 db_query — sous la limite 60/min.
    for _ in range(59):
        db_tools.db_query("SELECT 1", env="dev")
        fake_now[0] += 0.1
    # 10 db_propose_write — atteint la limite 10/min de leur propre deque.
    for _ in range(10):
        db_tools.db_propose_write(procedure="p", args={}, rationale="r")
        fake_now[0] += 0.1

    # Le 11ème propose_write doit raise — il a son propre compteur (10), pas celui de db_query (69).
    with pytest.raises(ProviderError, match="rate limit"):
        db_tools.db_propose_write(procedure="p", args={}, rationale="r")
    # Et db_query peut encore tirer une fois (59 + 1 = 60, encore dans la limite, le 61ème
    # serait la 1ère à péter, donc le 60ème doit toujours passer).
    result = db_tools.db_query("SELECT 1", env="dev")
    assert result["rowcount"] >= 0


def test_db_propose_write_throttle_resets_after_window(monkeypatch):
    """Après 60s, le compteur sliding-window propose_write se vide."""
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("N8N_DB_PIPELINE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("RATIS_AGENT_ID", "claude-code-main")
    monkeypatch.setattr(
        db_tools,
        "_post_proposal",
        lambda url, body, headers: (200, {"status": "pending_human_approval", "submission_id": "x"}),
    )
    db_tools._reset_propose_write_throttle_for_tests()
    fake_now = [5000.0]
    monkeypatch.setattr(db_tools, "_monotonic_now", lambda: fake_now[0])

    for _ in range(10):
        db_tools.db_propose_write(procedure="p", args={}, rationale="r")
        fake_now[0] += 0.1

    # 11ème dans la même fenêtre — KO.
    with pytest.raises(ProviderError, match="rate limit"):
        db_tools.db_propose_write(procedure="p", args={}, rationale="r")

    # Avance > 60s — fenêtre se vide, on peut re-tirer.
    fake_now[0] += 70.0
    result = db_tools.db_propose_write(procedure="p", args={}, rationale="r")
    assert result["status"] == "pending_human_approval"
