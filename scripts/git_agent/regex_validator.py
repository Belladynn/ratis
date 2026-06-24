"""Step 1 — Deterministic regex validation on PR diff."""

from __future__ import annotations

import re
from dataclasses import dataclass

RULES: list[dict] = [
    {
        "id": "R-01",
        "severity": "red",
        "pattern": r'os\.environ\.get\(.*?,\s*["\']["\']',
        "message": "Fallback vide interdit — utiliser require_env()",
        "exclude_tests": True,
    },
    {
        "id": "R-02",
        "severity": "red",
        "pattern": r"os\.environ\.get\(.*?,\s*None\)",
        "message": "Fallback None interdit — utiliser require_env()",
        "exclude_tests": True,
    },
    {
        "id": "R-04",
        "severity": "orange",
        "pattern": r"#\s*(TODO|FIXME|HACK|WORKAROUND)",
        "message": "Workaround non documente",
    },
    {
        "id": "R-05",
        "severity": "orange",
        "pattern": r"#\s*nosec(?!\s+B\d{3}\s+—)",
        "message": "# nosec sans justification — format : # nosec BXXX — raison",
    },
    {
        "id": "R-06",
        "severity": "orange",
        "pattern": r"@pytest\.mark\.skip",
        "message": "Test skippe sans justification",
    },
    {
        "id": "R-07",
        "severity": "orange",
        "pattern": r"@pytest\.mark\.xfail",
        "message": "Test xfail sans justification",
    },
    {
        "id": "R-10",
        "severity": "orange",
        "pattern": r"^(?!def |class |    ).*os\.environ",
        "message": "Variable d'env lue au niveau module",
        "exclude_tests": True,
    },
]

SEVERITY_EMOJI = {
    "red": "\U0001f534",
    "orange": "\U0001f7e0",
}


@dataclass
class Violation:
    rule_id: str
    severity: str
    file: str
    line: int
    message: str
    source: str


_TEST_PATH_PATTERNS = re.compile(r"(^|/)tests/|/test_[^/]+\.py$|/conftest\.py$")


def _is_test_file(filepath: str) -> bool:
    return bool(_TEST_PATH_PATTERNS.search(filepath))


def _parse_diff_files(diff: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """Parse unified diff into list of (filepath, [(line_number, content)])."""
    files: list[tuple[str, list[tuple[int, str]]]] = []
    current_file = None
    lines: list[tuple[int, str]] = []
    line_num = 0

    for raw_line in diff.splitlines():
        if raw_line.startswith("diff --git"):
            if current_file is not None:
                files.append((current_file, lines))
            current_file = None
            lines = []
        elif raw_line.startswith("+++ b/"):
            current_file = raw_line[6:]
        elif raw_line.startswith("@@ "):
            match = re.search(r"\+(\d+)", raw_line)
            if match:
                line_num = int(match.group(1)) - 1
        elif current_file is not None:
            if raw_line.startswith("+"):
                line_num += 1
                lines.append((line_num, raw_line[1:]))
            elif raw_line.startswith("-"):
                continue
            else:
                line_num += 1

    if current_file is not None:
        files.append((current_file, lines))

    return files


def validate(
    diff: str,
    bypasses_inline: set[tuple[str, str, int]],
    bypasses_permanent: set[tuple[str, str]],
) -> list[Violation]:
    """Run all regex rules against added lines in the diff.

    Args:
        diff: Raw unified diff text.
        bypasses_inline: Set of (rule_id, file, line) from inline comments.
        bypasses_permanent: Set of (rule_id, file) from exceptions.json.
    """
    violations: list[Violation] = []
    files = _parse_diff_files(diff)

    for filepath, added_lines in files:
        if not filepath.endswith(".py"):
            continue

        is_test = _is_test_file(filepath)

        for rule in RULES:
            if is_test and rule.get("exclude_tests"):
                continue
            compiled = re.compile(rule["pattern"])
            for line_num, content in added_lines:
                if compiled.search(content):
                    if (rule["id"], filepath, line_num) in bypasses_inline:
                        continue
                    if (rule["id"], filepath) in bypasses_permanent:
                        continue
                    violations.append(
                        Violation(
                            rule_id=rule["id"],
                            severity=rule["severity"],
                            file=filepath,
                            line=line_num,
                            message=rule["message"],
                            source=content.strip(),
                        )
                    )

    return violations


def has_blockers(violations: list[Violation]) -> bool:
    """Return True if any violation exists — all rules are blocking."""
    return len(violations) > 0
