# `scripts/seed/` — Personas seed for the `ratis_seed` local DB

Operator-facing documentation for the seed system. **Source of truth for
design** : `ARCH_seed_test_data.md` (root) — this README is the *operating
manual*.

---

## Purpose — what this thing does

Populate a dedicated local Postgres DB (`ratis_seed`) with 6 deterministic
personas + their stores / products / scans / monetization state, so that :

- The mobile dev app can log in via `__DEV__` picker as any persona and
  see a realistic populated UI (no manual scan-everything-from-scratch).
- Demo screenshots, product showcases, and regression-visual tests use a
  stable reference state with the same UUIDs / balances at every run.
- Backend integration tests sign JWTs against a known-state user without
  rebuilding the user graph each test.

The system **deliberately never runs against `ratis_dev`** (where you do
manual debugging) nor against any prod DB (DA-5 safety guards refuse).

---

## When to use which target

| Goal | Target | Effect |
|---|---|---|
| First-time setup on a fresh checkout | `make seed-db-init` | DROP + CREATE `ratis_seed` + `alembic upgrade head`. Idempotent. |
| Refresh DB with latest seed code | `make seed-rebuild` | Re-runs `seed-db-init` then invokes `scripts/seed/main.py`. ~60s on M4 Pro. |
| « I changed a persona, give me a fresh state » | `make seed-wipe` | TRUNCATE seeded tables (CASCADE) + re-run `main.py`. No DROP / no alembic replay. Faster than `seed-rebuild`. |
| Regenerate the barcode HTML for phone scanning | `make seed-barcodes` | Writes `docs/seed/barcodes.html` (26 EAN-13 SVG codes). |
| Switch to seed mode (point services at `ratis_seed`) | `make seed-up` | Writes a seed `.env.local` (from the consolidated `.env.example` template) + `docker compose up -d`. |
| Switch back to clean dev mode | `make dev-up` | Writes a dev `.env.local` (from the consolidated `.env.example` template) + `docker compose up -d`. |

`make help` lists everything.

### Common workflows

**First time on this machine** :

```bash
docker compose up -d              # Postgres + Redis running
make seed-db-init                 # creates `ratis_seed`
make seed-rebuild                 # populates it
make seed-up                      # services now read `ratis_seed`
make seed-barcodes                # generates the printable barcodes
open docs/seed/barcodes.html      # second screen for phone scanning
```

**Iterating on a persona** :

```bash
# edit scripts/seed/users.py / scans.py / monetization.py / etc.
make seed-wipe                    # TRUNCATE + re-run (no DROP/alembic)
```

**Back to clean dev for a manual scan debug** :

```bash
make dev-up
```

---

## Layout — what lives where

```
scripts/seed/
├── README.md                    ← you are here
├── __init__.py                  (empty, package marker)
├── _engine.py                   SQLAlchemy engine/session helper
├── main.py                      orchestrator + DA-5 safety guards
├── wipe.py                      `wipe_all()` + `make seed-wipe` entry
├── users.py                     6 personas + balances + admin audit samples
├── stores.py                    14 stores (12 OSM + 1 user_suggested + 1 disabled)
├── products.py                  25 food-only products (real OFF EANs)
├── barcodes.py                  generator for `docs/seed/barcodes.html`
├── scans.py                     ~3.5K scans + 500 receipts + 10 narrative scenarios
├── monetization.py              5 subs + 8 gift_card_orders + 5 withdrawals
├── product_knowledge.py         10 OCR auto-learn samples (5 confirmed + 5 unconfirmed)
└── tests/
    ├── test_safety_guards.py    DA-5 unit tests (no DB)
    └── test_seed_e2e.py         end-to-end against a disposable PG DB
```

Wiring is in `main.py` — when you add a new domain module, register it
there in dependency order.

---

## Personas — quick reference

Full detail in `ARCH_seed_test_data.md § Personas` ; UUIDs in
`scripts/seed/users.py` (`PERSONA_UUIDS`).

| Persona | Email | UUID suffix | State tested |
|---|---|---|---|
| Alice  🟢 | `dev_alice@ratis.app` | `…0000a` | Fresh registration, empty everything, 1 trial sub |
| Bob    🔵 | `dev_bob@ratis.app` | `…0000b` | Active daily user — 47 receipts + 23 e-labels + 3 manual scans |
| Charlie 🟣 | `dev_charlie@ratis.app` | `…0000c` | Power user premium — 312 receipts, 8 gift cards, 3 withdrawals, 47 500 CAB |
| Diane  🟡 | `dev_diane@ratis.app`* | `…0000d` | RGPD edge — soft-deleted, anonymised email, preserved cashback/withdrawals |
| Admin  🔴 | `dev_admin@ratis.app` | `…000ad` | Ops account — 0 personal scans, operator of admin_settings_audit samples |
| Eve    🟠 | `dev_eve@ratis.app` | `…0000e` | Shadow-banned — 140 scans BUT 0 CAB credits (silent skip invariant) |

\* Diane's email is anonymised on seed (becomes `deleted_<uuid>@ratis.app`)
to mirror the post-`DELETE /account` shape.

---

## Barcode scan workflow (printable HTML)

The `make seed-barcodes` target generates `docs/seed/barcodes.html` — a
single self-contained HTML file with 26 EAN-13 SVG barcodes (25 valid
food products + 1 synthetic invalid for testing the rejection UX).

Workflow on a dev machine :

