"""LLM clients for the pipeline Phase 2 comprehend step.

Two implementations of the :class:`worker.pipeline.comprehend.LLMClient`
Protocol :

- :class:`AnthropicLLMClient` — production wiring (Anthropic Claude
  ``/v1/messages`` API). Lazy-imports the ``anthropic`` SDK so the cold
  boot of the module is cheap. Keys / model are read from env vars
  shared with the legacy bridge (``LLM_API_KEY`` / ``LLM_MODEL``) so we
  don't multiply the deploy-ops surface — cf.
  ``worker/pipeline/llm_filter.py`` § ``make_default_llm_filter`` for
  the precedent.

- :class:`StubLLMClient` — tests. Returns a canned ``dict`` regardless
  of input and records the calls so assertions can verify the prompt
  flow without hitting the network.

Cf. ``ARCH_receipt_pipeline.md`` § Plan de migration / § Anti-patterns
(no silent drop : the comprehend module raises ``ComprehendError`` on
any failure these clients surface).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# Default Claude model for pipeline comprehend. Promoted to env via
# ``LLM_MODEL`` so deploy ops can swap without code change (R19). The
# constant remains here as a documented fallback.
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_TIMEOUT_S = 30.0


class AnthropicLLMClient:
    """Production LLM client — Anthropic Claude messages API.

    The ``anthropic`` SDK is lazy-imported in :meth:`extract` (first
    call) so that constructing this object — or merely importing the
    module — does NOT pay the SDK boot cost. Mirrors the legacy
    :class:`worker.pipeline.llm_filter.AnthropicLlmFilter` discipline.

    Env vars (consistent with the legacy bridge) :
      - ``LLM_API_KEY``  : Anthropic key. Constructor raises
        :class:`ValueError` if absent and no key is passed in.
      - ``LLM_MODEL``    : optional override of :data:`DEFAULT_ANTHROPIC_MODEL`.

    JSON parsing is defensive : Claude sometimes wraps the JSON in a
    ``\\`\\`\\`json`` fence even when prompted not to ; we strip the
    fence then ``json.loads`` so the protocol stays a plain ``dict``.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        resolved_key = api_key or os.environ.get("LLM_API_KEY", "").strip() or None
        if not resolved_key:
            raise ValueError(
                "AnthropicLLMClient requires an API key — pass api_key=... "
                "or set LLM_API_KEY in the environment (consistent with the "
                "legacy worker/pipeline/llm_filter.py wiring)."
            )
        self._api_key = resolved_key
        self._model = model or os.environ.get("LLM_MODEL", "").strip() or DEFAULT_ANTHROPIC_MODEL
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s
        self._client: Any = None  # populated on first .extract() — lazy import

    def extract(
        self,
        *,
        receipt_text: str,
        barcodes: list[str],
        prompt_template: str,
    ) -> dict[str, Any]:
        """Call Anthropic and return the parsed JSON dict.

        Substitutes ``{receipt_text}`` and ``{barcodes}`` in the
        ``prompt_template`` (provided by ``comprehend.py``). The
        comprehend layer always passes its own template ; we don't
        format anything else here so the Protocol stays minimal.
        """
        if self._client is None:
            from anthropic import Anthropic  # lazy — see class docstring

            self._client = Anthropic(api_key=self._api_key, timeout=self._timeout_s)

        prompt = prompt_template.replace("{receipt_text}", receipt_text)
        prompt = prompt.replace("{barcodes}", ", ".join(barcodes) if barcodes else "(aucun)")

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(block.text for block in response.content if block.type == "text")
        return _parse_llm_json(raw)


def _parse_llm_json(raw: str) -> dict[str, Any]:
    """Parse a Claude response that may or may not be markdown-fenced.

    Order of attempts :
      1. ``json.loads`` directly — works when Claude obeys the prompt.
      2. Strip a leading ``\\`\\`\\`json`` (or bare ``\\`\\`\\``) fence,
         then ``json.loads`` again.

    Raises :class:`ValueError` on unrecoverable parse failure — callers
    (the comprehend phase) wrap this into ``ComprehendError`` per
    ARCH § Anti-patterns (no silent drop).
    """
    text = raw.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        stripped = _strip_json_fence(text)
        parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError(f"LLM response is not a JSON object (got {type(parsed).__name__})")
    return parsed


def _strip_json_fence(text: str) -> str:
    """Strip a leading ``\\`\\`\\`json``/``\\`\\`\\`` fence and trailing ``\\`\\`\\```.

    Mirrors ``worker/pipeline/llm_filter._strip_json_fences`` defensive
    normalization for providers that emit fenced code despite the
    prompt requesting bare JSON.
    """
    s = text.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        if s.endswith("```"):
            s = s[: -len("```")]
        s = s.strip()
    return s


class StubLLMClient:
    """Test double — returns a canned response and records every call.

    Tests pass a hand-crafted JSON dict shaped like what the production
    Anthropic call would yield. The ``calls`` list lets tests assert the
    receipt_text / barcode list that comprehend forwarded.

    Strict on shape : the canned response must be a ``dict`` (mirrors
    the contract the production client enforces via
    :func:`_parse_llm_json`).
    """

    def __init__(self, canned_response: dict[str, Any]) -> None:
        if not isinstance(canned_response, dict):
            raise TypeError(f"StubLLMClient.canned_response must be a dict, got {type(canned_response).__name__}")
        self.canned_response = canned_response
        self.calls: list[dict[str, Any]] = []

    def extract(
        self,
        *,
        receipt_text: str,
        barcodes: list[str],
        prompt_template: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "receipt_text": receipt_text,
                "barcodes": list(barcodes),
                "prompt_template": prompt_template,
            }
        )
        return self.canned_response


__all__ = [
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TIMEOUT_S",
    "AnthropicLLMClient",
    "StubLLMClient",
]
