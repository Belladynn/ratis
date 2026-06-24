"""Tests for ``admin_ui.skills_admin_service`` — POC 8 Hermes review UI.

Covers the three buckets enumeration + the three mutating actions
(promote / archive / drop). The service writes directly to the
filesystem under a ``.claude/`` layout — every test injects a
``tmp_path`` as ``repo_root`` so no real repo is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from admin_ui import skills_admin_service as svc
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write_skill(repo_root: Path, bucket: str, name: str, frontmatter: dict[str, str], body: str = "") -> Path:
    """Write a fake SKILL.md under ``<repo>/.claude/<bucket>/<name>/SKILL.md``.

    ``bucket`` is the path segment matching the bucket convention :
    ``skills`` / ``skill-candidates`` / ``skill-archive``.
    """
    skill_dir = repo_root / ".claude" / bucket / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm_lines = ["---"]
    for k, v in frontmatter.items():
        fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    content = "\n".join(fm_lines) + "\n" + body
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(content, encoding="utf-8")
    return skill_md


def _seed_three_buckets(repo_root: Path) -> None:
    """Populate one skill per bucket for the list-all test."""
    _write_skill(
        repo_root,
        "skills",
        "active-one",
        {"name": "active-one", "description": "active skill"},
        body="# Body of active",
    )
    _write_skill(
        repo_root,
        "skill-candidates",
        "candidate-one",
        {
            "name": "candidate-one",
            "description": "candidate skill",
            "roi_verdict": "promote",
            "source_session": "abc-123",
            "generated_by": "claude-code-postmortem",
        },
        body="# Body candidate",
    )
    _write_skill(
        repo_root,
        "skill-archive",
        "archived-one",
        {"name": "archived-one", "description": "archived skill"},
    )


def _read_audit(repo_root: Path) -> list[dict]:
    log = repo_root / ".claude" / "skill-review-audit.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------
class TestFrontmatterParser:
    def test_parses_simple_key_value(self):
        fm, body = svc._parse_frontmatter("---\nname: foo\ndescription: bar\n---\nbody text\n")
        assert fm == {"name": "foo", "description": "bar"}
        assert body.strip() == "body text"

    def test_folds_block_scalar(self):
        text = "---\nname: foo\ndescription: >-\n  one two\n  three\n---\nbody\n"
        fm, _body = svc._parse_frontmatter(text)
        assert fm["name"] == "foo"
        assert fm["description"] == "one two three"

    def test_strips_wrapping_quotes(self):
        fm, _ = svc._parse_frontmatter("---\nname: \"foo\"\ndesc: 'bar'\n---\n")
        assert fm["name"] == "foo"
        assert fm["desc"] == "bar"

    def test_missing_frontmatter_returns_empty(self):
        fm, body = svc._parse_frontmatter("just a body\nno fences\n")
        assert fm == {}
        assert body == "just a body\nno fences\n"

    def test_unclosed_frontmatter_returns_empty(self):
        fm, body = svc._parse_frontmatter("---\nname: foo\nno close\n")
        assert fm == {}
        assert body.startswith("---")


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------
class TestListSkills:
    def test_list_skills_all_three_buckets(self, tmp_path):
        _seed_three_buckets(tmp_path)
        skills = svc.list_skills_all(repo_root=tmp_path)
        assert len(skills) == 3
        by_bucket = {s.bucket: s for s in skills}
        assert set(by_bucket) == {"active", "candidate", "archived"}
        assert by_bucket["candidate"].roi_verdict == "promote"
        assert by_bucket["candidate"].source_session == "abc-123"
        assert by_bucket["candidate"].generated_by == "claude-code-postmortem"
        # Candidate first (most actionable) in the ordering.
        assert skills[0].bucket == "candidate"

    def test_list_skips_dirs_without_skill_md(self, tmp_path):
        # Empty dir under candidates → must not crash, must not appear.
        (tmp_path / ".claude" / "skill-candidates" / "empty").mkdir(parents=True)
        skills = svc.list_skills_all(repo_root=tmp_path)
        assert skills == []

    def test_list_missing_buckets_returns_empty(self, tmp_path):
        # No .claude/ at all → empty listing, no crash.
        assert svc.list_skills_all(repo_root=tmp_path) == []

    def test_unknown_roi_verdict_falls_back_to_none(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "weird",
            {"name": "weird", "description": "x", "roi_verdict": "garbage"},
        )
        skills = svc.list_skills_all(repo_root=tmp_path)
        assert skills[0].roi_verdict is None

    def test_malformed_frontmatter_falls_back_to_unknown(self, tmp_path):
        skill_dir = tmp_path / ".claude" / "skill-candidates" / "broken"
        skill_dir.mkdir(parents=True)
        # No fences at all.
        (skill_dir / "SKILL.md").write_text("just a body", encoding="utf-8")
        skills = svc.list_skills_all(repo_root=tmp_path)
        assert len(skills) == 1
        assert skills[0].name == "broken"
        assert skills[0].description == "unknown"

    def test_body_excerpt_truncated(self, tmp_path):
        long_body = "abc " * 200
        _write_skill(
            tmp_path,
            "skill-candidates",
            "long",
            {"name": "long", "description": "x"},
            body=long_body,
        )
        skills = svc.list_skills_all(repo_root=tmp_path)
        assert len(skills[0].body_excerpt) <= 200


# ---------------------------------------------------------------------------
# Promote
# ---------------------------------------------------------------------------
class TestPromote:
    def test_promote_moves_file(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "to-promote",
            {"name": "to-promote", "description": "x"},
        )
        result = svc.promote_skill(name="to-promote", operator="alice", repo_root=tmp_path)
        assert result.bucket == "active"
        assert (tmp_path / ".claude/skills/to-promote/SKILL.md").is_file()
        assert not (tmp_path / ".claude/skill-candidates/to-promote").exists()

    def test_promote_unknown_raises_404(self, tmp_path):
        with pytest.raises(HTTPException) as ei:
            svc.promote_skill(name="missing", operator="alice", repo_root=tmp_path)
        assert ei.value.status_code == 404
        assert ei.value.detail == "skill_not_found"

    def test_promote_active_skill_raises_404(self, tmp_path):
        """A skill already active is not a valid promote target."""
        _write_skill(
            tmp_path,
            "skills",
            "already-active",
            {"name": "already-active", "description": "x"},
        )
        with pytest.raises(HTTPException) as ei:
            svc.promote_skill(name="already-active", operator="alice", repo_root=tmp_path)
        assert ei.value.status_code == 404

    def test_promote_writes_audit_entry(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "audit-me",
            {"name": "audit-me", "description": "x"},
        )
        svc.promote_skill(name="audit-me", operator="bob", repo_root=tmp_path)
        entries = _read_audit(tmp_path)
        assert len(entries) == 1
        e = entries[0]
        assert e["operator"] == "bob"
        assert e["action"] == "promote"
        assert e["skill"] == "audit-me"
        assert e["details"] == {"from": "candidate", "to": "active"}
        assert "ts" in e


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------
class TestArchive:
    def test_archive_from_candidate(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "c1",
            {"name": "c1", "description": "x"},
        )
        result = svc.archive_skill(name="c1", operator="alice", repo_root=tmp_path)
        assert result.bucket == "archived"
        assert (tmp_path / ".claude/skill-archive/c1/SKILL.md").is_file()

    def test_archive_from_active(self, tmp_path):
        _write_skill(
            tmp_path,
            "skills",
            "a1",
            {"name": "a1", "description": "x"},
        )
        result = svc.archive_skill(name="a1", operator="alice", repo_root=tmp_path)
        assert result.bucket == "archived"
        assert (tmp_path / ".claude/skill-archive/a1/SKILL.md").is_file()
        assert not (tmp_path / ".claude/skills/a1").exists()

    def test_archive_unknown_raises_404(self, tmp_path):
        with pytest.raises(HTTPException) as ei:
            svc.archive_skill(name="ghost", operator="alice", repo_root=tmp_path)
        assert ei.value.status_code == 404

    def test_archive_already_archived_raises_404(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-archive",
            "old",
            {"name": "old", "description": "x"},
        )
        with pytest.raises(HTTPException) as ei:
            svc.archive_skill(name="old", operator="alice", repo_root=tmp_path)
        assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# Drop
# ---------------------------------------------------------------------------
class TestDrop:
    def test_drop_candidate(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "dropme",
            {"name": "dropme", "description": "x"},
        )
        svc.drop_skill(name="dropme", operator="alice", repo_root=tmp_path)
        assert not (tmp_path / ".claude/skill-candidates/dropme").exists()

    def test_drop_active_raises_400(self, tmp_path):
        _write_skill(
            tmp_path,
            "skills",
            "active",
            {"name": "active", "description": "x"},
        )
        with pytest.raises(HTTPException) as ei:
            svc.drop_skill(name="active", operator="alice", repo_root=tmp_path)
        assert ei.value.status_code == 400
        assert ei.value.detail == "drop_only_candidates"

    def test_drop_archived_raises_400(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-archive",
            "old",
            {"name": "old", "description": "x"},
        )
        with pytest.raises(HTTPException) as ei:
            svc.drop_skill(name="old", operator="alice", repo_root=tmp_path)
        assert ei.value.status_code == 400

    def test_drop_unknown_raises_404(self, tmp_path):
        with pytest.raises(HTTPException) as ei:
            svc.drop_skill(name="ghost", operator="alice", repo_root=tmp_path)
        assert ei.value.status_code == 404

    def test_drop_writes_audit_entry(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "logged",
            {"name": "logged", "description": "x"},
        )
        svc.drop_skill(name="logged", operator="carol", repo_root=tmp_path)
        entries = _read_audit(tmp_path)
        assert len(entries) == 1
        assert entries[0]["action"] == "drop"
        assert entries[0]["operator"] == "carol"
        assert entries[0]["details"]["from"] == "candidate"
        assert entries[0]["details"]["to"] is None


# ---------------------------------------------------------------------------
# Audit log behavior
# ---------------------------------------------------------------------------
class TestAuditLog:
    def test_audit_appends_not_overwrites(self, tmp_path):
        _write_skill(tmp_path, "skill-candidates", "a", {"name": "a", "description": "x"})
        _write_skill(tmp_path, "skill-candidates", "b", {"name": "b", "description": "x"})
        svc.promote_skill(name="a", operator="op1", repo_root=tmp_path)
        svc.archive_skill(name="b", operator="op2", repo_root=tmp_path)
        entries = _read_audit(tmp_path)
        assert len(entries) == 2
        assert {e["action"] for e in entries} == {"promote", "archive"}

    def test_audit_entry_has_iso_timestamp(self, tmp_path):
        _write_skill(tmp_path, "skill-candidates", "x", {"name": "x", "description": "x"})
        svc.promote_skill(name="x", operator="op", repo_root=tmp_path)
        entry = _read_audit(tmp_path)[0]
        # ISO 8601 with timezone (Python isoformat → 'YYYY-MM-DDTHH:MM:SS.fff+00:00')
        assert "T" in entry["ts"]
        assert entry["ts"].endswith("+00:00") or entry["ts"].endswith("Z")


# ---------------------------------------------------------------------------
# Repo root resolution
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# POC 8 Layer 2-3 review surface — reviewed_by_claude / security / verdict_v2
# ---------------------------------------------------------------------------
class TestReviewedByClaudeSurface:
    def test_unreviewed_candidate_has_reviewed_false(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "fresh",
            {"name": "fresh", "description": "x"},
        )
        skills = svc.list_skills_all(repo_root=tmp_path)
        s = skills[0]
        assert s.reviewed_by_claude is False
        assert s.security_assessment is None
        assert s.verdict_v2 is None
        assert s.quality_score is None
        assert s.injection_patterns_detected is None

    def test_reviewed_candidate_surfaces_fields(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "vetted",
            {
                "name": "vetted",
                "description": "x",
                "reviewed_by_claude": "true",
                "security_assessment": "safe",
                "verdict_v2": "promote",
                "quality_score": "82",
                "injection_patterns_detected": "0",
            },
        )
        skills = svc.list_skills_all(repo_root=tmp_path)
        s = skills[0]
        assert s.reviewed_by_claude is True
        assert s.security_assessment == "safe"
        assert s.verdict_v2 == "promote"
        assert s.quality_score == 82
        assert s.injection_patterns_detected == 0

    def test_unknown_security_assessment_falls_back_to_none(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "weird",
            {
                "name": "weird",
                "description": "x",
                "reviewed_by_claude": "true",
                "security_assessment": "garbage",
                "verdict_v2": "also-garbage",
            },
        )
        skills = svc.list_skills_all(repo_root=tmp_path)
        s = skills[0]
        assert s.reviewed_by_claude is True
        assert s.security_assessment is None
        assert s.verdict_v2 is None

    def test_invalid_quality_score_is_none(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "bad-num",
            {"name": "bad-num", "description": "x", "quality_score": "abc"},
        )
        s = svc.list_skills_all(repo_root=tmp_path)[0]
        assert s.quality_score is None

    def test_to_dict_includes_new_fields(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "full",
            {
                "name": "full",
                "description": "x",
                "reviewed_by_claude": "true",
                "security_assessment": "suspect",
                "verdict_v2": "hold-for-improvement",
            },
        )
        s = svc.list_skills_all(repo_root=tmp_path)[0]
        d = s.to_dict()
        assert d["reviewed_by_claude"] is True
        assert d["security_assessment"] == "suspect"
        assert d["verdict_v2"] == "hold-for-improvement"


# ---------------------------------------------------------------------------
# update_target — candidate replaces an existing active skill
# ---------------------------------------------------------------------------
class TestUpdateTargetField:
    def test_update_target_parsed(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "improved",
            {
                "name": "improved",
                "description": "x",
                "update_target": "deploy",
                "update_reason": "Adds rollback step",
            },
        )
        s = svc.list_skills_all(repo_root=tmp_path)[0]
        assert s.update_target == "deploy"
        assert s.update_reason == "Adds rollback step"

    def test_update_target_string_null_treated_as_none(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "novel",
            {"name": "novel", "description": "x", "update_target": "null"},
        )
        s = svc.list_skills_all(repo_root=tmp_path)[0]
        assert s.update_target is None

    def test_update_target_empty_treated_as_none(self, tmp_path):
        _write_skill(
            tmp_path,
            "skill-candidates",
            "empty-tgt",
            {"name": "empty-tgt", "description": "x", "update_target": ""},
        )
        s = svc.list_skills_all(repo_root=tmp_path)[0]
        assert s.update_target is None


class TestPromoteUpdateFlow:
    """Promotion of a candidate carrying ``update_target`` performs a
    replace-and-archive : the active target is renamed under skill-archive/
    with a timestamp suffix, then the candidate slides in to take its place."""

    def test_promote_with_update_target_archives_active(self, tmp_path):
        # Active skill that will be superseded.
        _write_skill(
            tmp_path,
            "skills",
            "deploy",
            {"name": "deploy", "description": "old version"},
            body="# old body",
        )
        # Candidate proposing to update it.
        _write_skill(
            tmp_path,
            "skill-candidates",
            "deploy-v2",
            {
                "name": "deploy-v2",
                "description": "new version",
                "update_target": "deploy",
                "update_reason": "Adds rollback",
            },
            body="# new body",
        )
        result = svc.promote_skill(name="deploy-v2", operator="alice", repo_root=tmp_path)
        # Candidate now lives under the target name in active.
        assert result.bucket == "active"
        assert (tmp_path / ".claude/skills/deploy/SKILL.md").is_file()
        active_content = (tmp_path / ".claude/skills/deploy/SKILL.md").read_text()
        assert "new body" in active_content
        # Old version moved to archive under a timestamped name.
        archive_root = tmp_path / ".claude/skill-archive"
        archived = list(archive_root.glob("deploy-superseded-*"))
        assert len(archived) == 1, f"expected 1 archived dir, got {list(archive_root.iterdir())}"
        assert (archived[0] / "SKILL.md").is_file()
        # Candidate dir is gone.
        assert not (tmp_path / ".claude/skill-candidates/deploy-v2").exists()

    def test_promote_update_audit_action_is_promote_update(self, tmp_path):
        _write_skill(tmp_path, "skills", "deploy", {"name": "deploy", "description": "old"})
        _write_skill(
            tmp_path,
            "skill-candidates",
            "deploy-v2",
            {"name": "deploy-v2", "description": "new", "update_target": "deploy"},
        )
        svc.promote_skill(name="deploy-v2", operator="alice", repo_root=tmp_path)
        entries = _read_audit(tmp_path)
        assert len(entries) == 1
        assert entries[0]["action"] == "promote-update"
        assert entries[0]["details"]["update_target"] == "deploy"
        assert entries[0]["details"]["superseded_archive"].startswith("deploy-superseded-")

    def test_promote_with_dangling_update_target_falls_back_with_warning(self, tmp_path):
        """If update_target points to a non-existent active skill, the candidate
        is promoted as-add and a warning is captured in the audit."""
        _write_skill(
            tmp_path,
            "skill-candidates",
            "new-name",
            {"name": "new-name", "description": "x", "update_target": "ghost-skill"},
        )
        result = svc.promote_skill(name="new-name", operator="alice", repo_root=tmp_path)
        # Candidate ends up at its own name (not the dangling target).
        assert result.bucket == "active"
        assert (tmp_path / ".claude/skills/new-name/SKILL.md").is_file()
        # Audit captures the warning.
        entry = _read_audit(tmp_path)[0]
        assert entry["details"]["warning"] == "update_target_not_found"
        assert entry["details"]["declared_update_target"] == "ghost-skill"

    def test_promote_without_update_target_unchanged(self, tmp_path):
        """A regular candidate (no update_target) keeps the standard flow + 'promote' audit."""
        _write_skill(
            tmp_path,
            "skill-candidates",
            "plain",
            {"name": "plain", "description": "x"},
        )
        result = svc.promote_skill(name="plain", operator="alice", repo_root=tmp_path)
        assert result.bucket == "active"
        entry = _read_audit(tmp_path)[0]
        assert entry["action"] == "promote"
        assert "update_target" not in entry["details"]


class TestRepoRootResolution:
    def test_explicit_override_wins(self, tmp_path):
        assert svc.resolve_repo_root(override=tmp_path) == tmp_path

    def test_env_var_used_when_no_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        assert svc.resolve_repo_root() == tmp_path