1. `make seed-up` (services pointed at `ratis_seed`).
2. `make seed-barcodes` (regenerate if products edited).
3. Open `docs/seed/barcodes.html` on a **second screen / external monitor**.
4. From the phone running the dev build of the mobile app, scan barcodes
   directly from that screen.

The HTML auto-renders barcodes at a phone-camera-friendly size (~ 4cm wide
at typical desktop DPI). Each code shows the EAN underneath plus the
product name + category for verification.

The synthetic invalid (`9999999999999`) exists so you can demonstrate the
*"produit inconnu"* path of the scan UX without touching the catalog.

---

## Adding to the seed (when you touch a new table)

The seed catches up to the schema lazily — when a new table becomes
load-bearing for the UI you ship, add it to the seed in the same PR.

1. **Pick / create the domain module** (`scripts/seed/<domain>.py`).
   Existing examples : `users.py`, `stores.py`, `monetization.py`.
2. **Implement `seed_<thing>(session: Session) -> None`** :
   - Use ORM models from `ratis_core.models` (never raw SQL outside the
     `text(...)` helper for TRUNCATE / safety probes).
   - Make it **idempotent** : a quick `SELECT` against a deterministic
     row to short-circuit if already seeded. See `monetization.py`
     `_already_seeded()` for the canonical pattern.
   - Re-use `PERSONA_UUIDS` from `users.py` for persona FKs.
3. **Wire it in `main.py`** in dependency order (foundation tables before
   their dependents).
4. **Add to `wipe.py` `SEEDED_TABLES`** so `make seed-wipe` clears it.
5. **Add e2e assertions** in `tests/test_seed_e2e.py` :
   - At least one row-count check.
   - At least one idempotency check (run twice → same counts).
6. **Update `ARCH_seed_test_data.md § Step <N>`** with what shipped.

---

## Safety — what stops you from wiping prod

`scripts/seed/main.py` and `scripts/seed/wipe.py` BOTH refuse to run if
EITHER of the following is true :

- `ENVIRONMENT == 'production'` (case-insensitive), OR
- `DATABASE_URL` does not contain `_seed` or `_dev` substring.

This is the **DA-5** guard from the ARCH. It runs **before** any DB
session is opened, so a misconfigured `ENVIRONMENT=production` shell
aborts with a `RuntimeError` and no rows are touched.

The two checks are redundant on purpose — losing one (e.g. someone
sets `ENVIRONMENT=seed` on a prod box pointing at a prod URL) still
fails closed because of the URL substring check.

**Never bypass these guards.** If you genuinely need to seed a non-local
DB (say a Hetzner staging deployment), rename the DB to include
`_seed`/`_dev` in the URL or extend the allow-list in
`scripts/seed/main.py::_check_safety_guards` — and ship that change as
its own PR with explicit code review.

---

## Troubleshooting

### « `DATABASE_URL not set — refusing to run seed` »

You're not invoking via `make seed-rebuild` / `make seed-wipe`. Those
targets set the env vars inline. If you call `python -m scripts.seed.main`
directly, you must export `DATABASE_URL` and `ENVIRONMENT=seed` first.

### « alembic head mismatch / multiple heads »

`make seed-db-init` runs `alembic upgrade head` against `ratis_seed`.
If your branch has multiple migration heads, the command fails with a
clear error from alembic. Fix : merge / resolve the branches per the
standard alembic workflow (see `docs/ops/RUNBOOK_MIGRATION.md`).

### « `.env.local` not found / wrong DB after switching modes »

`make seed-up` / `make dev-up` write the matching `.env.local` directly
(seed/dev templates are consolidated into `.env.example`). If `.env.local`
exists but points at the wrong DB, re-run the matching make target.

### « `seed-rebuild` is slow on first run after pulling »

Normal — `uv sync` reinstalls deps + python-barcode wheel + alembic
replay (~40-60s on M4 Pro). Subsequent runs hit the warm `.venv` cache
and the seed itself runs in ~3-5s.

### « `wipe_all` says aborted : DATABASE_URL must contain `_seed`/`_dev` »

DA-5 working as designed. Verify your `.env.local` (cat it) — most
likely you're pointing at `ratis` or another DB that doesn't match the
substring rule.

### « `make seed-rebuild` ran twice but second run added rows »

A regression in idempotency — every `seed_*` function MUST short-circuit
on re-run via a deterministic SELECT. Open the offending module and
verify the `_already_seeded()` probe matches a row that was definitely
inserted on the first run. Reproduce via :

```bash
make seed-wipe                              # known clean state
make seed-rebuild                           # populate
# count any one table, say users :
docker compose exec -T postgres psql -U ratis ratis_seed \
  -c "SELECT count(*) FROM users;"
make seed-rebuild                           # should be 0 new rows
docker compose exec -T postgres psql -U ratis ratis_seed \
  -c "SELECT count(*) FROM users;"          # must match
```

---

## Full design

See [`ARCH_seed_test_data.md`](../../ARCH_seed_test_data.md) for :

- DA-1 (2 DBs strategy : `ratis_dev` clean + `ratis_seed` seeded)
- DA-2 (Pattern A : Python factories, not factory_boy)
- DA-3 (hardcoded stores+products subset, not OSM/OFF batches)
- DA-3-bis (food only — no hygiene/ménager/beauté)
- DA-4 (email sentinel `dev_*@ratis.app`)
- DA-5 (safety guards — described above)
- Roadmap (10 steps) + per-step status
- Personas full backstory + state matrix
