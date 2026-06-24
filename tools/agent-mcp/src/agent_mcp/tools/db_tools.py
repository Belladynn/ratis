"""Postgres read-only query tool — Module 8 of agent-mcp.

Exposes 1 typed tool :

* `db_query` (ops) — run a read-only SQL query against the dev or prod
  Ratis database.

Transport
---------
No `psycopg`, no Tailscale, no exposed Postgres port. `psql` is run inside
the database container :

* dev  → `docker exec -i ratis-postgres-1 psql ...`           (local, Mac mini)
* prod → `ssh ratis-prod docker exec -i ratis-postgres-1 psql ...` (Hetzner)

The SQL is fed through **stdin** — it never appears in an argv nor touches a
shell, so there is no shell-injection surface. For the prod hop the fixed
`docker exec … psql` command is shell-quoted as a single ssh argument.

Read-only guarantee
-------------------
The libpq connection options force `default_transaction_read_only=on` and a
`statement_timeout` AT CONNECT. Every transaction in the psql session is
therefore read-only — Postgres rejects any INSERT/UPDATE/DELETE/DDL. The
guarantee comes from the database, not from this code.

No Keychain
-----------
This module has NO Keychain entry — by design. psql connects as the trusted
local container user (no password), and the prod hop uses the SSH key already
configured in `~/.ssh/config` (Host `ratis-prod`). The `env → target` table
below holds no secret.

References
----------
* docs/superpowers/specs/2026-05-17-db-mcp-access-design.md
* ARCH_agent_mcp.md § périmètre (étendu à l'infra interne)
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import hmac
import io
import json
import os
import re
import shlex
import subprocess
import uuid
from collections import deque
from datetime import UTC, datetime
from time import monotonic as _stdlib_monotonic
from typing import Any

import httpx

from ..errors import ProviderError
from ..server import TOOLS_REGISTRY, register_tool

ENV_TARGETS: dict[str, dict[str, str]] = {
    "dev": {"ssh_host": "", "container": "ratis-postgres-1", "dbname": "ratis_dev"},
    "prod": {"ssh_host": "ratis-prod", "container": "ratis-postgres-1", "dbname": "ratis_prod"},
}
"""env → connection target. NOT secrets (no password ; SSH key auth for prod)."""

DB_USER = "ratis"
"""Postgres role psql connects as inside the container."""

MAX_ROWS = 1000
"""Cap on rows returned to the agent — keeps tool output token-bounded."""

# HSP4 M6 — throttle in-memory 60 req/min sur db_query (sliding window).
# Mono-process en V1.1 (un seul binaire MCP tourne) — pas besoin de Redis.
# Un attaquant qui relance le binaire pour reset le compteur reste en
# compétition avec son propre `statement_timeout` et le pool PG limité serveur.
THROTTLE_MAX_REQUESTS = 60
THROTTLE_WINDOW_SEC = 60.0
_throttle_timestamps: deque[float] = deque()

# L2 — throttle in-memory db_propose_write : borne plus serrée (10/min).
# Les propositions sont rares par nature (un agent qui écrit en boucle = anomalie).
# Layer 1 de la defense-in-depth contre la consommation API Anthropic non bornée
# (les layers 2 = rate-limit n8n et 3 = budget cap console Anthropic complètent).
# Deque séparée de db_query pour que les deux limites ne se mélangent pas.
PROPOSE_WRITE_THROTTLE_MAX = 10
_propose_write_throttle_timestamps: deque[float] = deque()


def _monotonic_now() -> float:
    """Indirection pour permettre de monkeypatcher le temps en test."""
    return _stdlib_monotonic()


def _check_throttle() -> None:
    """Vérifie le throttle 60 req/min sur db_query. Pop les timestamps hors-fenêtre,
    push le nouveau, raise ProviderError si > MAX.
    """
    now = _monotonic_now()
    threshold = now - THROTTLE_WINDOW_SEC
    while _throttle_timestamps and _throttle_timestamps[0] < threshold:
        _throttle_timestamps.popleft()
    if len(_throttle_timestamps) >= THROTTLE_MAX_REQUESTS:
        raise ProviderError(f"db_query: rate limit {THROTTLE_MAX_REQUESTS} req/{int(THROTTLE_WINDOW_SEC)}s atteint")
    _throttle_timestamps.append(now)


def _check_propose_write_throttle() -> None:
    """Vérifie le throttle 10 req/min sur db_propose_write (L2). Sliding window
    indépendante de db_query. Raise ProviderError si > PROPOSE_WRITE_THROTTLE_MAX.
    """
    now = _monotonic_now()
    threshold = now - THROTTLE_WINDOW_SEC
    while _propose_write_throttle_timestamps and _propose_write_throttle_timestamps[0] < threshold:
        _propose_write_throttle_timestamps.popleft()
    if len(_propose_write_throttle_timestamps) >= PROPOSE_WRITE_THROTTLE_MAX:
        raise ProviderError(
            f"db_propose_write: rate limit {PROPOSE_WRITE_THROTTLE_MAX} req/{int(THROTTLE_WINDOW_SEC)}s atteint"
        )
    _propose_write_throttle_timestamps.append(now)


def _reset_throttle_for_tests() -> None:
    """Test-only — vide le buffer db_query in-memory (utilisé par les fixtures)."""
    _throttle_timestamps.clear()


def _reset_propose_write_throttle_for_tests() -> None:
    """Test-only — vide le buffer db_propose_write in-memory."""
    _propose_write_throttle_timestamps.clear()


SUBPROCESS_TIMEOUT_SEC = 30.0
"""Wall-clock cap on the psql subprocess (defence in depth vs statement_timeout)."""

_CONN_OPTIONS = "options='-c default_transaction_read_only=on -c statement_timeout=5s'"
"""libpq connection options — read-only session + 5 s server-side query timeout."""

PIPELINE_URL_ENV = "N8N_DB_PIPELINE_WEBHOOK_URL"
PIPELINE_SECRET_ENV = "N8N_DB_PIPELINE_WEBHOOK_SECRET"  # noqa: S105 — env var name, not a secret value.  # pragma: allowlist secret
DISCORD_ALERT_ENV = "N8N_DISCORD_DIGEST_WEBHOOK_URL"
"""Discord webhook reused for the direct transport-failure alert (best-effort)."""

PIPELINE_TIMEOUT_SEC = 360.0
"""Wall-clock cap on the proposal POST. db_propose_write blocks until n8n has run
the machine stages (sandbox dry-run + invariants + 2 LLM passes ~ 2-5 min) — a
slow-but-responding pipeline is nominal, so the timeout is deliberately generous."""

DISCORD_ALERT_TIMEOUT_SEC = 5.0
"""Short timeout for the best-effort Discord alert — it must not add minutes
of latency on top of an already-failed pipeline submission."""

PIPELINE_MAX_TRIES = 3
"""Total attempts to reach the pipeline (1 initial + 2 retries) before alerting."""

# HSP4 M2 — identité de l'agent (env var, défaut claude-code-main).
DEFAULT_AGENT_ID = "claude-code-main"
AGENT_ID_REGEX = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")
AGENT_ID_ENV = "RATIS_AGENT_ID"

# HSP4 M6 — cap payload anti-exfil (32 kB sur le JSON sérialisé).
PAYLOAD_MAX_BYTES = 32 * 1024


def _run_psql(env: str, sql: str) -> str:
    """Run `sql` via psql against `env` ; return raw CSV stdout.

    Read-only is enforced by the connection options. The SQL travels through
    stdin only. Tests monkeypatch this function to avoid a real subprocess.
    """
    target = ENV_TARGETS[env]
    conninfo = f"dbname={target['dbname']} user={DB_USER} {_CONN_OPTIONS}"
    argv = [
        "docker",
        "exec",
        "-i",
        target["container"],
        "psql",
        "--csv",
        "-v",
        "ON_ERROR_STOP=1",
        conninfo,
    ]
    if target["ssh_host"]:
        remote = " ".join(shlex.quote(part) for part in argv)
        argv = ["ssh", target["ssh_host"], remote]

    try:
        proc = subprocess.run(
            argv,
            input=sql,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderError(f"db_query {env}: subprocess timed out after {SUBPROCESS_TIMEOUT_SEC}s") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:500]
        raise ProviderError(f"db_query {env} failed: {detail}")
    return proc.stdout


def db_query(sql: str, env: str = "dev") -> dict[str, Any]:
    """Run a read-only SQL query against the dev or prod database. Read-only. Scope: ops.

    Args :
        sql : the SQL to run. Writes (INSERT/UPDATE/DELETE/DDL) are rejected by
              Postgres — the session is opened read-only.
        env : "dev" (local Mac mini) or "prod" (Hetzner via SSH). Default "dev".

    Returns a dict ``{columns, rows, rowcount, truncated}``. At most ``MAX_ROWS``
    rows are returned ; ``truncated`` is true when the result had more.
    """
    if env not in ENV_TARGETS:
        raise ProviderError(f"db_query: unknown env {env!r} (expected one of {sorted(ENV_TARGETS)})")

    # HSP4 M6 — throttle 60 req/min (raise avant tout I/O psql).
    _check_throttle()

    raw_csv = _run_psql(env, sql)
    reader = csv.reader(io.StringIO(raw_csv))
    try:
        header = next(reader)
    except StopIteration:
        return {"columns": [], "rows": [], "rowcount": 0, "truncated": False}

    rows: list[list[str]] = []
    truncated = False
    for row in reader:
        if len(rows) >= MAX_ROWS:
            truncated = True
            break
        rows.append(row)
    return {
        "columns": header,
        "rows": rows,
        "rowcount": len(rows),
        "truncated": truncated,
    }


def _post_proposal(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, Any]:
    """POST the proposal to the pipeline webhook ; return (http_status, parsed_json_body).

    One attempt only — the retry loop lives in `db_propose_write`. The parsed
    body is `None` when the response is not JSON. Isolated so tests can
    monkeypatch it without a real network call.
    """
    resp = httpx.post(url, content=body, headers=headers, timeout=PIPELINE_TIMEOUT_SEC)
    try:
        parsed = resp.json()
    except Exception:
        parsed = None
    return resp.status_code, parsed


def _send_discord_alert(message: str) -> None:
    """Best-effort Discord alert when the pipeline is unreachable.

    Silent no-op if the webhook env var is unset, and never raises — the
    `ProviderError` raised by the caller is the agent-facing signal ; this
    is only the second, human-facing channel.
    """
    url = os.environ.get(DISCORD_ALERT_ENV)
    if not url:
        return
    with contextlib.suppress(Exception):  # best-effort ; must not mask the original error.
        httpx.post(url, json={"content": message}, timeout=DISCORD_ALERT_TIMEOUT_SEC)


def _resolve_agent_id() -> str:
    """Lit RATIS_AGENT_ID env, défaut DEFAULT_AGENT_ID, valide le regex."""
    aid = os.environ.get(AGENT_ID_ENV, DEFAULT_AGENT_ID)
    if not AGENT_ID_REGEX.match(aid):
        raise ProviderError(f"db_propose_write: agent_id invalide {aid!r} (regex ^[a-z][a-z0-9_-]{{2,31}}$)")
    return aid


def db_propose_write(
    procedure: str,
    args: dict[str, Any],
    rationale: str,
    client_message: str = "",
    investigation: str = "",
) -> dict[str, Any]:
    """Submit a DB write proposal to the approval pipeline. Never executes. Scope: ops.

    The agent proposes — it NEVER writes. The proposal is HMAC-signed and POSTed
    to the n8n approval pipeline. This call BLOCKS on the machine stages
    (sandbox dry-run + invariants + 2-pass LLM review ~ 2-5 min) and returns
    the verdict: {"status": "pending_human_approval", "submission_id": ...}
    when the machine gates pass, or {"status": "rejected", "submission_id":
    ..., "stage": ..., "feedback": [...]} when one fails — so the live agent
    can fix and re-submit. If the pipeline is unreachable, it is retried then
    a Discord alert is sent.

    HSP4 — `agent_id` (env RATIS_AGENT_ID, default `claude-code-main`) et
    `proposed_at` (UTC ISO-8601) sont auto-injectés dans le payload signé.
    Champs `mode`, `new_procedure_sql`, `checks`, `break_glass` retirés —
    la création de procédure passe par PR git uniquement, les invariants
    sont déclarés côté manifest n8n.

    Args:
        procedure: the catalogued support procedure name.
        args: the procedure call arguments (validated against manifest by n8n).
        rationale: human-readable why — for the audit log and the approval UI.
        client_message: raw client message that triggered the case — support context.
        investigation: the agent's investigation note — what it checked, why this write fixes it.
    """
    url = os.environ.get(PIPELINE_URL_ENV)
    secret = os.environ.get(PIPELINE_SECRET_ENV)
    if not url or not secret:
        raise ProviderError(f"db_propose_write: {PIPELINE_URL_ENV} / {PIPELINE_SECRET_ENV} not set")

    agent_id = _resolve_agent_id()
    submission_id = str(uuid.uuid4())
    proposed_at = datetime.now(UTC).isoformat()

    proposal = {
        "submission_id": submission_id,
        "agent_id": agent_id,
        "proposed_at": proposed_at,
        "procedure": procedure,
        "args": args,
        "rationale": rationale,
        "client_message": client_message,
        "investigation": investigation,
    }
    body = json.dumps(proposal).encode("utf-8")

    # HSP4 M6 — cap payload anti-exfil 32 kB (avant signature, avant POST).
    if len(body) > PAYLOAD_MAX_BYTES:
        raise ProviderError(f"db_propose_write: payload too large ({len(body)} > {PAYLOAD_MAX_BYTES} bytes)")

    # L2 — throttle 10 req/min (raise juste avant le POST, après validation locale).
    # Borne plus serrée que db_query (60/min) car les propositions sont rares de nature ;
    # toute requête au-delà déclencherait inutilement les 2 passes LLM côté n8n.
    _check_propose_write_throttle()

    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-Ratis-Signature": signature}

    last_error: Exception | None = None
    for _attempt in range(PIPELINE_MAX_TRIES):
        try:
            status, response_body = _post_proposal(url, body, headers)
        except Exception as exc:
            last_error = exc
            continue
        # Reached n8n. A non-2xx is a hard error (e.g. HMAC rejected) — not retried.
        if not 200 <= status < 300:
            raise ProviderError(f"db_propose_write: pipeline returned HTTP {status}")
        if not isinstance(response_body, dict) or "status" not in response_body:
            raise ProviderError("db_propose_write: pipeline returned a non-JSON or malformed response")
        outcome: dict[str, Any] = {
            "status": response_body["status"],
            "submission_id": submission_id,
        }
        if response_body.get("stage") is not None:
            outcome["stage"] = response_body["stage"]
        if response_body.get("feedback") is not None:
            outcome["feedback"] = response_body["feedback"]
        return outcome

    assert last_error is not None  # loop ran PIPELINE_MAX_TRIES (>0) times, all failing
    # All attempts failed to reach the pipeline — alert a human, then raise.
    _send_discord_alert(
        f":warning: db_propose_write: pipeline n8n injoignable apres "
        f"{PIPELINE_MAX_TRIES} tentatives (submission {submission_id}). "
        f"Derniere erreur : {last_error}"
    )
    raise ProviderError(f"db_propose_write: pipeline unreachable after {PIPELINE_MAX_TRIES} attempts: {last_error}")


# ---- registration -------------------------------------------------------

_REGISTERED = False


def register_all() -> None:
    """Register the `db` tools. Idempotent (mirrors `glitchtip_tools`)."""
    global _REGISTERED
    if _REGISTERED and "db_query" in TOOLS_REGISTRY and "db_propose_write" in TOOLS_REGISTRY:
        return
    if "db_query" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(db_query)
    if "db_propose_write" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(db_propose_write)
    _REGISTERED = True


def _reset_for_tests() -> None:
    """Test-only — drop the idempotence flag so `register_all()` re-runs."""
    global _REGISTERED
    _REGISTERED = False
