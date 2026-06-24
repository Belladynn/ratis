# syntax=docker/dockerfile:1.7-labs
#
# Build from the repo root (workspace root for uv):
#   docker build -f webservices/ratis_auth/Dockerfile -t ratis-auth .
#
# AWS ECS / docker-compose all call this the same way.
# Multi-arch build (linux/amd64 + linux/arm64) supported via Docker buildx —
# same Dockerfile works on x86 prod and the arm64 Mac mini without edits.
#
# Two-stage build:
#   builder  → installs uv + syncs the workspace venv at /app/.venv
#   runtime  → copies ONLY the venv + app source, runs as a non-root user.
# The runtime image carries no uv binary, no build cache and no toolchain.

# Bases pinned by digest for reproducible, supply-chain-safe builds.
# Dependabot (package-ecosystem: docker) bumps the tag+digest weekly.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:3901499594d85f713aee76c80dea03e349123899d5fe685b5c148be3eec9df84
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.5.11@sha256:0ac957607303916420297a4c9c213bb33fbd3c888f9cd7f4f7273596ebf42b85

# uv as a named stage from a global-scope ARG — the portable way to pull the uv
# binary from a pinned image: works on every BuildKit frontend (a variable in
# `COPY --from=$ARG` does not) and lets `COPY --from=uv` reference a real alias.
FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------------------
# Stage 1 — builder : resolve and install dependencies into /app/.venv
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# uv (single static binary, no pip gymnastics) — copied from the pinned uv stage.
COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

# Step 1: copy ONLY dependency manifests so Docker caches the venv across
# source changes. Any source change below invalidates step 3 only, not step 2.
COPY pyproject.toml uv.lock ./
COPY ratis_core/ ./ratis_core/
COPY webservices/ratis_auth/pyproject.toml ./webservices/ratis_auth/pyproject.toml

# Step 2: sync deps for this service only (frozen from workspace lockfile).
# --no-install-project skips installing the app itself — source not copied yet.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package ratis-auth --no-dev --frozen --no-install-project

# Step 3: copy source and install the package itself.
COPY webservices/ratis_auth/ ./webservices/ratis_auth/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package ratis-auth --no-dev --frozen

# ---------------------------------------------------------------------------
# Stage 2 — runtime : minimal image, non-root, only the venv + app source
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Non-root user, fixed uid/gid 1000 (no home, nologin shell). The app never
# writes to its own install tree, so an unprivileged uid is safe.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Bring the resolved venv + app source across, owned by the runtime user.
COPY --from=builder --chown=app:app /app /app

USER app

WORKDIR /app/webservices/ratis_auth

# AWS/compose may inject $PORT; local fallback to 8001.
EXPOSE 8001

# Liveness probe. python:slim ships no curl/wget, so we drive http.client from
# the venv Python. http.client does NOT raise on HTTP 4xx/5xx, so we assert the
# status explicitly : the probe exits 0 only on a 200 from the unauthenticated
# /health route (ratis_core.health), non-zero on any other status / refused
# connection / timeout.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import http.client,os,sys; c=http.client.HTTPConnection('127.0.0.1',int(os.environ.get('PORT','8001')),timeout=4); c.request('GET','/health'); sys.exit(0 if c.getresponse().status==200 else 1)"]

# --proxy-headers is mandatory behind Caddy/the TLS proxy: without it, FastAPI
# sees the wrong client IP and scheme (http not https), which breaks rate
# limiting and OAuth redirect URLs.
# --forwarded-allow-ips '*' is required to honor X-Forwarded-For from Caddy;
# uvicorn defaults to 127.0.0.1, which would make slowapi see the Caddy
# container IP for ALL requests → global rate-limit bucket → brute-force
# defense neutralized. Safe because uvicorn is only reachable from Caddy
# behind a firewall in prod (audit F-AU-4).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8001} --proxy-headers --forwarded-allow-ips '*'"]
