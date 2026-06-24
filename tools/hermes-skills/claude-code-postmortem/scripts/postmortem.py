"""claude-code-postmortem — main entry point.

Pipeline:

1. Discover candidate JSONL session files under ``~/.claude/projects/``.
2. For each candidate, decide whether it needs analysis (mtime cache + quiet-
   window check) and whether the hourly rate-limit allows it.
3. Parse the JSONL into a compact markdown transcript (drop bookkeeping noise).
4. Redact Tier-S secrets.
5. If long, chunk + summarize + synthesize via Codex; else one-shot.
6. Emit a human-readable post-mortem report and any candidate skills.
7. Log the run into the audit JSONL.

CLI flags (see ``__main__``):
    --dry-run     do everything but do not write the post-mortem file
    --no-llm      bypass Codex (use a stub response)
    --session P   analyze ONE specific JSONL path (skips state-based discovery)
    --force       ignore state cache (re-analyze even if mtime unchanged)
    --verbose     extra logging
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Allow `python scripts/postmortem.py` from inside the skill dir.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from codex_client import (
    CodexClient,
    stub_response_for_dry_run,
)
from redactor import redact
from state import PostmortemState

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

CLAUDE_PROJECTS_DIR = Path(
    os.environ.get(
        "HERMES_CLAUDE_PROJECTS_DIR",
        str(Path.home() / ".claude" / "projects"),
    )
)
POSTMORTEM_OUTPUT_DIR = Path(
    os.environ.get(
        "HERMES_POSTMORTEM_OUTPUT_DIR",
        str(Path.home() / ".claude" / "postmortems"),
    )
)
AUDIT_LOG_PATH = Path(
    os.environ.get(
        "HERMES_POSTMORTEM_AUDIT_LOG",
        str(Path.home() / ".hermes" / "state" / "claude-postmortem-audit.jsonl"),
    )
)

# Rough token estimate — 4-5 chars / token for mixed code+prose. We use 4.5.
CHARS_PER_TOKEN = 4.5
# Max input tokens we send in a single Codex call (leaves headroom for prompt
# scaffolding + completion).
MAX_TOKENS_SINGLE_SHOT = 150_000
CHUNK_TOKENS = 80_000

# Noisy types — these are bookkeeping events, no analytic value.
NOISE_TYPES = {"queue-operation", "last-prompt", "pr-link"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TranscriptStats:
    """Stats about a parsed transcript — used for the audit trail and reports."""

    session_id: str
    session_path: Path
    worktree_root: Path | None
    total_lines: int = 0
    type_counts: Counter = field(default_factory=Counter)
    tool_counts: Counter = field(default_factory=Counter)
    first_timestamp: str | None = None
    last_timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_path": str(self.session_path),
            "worktree_root": str(self.worktree_root) if self.worktree_root else None,
            "total_lines": self.total_lines,
            "type_counts": dict(self.type_counts),
            "tool_counts": dict(self.tool_counts.most_common(20)),
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
        }


@dataclass
class ParseResult:
    """Output of ``parse_session`` — transcript text + stats."""

    transcript: str
    stats: TranscriptStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Very rough token estimate. Good enough for chunking decisions."""
    return int(len(text) / CHARS_PER_TOKEN) + 1


def decode_project_dir_to_worktree(project_dir_name: str) -> Path | None:
    """Convert a ``~/.claude/projects/`` directory name back to a worktree path.

    The convention is to replace ``/`` with ``-`` in the worktree path. We
    invert that — best effort, returns None if the result doesn't exist on disk.
    """
    if not project_dir_name.startswith("-"):
        return None
    # Replace single dashes with slashes. Double-dashes in the encoded form
    # represent paths with a literal dash, but the simple replacement is
    # generally correct on macOS. We validate by existence below.
    candidate = "/" + project_dir_name[1:].replace("-", "/")
    p = Path(candidate)
    if p.exists():
        return p
    # Fallback: try to find any existing prefix.
    parts = project_dir_name[1:].split("-")
    for i in range(len(parts), 0, -1):
        prefix = "/" + "/".join(parts[:i])
        if Path(prefix).exists():
            return Path(prefix)
    return None


