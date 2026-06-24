# syntax=docker/dockerfile:1.7-labs
#
# Build from the repo root (uv workspace root):
#   docker build -f webservices/ratis_migrations/Dockerfile -t ratis-migrations .
#
# Run-once container that executes `alembic upgrade head` against $DATABASE_URL
# then exits. Wired in docker-compose.{,prod.}yml under the `migrate` profile,
# so it never starts on a regular `docker compose up`. No HEALTHCHECK — there is
# no long-running server to probe.
#
# Two-stage build: builder syncs the venv, runtime keeps only the venv + alembic
# config/revisions and runs as a non-root user.
#
# Why a dedicated image (not baked into ratis_auth or ratis_product_analyser):
#   - product_analyser is ~3.5 GB (PaddleOCR + paddlepaddle + opencv) — too
#     fat for what is essentially a 5-second alembic invocation
#   - auth carries OAuth/Stripe deps, irrelevant here
#   - keeping migrations isolated means a deploy can `--profile migrate run`
#     before any service container is even built/restarted

# Bases pinned by digest (Dependabot package-ecosystem: docker bumps these).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:3901499594d85f713aee76c80dea03e349123899d5fe685b5c148be3eec9df84
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.5.11@sha256:0ac957607303916420297a4c9c213bb33fbd3c888f9cd7f4f7273596ebf42b85

# uv as a named stage from a global-scope ARG (portable across BuildKit
# frontends; lets COPY --from=uv reference a real alias). See ratis_auth.
FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------------------
# Stage 1 — builder
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

# Step 1: copy ONLY dependency manifests so Docker caches the venv across
# alembic/ source changes. Adding a new revision file invalidates step 3 only,
# not step 2.
COPY pyproject.toml uv.lock ./
COPY ratis_core/ ./ratis_core/
COPY webservices/ratis_migrations/pyproject.toml ./webservices/ratis_migrations/pyproject.toml

# Step 2: sync deps for this package only (frozen from workspace lockfile).
# --no-install-project skips installing the runner itself — source not copied yet.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package ratis_migrations --no-dev --frozen --no-install-project

# Step 3: copy alembic config + revisions. alembic.ini lives at repo root
# alongside pyproject.toml, so we COPY it explicitly here.
COPY alembic.ini ./alembic.ini
COPY alembic/ ./alembic/
# HSP1 — `apply_procedure(name)` lit db/procedures/<name>.{sql,manifest.toml}
# au runtime des migrations qui cataloguent une procédure stockée. Sans ce
# COPY, l'upgrade prod casse en FileNotFoundError dès qu'une migration appelle
# `apply_procedure`. Le `.gitkeep` (présent dans le dossier en V1) suffisait
# tant que SP1 livrait un catalogue vide ; HSP1 le change.
COPY db/procedures/ ./db/procedures/
COPY webservices/ratis_migrations/ ./webservices/ratis_migrations/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package ratis_migrations --no-dev --frozen

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

# Run alembic from repo root so it picks up alembic.ini + alembic/ dir.
# DATABASE_URL is read by alembic/env.py from the environment (see env.py
# get_url() — handles postgresql:// → postgresql+psycopg:// normalisation).
WORKDIR /app

# Default command: upgrade to latest. Override at run time for downgrade /
# stamp / history inspection :
#   docker compose --profile migrate run --rm migrations alembic current
#   docker compose --profile migrate run --rm migrations alembic downgrade -1
CMD ["alembic", "upgrade", "head"]
