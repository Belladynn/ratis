"""Tier-S regex redactor — strips secrets before sending text to a cloud LLM.

Patterns are taken from HERMES_DISCOVERY.md §22 (Tier S — strict). Each match is
replaced by ``<<REDACTED:type>>`` so the structure of the transcript is preserved
but the secret value is never exposed. The redactor is intentionally conservative
(more false-positives than false-negatives) because the cost of leaking a key is
much higher than the cost of redacting a non-secret.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from re import Pattern

# Patterns ordered most-specific → least-specific so a JWT is not eaten by a
# generic "long base64" rule applied earlier.
_PATTERNS: list[tuple[str, Pattern[str]]] = [
    ("stripe_secret", re.compile(r"sk_live_[A-Za-z0-9]{24,}")),
    ("stripe_test", re.compile(r"sk_test_[A-Za-z0-9]{24,}")),
    ("github_token", re.compile(r"gh[ps]_[A-Za-z0-9]{36,}")),
    ("slack_bot_token", re.compile(r"xoxb-[A-Za-z0-9-]+")),
    ("notion_secret", re.compile(r"secret_[A-Za-z0-9]{43}")),
    ("notion_ntn", re.compile(r"ntn_[A-Za-z0-9_-]{40,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
    ("bcrypt_hash", re.compile(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}")),
    (
        "credit_card",
        re.compile(r"\b(?:4\d{3}|5[1-5]\d{2}|6011)[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
    ),
    # IBAN: 2 letters + 2 digits + 4-30 alphanumerics. Loose, may catch noise.
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{12,30}\b")),
]


@dataclass
class RedactionResult:
    """Result of a redaction pass."""

    text: str
    counts: Counter
    total: int

    def summary(self) -> str:
        if self.total == 0:
            return "0 redactions"
        parts = ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items()))
        return f"{self.total} redactions ({parts})"


def redact(text: str) -> RedactionResult:
    """Apply Tier-S regex redactions to ``text``.

    Returns the redacted text plus a Counter of how many matches per category
    were replaced. The Counter is logged into the audit trail so we know whether
    a session contained a lot of secrets (signal that something is leaking into
    transcripts that probably shouldn't be).
    """
    counts: Counter = Counter()
    out = text
    for name, pattern in _PATTERNS:
        replacement = f"<<REDACTED:{name}>>"

        def _sub(match: re.Match, _name: str = name) -> str:
            counts[_name] += 1
            return f"<<REDACTED:{_name}>>"

        out = pattern.sub(_sub, out)
        # The replacement above uses a closure that captures `name` correctly
        # via default arg, so re-binding `replacement` is just for readability.
        _ = replacement

    return RedactionResult(text=out, counts=counts, total=sum(counts.values()))


__all__ = ["RedactionResult", "redact"]