def _text_from_content(content: Any) -> str:
    """Flatten the polymorphic Claude Code ``message.content`` into text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            if t == "text":
                out.append(item.get("text", ""))
            elif t == "thinking":
                # Skip thinking — usually verbose & not actionable for post-mortem.
                continue
            elif t == "tool_use":
                name = item.get("name", "?")
                inp = item.get("input", {}) or {}
                summary = _summarize_tool_input(name, inp)
                out.append(f"[tool: {name}] {summary}")
            elif t == "tool_result":
                # Truncate tool output — full output is rarely informative for
                # post-mortem and easily blows the token budget.
                raw = item.get("content")
                if isinstance(raw, list):
                    raw_text = " ".join(x.get("text", "") for x in raw if isinstance(x, dict))
                else:
                    raw_text = str(raw or "")
                truncated = raw_text[:300] + ("…" if len(raw_text) > 300 else "")
                out.append(f"[tool_result] {truncated}")
            elif t == "image":
                out.append("[image]")
        return "\n".join(s for s in out if s)
    return str(content)


def _summarize_tool_input(name: str, inp: dict[str, Any]) -> str:
    """Compact one-line summary of a tool invocation. Truncates long fields."""
    if name == "Bash":
        cmd = str(inp.get("command", ""))
        return cmd[:200] + ("…" if len(cmd) > 200 else "")
    if name in {"Read", "Write", "Edit"}:
        return str(inp.get("file_path", ""))[:200]
    if name == "Grep":
        pattern = str(inp.get("pattern", ""))[:80]
        path = str(inp.get("path", ""))[:80]
        return f"pattern={pattern!r} path={path}"
    if name == "WebFetch":
        return str(inp.get("url", ""))[:200]
    if name == "Skill":
        return str(inp.get("skill", ""))[:100]
    # Generic — list the first 2 key=value pairs.
    pairs = []
    for k, v in list(inp.items())[:2]:
        val = str(v)
        pairs.append(f"{k}={val[:80]}")
    return " ".join(pairs)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_session(session_path: Path) -> ParseResult:
    """Parse a JSONL session file into a compact markdown-style transcript.

    Filters out bookkeeping types (queue-operation, last-prompt, pr-link) and
    keeps user/assistant/system/attachment for the actual conversation.
    """
    session_id = session_path.stem
    project_dir = session_path.parent.name
    worktree = decode_project_dir_to_worktree(project_dir)

    stats = TranscriptStats(
        session_id=session_id,
        session_path=session_path,
        worktree_root=worktree,
    )

    lines: list[str] = []
    with session_path.open("r", encoding="utf-8") as f:
        for raw in f:
            stats.total_lines += 1
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                stats.type_counts["_parse_error"] += 1
                continue

            mtype = obj.get("type", "_missing")
            stats.type_counts[mtype] += 1

            ts = obj.get("timestamp")
            if ts:
                stats.first_timestamp = stats.first_timestamp or ts
                stats.last_timestamp = ts

            if mtype in NOISE_TYPES:
                continue

            block = _render_event(obj, stats)
            if block:
                lines.append(block)

    transcript = "\n\n".join(lines)
    return ParseResult(transcript=transcript, stats=stats)


def _render_event(obj: dict[str, Any], stats: TranscriptStats) -> str:
    """Render a single JSONL event into the compact transcript form, or empty."""
    mtype = obj.get("type")

    if mtype == "user":
        msg = obj.get("message") or {}
        text = _text_from_content(msg.get("content"))
        return f"[user]\n{text}" if text.strip() else ""

    if mtype == "assistant":
        msg = obj.get("message") or {}
        content = msg.get("content") or []
        # Count tool uses for stats.
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    stats.tool_counts[item.get("name", "?")] += 1
        text = _text_from_content(content)
        return f"[assistant]\n{text}" if text.strip() else ""

    if mtype == "system":
        # Most system events are hook noise — keep only the subtype tag.
        subtype = obj.get("subtype", "")
        if not subtype:
            return ""
        return f"[system] {subtype}"

    if mtype == "attachment":
        att = obj.get("attachment") or {}
        att_type = att.get("type", "?")
        if att_type == "hook_non_blocking_error":
            stderr = (att.get("stderr") or "")[:200]
            return f"[attachment: hook_error] {stderr}"
        return f"[attachment: {att_type}]"

    return ""


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_transcript(transcript: str, chunk_tokens: int = CHUNK_TOKENS) -> list[str]:
    """Split a transcript into ~``chunk_tokens``-sized chunks at block boundaries.

    The transcript is composed of ``\\n\\n``-separated blocks (one per event).
    We greedily pack blocks into chunks. If a single block alone exceeds the
    limit (rare — a giant tool_result), we hard-split it on character count.
    """
    chunk_char_budget = int(chunk_tokens * CHARS_PER_TOKEN)
    blocks = transcript.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for block in blocks:
        block_len = len(block) + 2  # account for separator
        if block_len > chunk_char_budget:
            # Flush current
            if current:
                chunks.append("\n\n".join(current))
                current, current_len = [], 0
            # Hard-split oversized block
            for i in range(0, len(block), chunk_char_budget):
                chunks.append(block[i : i + chunk_char_budget])
            continue

        if current_len + block_len > chunk_char_budget and current:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0

        current.append(block)
        current_len += block_len

    if current:
        chunks.append("\n\n".join(current))
    return chunks


# ---------------------------------------------------------------------------
# Codex prompting
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = (
    "You are analyzing a Claude Code session transcript to identify patterns and "
    "produce a post-mortem. Be concise, factual, and skeptical. Suggest a "
    "skill candidate only when the same friction or workflow appeared at least "
    "twice in the session. For EACH candidate, compute a ROI score that helps "
    "the operator decide whether to promote, review, or archive it. Be honest: "
    "if a candidate solves a one-shot bug too specific to recur, give it "
    "verdict='archive'. Only verdict='promote' for genuinely recurrent and "
    "reusable patterns. Output STRICT JSON matching the schema in the user prompt."
)


def _build_user_prompt(
    transcript: str,
    stats: TranscriptStats,
    existing: ExistingSkills,
) -> str:
    skills_block = _format_existing_skills_block(existing)
    # S608 false-positive : this is an LLM prompt template, not a SQL query.
    # Ruff trips on the literal word "update" in the JSON schema documentation
    # for the candidate.update_target field (R33-justified suppression).
    return f"""Session metadata:
