"""Unit tests for the pipeline LLM clients (Anthropic + Stub).

Pure-functional, no DB. Anthropic SDK is monkeypatched so tests don't
hit the network. Cf. ARCH § Anti-patterns (no silent drop on parse
failure → comprehend layer surfaces ComprehendError).
"""

from __future__ import annotations

from typing import Any

import pytest
from worker.pipeline.llm_clients import (
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicLLMClient,
    StubLLMClient,
    _parse_llm_json,
    _strip_json_fence,
)

# ── StubLLMClient ─────────────────────────────────────────────────────────


def test_stub_returns_canned_response():
    canned = {"items": [{"raw_label": "MILK", "total_cents": 199}]}
    stub = StubLLMClient(canned)

    out = stub.extract(
        receipt_text="MILK 1.99",
        barcodes=["1234567890123"],
        prompt_template="ignored",
    )

    assert out is canned


def test_stub_records_each_call():
    stub = StubLLMClient({"items": []})

    stub.extract(receipt_text="A", barcodes=["x"], prompt_template="p")
    stub.extract(receipt_text="B", barcodes=[], prompt_template="p")

    assert len(stub.calls) == 2
    assert stub.calls[0]["receipt_text"] == "A"
    assert stub.calls[0]["barcodes"] == ["x"]
    assert stub.calls[1]["receipt_text"] == "B"
    assert stub.calls[1]["barcodes"] == []


def test_stub_rejects_non_dict_canned_response():
    with pytest.raises(TypeError):
        StubLLMClient([{"not": "a dict"}])  # type: ignore[arg-type]


# ── AnthropicLLMClient — config / env ────────────────────────────────────


def test_anthropic_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key"):
        AnthropicLLMClient()


def test_anthropic_reads_env_api_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key-xyz")
    monkeypatch.delenv("LLM_MODEL", raising=False)

    client = AnthropicLLMClient()

    # Internal field — we don't run the constructor lazily but the model
    # default must be honored.
    assert client._model == DEFAULT_ANTHROPIC_MODEL


def test_anthropic_explicit_args_override_env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("LLM_MODEL", "env-model")

    client = AnthropicLLMClient(api_key="explicit-key", model="explicit-model")

    assert client._api_key == "explicit-key"
    assert client._model == "explicit-model"


# ── AnthropicLLMClient — extract() with monkeypatched SDK ────────────────


class _FakeBlock:
    def __init__(self, text: str, type_: str = "text") -> None:
        self.text = text
        self.type = type_


class _FakeResponse:
    def __init__(self, text_payload: str) -> None:
        self.content = [_FakeBlock(text_payload)]


class _FakeMessages:
    def __init__(self, payload: str) -> None:
        self._payload = payload
        self.last_args: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.last_args = kwargs
        return _FakeResponse(self._payload)


class _FakeAnthropic:
    """Minimal fake of the anthropic SDK Anthropic client."""

    def __init__(self, payload: str) -> None:
        self.messages = _FakeMessages(payload)


def _patch_anthropic(monkeypatch, payload: str) -> _FakeAnthropic:
    fake = _FakeAnthropic(payload)
    import sys
    from types import ModuleType

    fake_module = ModuleType("anthropic")

    def _factory(**kwargs: Any) -> _FakeAnthropic:
        return fake

    fake_module.Anthropic = _factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return fake


def test_anthropic_extract_parses_bare_json(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test")
    fake = _patch_anthropic(monkeypatch, '{"items": []}')

    client = AnthropicLLMClient()
    out = client.extract(
        receipt_text="hello",
        barcodes=["123"],
        prompt_template="say {receipt_text} bcs={barcodes}",
    )

    assert out == {"items": []}
    # Verify we substituted the placeholders before sending the prompt.
    sent = fake.messages.last_args
    assert sent is not None
    user_content = sent["messages"][0]["content"]
    assert "hello" in user_content
    assert "123" in user_content


def test_anthropic_extract_strips_json_fence(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test")
    fenced = '```json\n{"header": {"brand": "INTERMARCHE"}}\n```'
    _patch_anthropic(monkeypatch, fenced)

    client = AnthropicLLMClient()
    out = client.extract(receipt_text="x", barcodes=[], prompt_template="t {receipt_text} {barcodes}")

    assert out == {"header": {"brand": "INTERMARCHE"}}


def test_anthropic_extract_substitutes_no_barcodes_marker(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test")
    fake = _patch_anthropic(monkeypatch, "{}")

    client = AnthropicLLMClient()
    client.extract(receipt_text="x", barcodes=[], prompt_template="bc={barcodes}")

    assert fake.messages.last_args is not None
    sent_content = fake.messages.last_args["messages"][0]["content"]
    assert "(aucun)" in sent_content


# ── _parse_llm_json / _strip_json_fence helpers ──────────────────────────


def test_parse_llm_json_bare_object():
    assert _parse_llm_json('{"a": 1}') == {"a": 1}


def test_parse_llm_json_fenced():
    assert _parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_llm_json_rejects_non_object():
    with pytest.raises(ValueError, match="JSON object"):
        _parse_llm_json("[1, 2, 3]")


def test_parse_llm_json_raises_on_garbage():
    # JSONDecodeError is a subclass of ValueError ; matching ValueError
    # keeps the test resilient if the helper wraps the raw decode error.
    with pytest.raises(ValueError, match=".*"):
        _parse_llm_json("not even close to json")


def test_strip_json_fence_no_fence_is_noop():
    assert _strip_json_fence('{"a": 1}') == '{"a": 1}'


def test_strip_json_fence_handles_bare_triple_backticks():
    assert _strip_json_fence('```\n{"a": 1}\n```') == '{"a": 1}'
