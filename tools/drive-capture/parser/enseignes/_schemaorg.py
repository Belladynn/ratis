"""Shared schema.org / HTML extraction helpers for server-side rendered drives.

Auchan and Système U both ship structured product data inside server-rendered
HTML rather than a JSON SPA state — Auchan via *microdata*
(``itemscope``/``itemprop`` attributes) and Système U via JSON-LD
(``<script type="application/ld+json">``) plus inline ``data-*`` JSON blobs.

The helpers here stay deliberately small and dependency-free (stdlib only):
the parser tool is standalone, so no ``lxml``/``beautifulsoup`` is available.
They are private to the ``enseignes`` package — the ``_`` prefix keeps them
out of the CLI's drop-in parser auto-discovery.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
from collections.abc import Iterator

logger = logging.getLogger(__name__)


def unescape(text: str | None) -> str | None:
    """Decode HTML entities and collapse whitespace runs to single spaces.

    Server-rendered names arrive entity-encoded (``b&#x153;uf``) and padded
    with template indentation/newlines — both get normalised here so the
    extracted product name is clean.
    """
    if text is None:
        return None
    decoded = _html.unescape(text)
    collapsed = re.sub(r"\s+", " ", decoded).strip()
    return collapsed or None


def iter_ld_json(html: str) -> Iterator[dict]:
    """Yield every parsed ``<script type="application/ld+json">`` object.

    Blocks whose body is not valid JSON are skipped. JSON arrays at the top
    level are flattened so each element is yielded individually.
    """
    pattern = re.compile(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(html):
        body = match.group(1).strip()
        if not body:
            continue
        try:
            obj = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    yield item
        elif isinstance(obj, dict):
            yield obj


def extract_json_assignment(html: str, marker: str) -> dict | None:
    """Pull a ``<marker> = { ... }`` JSON object out of an inline script.

    ``marker`` is a literal JS identifier (e.g. ``tc_vars``). The object end
    is found by a string-aware balanced-brace scan, so trailing script code
    after the object does not break parsing. Returns ``None`` when the marker
    is absent or the object is unparsable.
    """
    assign = re.search(re.escape(marker) + r"\s*=\s*", html)
    if not assign:
        return None
    start = assign.end()
    if start >= len(html) or html[start] != "{":
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def split_itemscopes(html: str, schema_type: str) -> list[str]:
    """Split ``html`` into chunks, each starting at one ``schema.org`` itemscope.

    ``schema_type`` is the schema.org type name (e.g. ``Product``). Matches
    both ``http://`` and ``https://`` schema URLs. The first chunk (page
    chrome before the first match) is dropped — only the per-item chunks are
    returned, each running until the next item starts.
    """
    boundary = re.compile(
        r'(?=<[a-zA-Z]+[^>]*itemtype="https?://schema\.org/'
        + re.escape(schema_type)
        + r'")'
    )
    chunks = boundary.split(html)
    return chunks[1:] if len(chunks) > 1 else []


def itemprop_meta(chunk: str, prop: str) -> str | None:
    """Value of ``<meta itemprop="<prop>" content="...">`` within ``chunk``."""
    match = re.search(
        r'<meta[^>]*itemprop="' + re.escape(prop) + r'"[^>]*content="([^"]*)"',
        chunk,
    )
    if match is None:
        # attribute order on the tag can vary — try content before itemprop
        match = re.search(
            r'<meta[^>]*content="([^"]*)"[^>]*itemprop="' + re.escape(prop) + r'"',
            chunk,
        )
    return unescape(match.group(1)) if match else None