- Session UUID: {stats.session_id}
- Worktree: {stats.worktree_root}
- First event: {stats.first_timestamp}
- Last event:  {stats.last_timestamp}
- Total raw lines: {stats.total_lines}
- Type counts: {dict(stats.type_counts)}
- Top tools used: {dict(stats.tool_counts.most_common(10))}

Existing custom skills inventory (3 buckets — respect all three rules):
{skills_block}

Transcript (redacted, may be a chunk of a longer session):
<<<TRANSCRIPT
{transcript}
TRANSCRIPT>>>

Produce a JSON object with these fields exactly:
{{
  "summary": "1-paragraph summary of what was accomplished",
  "outcomes": ["completed" | "partially completed" | "stuck" | "abandoned"],
  "outcome_reason": "why this outcome",
  "tools_used": [{{"name": "...", "count": N}}],
  "patterns_observed": [
    {{"name": "...", "frequency": N, "context": "..."}}
  ],
  "skill_match": [
    {{"existing_skill": "name", "was_used": true|false, "should_have_been_used": true|false}}
  ],
  "skill_candidates": [
    {{
      "name": "skill-name-kebab",
      "description": "1-line desc",
      "trigger_pattern": "when X happens",
      "procedure_outline": "1-3 step skeleton",
      "update_target": "name-of-existing-active-skill-or-null",
      "update_reason": "what this update brings vs the active version (or null)",
      "roi_score": {{
        "frequency_in_session": N,
        "reusability_outside_context": "high" | "medium" | "low",
        "operator_cost_saved": "high" | "medium" | "low",
        "specificity_warning": "what would make this skill too narrow (or null if not)",
        "verdict": "promote" | "review" | "archive",
        "verdict_reason": "1-sentence justification"
      }}
    }}
  ],
  "warnings": ["any anti-patterns detected"]
}}

ROI scoring rules (be strict):
- verdict="promote" ONLY if frequency_in_session >= 2 AND reusability_outside_context != "low".
- verdict="archive" if frequency_in_session == 1 OR reusability_outside_context == "low"
  (the skill solves a one-shot bug too specific to recur — keep it as memory but
  do not pollute the active skill registry).
- verdict="review" for the edge cases where frequency is borderline OR the
  procedure_outline is ambiguous (operator should refine before promoting).

update_target rules :
- If the candidate IMPROVES an existing active skill (better trigger, missing
  edge-case, refined procedure) — set `update_target` to that active skill's
  exact `name` AND fill `update_reason` with what is added vs the current
  version. The admin UI will then offer a "replace" action.
- If the candidate is genuinely new (no overlap with any active skill) — set
  `update_target: null` and `update_reason: null`."""  # noqa: S608


CHUNK_SUMMARY_SYSTEM = (
    "You are summarizing one chunk of a longer Claude Code session transcript. "
    "Extract: (1) what the agent worked on in this chunk, (2) tools heavily used, "
    "(3) any recurring patterns or friction observed, (4) any apparent errors or "
    "retries. Be terse. Output plain text, 8-15 lines max."
)


def _build_chunk_summary_prompt(transcript_chunk: str, idx: int, total: int) -> str:
    return f"""Chunk {idx + 1} of {total}.

<<<TRANSCRIPT_CHUNK
{transcript_chunk}
TRANSCRIPT_CHUNK>>>

Summarize as instructed."""


def _build_synthesis_prompt(
    chunk_summaries: list[str],
    stats: TranscriptStats,
    existing: ExistingSkills,
) -> str:
    skills_block = _format_existing_skills_block(existing)
    summaries_block = "\n\n---\n\n".join(f"Chunk {i + 1}:\n{s}" for i, s in enumerate(chunk_summaries))
    return f"""You are synthesizing per-chunk summaries of a long Claude Code session
into a single post-mortem JSON object.

Session metadata:
- Session UUID: {stats.session_id}
- Worktree: {stats.worktree_root}
- First event: {stats.first_timestamp}
- Last event:  {stats.last_timestamp}
- Total raw lines: {stats.total_lines}
- Top tools used: {dict(stats.tool_counts.most_common(10))}

