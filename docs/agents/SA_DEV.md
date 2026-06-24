# SA_DEV.md — rules for development subagents

> **Subagent: read this file FIRST when you are dispatched on a dev task.**
> Completes `docs/agents/CLAUDE.md` (shared, already auto-loaded). This file adds the specific coding rules.
> R26 v2: you can **propose** a modification to this file (audit, new convention) — ask the operator for confirmation before committing.

## role
You are a **dev subagent**. You implement features, fix bugs, write tests, migrations. You follow strict TDD. You commit in small atomic units. You don't discuss strategy — your orchestrator has already done that.

## rule #1 — clean solution ALWAYS (R33)
Before any other technical rule, respect this one:

**Never a workaround. Never a shortcut. Never a "just to unblock".**

- If the test doesn't pass, **fix the logic** — don't hardcode the value to make the test pass
- If a lint rule complains, **understand why and fix it** — don't add `# noqa` / `// eslint-disable` without written justification
- If the pattern you need doesn't exist, **implement it properly** — don't copy-paste a duplicated function
- If you are blocked, **ask the orchestrator** via your report-back `BLOCKERS` — don't ship a hack
- If a test is broken by your change and you think it needs to be modified → **justify in depth** in `SESSION_LOG.md` (R01). Never delete a test.
- `dev-cost` does not apply to you: you are fast and precise. The clean method is always affordable. Shortcuts create technical debt that costs 10× more later.

If you catch yourself thinking "I'll just do this quickly to make it work" — **STOP**. That's a sign to re-evaluate the approach, not to code faster.

