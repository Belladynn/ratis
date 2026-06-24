"""
Optional Sentry-compatible integration — shared across all Ratis services.

Backend cible 2026-05-31+ : **GlitchTip self-hosted** (cf `ARCH_incident_management.md`).
GlitchTip implémente le protocole Sentry SDK nativement → ce module reste compatible
sans changement de code. Pointer `SENTRY_DSN` vers le DSN GlitchTip stocké dans
le Keychain `ratis-agent-mcp` (account `ops-glitchtip-dsn-ratis-backend` pour les
services Python, `ops-glitchtip-dsn-ratis-mobile` pour Expo, `ops-glitchtip-dsn-n8n-workflows`
pour les batchs / workflows n8n).

Récupérer un DSN au runtime :
    export SENTRY_DSN=$(security find-generic-password -s ratis-agent-mcp -a ops-glitchtip-dsn-ratis-backend -w)

No-op when:
- SENTRY_DSN env var is absent (disabled, local/test)
- sentry-sdk is not installed in the service (graceful fallback)

Usage in any service's lifespan:
    from ratis_core.observability import init_sentry
    init_sentry("ratis_rewards")

Env vars:
    SENTRY_DSN          — DSN GlitchTip (ou Sentry SaaS legacy). Absent = disabled.
    SENTRY_ENVIRONMENT  — "production" / "staging" / "development" (default: "development")
    SENTRY_SEND_PII     — "true" to send PII (default: "false" — RGPD strict)
    SENTRY_DEDUP_TTL    — seconds between identical errors sent (default: 60)

Quota management (GlitchTip self-hosted = pas de plafond, contrairement à Sentry cloud free tier 5K events/mois) :
    1. before_send — deduplicates identical errors within SENTRY_DEDUP_TTL seconds
       (one alert still fires; subsequent occurrences are silently dropped).
    2. before_send — drops HTTP 4xx client errors (FastAPI catches them before Sentry
       normally sees them, but we filter defensively in case of manual capture_exception).
    3. ignore_errors — transient network / OS exceptions that are noise, not bugs.
    4. traces_sample_rate=0.0 — no performance monitoring V1 (à activer V1.5+ si volume justifie).
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# Transient exceptions that are OS/network noise — never worth a Sentry event.
_IGNORED_ERRORS: list[type[BaseException]] = [
    ConnectionResetError,
    BrokenPipeError,
    TimeoutError,
    ConnectionAbortedError,
]

# Process-local dedup cache: {fingerprint: last_sent_timestamp}
_dedup_cache: dict[str, float] = {}


def _make_before_send(dedup_ttl: float):  # type: ignore[return]
    """Return a before_send hook that:
    - drops HTTP 4xx client errors (not actionable bugs)
    - deduplicates identical errors within dedup_ttl seconds
    """

    def before_send(event: dict, hint: dict) -> dict | None:  # type: ignore[return]
        exc_info = hint.get("exc_info")
        if exc_info is None:
            return event

        exc_type, exc_value, _ = exc_info

        # Drop HTTP 4xx — client errors, not server bugs.
        # FastAPI normally catches these before Sentry, but guard against
        # manual capture_exception() calls in service code.
        if hasattr(exc_value, "status_code") and exc_value.status_code < 500:
            return None

        fingerprint = f"{exc_type.__name__}:{exc_value}"
        now = time.monotonic()
        last_sent = _dedup_cache.get(fingerprint, 0.0)
        if now - last_sent < dedup_ttl:
            # Already reported recently — drop silently.
            return None

        _dedup_cache[fingerprint] = now
        return event

    return before_send


# ── Langfuse LLM tracing ────────────────────────────────────────────────────
# Self-hosted only (RGPD : receipt text is purchase data — never Cloud).
# See docs/arch/ARCH_llm_observability.md (DA-LO1..5).

# Process-local idempotence guard : AnthropicInstrumentor patches the
# ``anthropic`` SDK globally, so instrumenting twice in the same process is
# wasteful and can double-wrap spans. Set once on first successful init.
_langfuse_initialised = False


def _disable_langfuse_sdk() -> None:
    """Hard-disable the langfuse SDK in-process so a ``@observe``-decorated
    function is a true pass-through.

    Why this is needed : ``@observe`` lazily auto-initialises the global
    langfuse client on first call *even with empty keys*. That client
    registers an OTEL span processor whose exporter then logs a noisy
    "Failed to export span batch" at interpreter shutdown (no host → bad URL).
    Setting ``LANGFUSE_TRACING_ENABLED=false`` is the SDK's own kill-switch.
    Called on every no-op / refusal path of :func:`init_langfuse` so tracing
    stays genuinely inert (no background threads, no network) when disabled.

    We only set it when the operator hasn't pinned a value, so an explicit
    ``LANGFUSE_TRACING_ENABLED`` in the environment always wins.
    """
    os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")


def init_langfuse(service_name: str) -> None:
    """Initialise Langfuse LLM tracing for the given service. Silent no-op if
    the project keys are absent — same contract as :func:`init_sentry`.

    Hard RGPD guard (DA-LO4) : if keys are present but ``LANGFUSE_HOST`` is
    missing, the Langfuse SDK would silently default to ``cloud.langfuse.com``
    and ship purchase data off-host. We refuse that : keys without an explicit
    host → warning + no-op, never a cloud default.

    Wiring is OTEL-based : ``AnthropicInstrumentor().instrument()`` patches the
    ``anthropic`` SDK globally so the production
    ``AnthropicLLMClient.extract`` call is captured as a *generation* without
    touching the call site. Must be invoked POST-fork in the Celery worker
    (``worker_process_init``) — OTEL export threads created before ``fork`` are
    dead in the child (DA-LO2). Never crashes the caller.
    """
    global _langfuse_initialised

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    # No-op silently when the project keys are absent (local / test / CI).
    if not public_key or not secret_key:
        _disable_langfuse_sdk()
        return

    host = os.environ.get("LANGFUSE_HOST")
    if not host:
        # DA-LO4 — keys set but no host : the SDK would default to
        # cloud.langfuse.com. Refuse (purchase data is RGPD-sensitive).
        _disable_langfuse_sdk()
        logger.warning(
            "LANGFUSE keys set but LANGFUSE_HOST missing — refusing cloud default (RGPD), tracing disabled",
        )
        return

    if _langfuse_initialised:
        return

    try:
        from langfuse import get_client
        from opentelemetry.instrumentation.anthropic import (  # type: ignore[import-untyped]
            AnthropicInstrumentor,
        )
    except ImportError:
        _disable_langfuse_sdk()
        logger.warning(
            "LANGFUSE keys are set but langfuse / "
            "opentelemetry-instrumentation-anthropic are not installed in %s "
            "— LLM tracing disabled",
            service_name,
        )
        return

    # Patch the anthropic SDK globally so generations are captured at the
    # call site without modifying it.
    AnthropicInstrumentor().instrument()
    client = get_client()
    _langfuse_initialised = True

    # auth_check hits the configured host — log the outcome but never let a
    # connectivity hiccup take down worker boot (tracing is best-effort).
    try:
        authenticated = client.auth_check()
    except Exception:
        logger.warning(
            "Langfuse auth_check raised for %s (host=%s) — tracing left on, may be unauthenticated",
            service_name,
            host,
            exc_info=True,
        )
        authenticated = None

    logger.info(
        "Langfuse initialised — service=%s host=%s auth_check=%s",
        service_name,
        host,
        authenticated,
    )


def init_sentry(service_name: str) -> None:
    """Initialise Sentry for the given service. Silent no-op if DSN absent."""
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return

    try:
        import sentry_sdk  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed in %s — Sentry disabled",
            service_name,
        )
        return

    environment = os.environ.get("SENTRY_ENVIRONMENT", "development")
    send_pii = os.environ.get("SENTRY_SEND_PII", "false").lower() == "true"
    dedup_ttl = float(os.environ.get("SENTRY_DEDUP_TTL", "60"))

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        server_name=service_name,
        send_default_pii=send_pii,
        # Free tier: no performance monitoring (traces would burn the quota).
        traces_sample_rate=0.0,
        # Transient OS/network noise — not actionable, drop before sending.
        ignore_errors=_IGNORED_ERRORS,
        before_send=_make_before_send(dedup_ttl),
    )
    logger.info(
        "Sentry initialised — service=%s environment=%s send_pii=%s dedup_ttl=%ss",
        service_name,
        environment,
        send_pii,
        dedup_ttl,
    )
