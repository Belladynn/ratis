"""Shared helpers for reading Phase-1 capture NDJSON files.

Capture lines can be very large (full HTML pages embed JSON blobs), so we
always stream one line at a time and never load a whole file into memory.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_RE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*")


def iter_records(ndjson_path: str | Path) -> Iterator[dict]:
    """Yield one parsed capture record per NDJSON line.

    Malformed lines are skipped silently — a single bad line should never
    abort a whole parse run.
    """
    n_read = 0
    n_skipped = 0
    with open(ndjson_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                n_skipped += 1
                continue
            n_read += 1
            yield record
    if n_skipped:
        logger.warning("%d malformed NDJSON line(s) skipped", n_skipped)
    logger.info("read %d NDJSON response(s) from %s", n_read, ndjson_path)


def extract_initial_state(html: str | None) -> dict | None:
    """Pull the ``window.__INITIAL_STATE__ = {...}`` JSON blob out of an HTML
    page.

    The assignment is followed by other script content, so we balance-scan
    the braces (string-aware) to find the exact end of the object. Returns
    ``None`` when the marker is absent or the JSON is unparsable.
    """
    if not html or "__INITIAL_STATE__" not in html:
        return None
    match = _STATE_RE.search(html)
    if not match:
        return None

    start = match.end()
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
                blob = html[start : i + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return None
    return None
