# syntax=docker/dockerfile:1.7-labs
# Build from repo root: docker build -f webservices/ratis_notifier/Dockerfile -t ratis-notifier .
#
# Two-stage build: builder syncs the venv, runtime keeps only the venv + source
# and runs as a non-root user. See ratis_auth/Dockerfile for the annotated template.

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

COPY pyproject.toml uv.lock ./
COPY ratis_core/ ./ratis_core/
COPY webservices/ratis_notifier/pyproject.toml ./webservices/ratis_notifier/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package ratis-notifier --no-dev --frozen --no-install-project

COPY webservices/ratis_notifier/ ./webservices/ratis_notifier/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package ratis-notifier --no-dev --frozen

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

WORKDIR /app/webservices/ratis_notifier

EXPOSE 8005

# Liveness probe — http.client returns for any HTTP reply (4xx/5xx included),
# so we assert the status explicitly : exit 0 only on a 200 from the
# unauthenticated /health route (ratis_core.health), non-zero otherwise /
# refused / timeout.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import http.client,os,sys; c=http.client.HTTPConnection('127.0.0.1',int(os.environ.get('PORT','8005')),timeout=4); c.request('GET','/health'); sys.exit(0 if c.getresponse().status==200 else 1)"]

# --forwarded-allow-ips '*' honors X-Forwarded-For from Caddy (default 127.0.0.1
# would make rate-limit / client-IP logging see the proxy, not the real client).
# Safe because uvicorn is reachable only from Caddy behind a firewall in prod
# (audit F-AU-4).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8005} --proxy-headers --forwarded-allow-ips '*'"]
