# syntax=docker/dockerfile:1.7-labs
#
# Build from repo root:
#   docker build -f webservices/ratis_product_analyser/Dockerfile -t ratis-product-analyser .
#
# This service is the fat one — the OCR stack pulls OpenCV + PaddlePaddle + pyzbar.
# Expected image size: ~2 GB compressed, ~3.5 GB uncompressed.
#
# Two-stage build: builder syncs the venv, runtime keeps the venv + source +
# the native libs OpenCV/pyzbar/PaddlePaddle need at import time, runs non-root.
#
# PaddleOCR downloads its detection/recognition/classification models on first
# call (~200 MB into ~/.paddleocr/). We pre-warm them at build time in the
# runtime stage (as the app user, into its home) so cold start doesn't punish
# the first real scan and the non-root user can read them.

# Bases pinned by digest (Dependabot package-ecosystem: docker bumps these).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:3901499594d85f713aee76c80dea03e349123899d5fe685b5c148be3eec9df84
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.5.11@sha256:0ac957607303916420297a4c9c213bb33fbd3c888f9cd7f4f7273596ebf42b85

# uv as a named stage from a global-scope ARG (portable across BuildKit
# frontends; lets COPY --from=uv reference a real alias). See ratis_auth.
FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------------------
# Stage 1 — builder : resolve and install the (heavy) OCR venv at /app/.venv
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
COPY webservices/ratis_product_analyser/pyproject.toml ./webservices/ratis_product_analyser/pyproject.toml

# Heavy step — the paddlepaddle wheel is ~200 MB. The cache mount keeps it
# between rebuilds; nothing from /root/.cache is carried into the runtime image.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package ratis-product-analyser --no-dev --frozen --no-install-project

COPY webservices/ratis_product_analyser/ ./webservices/ratis_product_analyser/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package ratis-product-analyser --no-dev --frozen

# ---------------------------------------------------------------------------
# Stage 2 — runtime : native OCR libs + venv + source, non-root, models pre-warmed
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    # Disable PaddlePaddle GPU checks (CPU-only deployment).
    FLAGS_allocator_strategy=auto_growth

# System libraries required AT RUNTIME by opencv-python-headless, pyzbar,
# PaddleOCR and python-magic. These must live in the runtime stage because the
# OCR engine imports them at first scan, not at build time.
#   libgl1 + libglib2.0-0 : OpenCV runtime
#   libgomp1              : OpenMP used by PaddlePaddle
#   libzbar0              : pyzbar (ZBar shared lib) for barcode decoding
#   libmagic1             : python-magic (file-type detection in ratis_core.uploads)
# DL3008: apt versions intentionally unpinned — they differ across amd64/arm64 and
# Debian point releases; the base is digest-pinned (freezing the apt snapshot) and
# Dependabot bumps that digest.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libzbar0 \
        libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user (uid/gid 1000) WITH a home dir — PaddleOCR caches its models
# under $HOME/.paddleocr, so the runtime user needs a writable, owned home.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder --chown=app:app /app /app

USER app

# Pre-download PaddleOCR models so cold start is just service boot, not a 30 s
# model download. Runs as the app user → models land in /home/app/.paddleocr,
# readable by the same uid at runtime. Mirrors worker/ocr/ocr_engine.py
# (use_angle_cls=True, lang='fr'). Non-fatal: a build-host network hiccup must
# not break the image — models then download lazily on the first request.
RUN python -c "from paddleocr import PaddleOCR; PaddleOCR(use_angle_cls=True, lang='fr', show_log=False)" \
    || echo "WARN: PaddleOCR model pre-warm failed — models will download on first request"

WORKDIR /app/webservices/ratis_product_analyser

EXPOSE 8003

# Liveness probe — http.client returns for any HTTP reply (4xx/5xx included),
# so we assert the status explicitly : exit 0 only on a 200 from the
# unauthenticated /health route (ratis_core.health), non-zero otherwise /
# refused / timeout. Longer start-period covers the OCR cold boot.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import http.client,os,sys; c=http.client.HTTPConnection('127.0.0.1',int(os.environ.get('PORT','8003')),timeout=4); c.request('GET','/health'); sys.exit(0 if c.getresponse().status==200 else 1)"]

# --forwarded-allow-ips '*' honors X-Forwarded-For from Caddy (default 127.0.0.1
# would make rate-limit / client-IP logging see the proxy, not the real client).
# Safe because uvicorn is reachable only from Caddy behind a firewall in prod
# (audit F-AU-4).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8003} --proxy-headers --forwarded-allow-ips '*'"]