Existing custom skills inventory (3 buckets — respect all three rules):
{skills_block}

Per-chunk summaries:
{summaries_block}

Produce a JSON object with these fields exactly (same schema as a single-shot
analysis):
{{
  "summary": "...",
  "outcomes": ["..."],
  "outcome_reason": "...",
  "tools_used": [{{"name": "...", "count": N}}],
  "patterns_observed": [{{"name": "...", "frequency": N, "context": "..."}}],
  "skill_match": [{{"existing_skill": "...", "was_used": true|false, "should_have_been_used": true|false}}],
  "skill_candidates": [
    {{
      "name": "...",
      "description": "...",
      "trigger_pattern": "...",
      "procedure_outline": "...",
      "update_target": "name-of-existing-active-skill-or-null",
      "update_reason": "what this update brings vs the active version (or null)",
      "roi_score": {{
        "frequency_in_session": N,
        "reusability_outside_context": "high" | "medium" | "low",
        "operator_cost_saved": "high" | "medium" | "low",
        "specificity_warning": "...",
        "verdict": "promote" | "review" | "archive",
        "verdict_reason": "..."
      }}
    }}
  ],
  "warnings": ["..."]
}}

Apply the SAME strict ROI scoring rules as in the single-shot prompt:
- promote: frequency >= 2 AND reusability != "low".
- archive: frequency == 1 OR reusability == "low".
- review: borderline cases.

Apply the SAME update_target rules:
- If the candidate IMPROVES an existing ACTIVE skill, set `update_target` to
  that name AND fill `update_reason` with the differential. Otherwise both are
  null."""  # noqa: S608


# ---------------------------------------------------------------------------
# Existing-skill discovery
# ---------------------------------------------------------------------------


@dataclass
class SkillDigest:
    """Compact view of an existing skill — name + description + trigger.

    Used to brief the LLM with enough context to decide whether a new
    candidate is genuinely novel OR an *improvement* of an existing
    active skill. Trigger and description are extracted best-effort from
    the YAML frontmatter ; missing fields become empty strings.
    """

    name: str
    description: str = ""
    trigger: str = ""


@dataclass
class ExistingSkills:
    """3 catégories de skills déjà connues par le projet, pour éviter doublons / re-propositions.

    - ``active``    : skills validés et actifs sous ``.claude/skills/`` (à ne pas dupliquer)
    - ``candidates``: skills en attente review sous ``.claude/skill-candidates/``
                     (suggérer une *extension* si même pattern observé, pas un doublon)
    - ``archived``  : skills explicitement rejetés sous ``.claude/skill-archive/``
                     (NE PAS re-proposer, le motif de rejet est documenté dans le SKILL.md archivé)
    - ``active_digests`` : same set as ``active``, with description + trigger inline so
                           the LLM can propose an ``update_target`` when a candidate
                           improves on an existing active skill.
    """

    active: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    active_digests: list[SkillDigest] = field(default_factory=list)


def _list_skill_dir(skills_dir: Path) -> list[str]:
    """Énumère les noms de skills d'un dossier ``.claude/<bucket>/`` (skills = dossiers avec SKILL.md ou .md plats)."""
    if not skills_dir.exists():
        return []
    out: list[str] = []
    for entry in sorted(skills_dir.iterdir()):
        if entry.is_dir() and (entry / "SKILL.md").exists():
            out.append(entry.name)
        elif entry.is_file() and entry.suffix == ".md":
            out.append(entry.stem)
    return out


def _extract_skill_digest(name: str, skill_md_path: Path) -> SkillDigest:
    """Best-effort scrape of (description, trigger) from a SKILL.md frontmatter.

    Used to feed the LLM enough context to decide whether to set
    ``update_target`` on a candidate. Failures are silent — a missing
    digest just yields empty strings, the LLM will skip update_target on
    that target.
    """
    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except OSError:
        return SkillDigest(name=name)
    description = ""
    trigger = ""
    # Frontmatter block.
    if text.startswith("---"):
        close = text.find("\n---", 3)
        if close > 0:
            fm = text[3:close]
            for raw in fm.splitlines():
                line = raw.strip()
                if line.startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip("'\"")
                elif line.startswith("trigger:") or line.startswith("trigger_pattern:"):
                    trigger = line.split(":", 1)[1].strip().strip("'\"")
    # Block-scalar description : if description starts as ">-" / ">" the
    # actual content is on the next indented line(s). We grab the first
    # paragraph for brevity (LLM context budget).
    if description in (">", ">-", "|", "|-", ""):
        lines = text.splitlines()
        in_block = False
        collected: list[str] = []
        for ln in lines:
            stripped = ln.strip()
            if not in_block:
                if stripped.startswith("description:"):
                    in_block = True
                continue
            if not ln.startswith((" ", "\t")):
                break
            if stripped:
                collected.append(stripped)
        if collected:
            description = " ".join(collected)
    return SkillDigest(name=name, description=description[:300], trigger=trigger[:200])


