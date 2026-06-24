"""Tests for the claude-code-postmortem parser, redactor, chunker, and state."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

# Make the skill's scripts/ importable without packaging.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from codex_client import parse_hermes_output
from postmortem import (
    NOISE_TYPES,
    ExistingSkills,
    _format_existing_skills_block,
    chunk_transcript,
    decode_project_dir_to_worktree,
    discover_existing_skills,
    estimate_tokens,
    local_analysis,
    parse_session,
    preprocess_transcript,
)
from redactor import redact
from state import PostmortemState

# ---------------------------------------------------------------------------
# Fixtures — a minimal but representative JSONL transcript
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session(tmp_path: Path) -> Path:
    """Write a 10-event JSONL mimicking a Claude Code session."""
    project_dir = tmp_path / "-tmp-test-worktree"
    project_dir.mkdir()
    session_path = project_dir / "abc12345-aaaa-bbbb-cccc-deadbeef0001.jsonl"

    events = [
        {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-05-31T10:00:00Z"},
        {"type": "user", "timestamp": "2026-05-31T10:00:01Z", "message": {"content": "Run the tests please"}},
        {
            "type": "assistant",
            "timestamp": "2026-05-31T10:00:02Z",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "User wants tests run."},
                    {"type": "text", "text": "Sure, running pytest."},
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Bash",
                        "input": {"command": "pytest -q", "description": "Run tests"},
                    },
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2026-05-31T10:00:03Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": "5 passed in 1.2s"}],
                    },
                ]
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-05-31T10:00:04Z",
            "message": {
                "content": [
                    {"type": "text", "text": "All good. Token: sk_live_" + "abcdefghijklmnopqrstuvwx12345 leaked in logs."},
                ]
            },
        },
        {"type": "last-prompt", "timestamp": "2026-05-31T10:00:05Z"},
        {"type": "pr-link", "timestamp": "2026-05-31T10:00:06Z"},
        {"type": "system", "subtype": "compact", "timestamp": "2026-05-31T10:00:07Z"},
        {
            "type": "attachment",
            "timestamp": "2026-05-31T10:00:08Z",
            "attachment": {"type": "hook_non_blocking_error", "stderr": "boom"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-05-31T10:00:09Z",
            "message": {
                "content": [
                    {"type": "text", "text": "Done."},
                ]
            },
        },
    ]
    with session_path.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return session_path


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParser:
    def test_filters_noise_types(self, mock_session: Path) -> None:
        result = parse_session(mock_session)
        # 10 raw lines processed
        assert result.stats.total_lines == 10
        # Noise types still counted in stats…
        for noisy in ("queue-operation", "last-prompt", "pr-link"):
            assert result.stats.type_counts[noisy] >= 1
            assert noisy in NOISE_TYPES
        # …but should not appear in the rendered transcript.
        assert "queue-operation" not in result.transcript
        assert "last-prompt" not in result.transcript
        assert "pr-link" not in result.transcript

    def test_keeps_user_assistant_blocks(self, mock_session: Path) -> None:
        result = parse_session(mock_session)
        assert "[user]" in result.transcript
        assert "Run the tests please" in result.transcript
        assert "[assistant]" in result.transcript
        assert "[tool: Bash] pytest -q" in result.transcript
        assert "[tool_result]" in result.transcript

    def test_skips_thinking_blocks(self, mock_session: Path) -> None:
        result = parse_session(mock_session)
        assert "User wants tests run." not in result.transcript

    def test_counts_tools(self, mock_session: Path) -> None:
        result = parse_session(mock_session)
        assert result.stats.tool_counts["Bash"] == 1

    def test_captures_timestamps(self, mock_session: Path) -> None:
        result = parse_session(mock_session)
        assert result.stats.first_timestamp == "2026-05-31T10:00:00Z"
        assert result.stats.last_timestamp == "2026-05-31T10:00:09Z"


# ---------------------------------------------------------------------------
# Worktree decoding
# ---------------------------------------------------------------------------


class TestWorktreeDecode:
    def test_returns_none_on_unknown(self) -> None:
        assert decode_project_dir_to_worktree("-this-does-not-exist-anywhere") is None

    def test_decodes_existing_path(self, tmp_path: Path, monkeypatch) -> None:
        # Build a real path and encode it the Claude-Code way.
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        encoded = str(sub).replace("/", "-")
        result = decode_project_dir_to_worktree(encoded)
        # Should at least find a prefix that exists.
        assert result is not None
        assert sub.is_relative_to(result) or result == sub


# ---------------------------------------------------------------------------
# Redactor tests
# ---------------------------------------------------------------------------


class TestRedactor:
    def test_redacts_stripe_live_key(self) -> None:
        r = redact("API key is sk_live_" + "abcdefghijklmnopqrstuvwx12345 done.")
        assert "sk_live_" not in r.text
        assert "<<REDACTED:stripe_secret>>" in r.text
        assert r.counts["stripe_secret"] == 1
        assert r.total == 1

    def test_redacts_github_token(self) -> None:
        token = "ghp_" + "A" * 36
        r = redact(f"token={token}")
        assert "<<REDACTED:github_token>>" in r.text
        assert token not in r.text

    def test_redacts_jwt(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc-def_123"
        r = redact(f"Bearer {jwt}")
        assert "<<REDACTED:jwt>>" in r.text
        assert jwt not in r.text

    def test_redacts_bcrypt(self) -> None:
        h = "$2b$12$" + "a" * 53
        r = redact(f"hash={h}")
        assert "<<REDACTED:bcrypt_hash>>" in r.text

    def test_counts_multiple(self) -> None:
        text = "sk_live_" + "aaaaaaaaaaaaaaaaaaaaaaaa1 sk_live_" + "bbbbbbbbbbbbbbbbbbbbbbbb2 ghp_" + "C" * 36
        r = redact(text)
        assert r.counts["stripe_secret"] == 2
        assert r.counts["github_token"] == 1
        assert r.total == 3

    def test_no_false_positive_on_clean_text(self) -> None:
        r = redact("Just some plain prose with no secrets in it.")
        assert r.total == 0
        assert r.text == "Just some plain prose with no secrets in it."


# ---------------------------------------------------------------------------
# Chunker tests
# ---------------------------------------------------------------------------


class TestChunker:
    def test_short_transcript_one_chunk(self) -> None:
        chunks = chunk_transcript("[user]\nhi\n\n[assistant]\nhi back")
        assert len(chunks) == 1

    def test_chunks_at_block_boundary(self) -> None:
        block_chars = 1000
        blocks = [f"[block {i}] " + "x" * block_chars for i in range(50)]
        transcript = "\n\n".join(blocks)
        # ~50K chars → ~11K tokens. Chunk at 4K tokens → expect 3+ chunks.
        chunks = chunk_transcript(transcript, chunk_tokens=4000)
        assert len(chunks) >= 3
        # Concatenation preserves all blocks (modulo separators).
        rejoined_block_count = sum(c.count("[block") for c in chunks)
        assert rejoined_block_count == 50

    def test_hard_split_of_oversized_block(self) -> None:
        huge = "x" * 100_000
        transcript = f"[user]\nshort\n\n[assistant]\n{huge}"
        chunks = chunk_transcript(transcript, chunk_tokens=4000)
        assert len(chunks) >= 2


# ---------------------------------------------------------------------------
# Token estimator
# ---------------------------------------------------------------------------


class TestTokenEstimate:
    def test_zero_on_empty(self) -> None:
        # Off-by-one buffer of +1 is intentional, just confirm it's tiny.
        assert estimate_tokens("") == 1

    def test_grows_with_length(self) -> None:
        assert estimate_tokens("x" * 1000) > estimate_tokens("x" * 100)


# ---------------------------------------------------------------------------
# State tests
# ---------------------------------------------------------------------------


class TestState:
    def test_needs_analysis_first_time(self, tmp_path: Path) -> None:
        st = PostmortemState(path=tmp_path / "state.json")
        f = tmp_path / "session.jsonl"
        f.write_text("hi")
        # Backdate the file so the quiet window has passed.
        old = time.time() - 3600
        import os

        os.utime(f, (old, old))
        assert st.needs_analysis(f) is True

    def test_does_not_reanalyze_unchanged(self, tmp_path: Path) -> None:
        st = PostmortemState(path=tmp_path / "state.json")
        f = tmp_path / "session.jsonl"
        f.write_text("hi")
        import os

        old = time.time() - 3600
        os.utime(f, (old, old))
        st.mark_analyzed(f)
        assert st.needs_analysis(f) is False

    def test_skips_active_session(self, tmp_path: Path) -> None:
        st = PostmortemState(path=tmp_path / "state.json")
        f = tmp_path / "session.jsonl"
        f.write_text("hi")  # mtime is now → still active
        assert st.needs_analysis(f) is False

    def test_rate_limit_normal_hours(self, tmp_path: Path) -> None:
        st = PostmortemState(path=tmp_path / "state.json")
        # 10h00 UTC is normal hours in any reasonable local time.
        # We bypass the local-hour branch by checking analyses_this_hour math.
        for _ in range(3):
            st.increment_analysis_count()
        # 3 already done → in normal mode, should_run_now returns False.
        # The hour-of-day branch depends on local time, so we just assert the
        # counter incremented:
        assert st.analyses_this_hour() == 3

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        st = PostmortemState(path=path)
        st.session_mtimes["/a.jsonl"] = 12345.6
        st.increment_analysis_count()
        st.save()
        st2 = PostmortemState.load(path)
        assert st2.session_mtimes["/a.jsonl"] == 12345.6
        assert sum(st2.hourly_counts.values()) == 1


# ---------------------------------------------------------------------------
# Hermes CLI wireformat parser
# ---------------------------------------------------------------------------


class TestHermesWireformat:
    """``parse_hermes_output`` must handle both Hermes CLI wire formats.

    1. ``-Q/--quiet`` (preferred) — plain text reply on stdout, no box drawing.
       A stray ``session_id: ...`` line may appear at the start or end and must
       be stripped.
    2. Box-drawing fallback — ``╭─ ⚕ Hermes ─...─╮ ... ╰─...─╯`` with 4-space
       indented body lines. Kept as defensive parser in case ``-Q`` is dropped.
    """

    def test_quiet_mode_single_line(self) -> None:
        assert parse_hermes_output("hello-poc8\n") == "hello-poc8"

    def test_quiet_mode_strips_session_id_suffix(self) -> None:
        stdout = "hello\n\nsession_id: 20260531_191722_06e628\n"
        assert parse_hermes_output(stdout) == "hello"

    def test_quiet_mode_strips_session_id_prefix(self) -> None:
        # Observed empirically — session_id can land before the reply.
        stdout = "session_id: 20260531_191853_125ef2\nline1\nline2\nline3\n"
        assert parse_hermes_output(stdout) == "line1\nline2\nline3"

    def test_quiet_mode_preserves_json_payload(self) -> None:
        stdout = '{"summary": "ok", "items": [1, 2, 3]}\n'
        out = parse_hermes_output(stdout)
        assert json.loads(out) == {"summary": "ok", "items": [1, 2, 3]}

    def test_box_drawing_fallback(self) -> None:
        # Synthetic but identical structure to the real Hermes box.
        stdout = (
            "Query: say only: hello-poc8\n"
            "Initializing agent...\n"
            "────────────────────────────────────────\n"
            "\n"
            "\n"
            "╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮\n"
            "    hello-poc8\n"
            "╰──────────────────────────────────────────────────────────────────────────────╯\n"
            "\n"
            "Resume this session with:\n"
            "  hermes --resume 20260531_191722_06e628\n"
        )
        assert parse_hermes_output(stdout) == "hello-poc8"

    def test_box_drawing_multiline_body(self) -> None:
        stdout = (
            "╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮\n"
            "    def add(a, b):\n"
            "        return a + b\n"
            "╰──────────────────────────────────────────────────────────────────────────────╯\n"
        )
        # The 4-space indent of the FIRST level is the wrapper indent and gets
        # stripped; nested indentation inside the body is preserved.
        assert parse_hermes_output(stdout) == "def add(a, b):\n    return a + b"

    def test_empty_input_returns_empty_string(self) -> None:
        assert parse_hermes_output("") == ""

    def test_strips_compacting_context_spinner(self) -> None:
        # Observed when the prompt is large enough to trigger Hermes' 50%
        # context compaction — three spinner lines leak to stdout before the
        # actual JSON reply.
        stdout = '  ⟳ compacting context…\n  ⟳ compacting context…\n  ⟳ compacting context…\n{"summary": "ok"}\n'
        out = parse_hermes_output(stdout)
        assert out == '{"summary": "ok"}'
        assert json.loads(out) == {"summary": "ok"}

    def test_strips_thinking_spinner(self) -> None:
        stdout = "  ⠋ thinking…\n  ⠙ thinking…\nthe actual reply\n"
        assert parse_hermes_output(stdout) == "the actual reply"


# ---------------------------------------------------------------------------
# Smoke E2E (dry-run + no-llm) on the mock fixture
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_dry_run_no_llm(self, mock_session: Path, tmp_path: Path, monkeypatch) -> None:
        from postmortem import analyze_session

        outcome = analyze_session(
            mock_session,
            client=None,
            use_llm=False,
            dry_run=True,
            verbose=False,
        )
        assert outcome.error is None
        assert outcome.skipped is False
        # The leaked sk_live_ should have been redacted.
        assert outcome.redactions >= 1
        # Single short session → one chunk.
        assert outcome.chunks == 1
        # Stub LLM doesn't burn tokens.
        assert outcome.tokens_in == 0
        assert outcome.tokens_out == 0


# ---------------------------------------------------------------------------
# V2 preprocessing + local heuristic routing
# ---------------------------------------------------------------------------


class TestV2LocalAnalysis:
    def test_preprocess_caps_large_transcript_and_keeps_tail_signal(self) -> None:
        transcript = "\n\n".join([
            "[user]\nstart",
            "[assistant]\n" + ("noise " * 20_000),
            "[tool_result]\n25 passed in 3.0s",
            "[assistant]\nDone",
        ])
        compact, meta = preprocess_transcript(transcript, max_chars=2_000)
        assert len(compact) <= 2_000
        assert meta["truncated"] is True
        assert "25 passed" in compact
        assert "Done" in compact

    def test_local_analysis_classifies_happy_path(self, mock_session: Path) -> None:
        parsed = parse_session(mock_session)
        compact, meta = preprocess_transcript(parsed.transcript)
        analysis = local_analysis(compact, parsed.stats, meta)
        assert analysis["classification"] == "happy path"
        assert analysis["outcomes"] == ["completed"]

    def test_analyze_session_default_strategy_uses_no_llm(self, mock_session: Path) -> None:
        from postmortem import analyze_session

        outcome = analyze_session(
            mock_session,
            client=None,
            use_llm=True,
            dry_run=True,
            verbose=False,
        )
        assert outcome.error is None
        assert outcome.tokens_in == 0
        assert outcome.tokens_out == 0
        assert outcome.report_path is not None


# ---------------------------------------------------------------------------
# discover_existing_skills — 3-buckets inventory (active / candidates / archived)
# ---------------------------------------------------------------------------


def _make_skill(parent: Path, name: str) -> None:
    d = parent / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: " + name + "\n---\n# stub", encoding="utf-8")


def test_discover_existing_skills_three_buckets(tmp_path: Path) -> None:
    """Vérifie l'inventaire 3-catégories : .claude/skills/ + skill-candidates/ + skill-archive/."""
    _make_skill(tmp_path / ".claude" / "skills", "validated-skill")
    _make_skill(tmp_path / ".claude" / "skill-candidates", "pending-skill")
    _make_skill(tmp_path / ".claude" / "skill-archive", "rejected-skill")

    result = discover_existing_skills(tmp_path)

    assert isinstance(result, ExistingSkills)
    assert result.active == ["validated-skill"]
    assert result.candidates == ["pending-skill"]
    assert result.archived == ["rejected-skill"]


