"""Product knowledge — pattern matching utilities for product_knowledge.json.

Consumers load a section once at startup and pass it to classify():

    from ratis_core.knowledge import classify, load_knowledge

    _STORAGE_RULES = load_knowledge()["storage_type"]

    result = classify(_STORAGE_RULES, tags=["fr:produits-frais"], text="")
    # → "fresh"

Pattern conventions (defined in ratis_core/data/product_knowledge.json):
  "surgel"  → substring match in tag string or free text
  "frais$"  → exact token in OFF tags (split on ':' and '-'),
               or whole-word (\\b...\\b) in free text
"""

from __future__ import annotations

import json
import re
from importlib.resources import files as _pkg_files  # Traversable — wheel-safe

_KNOWLEDGE_REF = _pkg_files("ratis_core") / "data" / "product_knowledge.json"

if not _KNOWLEDGE_REF.is_file():
    raise FileNotFoundError(
        f"Product knowledge config not found: {_KNOWLEDGE_REF}. "
        "Ensure ratis_core/data/product_knowledge.json is present."
    )


def load_knowledge() -> dict:
    """Load and return the full product_knowledge.json as a dict.

    The result is NOT cached — callers must cache at module level:
        _RULES = load_knowledge()["storage_type"]  # once, at import time
    """
    with _KNOWLEDGE_REF.open(encoding="utf-8") as fh:
        return json.load(fh)


def match_tag(pattern: str, tag: str) -> bool:
    """Match a pattern against a lowercase OFF tag (e.g. 'en:fresh-foods').

    Pattern without $ → substring in the full tag string.
    Pattern with $    → exact token after splitting on ':' and '-'.

    Examples:
        match_tag("frais$", "fr:produits-frais")      → True
        match_tag("frais$", "fr:confitures-de-fraises") → False
        match_tag("surgel",  "fr:surgeles")            → True
    """
    if pattern.endswith("$"):
        return pattern[:-1] in re.split(r"[:\-]", tag)
    return pattern in tag


def match_text(pattern: str, text: str) -> bool:
    """Match a pattern against lowercase free text.

    Pattern without $ → substring search.
    Pattern with $    → whole-word search via \\b boundaries.

    Examples:
        match_text("frais$", "à conserver frais") → True
        match_text("frais$", "parfum fraises")    → False
        match_text("réfrigér", "réfrigéré")       → True
    """
    if pattern.endswith("$"):
        return bool(re.search(r"\b" + re.escape(pattern[:-1]) + r"\b", text))
    return pattern in text


def classify(
    section: dict[str, list[str]],
    tags: list[str],
    text: str = "",
) -> str | None:
    """Return the first category key whose patterns match any tag or text.

    Iterates section keys in insertion order — put higher-priority categories
    first in product_knowledge.json (e.g. 'frozen' before 'fresh').

    Args:
        section: mapping category_name → patterns (from product_knowledge.json)
        tags:    lowercased OFF tags (categories_tags + labels_tags combined)
        text:    lowercased free text (conservation_conditions or similar)

    Returns:
        The first matching category key, or None if no pattern matches.

    Example:
        section = {"frozen": ["frozen$"], "fresh": ["frais$"]}
        classify(section, ["en:frozen-foods"]) → "frozen"
        classify(section, ["en:beverages"])    → None
    """
    for category, patterns in section.items():
        for pattern in patterns:
            if any(match_tag(pattern, tag) for tag in tags):
                return category
            if text and match_text(pattern, text):
                return category
    return None
