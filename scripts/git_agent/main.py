"""ratis_git_agent — Orchestrator.

Sequential pipeline: regex validation -> LLM review -> GitHub report.
If regex finds any violation, LLM step is skipped — all rules are blocking.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from .exceptions import find_inline_bypasses, load_permanent_bypasses
from .llm_reviewer import review as llm_review
from .regex_validator import SEVERITY_EMOJI, has_blockers, validate
from .reporter import build_report, post_comment, post_review

_FALSE_POSITIVE_BLOCK_RE = re.compile(
    r"git-agent:false-positive\s+"
    r"finding:\s*(?P<finding>.+?)\s+"
    r"file:\s*(?P<file>\S+)\s+"
    r"reason:\s*(?P<reason>.+?)(?=\ngit-agent:false-positive|\Z)",
    re.DOTALL,
)


def _get_pr_diff() -> str:
    """Get the diff of the PR against its base branch."""
    base_ref = os.environ.get("GITHUB_BASE_REF") or "main"  # empty string for issue_comment events
    result = subprocess.run(
        ["git", "diff", f"origin/{base_ref}...HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _get_triggering_comment() -> str:
    """Return the body of the comment that triggered this run, or empty string."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return ""
    try:
        with open(event_path, encoding="utf-8") as f:
            event = json.load(f)
        return event.get("comment", {}).get("body", "")
    except Exception:
        # GITHUB_EVENT_PATH may be absent or malformed outside CI — degrade gracefully
        return ""


def _parse_false_positives(comment: str) -> list[dict]:
    """Extract false-positive annotations from a /review comment.

    Expected format (repeatable):
        git-agent:false-positive
        finding: <description>
        file: <file>:<line>
        reason: <justification>
    """
    results = []
    for m in _FALSE_POSITIVE_BLOCK_RE.finditer(comment):
        results.append(
            {
                "finding": m.group("finding").strip(),
                "file": m.group("file").strip(),
                "reason": m.group("reason").strip(),
            }
        )
    return results


def main() -> None:
    print("=== Ratis Git Agent ===")

    # Get diff
    diff = _get_pr_diff()
    if not diff.strip():
        print("No diff found — skipping review.")
        return

    # Load bypasses
    bypasses_permanent = load_permanent_bypasses()
    bypasses_inline = find_inline_bypasses(diff)

    # Step 1 — Regex validation
    print("Step 1: Regex validation...")
    violations = validate(diff, bypasses_inline, bypasses_permanent)
    has_blockers(violations)

    if violations:
        for v in violations:
            emoji = SEVERITY_EMOJI.get(v.severity, "")
            print(f"  {emoji} {v.rule_id} | {v.file}:{v.line} | {v.message}")
            print(f"     > {v.source}")
        print(f"\n  BLOCKED — {len(violations)} violation(s), LLM review skipped.")
        report = build_report(violations, blocked_by_regex=True)
        post_comment(report)
        post_review(approved=False)
        sys.exit(1)

    print("  Aucune violation.")

    # Parse false-positive overrides from the triggering comment (issue_comment events)
    false_positives: list[dict] = []
    triggering_comment = _get_triggering_comment()
    if triggering_comment:
        false_positives = _parse_false_positives(triggering_comment)
        if false_positives:
            print(f"  {len(false_positives)} false-positive(s) annotated in comment.")

    # Step 2 — LLM review (optional, requires ANTHROPIC_API_KEY)
    llm_result = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("Step 2: LLM review...")
        try:
            llm_result = llm_review(diff, false_positives=false_positives or None)
        except Exception as exc:
            print(f"  LLM review failed: {exc}")
            print("  Fallback — regex-only mode.")
    else:
        print("\n  ANTHROPIC_API_KEY not set — LLM review skipped.")

    # Build and post report
    report = build_report(
        violations,
        blocked_by_regex=False,
        llm_result=llm_result,
    )
    post_comment(report)

    if llm_result is not None:
        post_review(approved=llm_result["approved"])
        if not llm_result["approved"]:
            print("  LLM review: BLOCKED")
            sys.exit(1)
        print("  LLM review: APPROVED")
    else:
        print("  Regex OK — PR approuvee (LLM review desactivee).")
        post_review(approved=True)


if __name__ == "__main__":
    main()