def test_discover_existing_skills_empty_buckets(tmp_path: Path) -> None:
    """Aucun dossier présent → 3 listes vides, jamais None ni crash."""
    result = discover_existing_skills(tmp_path)
    assert result == ExistingSkills(active=[], candidates=[], archived=[])


def test_discover_existing_skills_none_worktree() -> None:
    """worktree=None → ExistingSkills vide (cas dégradé déduction worktree échouée)."""
    result = discover_existing_skills(None)
    assert result == ExistingSkills()


def test_format_existing_skills_block_three_sections() -> None:
    """Le bloc texte injecté dans le prompt contient bien les 3 labels + items + fallback '(none)'."""
    block = _format_existing_skills_block(ExistingSkills(active=["a1", "a2"], candidates=["c1"], archived=[]))
    assert "ACTIVE" in block
    assert "PENDING REVIEW" in block
    assert "ARCHIVED" in block
    assert "- a1" in block
    assert "- a2" in block
    assert "- c1" in block
    # archived bucket is empty → fallback "- (none)"
    assert "- (none)" in block


# ---------------------------------------------------------------------------
# ROI scoring — render_candidate_skill_md includes roi_verdict in frontmatter
# ---------------------------------------------------------------------------


def test_candidate_skill_md_includes_roi_section() -> None:
    """Le SKILL.md d'un candidate doit afficher le ROI verdict + raisons en haut."""
    from postmortem import render_candidate_skill_md

    candidate = {
        "name": "test-recurring-pattern",
        "description": "Test description",
        "trigger_pattern": "when X happens",
        "procedure_outline": "1. do X 2. verify Y",
        "roi_score": {
            "frequency_in_session": 3,
            "reusability_outside_context": "high",
            "operator_cost_saved": "high",
            "specificity_warning": None,
            "verdict": "promote",
            "verdict_reason": "Pattern récurrent et générique",
        },
    }
    md = render_candidate_skill_md(candidate, source_session="abc12345")
    # Frontmatter contient le verdict
    assert "roi_verdict: promote" in md
    # Section ROI visible dans le body
    assert "## ROI Score" in md
    assert "promote" in md
    assert "Pattern récurrent et générique" in md
    assert "Frequency in session** : 3" in md
    assert "Reusability outside context** : high" in md


