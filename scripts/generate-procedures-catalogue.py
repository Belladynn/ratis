#!/usr/bin/env python3
"""Generate PROCEDURES.md — catalogue of support stored procedures.

Scans `db/procedures/*.sql` (the git source of truth, ignoring files prefixed
`_` like `_TEMPLATE.sql`), extracts each procedure's name, arguments and
COMMENT, reads its sidecar `.manifest.toml` (HSP1 — facing, money_tier,
purpose, affected tables), and writes `docs/arch/PROCEDURES.md`.

Modes :
    (default)            generate PROCEDURES.md from the git .sql + .manifest.toml
    --check              CI freshness — exit 1 if PROCEDURES.md is stale OR a
                         manifest sidecar is missing
    --check-live <env>   reconcile git vs the live DB's pg_proc (env: dev|prod)

Rerun whenever db/procedures/ changes :
    python scripts/generate-procedures-catalogue.py
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

# HSP1 — manifest loader lives in ratis_core. Importing it keeps the schema
# validation single-sourced (Pydantic model) ; the script does NOT re-parse
# TOML manually.
REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT / "ratis_core"))
from ratis_core.db_procedure_manifest import load_manifest

PROCEDURES_DIR = REPO_ROOT / "db" / "procedures"
OUTPUT = REPO_ROOT / "docs" / "arch" / "PROCEDURES.md"

# env → live DB target. Mirrors the agent-mcp db_tools transport (db_query V0).
ENV_TARGETS: dict[str, dict[str, str]] = {
    "dev": {"ssh_host": "", "container": "ratis-postgres-1", "dbname": "ratis_dev"},
    "prod": {"ssh_host": "ratis-prod", "container": "ratis-postgres-1", "dbname": "ratis_prod"},
}

_CREATE_RE = re.compile(
    r"CREATE\s+OR\s+REPLACE\s+PROCEDURE\s+(?P<name>\w+)\s*\((?P<args>[^)]*)\)",
    re.IGNORECASE,
)
_COMMENT_RE = re.compile(
    r"COMMENT\s+ON\s+PROCEDURE\s+\w+[^']*?IS\s*'(?P<comment>(?:[^']|'')*)'",
    re.IGNORECASE | re.DOTALL,
)


def parse_procedure(sql_path: Path) -> dict[str, object]:
    """Extract {name, args, comment, purpose, facing, money_tier, affects_tables}
    from one db/procedures/*.sql file + its sidecar `.manifest.toml`.

    Raises FileNotFoundError if the sidecar is absent.
    """
    text = sql_path.read_text(encoding="utf-8")
    create = _CREATE_RE.search(text)
    if not create:
        raise ValueError(f"{sql_path.name}: no `CREATE OR REPLACE PROCEDURE` found")
    comment = _COMMENT_RE.search(text)
    if not comment:
        raise ValueError(f"{sql_path.name}: no `COMMENT ON PROCEDURE ... IS '...'` found")

    manifest_path = sql_path.with_suffix("").with_suffix(".manifest.toml")
    # NB: `with_suffix("").with_suffix(".manifest.toml")` drops `.sql` then adds
    # `.manifest.toml` — handles `support_x.sql` -> `support_x.manifest.toml`.
    if not manifest_path.exists():
        raise FileNotFoundError(f"{sql_path.name}: HSP1 sidecar manifest missing at {manifest_path.name}")
    manifest = load_manifest(manifest_path)

    return {
        "name": create.group("name"),
        "args": " ".join(create.group("args").split()),
        "comment": comment.group("comment").replace("''", "'").strip(),
        "purpose": manifest.purpose,
        "facing": manifest.facing,
        "money_tier": manifest.money_tier,
        "direction": manifest.direction,
        "affects_tables": sorted({a.table for a in manifest.affects}),
    }


def collect_procedures() -> list[dict[str, object]]:
    """Parse every db/procedures/*.sql, ignoring `_`-prefixed files."""
    if not PROCEDURES_DIR.exists():
        return []
    return [parse_procedure(p) for p in sorted(PROCEDURES_DIR.glob("*.sql")) if not p.name.startswith("_")]


def generate() -> str:
    """Build the PROCEDURES.md content."""
    procs = collect_procedures()
    lines = [
        "# Ratis — Catalogue des procédures stockées support",
        "",
        "> **Auto-généré — ne pas éditer à la main.**",
        "> Régénérer : `python scripts/generate-procedures-catalogue.py`",
        "> CI vérifie la fraîcheur à chaque PR (`.github/workflows/doc-inventories.yml`).",
        "",
    ]
    if not procs:
        lines += [
            "Aucune procédure support à ce jour — `db/procedures/` ne contient encore aucune procédure.",
            "",
        ]
    else:
        lines += [
            "| Procédure | Arguments | Facing | Tier | Direction | Tables affectées | Description |",
            "|---|---|---|---|---|---|---|",
        ]
        for p in procs:
            facing = "✓" if p["facing"] else "—"
            tables = ", ".join(f"`{t}`" for t in p["affects_tables"])
            lines.append(
                f"| `{p['name']}` | `{p['args']}` | {facing} | `{p['money_tier']}` | "
                f"`{p['direction']}` | {tables} | {p['purpose']} |"
            )
        lines.append("")
    lines += ["---", "", f"**Total : {len(procs)} procédure(s).**", ""]
    return "\n".join(lines)


def _run_psql(env: str, sql: str) -> str:
    """Run read-only SQL against `env` via psql-over-(ssh-)docker.

    Mirrors the agent-mcp db_tools transport (we do NOT import that module —
    it lives in a not-yet-merged branch ; the pattern is small enough to
    duplicate).
    """
    target = ENV_TARGETS[env]
    conninfo = (
        f"dbname={target['dbname']} user=ratis options='-c default_transaction_read_only=on -c statement_timeout=10s'"
    )
    argv = [
        "docker",
        "exec",
        "-i",
        target["container"],
        "psql",
        "-tA",
        "-v",
        "ON_ERROR_STOP=1",
        conninfo,
    ]
    if target["ssh_host"]:
        argv = ["ssh", target["ssh_host"], " ".join(shlex.quote(a) for a in argv)]
    proc = subprocess.run(argv, input=sql, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"psql {env} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout


def check_live(env: str) -> int:
    """Compare git db/procedures/ against the live DB's support_* procedures."""
    git_names = {p["name"] for p in collect_procedures()}
    sql = r"SELECT proname FROM pg_proc WHERE proname LIKE 'support\_%' ORDER BY proname"
    live_names = {ln.strip() for ln in _run_psql(env, sql).splitlines() if ln.strip()}

    only_git = sorted(git_names - live_names)
    only_live = sorted(live_names - git_names)
    if not only_git and not only_live:
        print(f"OK: db/procedures/ matches {env} live pg_proc ({len(git_names)} procedure(s)).")
        return 0
    if only_git:
        print(f"DRIFT: in git but not live on {env}: {only_git}", file=sys.stderr)
    if only_live:
        print(f"DRIFT: live on {env} but not in git: {only_live}", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    if "--check-live" in argv:
        idx = argv.index("--check-live")
        env = argv[idx + 1] if idx + 1 < len(argv) else ""
        if env not in ENV_TARGETS:
            print(f"ERROR: --check-live requires an env ({sorted(ENV_TARGETS)})", file=sys.stderr)
            return 2
        return check_live(env)

    try:
        new_content = generate()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if "--check" in argv:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8").replace("\r\n", "\n") != new_content:
            print("ERROR: PROCEDURES.md is out of date. Run scripts/generate-procedures-catalogue.py", file=sys.stderr)
            return 1
        print("OK: PROCEDURES.md is up to date.")
        return 0

    with OUTPUT.open("w", encoding="utf-8", newline="\n") as f:
        f.write(new_content)
    print(f"Wrote PROCEDURES.md ({OUTPUT.stat().st_size} bytes).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
