# syntax=docker/dockerfile:1.7-labs
#
# Shared Dockerfile for all `batch/ratis_batch_*` runners.
#
# Build from the repo root (uv workspace root) :
#   docker build -f batch/Dockerfile -t ratis-batch .
#
# One image, many entry points. Each batch service in `docker-compose.prod.yml`
# uses this same image and overrides `command:` with its own script path
# (e.g. `["batch/ratis_batch_consensus/consensus.py"]`). Run-once containers —
# no HEALTHCHECK (nothing long-running to probe).
#
# Two-stage build: builder syncs the whole-workspace venv, runtime keeps only
# the venv + batch sources and runs as a non-root user.
#
# Why a single shared image rather than nine per-batch Dockerfiles :
#   - All batches share the same base : python 3.12 + uv + ratis_core + psycopg
#   - The fattest extras (PaddleOCR, paddlepaddle, opencv) live in
#     webservices/ratis_product_analyser/Dockerfile — batches don't need them
#   - We `uv sync --all-packages` once, so ratis-core + every ratis-batch-*
#     workspace member resolves under the same .venv
#   - Build cache is shared across batches : adding one revision to one batch
#     only invalidates the final layer, not the venv layer
#
# This mirrors webservices/ratis_migrations/Dockerfile (the "run-once
# container" pattern) but generalised. See ARCH_deployment.md § "Exécution
# de batches en prod".

# Bases pinned by digest (Dependabot package-ecosystem: docker bumps these).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:3901499594d85f713aee76c80dea03e349123899d5fe685b5c148be3eec9df84
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.5.11@sha256:0ac957607303916420297a4c9c213bb33fbd3c888f9cd7f4f7273596ebf42b85

# uv as a named stage from a global-scope ARG (portable across BuildKit
# frontends; lets COPY --from=uv reference a real alias). See ratis_auth.
FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------------------
# Stage 1 — builder : sync the whole workspace venv at /app/.venv
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# uv (single static binary) — copied from the pinned uv stage.
COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

# Step 1 : copy ONLY dependency manifests so Docker caches the venv across
# source-only changes. Adding a new revision file in any batch invalidates
# step 3 only, not step 2.
COPY pyproject.toml uv.lock .python-version ./
COPY ratis_core/ ./ratis_core/
COPY batch/ratis_batch_achievements/pyproject.toml ./batch/ratis_batch_achievements/pyproject.toml
COPY batch/ratis_batch_consensus/pyproject.toml ./batch/ratis_batch_consensus/pyproject.toml
COPY batch/ratis_batch_mystery_announce/pyproject.toml ./batch/ratis_batch_mystery_announce/pyproject.toml
COPY batch/ratis_batch_off_sync/pyproject.toml ./batch/ratis_batch_off_sync/pyproject.toml
COPY batch/ratis_batch_osm_sync/pyproject.toml ./batch/ratis_batch_osm_sync/pyproject.toml
COPY batch/ratis_batch_purge/pyproject.toml ./batch/ratis_batch_purge/pyproject.toml
COPY batch/ratis_batch_push_receipts/pyproject.toml ./batch/ratis_batch_push_receipts/pyproject.toml
COPY batch/ratis_batch_reconciliation/pyproject.toml ./batch/ratis_batch_reconciliation/pyproject.toml
COPY batch/ratis_batch_referral_payout/pyproject.toml ./batch/ratis_batch_referral_payout/pyproject.toml
COPY batch/ratis_batch_savings/pyproject.toml ./batch/ratis_batch_savings/pyproject.toml
COPY batch/ratis_batch_sirene_sync/pyproject.toml ./batch/ratis_batch_sirene_sync/pyproject.toml
COPY batch/ratis_batch_vrac_seed/pyproject.toml ./batch/ratis_batch_vrac_seed/pyproject.toml
# ratis_migrations is a workspace member (referenced from root pyproject.toml).
# Even though batches don't run migrations, `uv sync --all-packages` walks
# every workspace member, so its pyproject must be present here.
COPY webservices/ratis_migrations/pyproject.toml ./webservices/ratis_migrations/pyproject.toml

# Step 2 : sync deps for the whole workspace (frozen). `--all-packages` makes
# every `ratis-batch-*` member usable from the same .venv ; `--no-install-project`
# skips installing source for now (sources are not COPYed yet).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --all-packages --no-dev --frozen --no-install-project

# Step 3 : copy batch sources + config files. Each batch reads its DB / external
# config from env vars + ratis_core.settings (which loads ratis_settings.json
# from ratis_core/, so no separate copy needed).
COPY batch/ ./batch/

# ratis_batch_achievements imports the dispatcher (`_unlock`, `TRIGGER_HANDLERS`,
# `_BATCH_ONLY_HANDLERS`) from the rewards service. That module uses flat-layout
# imports (`from repositories.cab_repository import award_cab`) that resolve via
# `webservices/ratis_rewards/` on sys.path. The batch entrypoint adds the dir to
# sys.path defensively if present — so we copy the rewards source tree here
# (deps already installed via Step 2's --all-packages workspace sync). We
# deliberately copy only what the achievement_service transitively touches :
# services/, repositories/, and __init__-level files. Other rewards subpackages
# (routes/, schemas/) are not imported and stay out.
COPY webservices/ratis_rewards/services/ ./webservices/ratis_rewards/services/
COPY webservices/ratis_rewards/repositories/ ./webservices/ratis_rewards/repositories/

# Final install : now that sources are present, install the projects themselves
# into the venv so `python batch/.../foo.py` and `from ratis_batch_X import ...`
# both work.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --all-packages --no-dev --frozen

# ---------------------------------------------------------------------------
# Stage 2 — runtime (non-root)
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder --chown=app:app /app /app

USER app

# Default entrypoint is `python` so each docker-compose service can pass the
# batch script as `command: ["batch/ratis_batch_X/foo.py", "--flag"]` and
# extra args propagate via `docker compose run --rm batch_X --extra-flag`.
ENTRYPOINT ["python"]