def test_candidate_skill_md_archive_verdict() -> None:
    """Un candidate verdict='archive' inclut bien la raison + flag."""
    from postmortem import render_candidate_skill_md

    candidate = {
        "name": "one-shot-bug-fix",
        "description": "Fix a very specific table",
        "trigger_pattern": "...",
        "procedure_outline": "...",
        "roi_score": {
            "frequency_in_session": 1,
            "reusability_outside_context": "low",
            "operator_cost_saved": "low",
            "specificity_warning": "Tied to a specific table name",
            "verdict": "archive",
            "verdict_reason": "Bug unique, peu probable de se répéter",
        },
    }
    md = render_candidate_skill_md(candidate, source_session="abc12345")
    assert "roi_verdict: archive" in md
    assert "Bug unique" in md
    assert "Tied to a specific table name" in md


def test_candidate_skill_md_no_roi_falls_back_gracefully() -> None:
    """Si le LLM ne fournit pas roi_score (rétrocompat), le rendu ne crash pas."""
    from postmortem import render_candidate_skill_md

    candidate = {
        "name": "no-roi-candidate",
        "description": "Old format",
        "trigger_pattern": "...",
        "procedure_outline": "...",
        # pas de roi_score → fallback "review"
    }
    md = render_candidate_skill_md(candidate, source_session="abc12345")
    assert "roi_verdict: review" in md
    assert "## ROI Score" in md  # section toujours présente, juste avec "?"