## start-of-task checklist
1. If the task touches an **endpoint** → `Grep docs/reference/ENDPOINTS.md` first (R27). NEVER invent an endpoint that already exists.
2. If the task references an **ARCH** → **by default `docs_search(question)` via docs-mcp** (Module 9 agent-mcp, delivered #560/#561) → `docs_get(id)` for target sections. Fallback: `Grep docs/reference/ARCH_INVENTORY.md` + `Read offset=<line> limit=<Y>` (R29). Do NOT read the entire ARCH.
3. Note in `SESSION_LOG.md`:
   ```
   ## [date] — [your task]
   Files: [initial list]
   Summary: [1 line]
   ```
4. Start by **writing the tests** (R01 TDD). Make them fail. Then implement.

## end-of-task checklist (MANDATORY before reporting `STATUS: done`)

Before reporting `done` to the orchestrator:
1. **Update the referenced ARCH**:
   - Check off the implementation checklist items you just completed
   - Add decisions made along the way (e.g., "chose approach X over Y because Z")
   - Update the overall status if the entire phase is done
   - If no ARCH is referenced for this task (rare — urgent fix, hotfix), mention it in your report `NOTES`
2. **Flag new pitfalls** in the report-back `NOTES`:
   - If you encountered a pitfall not documented in `docs/agents/SA_DEV.md` § pitfalls → list it with (file, symptom, fix)
   - The orchestrator will decide whether to add it to `docs/known/KNOWN_PROBLEMS.md` (you do NOT modify these gitignored files yourself)
3. **Verify CI is green** (R15): `gh pr checks <pr>` or `gh run watch <run-id>` → all checks SUCCESS. If red: `gh run view --log-failed <id>` + precise fix + re-push, until green. See § "tests — CI-ONLY" below for the full cycle. Red tests = `STATUS: blocked` or `partial`, never `done`.
4. **Verify the PR is open** (if dispatch included PR): `gh pr view <N>` → URL in the report-back
5. **Report-back** in canonical format (see § report-back format)

## hard rules — code discipline

### tests (R01-R02, R22)
- **TDD-first** always. Failing test → implementation → passing test → commit. Never code without a test. This discipline remains **inflexible**, only the execution environment changes.
- **Never delete a test**. If blocking: `@pytest.mark.skip(reason="...")` + entry `DECISIONS_PENDING.md`
- Modify a test → justify in `SESSION_LOG.md` with a clear reason
- BDD-tests: DB=`ratis_test` · `create_all` + `DROP SCHEMA CASCADE` · SA-2.0 `begin_nested()` + `after_transaction_end` · fixture `assert_no_pending_changes` mandatory in each `conftest.py` (CI enforces)
- Pre-merge: CI green (R15). Pushing red is OK as an intermediate step, **merging red is forbidden**.

#### tests — local pytest allowed with timeout

**Update 2026-04-28 PM**: restoration of local pytest after Explore SA investigation. Root cause identified: pytest **was not actually hung**, just slow (setup_db fixture + paddleocr lazy + httpx SSL init = 1-3 min startup). Without `pytest-timeout` configured globally, SAs interpreted "slow" as "stuck".

**Fix applied**: `[tool.pytest.ini_options] timeout = 60` + `timeout_method = "thread"` in each service's `pyproject.toml` (`thread` avoids SIGALRM → compatible macOS/Linux/Windows). If a test takes >60s pytest aborts it with a clear message → we know THAT test is the problem, not a guess of "it's been hung for 5 min".

**Recommended recipe**: use `scripts/run-tests.sh` (minimal-output wrapper):

```bash
./scripts/run-tests.sh <target>
# Output (1 line if pass, 5-15 lines if fail):
#   PASSED 23 in 4.2s
# OR
#   FAILED 2:
#     tests/test_X.py::test_Y - assert 5 == 6
#     tests/test_Z.py::test_W - TypeError: unexpected kwarg 'foo'
```

The wrapper:
- Automatically detects `--package` based on the path (no mental mapping needed)
- Forces `-q --tb=line --no-header` → minimum tokens
- Preserves exit code (0 pass, 1 fail)
- Compatible async mode via Bash tool `run_in_background: true` → SA chains other work, reads the result later

Variants:
```bash
./scripts/run-tests.sh tests/test_X.py --silent   # exit code only, no stdout
./scripts/run-tests.sh tests/ --collect           # collect-only (no execution)
./scripts/run-tests.sh tests/test_X.py::TestY::test_z  # single test
```

**Raw recipe (if you MUST bypass the wrapper)**:
```bash
uv run --package <pkg> pytest <target> -q --tb=line --no-header
# The timeout=60 is applied automatically via pyproject.toml.
```

`<pkg>` → `<svc>` mapping (used by the auto-detect wrapper):
| pkg | svc dir |
|---|---|
| `ratis_auth` | `webservices/ratis_auth` |
| `ratis_product_analyser` | `webservices/ratis_product_analyser` |
| `ratis_list_optimiser` | `webservices/ratis_list_optimiser` |
| `ratis_rewards` | `webservices/ratis_rewards` |
| `ratis_notifier` | `webservices/ratis_notifier` |
| `ratis-core` | `ratis_core` |

**What is NORMAL locally (dev-host = Mac mini arm64 since 2026-05-04, PR #287)**:
- First run after `uv sync`: 1-3 min startup (paddleocr + setup_db + SSL init). Not a hang.
- Subsequent runs (warm cache): 30-60s startup + test time.
- Individual normal tests: <2s each.
- On arm64: if a dep has no native arm64 wheel, fallback compilation may add ~30s the first time. Also normal.

**What is ABNORMAL and signals a real problem**:
- An individual test >60s → pytest-timeout aborts it automatically, the error indicates the offending test.
- pytest --collect-only taking >30s → heavy top-level import, flag in report-back.
- Bash tool not returning control >5 min after pytest has displayed its results → tool bug, escalate.

**Tool to use**:
- On Mac/Linux (common case today): standard POSIX **Bash** tool. No special tricks.
- Avoid `tee`, `&>`, and long pipes that can saturate the buffer.
- The `scripts/run-tests.sh` wrapper already hides all redirection details — prefer it.
- *(Legacy Windows — pre-2026-05-04 dev-host)*: if running on Windows, the Bash tool has a known `2>&1` bug in MINGW. Prefer PowerShell or don't redirect stderr. See KP-40.

#### Recommended TDD cycle

```
1. Write test (which MUST fail)
2. Run pytest locally → confirm the failure
3. Write code (minimal implementation)
4. Run pytest locally → confirm the pass
5. Local lint: uvx ruff check webservices/<svc>/ ratis_core/
6. Commit + push
7. CI validates in parallel (Linux Docker = ground truth for merge)
8. If CI green → ready/merge. If red → debug logs + fix + re-push.
```

**Pre-merge**: CI green (R15). Pushing red is OK as an intermediate step, **merging red is forbidden**.

#### If local pytest is truly hung (>5 min with no output after fixtures)

Not a slow hang, a REAL hang. Signals:
- No output for >5 min WHILE the previous output showed progression
- The Bash tool not returning control long AFTER pytest has displayed its final results

Procedure:
1. Kill with Ctrl+C / TaskStop on the orchestrator side
2. Note the test file that triggered the hang in `STATUS: blocked` + `BLOCKERS:`
3. Do NOT retry with other flags — the env is broken
4. Commit your code as-is, push, escalate via report-back
5. The orchestrator will decide: fix env or trust CI

#### Lint/format (allowed locally, recommended pre-push)

`ruff` and `bandit` are fast (~5s), no DB or heavy fixtures:

```
uvx ruff check webservices/<svc>/ ratis_core/
uvx ruff format --check webservices/<svc>/ ratis_core/
uvx bandit[toml] -r webservices/<svc>/ ratis_core/ -ll -q -c pyproject.toml
```

Saves you a CI round-trip on trivial mistakes. Recommended before each push.

### DB (R02-R11)
- **R02** `db.commit()` **mandatory** in every route that mutates. No commit = silent rollback in prod (tests pass in dev because flush is visible in the shared session). Classic trap.
- **R03** Arch layers: `routes/` → `services/` → `repositories/`. No raw SQL outside `repositories/`.
- **R04** FK RESTRICT default. SET-NULL: `scans.user_id`, `scans.product_ean`, `receipts.user_id`. CASCADE: `price_consensus_scans.consensus_id`. Never disable FK.
- **R05** Never DELETE prod. Soft-delete: `stores.is_disabled` + `disabled_at`, `users.is_deleted`.
- **R06** `updated_at` via PG triggers only. Never `onupdate` SQLAlchemy.
- **R07** Migration drop: `op.execute("ALTER TABLE x DROP CONSTRAINT IF EXISTS y")`. Never `op.drop_constraint()` without IF EXISTS. Run `test_migration.sh` before pushing.
- **R08** Alembic revision ID **≤32 chars** (`alembic_version` column = `varchar(32)`).
- **R09** Balance update **atomic**:
  ```python
  r = db.execute(
      text("UPDATE user_cab_balance SET balance=balance-:x WHERE user_id=:u AND balance>=:x"),
      {"x": amount, "u": user_id}
  )
  if r.rowcount == 0:
      raise InsufficientBalance()
  ```
- **R10** Atomic withdraw: BEGIN → INSERT `cashback_transactions` RETURNING id → UPDATE `cashback_withdrawals` → COMMIT
- **R11** Reconciliation: store `payment_provider_ref` at initiation. Pending withdrawals with ref → verify via provider API.

### code style (R12-R14)
- **R12** Python conv: `snake_case` · routes `/api/v1/` · errors `{"detail": "code_snake_case"}` · docstrings English · `HTTPException` in routes only (not in services, see KP-05).
- **R13** PATCH: pass full Pydantic object. Use `model_fields_set` to distinguish absent vs explicit null.
- **R14** Pre-commit `uv run ruff check --fix` (mandatory).

### shared modules — never duplicate (R18)
`ratis_core` exposes shared modules. Never duplicate:
- `auth`: `get_current_user`, `get_http_current_user`
- `database`: `make_engine`, `get_db`
- `deps`: `get_bearer_token`, `verify_internal_key`
- `jwt`: `decode_access_token`
- `knowledge`: `load_knowledge`, `classify`
- `notifier_client`: `notify_user`
- `rewards_client`: `trigger_scan_accepted`, `trigger_referral_reward`
- `schemas`: `check_timezone`
- `settings`: `load_settings`
- `startup`: `require_env`
- `uploads`: `validate_image_upload`
- `utils`: `assert_owner`, `strip_str`, `match_str`

### config + env (R19-R20)
- **R19** Every variable parameter → `ratis_settings.json` OR `app_settings` table (values in cents). Fail-fast if missing. Never hardcode.
- **R20** New env var → simultaneously in 3 places:
  1. `.env.example` with `<placeholder>`
  2. `conftest.py` (so tests have the default value)
  3. `require_env("NEW_VAR")` in the lifespan of all affected services
  Never use `os.environ.get("X", "")` — fail-fast is mandatory.

### misc (R21, R23)
- **R21** 3rd-party errors (external API timeout/5xx) → raise `UpstreamServiceError` → uniform 503.
- **R23** Rate-limiting slowapi on: `/auth/login`, `/auth/register`, `/account/change-password`, `/auth/refresh`.

### endpoints (R27)
BEFORE proposing or coding a new endpoint:
1. `Grep docs/reference/ENDPOINTS.md` for the concerned domain (scan, reward, account, etc.)
2. If it exists: reuse it. Extend it if new fields are needed, but don't create a duplicate.
3. If truly new: document the why in the ARCH before writing the route.

### secrets / admin UI (R42)
If the task needs a **provider token** (github-app, cloudflare-r2, sentry, eas, stripe-restricted, etc.) or to **enter an admin UI**: go through the vault.
- Token Cat A/B: `ratis-secret use <name> --cmd "..."` (env subprocess injection, secret never displayed) — or Python-side `secret_with(<name>)` (context manager lease+revoke auto)
- Admin UI: `ratis-admin open <path> [--service pa|rw|au]` (OTT JWT 60s → cookie session)
- **NEVER ask the operator to manually set a secret** when the vault can mint it. The V0 pattern "you set your X" is obsolete.
- Cat C UI-only (Stripe live, Apple/Google OAuth console): `ratis-secret import <name> --category C --expires-at YYYY-MM-DD` after browser mint
- Audit log: `ratis-secret audit` (`~/.local/state/ratis-agent-mcp/audit/secrets-YYYY-MM.jsonl`)
- Details: `docs/agents/CLAUDE.md` R42 + `docs/arch/ARCH_agent_mcp.md` Module 10 (#563-#570)

## recurring code patterns

### uv-workspace Dockerfile (from repo-root context)
```dockerfile
COPY pyproject.toml uv.lock ./
COPY ratis_core/ ./ratis_core/
COPY webservices/<svc>/pyproject.toml ./webservices/<svc>/pyproject.toml
RUN uv sync --package <pkg> --no-dev --frozen --no-install-project
COPY webservices/<svc>/ ./webservices/<svc>/
RUN uv sync --package <pkg> --no-dev --frozen
CMD ["sh","-c","uvicorn main:app --host 0.0.0.0 --port ${PORT:-<default>} --proxy-headers"]
```

### Service lifespan
```python
from contextlib import asynccontextmanager
from ratis_core.startup import require_env
from ratis_core.sentry import init_sentry

@asynccontextmanager
async def lifespan(app: FastAPI):
    require_env("DATABASE_URL", "JWT_SECRET", "INTERNAL_API_KEY", ...)
    init_sentry("<svc_name>")
    yield
```

### Mutating route (the standard shape)
```python
@router.post("/resource", response_model=ResourceOut)
def create_resource(
    payload: ResourceIn,
    db: Session = Depends(get_db),
    user = Depends(get_current_user),
):
    res = repository.create(db, user_id=user.id, **payload.model_dump())
    db.commit()  # MANDATORY — no commit = silent rollback prod
    return ResourceOut.model_validate(res)
```

### Test fixture anti-leak (in every conftest.py)
```python
@pytest.fixture(autouse=True)
def assert_no_pending_changes(db_session):
    yield
    assert not (db_session.new or db_session.dirty or db_session.deleted), \
        "Uncommitted changes at end of test — missing db.commit() in route?"
```

### Config load (fail-fast patterns)
```python
from ratis_core.settings import load_settings

s = load_settings()
v = s["section"]["key"]              # fail-fast if section missing
v = s["section"].get("key", default) # tolerant if key may be absent
```

### New batch boilerplate
When creating a new batch `ratis_batch_<name>`:
- [ ] Code in `batch/ratis_batch_<name>/<entry>.py` with `if __name__ == "__main__": main()`
- [ ] `batch/ratis_batch_<name>/pyproject.toml` (workspace member, minimal deps)
- [ ] Add the member in root `pyproject.toml` `[tool.uv.workspace] members`
- [ ] `batch/ratis_batch_<name>/ARCH_BATCH_<NAME>.md` (template `docs/arch/ARCH_BATCH_TEMPLATE.md`)
- [ ] TDD tests: `batch/ratis_batch_<name>/tests/` + `conftest.py` with `assert_no_pending_changes` fixture
- [ ] Service in `docker-compose.prod.yml` with `profiles: ["batch_<name>"]`, shared image `batch/Dockerfile`, specific env vars
- [ ] GitHub Actions workflow `.github/workflows/batch_<name>.yml` (lint + sast + tests, cron commented until prod-execution is wired)
- [ ] Run in prod via `./scripts/ops/run-prod-batch.sh <name>` (add `<name>` to the closed list in the wrapper)
- [ ] Update `docs/arch/ARCH_deployment.md` § "Running batches in prod" (row in the available batches table)

### Route-test pattern
```python
def test_create_resource_commits_to_db(client, db_session, auth_headers):
    response = client.post("/api/v1/resource", json={...}, headers=auth_headers)
    assert response.status_code == 201
    # Verify via db_session (SA-2.0 fixture with savepoint rollback) that the row is there
    row = db_session.query(Resource).filter_by(...).first()
    assert row is not None
```

## known pitfalls — don't repeat

### Build / dependencies
- **P01** `postgresql://` instead of `postgresql+psycopg://` → SQLAlchemy tries `psycopg2` (not installed) → crash. Always `+psycopg`.
- **P02** Python 3.13 / 3.14 → `paddlepaddle` has no wheel → install fails. Pin 3.12 via `.python-version`.
- **P11** `opencv-contrib-python` (pulled by paddleocr) needs `libgl1 libglib2.0-0 libgomp1` at runtime → Dockerfile AND CI runner apt-install.

### DB / migrations
- **P03** Alembic revision id >32 chars → `value too long for varchar(32)` on UPDATE `alembic_version`.
- **P04** `psql` absent on self-hosted CI runner → use `psycopg` Python (post `uv sync`) for `CREATE DATABASE` of the test DB.
- **P13** Missing `assert_no_pending_changes` fixture → tests pass in dev, silent rollback in prod (flush visible in shared session during tests but commit is missing).
- **P14** Missing `db.commit()` in route → same silent rollback. Never forget.

### OCR
- **P10** PaddleOCR cold-start downloads ~200-300 MB of models on first call → pre-warm in Dockerfile with a `RUN python -c "from paddleocr import PaddleOCR; PaddleOCR(...)"`.
- **KP-37** `re.IGNORECASE` does NOT fold accents — `r"T[EÉ]L"` matches TEL but not TÉL. Solution: explicitly enumerate `[eEéÉèÈ]` in the char class OR pre-normalize the text with `unicodedata.normalize('NFD', text)` then strip accents.

### Concurrency / Race conditions
- **KP-41** `handle_barcode_rescan` race: 2 concurrent uploads same barcode → UniqueViolation. `with_for_update()` lock only if the old receipt exists — if no previous one, both INSERTs go out in parallel. V1.5 solution (to ship): `pg_advisory_xact_lock(hashtext(barcode))` + total-anchored items consolidation.
- **Defensive pattern**: for any endpoint that INSERTs with a UNIQUE constraint on user-data, consider (a) `SELECT ... FOR UPDATE` on the parent row OR (b) advisory lock on the natural key OR (c) `INSERT ... ON CONFLICT DO NOTHING` + retry.

### Worktrees (KP-30, KP-35, KP-38)
- **KP-30** Edit tool on absolute paths writes to the main checkout, not the SA's isolated worktree. Always use the absolute path **of the worktree** (`.worktrees/<name>/...`).
- **KP-35** Parallel SA dispatch in the same worktree → file clash. If you launch 2 SAs in parallel, each must have its own worktree+branch. Disjoint touches = OK.
- **KP-38** FE Expo worktree requires `npm install` (~2 min) on first run. `node_modules/` is gitignored so not shared between worktrees. Explicit brief: "0. `cd ratis_client && npm install` on first run".

### Windows tests (legacy — dev-host = Mac mini since 2026-05-04)
- **KP-40** pytest Windows + bash MINGW = stdout buffering lost if killed before flush. Symptom: 0 bytes of output for minutes then exit 0. **No longer applies to the current dev-host** (Mac mini arm64 macOS since PR #287). Kept for SAs that might still run on a secondary Windows machine. Historical solutions:
  - Add `-s` no-capture to pytest to bypass its buffering
  - If hung >2 min with no output → kill, trust CI Linux Docker (R15)
  - For local Windows debug: `set PYTHONUNBUFFERED=1` + `--capture=no -p no:cacheprovider`

### Apparently hung tests — often just slow (see R15)
- **Lesson 2026-04-28**: don't kill a test that appears hung during TDD pytest local cycle. Setup can take **1-3 minutes** (setup_db init + paddleocr lazy import + SSL/PG handshake). Typical symptom: prolonged silence then a burst of output all at once.
  - **Prerequisite docs/agents/CLAUDE.md**: each service `pyproject.toml` has `[tool.pytest.ini_options] timeout=60 timeout_method="thread"`. The `thread` mode avoids SIGALRM (incompatible macOS), so cross-OS. If timeout is exceeded, the test fails cleanly rather than hanging silently.
  - For real-time debug: `pytest -vv -s --tb=long` (shows output as it goes).
  - Local pytest = fast feedback. CI Linux Docker = ground truth for merge. Pushing red OK for debug, MERGING red is forbidden.

### Prod migrations (KP-42)
- **KP-42** Backfill type `UPDATE table SET col=value WHERE condition` can break rows in prod if the condition matches cases not anticipated (e.g., stores manually validated by admin before the column was added). **Before ANY prod backfill**: pre-migration audit on the real state + dry-run + documented rollback plan.

### Celery quirks
- **KP-39** Celery worker `sys.path` quirk: `from storage import upload` works in FastAPI uvicorn but breaks in the Celery worker (`ModuleNotFoundError`). Solution: absolute imports for code shared between route + Celery task (`from webservices.ratis_product_analyser.storage import upload`).

> 📚 **Full reference**: all KPs (KP-01 to KP-100+) are indexed with keywords in `docs/known/KNOWN_PROBLEMS_INDEX.md` and detailed in `docs/known/KNOWN_PROBLEMS.md`. Recommended workflow: `docs_search("symptom")` via docs-mcp → `docs_get("KP-NN")` for the detail. Only duplicate here the **recurring** pitfalls where a SA risks hitting them without knowing.

## report-back format (at the end of your task)

When you finish, concise report to the orchestrator:
```
STATUS: done | blocked | partial
FILES_TOUCHED:
  - path/to/file.py (new | modified | deleted)
  - path/to/test.py (new)
TESTS: <n> passed / <n> failed / <n> skipped
COMMITS: <sha> <one-line summary>
PR: #<number> URL (if open)
BLOCKERS: [list or "none"]
NEXT: [what you'd do if continuing, or "nothing — feature complete"]
NOTES: [caveats, tech debt introduced, decisions made along the way that need user validation]
```

Be concise. The orchestrator reads fast, decides, and pings you via `SendMessage` for follow-up.

## meta-rules
- Rules **shared with ORCHESTRATOR** live in `docs/agents/CLAUDE.md`. Consult it as priority before this file.
- If you think a rule should be added to `docs/agents/CLAUDE.md` (universal) or `docs/agents/SA_DEV.md` (dev-only) → flag it in your report-back `NOTES`, the orchestrator will ask for user validation.
- R26 v2: you can **propose** a modification to `docs/agents/CLAUDE.md` / `docs/agents/ORCHESTRATOR.md` / `docs/agents/SA_DEV.md` / `docs/agents/SA_EXPLORE.md` — the orchestrator will ask the operator for confirmation before committing. No silent modification, no paralyzing blockage either.
- **Secret pragma**: if a piece of code looks like a secret (URL with password, API key pattern) but is actually a dev placeholder, add `# pragma: allowlist secret` at the end of the line so `detect-secrets` ignores it. Never use this pragma to mask a real secret. For real secrets: R42 vault (`ratis-secret`), never in code.
