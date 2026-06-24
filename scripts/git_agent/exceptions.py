"""Bypass management — inline comments and permanent exceptions.json."""

from __future__ import annotations

import json
import re
from pathlib import Path

EXCEPTIONS_PATH = Path(__file__).parent / "exceptions.json"

INLINE_PATTERN = re.compile(r"#\s*git-agent:ignore\s+(R-\d{2})")


def load_permanent_bypasses() -> set[tuple[str, str]]:
    """Load permanent bypasses from exceptions.json.

    Returns set of (rule_id, file_path).
    """
    bypasses: set[tuple[str, str]] = set()
    if not EXCEPTIONS_PATH.exists():
        return bypasses

    data = json.loads(EXCEPTIONS_PATH.read_text(encoding="utf-8"))
    for entry in data.get("exceptions", []):
        rule = entry.get("rule", "")
        filepath = entry.get("file", "")
        if rule and filepath:
            bypasses.add((rule, filepath))

    return bypasses


def find_inline_bypasses(diff: str) -> set[tuple[str, str, int]]:
    """Scan diff for inline bypass comments.

    Returns set of (rule_id, file_path, line_number).
    """
    bypasses: set[tuple[str, str, int]] = set()
    current_file = None
    line_num = 0

    for raw_line in diff.splitlines():
        if raw_line.startswith("+++ b/"):
            current_file = raw_line[6:]
        elif raw_line.startswith("@@ "):
            match = re.search(r"\+(\d+)", raw_line)
            if match:
                line_num = int(match.group(1)) - 1
        elif raw_line.startswith("+"):
            line_num += 1
            inline_match = INLINE_PATTERN.search(raw_line)
            if inline_match and current_file:
                rule_id = inline_match.group(1)
                bypasses.add((rule_id, current_file, line_num))
        elif not raw_line.startswith("-"):
            line_num += 1

    return bypasses