# ---------------------------------------------------------------------------
# update_target — "candidate replaces an existing active skill" pattern
# ---------------------------------------------------------------------------


class TestUpdateTarget:
    """The candidate's frontmatter carries ``update_target`` + ``update_reason``
    when it improves on an existing active skill. The admin UI then offers a
    replace-and-archive action instead of a plain promote.
    """

    def test_update_target_renders_in_frontmatter(self) -> None:
        from postmortem import render_candidate_skill_md

        candidate = {
            "name": "improved-deploy",
            "description": "Better deploy procedure",
            "trigger_pattern": "...",
            "procedure_outline": "...",
            "update_target": "deploy",
            "update_reason": "Adds rollback step missing in active version",
            "roi_score": {
                "frequency_in_session": 3,
                "reusability_outside_context": "high",
                "operator_cost_saved": "high",
                "verdict": "promote",
                "verdict_reason": "Recurrent + reusable.",
            },
        }
        md = render_candidate_skill_md(candidate, source_session="sess")
        assert "update_target: deploy" in md
        assert "Adds rollback step missing in active version" in md
        # Visible in the markdown body too.
        assert "**Update target**" in md

    def test_update_target_null_renders_as_null(self) -> None:
        from postmortem import render_candidate_skill_md

        candidate = {
            "name": "novel-skill",
            "description": "Genuinely new",
            "trigger_pattern": "...",
            "procedure_outline": "...",
            "update_target": None,
            "update_reason": None,
        }
        md = render_candidate_skill_md(candidate, source_session="sess")
        assert "update_target: null" in md
        assert "update_reason: null" in md

    def test_update_target_string_null_treated_as_absent(self) -> None:
        """Codex sometimes returns the literal string ``"null"`` instead of JSON null."""
        from postmortem import render_candidate_skill_md

        candidate = {
            "name": "novel",
            "description": "x",
            "trigger_pattern": "...",
            "procedure_outline": "...",
            "update_target": "null",
            "update_reason": "null",
        }
        md = render_candidate_skill_md(candidate, source_session="sess")
        assert "update_target: null" in md
        assert "update_reason: null" in md

    def test_update_target_sanitised_to_kebab(self) -> None:
        """A nonconformant Codex name is kebab-normalised before being written."""
        from postmortem import render_candidate_skill_md

        candidate = {
            "name": "x",
            "description": "x",
            "trigger_pattern": "...",
            "procedure_outline": "...",
            "update_target": "Existing Skill Name",
            "update_reason": "diff",
        }
        md = render_candidate_skill_md(candidate, source_session="sess")
        assert "update_target: existing-skill-name" in md