def discover_existing_skills(worktree: Path | None) -> ExistingSkills:
    """Inventaire 3-catégories des skills du projet pour briefer le LLM (anti-doublon, anti-re-proposition rejetée)."""
    if not worktree:
        return ExistingSkills()
    active_names = _list_skill_dir(worktree / ".claude" / "skills")
    digests: list[SkillDigest] = []
    for name in active_names:
        # Skill directory form takes priority over flat .md.
        dir_md = worktree / ".claude" / "skills" / name / "SKILL.md"
        flat_md = worktree / ".claude" / "skills" / f"{name}.md"
        if dir_md.exists():
            digests.append(_extract_skill_digest(name, dir_md))
        elif flat_md.exists():
            digests.append(_extract_skill_digest(name, flat_md))
        else:
            digests.append(SkillDigest(name=name))
    return ExistingSkills(
        active=active_names,
        candidates=_list_skill_dir(worktree / ".claude" / "skill-candidates"),
        archived=_list_skill_dir(worktree / ".claude" / "skill-archive"),
        active_digests=digests,
    )


def _format_existing_skills_block(existing: ExistingSkills) -> str:
    """Construit le bloc texte (3 sections claires) inséré dans le prompt LLM.

    For the ACTIVE section, each line carries the description + trigger
    so the LLM can decide whether the new candidate is genuinely novel
    or an *improvement* of an existing active skill (in which case it
    must populate ``update_target``).
    """

    def _section(label: str, items: list[str]) -> list[str]:
        lines = [label]
        if items:
            lines.extend(f"- {s}" for s in items)
        else:
            lines.append("- (none)")
        return lines

    def _digest_section(label: str, digests: list[SkillDigest]) -> list[str]:
        lines = [label]
        if digests:
            for d in digests:
                bits = [f"- {d.name}"]
                if d.description:
                    bits.append(f"  description: {d.description}")
                if d.trigger:
                    bits.append(f"  trigger: {d.trigger}")
                lines.extend(bits)
        else:
            lines.append("- (none)")
        return lines

    parts: list[str] = []
    parts.extend(
        _digest_section(
            "ACTIVE (already validated — if a candidate IMPROVES one of these, "
            "set its `update_target` to the matching name ; otherwise do NOT propose "
            "a duplicate):",
            existing.active_digests or [SkillDigest(name=n) for n in existing.active],
        )
    )
    parts.append("")
    parts.extend(
        _section(
            "PENDING REVIEW (candidates from previous postmortems, do NOT propose "
            "duplicates; if the same pattern recurred, suggest enhancement of the "
            "existing candidate instead):",
            existing.candidates,
        )
    )
    parts.append("")
    parts.extend(
        _section(
            "ARCHIVED (explicitly rejected by operator — DO NOT propose again; "
            "the rejection reason is documented in the archived SKILL.md):",
            existing.archived,
        )
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------


def render_report_md(
    parsed: ParseResult,
    analysis: dict[str, Any],
    *,
    redaction_summary: str,
    chunks_count: int,
    tokens_in: int,
    tokens_out: int,
    model: str,
) -> str:
    stats = parsed.stats
    short_id = stats.session_id[:8]
    lines = [
        f"# Post-mortem — session {short_id}",
        "",
        f"- **Session UUID**: `{stats.session_id}`",
        f"- **Source**: `{stats.session_path}`",
        f"- **Worktree**: `{stats.worktree_root}`",
        f"- **First event**: {stats.first_timestamp}",
        f"- **Last event**:  {stats.last_timestamp}",
        f"- **Raw lines**: {stats.total_lines}",
        f"- **Redaction**: {redaction_summary}",
        f"- **Chunks**: {chunks_count}",
        f"- **Model**: {model}  ·  **Tokens in/out**: {tokens_in} / {tokens_out}",
        "",
        "## Summary",
        "",
        str(analysis.get("summary", "(no summary)")),
        "",
        "## Outcome",
        "",
        f"- **Outcomes**: {', '.join(analysis.get('outcomes', []) or ['(unknown)'])}",
        f"- **Reason**: {analysis.get('outcome_reason', '(unknown)')}",
        "",
        "## Tools used",
        "",
    ]
    for tu in analysis.get("tools_used", []) or []:
        lines.append(f"- `{tu.get('name')}` × {tu.get('count')}")
    if not analysis.get("tools_used"):
        lines.append("(none reported by LLM — see raw stats below)")

    lines += ["", "## Patterns observed", ""]
    for p in analysis.get("patterns_observed", []) or []:
        lines.append(f"- **{p.get('name')}** (×{p.get('frequency')}): {p.get('context')}")
    if not analysis.get("patterns_observed"):
        lines.append("(none)")

    lines += ["", "## Skill match", ""]
    for sm in analysis.get("skill_match", []) or []:
        lines.append(
            f"- `{sm.get('existing_skill')}` — used: {sm.get('was_used')}, "
            f"should have been: {sm.get('should_have_been_used')}"
        )
    if not analysis.get("skill_match"):
        lines.append("(no existing skills referenced)")

    lines += ["", "## Skill candidates", ""]
    candidates = analysis.get("skill_candidates", []) or []
    if not candidates:
        lines.append("(none)")
    # Tri visuel : promote en haut, review au milieu, archive en bas
    _verdict_order = {"promote": 0, "review": 1, "archive": 2}
    candidates_sorted = sorted(
        candidates,
        key=lambda c: _verdict_order.get((c.get("roi_score") or {}).get("verdict", "review"), 1),
    )
    for c in candidates_sorted:
        roi = c.get("roi_score") or {}
        verdict = roi.get("verdict", "review")
        verdict_emoji = {"promote": "🟢", "review": "🟡", "archive": "🔴"}.get(verdict, "🟡")
        lines.append(f"### {verdict_emoji} `{c.get('name')}` — verdict: **{verdict}**")
        lines.append("")
        lines.append(f"- **Description**: {c.get('description')}")
        lines.append(f"- **Trigger**: {c.get('trigger_pattern')}")
        lines.append(f"- **Procedure outline**: {c.get('procedure_outline')}")
        if roi:
            lines.append("- **ROI score**:")
            lines.append(f"  - frequency_in_session: {roi.get('frequency_in_session', '?')}")
            lines.append(f"  - reusability_outside_context: {roi.get('reusability_outside_context', '?')}")
            lines.append(f"  - operator_cost_saved: {roi.get('operator_cost_saved', '?')}")
            if roi.get("specificity_warning"):
                lines.append(f"  - specificity_warning: {roi.get('specificity_warning')}")
            lines.append(f"  - **verdict_reason**: {roi.get('verdict_reason', '?')}")
        lines.append("")

    lines += ["## Warnings", ""]
    for w in analysis.get("warnings", []) or []:
        lines.append(f"- {w}")
    if not analysis.get("warnings"):
        lines.append("(none)")

    lines += [
        "",
        "## Raw stats (from parser)",
        "",
        "```json",
        json.dumps(stats.to_dict(), indent=2, sort_keys=True, default=str),
        "```",
        "",
    ]
    return "\n".join(lines)


_KEBAB_RE = re.compile(r"[^a-z0-9-]+")


def _safe_kebab(name: str) -> str:
    s = name.strip().lower().replace(" ", "-").replace("_", "-")
    s = _KEBAB_RE.sub("", s)
    return s.strip("-") or "unnamed-candidate"


def render_candidate_skill_md(candidate: dict[str, Any], source_session: str) -> str:
    name = _safe_kebab(candidate.get("name", "unnamed-candidate"))
    desc = candidate.get("description", "")
    trigger = candidate.get("trigger_pattern", "")
    outline = candidate.get("procedure_outline", "")
    roi = candidate.get("roi_score") or {}
    verdict = roi.get("verdict", "review")
    verdict_reason = roi.get("verdict_reason", "")
    frequency = roi.get("frequency_in_session", "?")
    reusability = roi.get("reusability_outside_context", "?")
    cost_saved = roi.get("operator_cost_saved", "?")
    specificity_warning = roi.get("specificity_warning") or ""

    # Update-via-candidate fields — defensive against missing / null /
    # non-string values (Codex sometimes returns Python None, sometimes "null").
    raw_update_target = candidate.get("update_target")
    update_target = ""
    if isinstance(raw_update_target, str) and raw_update_target.strip().lower() not in (
        "",
        "null",
        "none",
    ):
        update_target = _safe_kebab(raw_update_target)
    raw_update_reason = candidate.get("update_reason")
    update_reason = (
        str(raw_update_reason).strip()
        if isinstance(raw_update_reason, str) and raw_update_reason.strip().lower() not in ("", "null", "none")
        else ""
    )

    # Section ROI à intégrer en haut pour visibilité immédiate
    roi_block = f"""## ROI Score (auto-évalué par le postmortem)

- **Verdict** : `{verdict}` — {verdict_reason}
- **Frequency in session** : {frequency}
- **Reusability outside context** : {reusability}
- **Operator cost saved** : {cost_saved}
"""
    if specificity_warning:
        roi_block += f"- **Specificity warning** : {specificity_warning}\n"
    if update_target:
        roi_block += f"- **Update target** : `{update_target}`"
        if update_reason:
            roi_block += f" — {update_reason}"
        roi_block += "\n"
    roi_block += f"""
Workflow :
- `promote` → `mv .claude/skill-candidates/{name} .claude/skills/{name}`
- `archive` → `mv .claude/skill-candidates/{name} .claude/skill-archive/{name}` (puis ajouter `## Why archived`)
- `review`  → éditer ce fichier, puis re-décider
"""

    # Frontmatter — always include update_target keys (null when absent) so
    # downstream parsers don't have to guess.
    update_target_line = f"update_target: {update_target}" if update_target else "update_target: null"
    update_reason_line = f'update_reason: "{update_reason}"' if update_reason else "update_reason: null"

    return f"""---
name: {name}
description: "{desc}"
status: candidate
roi_verdict: {verdict}
source_session: {source_session}
generated_by: claude-code-postmortem
{update_target_line}
{update_reason_line}
---

# {name} (candidate)

> Auto-generated skill proposal. Review the ROI verdict below, then promote /
> archive / refine accordingly.

{roi_block}
## When to Use

{trigger}

## When NOT to Use

(to be filled in during review)

## Procedure

{outline}
"""


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def append_audit(entry: dict[str, Any]) -> None:
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ---------------------------------------------------------------------------
# Orchestration — one session
# ---------------------------------------------------------------------------


@dataclass
class RunOutcome:
    session_path: Path
    skipped: bool = False
    skip_reason: str | None = None
    report_path: Path | None = None
    candidates_written: list[Path] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    redactions: int = 0
    chunks: int = 0
    stats: TranscriptStats | None = None
    error: str | None = None


def analyze_session(
    session_path: Path,
    *,
    client: CodexClient | None,
    use_llm: bool,
    dry_run: bool,
    verbose: bool = False,
) -> RunOutcome:
    """End-to-end analysis of one session JSONL."""
    outcome = RunOutcome(session_path=session_path)
    try:
        parsed = parse_session(session_path)
    except Exception as e:
        outcome.error = f"parse failed: {e}"
        return outcome

    outcome.stats = parsed.stats

    if not parsed.transcript.strip():
        outcome.skipped = True
        outcome.skip_reason = "empty transcript after filtering"
        return outcome

    redaction = redact(parsed.transcript)
    outcome.redactions = redaction.total

    if verbose:
        print(
            f"  parsed: {parsed.stats.total_lines} lines, "
            f"transcript ~{estimate_tokens(redaction.text)} tokens, "
            f"{redaction.summary()}",
            file=sys.stderr,
        )

    existing = discover_existing_skills(parsed.stats.worktree_root)
    transcript_tokens = estimate_tokens(redaction.text)

    if transcript_tokens > MAX_TOKENS_SINGLE_SHOT:
        chunks = chunk_transcript(redaction.text)
    else:
        chunks = [redaction.text]
    outcome.chunks = len(chunks)

    if verbose:
        print(f"  chunks: {len(chunks)}", file=sys.stderr)

    # --- LLM call(s) -----------------------------------------------------
    if not use_llm:
        llm_resp = stub_response_for_dry_run(redaction.text)
        analysis = llm_resp.parse_json()
        outcome.tokens_in = llm_resp.tokens_in
        outcome.tokens_out = llm_resp.tokens_out
        model = llm_resp.model
    else:
        if client is None or not client.is_available():
            outcome.error = "Codex client unavailable (no OPENAI_API_KEY)"
            return outcome
        try:
            if len(chunks) == 1:
                llm_resp = client.call(
                    SYSTEM_PROMPT,
                    _build_user_prompt(chunks[0], parsed.stats, existing),
                )
                analysis = llm_resp.parse_json()
                outcome.tokens_in = llm_resp.tokens_in
                outcome.tokens_out = llm_resp.tokens_out
                model = llm_resp.model
            else:
                summaries: list[str] = []
                tin = 0
                tout = 0
                for i, ch in enumerate(chunks):
                    r = client.call(
                        CHUNK_SUMMARY_SYSTEM,
                        _build_chunk_summary_prompt(ch, i, len(chunks)),
                        response_format_json=False,
                    )
                    summaries.append(r.content)
                    tin += r.tokens_in
                    tout += r.tokens_out
                final = client.call(
                    SYSTEM_PROMPT,
                    _build_synthesis_prompt(summaries, parsed.stats, existing),
                )
                analysis = final.parse_json()
                tin += final.tokens_in
                tout += final.tokens_out
                outcome.tokens_in = tin
                outcome.tokens_out = tout
                model = final.model
        except Exception as e:
            outcome.error = f"LLM call failed: {e}"
            return outcome

    # --- Render & write --------------------------------------------------
    report_md = render_report_md(
        parsed,
        analysis,
        redaction_summary=redaction.summary(),
        chunks_count=len(chunks),
        tokens_in=outcome.tokens_in,
        tokens_out=outcome.tokens_out,
        model=model,
    )

    date = (parsed.stats.last_timestamp or _today_iso())[:10]
    short_id = parsed.stats.session_id[:8]
    report_path = POSTMORTEM_OUTPUT_DIR / f"{date}-{short_id}.md"

    if not dry_run:
        POSTMORTEM_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_md, encoding="utf-8")
        outcome.report_path = report_path

        # Skill candidates
        worktree = parsed.stats.worktree_root
        if worktree:
            # Path HORS de `.claude/skills/` (au niveau au-dessus). Sinon Claude Code
            # scanne récursivement `.claude/skills/**/SKILL.md` et charge les candidates
            # comme skills actifs AVANT promotion humaine. Convention : `.claude/skills/`
            # = skills validés uniquement ; `.claude/skill-candidates/` = en attente review.
            # Promotion = `mv .claude/skill-candidates/X .claude/skills/X` (simple).
            candidates_dir = worktree / ".claude" / "skill-candidates"
            for cand in analysis.get("skill_candidates", []) or []:
                if not isinstance(cand, dict):
                    continue
                skill_name = _safe_kebab(cand.get("name", "unnamed-candidate"))
                target_dir = candidates_dir / skill_name
                target_dir.mkdir(parents=True, exist_ok=True)
                skill_md = target_dir / "SKILL.md"
                skill_md.write_text(
                    render_candidate_skill_md(cand, parsed.stats.session_id),
                    encoding="utf-8",
                )
                outcome.candidates_written.append(skill_md)
    else:
        outcome.report_path = report_path  # would-be path, not written

    return outcome


