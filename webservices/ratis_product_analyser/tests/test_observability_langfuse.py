"""Unit tests for ``ratis_core.observability.init_langfuse``.

Mirrors the no-op-if-empty contract of ``init_sentry`` and locks in the
hard RGPD guard (DA-LO4 — never default to Langfuse Cloud). Zero network :
the Langfuse SDK is never reached on the no-op paths, and the import is
patched out where we assert instrumentation does NOT run.

cf docs/arch/ARCH_llm_observability.md (DA-LO1..5).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
import ratis_core.observability as observability


@pytest.fixture(autouse=True)
def _reset_langfuse_init_flag(monkeypatch):
    """The process-local idempotence flag must not bleed across tests.

    ``_disable_langfuse_sdk`` mutates ``LANGFUSE_TRACING_ENABLED`` via
    ``os.environ.setdefault`` (not monkeypatch) ; clear it through monkeypatch
    so the kill-switch state never leaks between tests.
    """
    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)
    observability._langfuse_initialised = False
    yield
    observability._langfuse_initialised = False


def _clear_langfuse_env(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)


def test_init_langfuse_noop_when_keys_absent(monkeypatch):
    """No keys → silent no-op, no instrumentation, no exception."""
    _clear_langfuse_env(monkeypatch)

    instrumentor = MagicMock()
    with patch.dict(
        "sys.modules",
        {"opentelemetry.instrumentation.anthropic": MagicMock(AnthropicInstrumentor=instrumentor)},
    ):
        # Must not raise.
        observability.init_langfuse("ratis_product_analyser")

    instrumentor.assert_not_called()
    assert observability._langfuse_initialised is False
    # The SDK kill-switch is engaged so @observe stays a true pass-through.
    assert os.environ.get("LANGFUSE_TRACING_ENABLED") == "false"


def test_init_langfuse_noop_when_secret_key_absent(monkeypatch):
    """Public key alone is not enough — both keys required (like Sentry DSN)."""
    _clear_langfuse_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")

    instrumentor = MagicMock()
    with patch.dict(
        "sys.modules",
        {"opentelemetry.instrumentation.anthropic": MagicMock(AnthropicInstrumentor=instrumentor)},
    ):
        observability.init_langfuse("ratis_product_analyser")

    instrumentor.assert_not_called()
    assert observability._langfuse_initialised is False


def test_init_langfuse_refuses_cloud_default_when_host_missing(monkeypatch, caplog):
    """DA-LO4 — keys present but LANGFUSE_HOST absent → warn + no-op.

    The SDK would silently default to cloud.langfuse.com and ship purchase
    data off-host. We refuse : no instrumentation, a RGPD warning logged.
    """
    _clear_langfuse_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    # LANGFUSE_HOST intentionally unset.

    instrumentor = MagicMock()
    with (
        patch.dict(
            "sys.modules",
            {"opentelemetry.instrumentation.anthropic": MagicMock(AnthropicInstrumentor=instrumentor)},
        ),
        caplog.at_level("WARNING"),
    ):
        observability.init_langfuse("ratis_product_analyser")

    instrumentor.assert_not_called()
    assert observability._langfuse_initialised is False
    assert os.environ.get("LANGFUSE_TRACING_ENABLED") == "false"
    assert any("LANGFUSE_HOST missing" in rec.message and "RGPD" in rec.message for rec in caplog.records), (
        "expected the RGPD cloud-refusal warning"
    )


def test_init_langfuse_instruments_when_fully_configured(monkeypatch):
    """All 3 vars present → instrument the SDK once + fetch the client.

    The Langfuse SDK is fully mocked (no network) ; we only assert the
    wiring contract : instrument() called, get_client() called.
    """
    _clear_langfuse_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")

    instrumentor_cls = MagicMock()
    fake_client = MagicMock()
    fake_client.auth_check.return_value = True
    get_client = MagicMock(return_value=fake_client)

    with patch.dict(
        "sys.modules",
        {
            "opentelemetry.instrumentation.anthropic": MagicMock(AnthropicInstrumentor=instrumentor_cls),
            "langfuse": MagicMock(get_client=get_client),
        },
    ):
        observability.init_langfuse("ratis_product_analyser")

    instrumentor_cls.return_value.instrument.assert_called_once_with()
    get_client.assert_called_once_with()
    assert observability._langfuse_initialised is True


def test_init_langfuse_idempotent(monkeypatch):
    """Second call in the same process must not re-instrument the SDK."""
    _clear_langfuse_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")

    instrumentor_cls = MagicMock()
    fake_client = MagicMock()
    fake_client.auth_check.return_value = True
    get_client = MagicMock(return_value=fake_client)

    with patch.dict(
        "sys.modules",
        {
            "opentelemetry.instrumentation.anthropic": MagicMock(AnthropicInstrumentor=instrumentor_cls),
            "langfuse": MagicMock(get_client=get_client),
        },
    ):
        observability.init_langfuse("ratis_product_analyser")
        observability.init_langfuse("ratis_product_analyser")

    instrumentor_cls.return_value.instrument.assert_called_once_with()


def test_init_langfuse_survives_auth_check_failure(monkeypatch):
    """A failing auth_check must never crash the caller (worker boot)."""
    _clear_langfuse_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")

    instrumentor_cls = MagicMock()
    fake_client = MagicMock()
    fake_client.auth_check.side_effect = RuntimeError("connection refused")
    get_client = MagicMock(return_value=fake_client)

    with patch.dict(
        "sys.modules",
        {
            "opentelemetry.instrumentation.anthropic": MagicMock(AnthropicInstrumentor=instrumentor_cls),
            "langfuse": MagicMock(get_client=get_client),
        },
    ):
        # Must not raise despite auth_check blowing up.
        observability.init_langfuse("ratis_product_analyser")

    instrumentor_cls.return_value.instrument.assert_called_once_with()
    assert observability._langfuse_initialised is True


def test_init_langfuse_noop_when_sdk_not_installed(monkeypatch):
    """Keys + host present but SDK import fails → warn + no-op, no crash."""
    _clear_langfuse_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")

    # Force the in-function import to raise ImportError.
    with patch.dict(
        "sys.modules",
        {
            "langfuse": None,
            "opentelemetry.instrumentation.anthropic": None,
        },
    ):
        observability.init_langfuse("ratis_product_analyser")

    assert observability._langfuse_initialised is False
