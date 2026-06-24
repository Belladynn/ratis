#!/usr/bin/env python3
"""SubagentStart hook — auto-inject SA_*.md rules based on subagent type.

Reads hook input JSON from stdin. If the subagent_type matches a mapped type,
reads the corresponding SA_*.md file and outputs `hookSpecificOutput.additionalContext`
JSON which Claude Code injects into the subagent's initial context.

Mapping:
  subagent_type="Explore" → docs/agents/SA_EXPLORE.md (research discipline: grep→index→seg→full)

Other subagent types (general-purpose, code-reviewer, Plan) are NOT auto-injected :
  - general-purpose covers dev AND non-dev tasks → orchestrator briefs docs/agents/SA_DEV.md manually when needed
  - code-reviewer has its own system prompt from the plugin
  - Plan uses superpowers:writing-plans skill

Rationale: defense in depth. Orchestrator STILL mentions docs/agents/SA_EXPLORE.md in brief (per
R32) but the hook guarantees auto-injection even if brief forgets it.

Fails silently (exit 0) on any error — a broken hook must not block subagent spawn.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Map subagent_type → (file path, section label)
TYPE_FILE_MAP = {
    "Explore": REPO_ROOT / "docs" / "agents" / "SA_EXPLORE.md",
}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # bad input → no-op, don't block

    tool_input = payload.get("tool_input", {}) or {}
    subagent_type = tool_input.get("subagent_type", "") or ""

    target = TYPE_FILE_MAP.get(subagent_type)
    if not target or not target.exists():
        return 0  # no match → no-op

    try:
        content = target.read_text(encoding="utf-8")
    except Exception:
        return 0  # read error → no-op

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": (
                f"## Auto-injected rules for {subagent_type} subagent ({target.name})\n\n"
                f"These are your operational rules for this task. Read them BEFORE starting.\n\n"
                f"{content}"
            ),
        }
    }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