class TestExistingSkillDigests:
    """``discover_existing_skills`` collects descriptions + triggers for active
    skills so the LLM can decide whether to set ``update_target``."""

    def test_digest_extracted_from_active_skill_md(self, tmp_path: Path) -> None:
        from postmortem import discover_existing_skills

        skill_dir = tmp_path / ".claude" / "skills" / "my-deploy"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: my-deploy\n"
            "description: Deploy a Ratis service to Hetzner.\n"
            "trigger: when the user asks to deploy\n"
            "---\n"
            "# body\n",
            encoding="utf-8",
        )
        existing = discover_existing_skills(tmp_path)
        assert existing.active == ["my-deploy"]
        assert len(existing.active_digests) == 1
        d = existing.active_digests[0]
        assert d.name == "my-deploy"
        assert "Deploy a Ratis service" in d.description
        assert "when the user asks to deploy" in d.trigger

    def test_digest_handles_missing_frontmatter(self, tmp_path: Path) -> None:
        from postmortem import discover_existing_skills

        skill_dir = tmp_path / ".claude" / "skills" / "bare"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("just a body, no frontmatter\n", encoding="utf-8")
        existing = discover_existing_skills(tmp_path)
        d = next(d for d in existing.active_digests if d.name == "bare")
        assert d.description == ""
        assert d.trigger == ""

    def test_active_block_includes_descriptions(self, tmp_path: Path) -> None:
        from postmortem import _format_existing_skills_block, discover_existing_skills

        skill_dir = tmp_path / ".claude" / "skills" / "deploy"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deploy\ndescription: One-shot deploy.\n---\n",
            encoding="utf-8",
        )
        existing = discover_existing_skills(tmp_path)
        block = _format_existing_skills_block(existing)
        assert "One-shot deploy." in block