def _today_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_candidates(
    state: PostmortemState,
    *,
    force: bool = False,
) -> list[Path]:
    """Return JSONL paths that look ready for analysis."""
    if not CLAUDE_PROJECTS_DIR.exists():
        return []
    out: list[Path] = []
    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            if force or state.needs_analysis(jsonl):
                out.append(jsonl)
    # Stable order — oldest mtime first.
    out.sort(key=lambda p: p.stat().st_mtime)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-code-postmortem",
        description="Analyze Claude Code session JSONL transcripts to produce post-mortems and skill candidates.",
    )
    p.add_argument("--session", type=Path, help="Analyze a single JSONL path")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write the post-mortem report or candidate skills",
    )
    p.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip Codex API call — use a stub response",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Ignore state cache and re-analyze",
    )
    p.add_argument(
        "--max-sessions",
        type=int,
        default=10,
        help="Cap on sessions to analyze in this run",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(list(argv) if argv is not None else None)

    state = PostmortemState.load()
    use_llm = not args.no_llm
    client = CodexClient() if use_llm else None

    if args.session:
        candidates = [args.session]
    else:
        candidates = discover_candidates(state, force=args.force)

    if not candidates:
        print("[postmortem] no candidate sessions — nothing to do.")
        return 0

    if not args.session:
        # Respect rate limit. If --session is explicit, the operator overrides.
        if not state.should_run_now(len(candidates)):
            print(f"[postmortem] rate-limit reached ({state.analyses_this_hour()} this hour) or empty queue — exiting.")
            return 0

    processed = 0
    for path in candidates[: args.max_sessions]:
        if not args.session and not state.should_run_now(1):
            print("[postmortem] hourly cap reached mid-run — stopping.")
            break
        if args.verbose:
            print(f"[postmortem] analyzing {path}", file=sys.stderr)

        outcome = analyze_session(
            path,
            client=client,
            use_llm=use_llm,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )

        # Audit + state
        audit_entry = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "session_path": str(outcome.session_path),
            "session_id": outcome.stats.session_id if outcome.stats else None,
            "skipped": outcome.skipped,
            "skip_reason": outcome.skip_reason,
            "error": outcome.error,
            "tokens_in": outcome.tokens_in,
            "tokens_out": outcome.tokens_out,
            "redactions": outcome.redactions,
            "chunks": outcome.chunks,
            "report_path": str(outcome.report_path) if outcome.report_path else None,
            "candidates_count": len(outcome.candidates_written),
            "dry_run": args.dry_run,
            "no_llm": args.no_llm,
        }
        append_audit(audit_entry)

        if not outcome.error and not args.dry_run and not args.session:
            state.mark_analyzed(path)
            if use_llm:
                state.increment_analysis_count()

        # Human-friendly summary line
        if outcome.error:
            print(f"[postmortem] ERROR {path.name}: {outcome.error}")
        elif outcome.skipped:
            print(f"[postmortem] SKIP  {path.name}: {outcome.skip_reason}")
        else:
            print(
                f"[postmortem] OK    {path.name}  "
                f"tokens={outcome.tokens_in}+{outcome.tokens_out}  "
                f"redactions={outcome.redactions}  chunks={outcome.chunks}  "
                f"candidates={len(outcome.candidates_written)}  "
                f"report={outcome.report_path}"
            )
        processed += 1

    state.save()
    print(f"[postmortem] processed {processed} session(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
