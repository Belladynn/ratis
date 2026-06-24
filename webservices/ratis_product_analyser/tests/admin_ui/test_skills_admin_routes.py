"""Tests for the ``/admin/ui/skills`` HTML routes.

Uses ``raw_client`` (real cookie-session dep) — the routes are auth-gated
and the operator handle drives the audit-log entries. Each test sets
``RATIS_REPO_ROOT`` via monkeypatch to a ``tmp_path`` so the filesystem
mutations stay hermetic.
"""

from __future__ import annotations

import json
from pathlib import Path

_ADMIN_KEY = "test-admin-key-padded-to-32-chars-min"


def _login(raw_client, operator: str = "tester") -> None:
    raw_client.post(
        "/admin/ui/login",
        data={"api_key": _ADMIN_KEY, "operator": operator},
        follow_redirects=False,
    )


def _seed_candidate(
    repo_root: Path,
    name: str,
    *,
    description: str = "test skill",
    reviewed: bool = True,
    extra_fm: dict[str, str] | None = None,
) -> None:
    """Seed a candidate SKILL.md.

    ``reviewed=True`` (default) marks the candidate as already
    triple-validated so it appears under the default
    ``/admin/ui/skills?reviewed=true`` filter without test rewrites.
    """
    skill_dir = repo_root / ".claude" / "skill-candidates" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        f"name: {name}",
        f"description: {description}",
        "roi_verdict: promote",
        "source_session: sess-1",
        "generated_by: claude-code-postmortem",
    ]
    if reviewed:
        fm_lines += [
            "reviewed_by_claude: true",
            "security_assessment: safe",
            "verdict_v2: promote",
            "quality_score: 80",
            "injection_patterns_detected: 0",
        ]
    if extra_fm:
        for k, v in extra_fm.items():
            fm_lines.append(f"{k}: {v}")
    fm = "---\n" + "\n".join(fm_lines) + "\n---\nbody"
    (skill_dir / "SKILL.md").write_text(fm, encoding="utf-8")


def _read_audit(repo_root: Path) -> list[dict]:
    log = repo_root / ".claude" / "skill-review-audit.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestSkillsListPage:
    def test_without_cookie_redirects_to_login(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        r = raw_client.get("/admin/ui/skills", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].endswith("/admin/ui/login")

    def test_lists_candidate_skills(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        _seed_candidate(tmp_path, "alpha")
        _seed_candidate(tmp_path, "beta")
        _login(raw_client)
        r = raw_client.get("/admin/ui/skills", follow_redirects=False)
        assert r.status_code == 200
        assert "alpha" in r.text
        assert "beta" in r.text
        assert "Skills review" in r.text

    def test_filter_by_bucket(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        _seed_candidate(tmp_path, "cand-x")
        # Active skill — must NOT appear under bucket=candidate.
        active_dir = tmp_path / ".claude/skills/active-y"
        active_dir.mkdir(parents=True)
        (active_dir / "SKILL.md").write_text("---\nname: active-y\ndescription: y\n---\nbody", encoding="utf-8")
        _login(raw_client)
        r = raw_client.get("/admin/ui/skills?bucket=candidate", follow_redirects=False)
        assert r.status_code == 200
        assert "cand-x" in r.text
        assert "active-y" not in r.text

    def test_search_filters_by_substring(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        _seed_candidate(tmp_path, "github-helper", description="github CLI wrapper")
        _seed_candidate(tmp_path, "deploy-thing", description="deploys stuff")
        _login(raw_client)
        r = raw_client.get("/admin/ui/skills?search=github", follow_redirects=False)
        assert "github-helper" in r.text
        assert "deploy-thing" not in r.text


class TestSkillsMutations:
    def test_promote_moves_skill(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        _seed_candidate(tmp_path, "promoteme")
        _login(raw_client, operator="op1")
        r = raw_client.post("/admin/ui/skills/promoteme/promote", follow_redirects=False)
        assert r.status_code == 303
        assert "/admin/ui/skills" in r.headers["location"]
        assert (tmp_path / ".claude/skills/promoteme/SKILL.md").is_file()
        # Audit entry written with the operator handle.
        entries = _read_audit(tmp_path)
        assert any(e["action"] == "promote" and e["operator"] == "op1" for e in entries)

    def test_archive_moves_skill(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        _seed_candidate(tmp_path, "archme")
        _login(raw_client)
        r = raw_client.post("/admin/ui/skills/archme/archive", follow_redirects=False)
        assert r.status_code == 303
        assert (tmp_path / ".claude/skill-archive/archme/SKILL.md").is_file()

    def test_drop_removes_candidate(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        _seed_candidate(tmp_path, "drophere")
        _login(raw_client)
        r = raw_client.post("/admin/ui/skills/drophere/drop", follow_redirects=False)
        assert r.status_code == 303
        assert not (tmp_path / ".claude/skill-candidates/drophere").exists()

    def test_promote_unknown_redirects_with_error(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        _login(raw_client)
        r = raw_client.post("/admin/ui/skills/ghost/promote", follow_redirects=False)
        assert r.status_code == 303
        # error query param carries the failure reason.
        assert "error=" in r.headers["location"]


# ---------------------------------------------------------------------------
# POC 8 — `reviewed_by_claude` filter + unreviewed banner
# ---------------------------------------------------------------------------
class TestReviewedFilter:
    def test_default_filter_hides_unreviewed_candidates(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        _seed_candidate(tmp_path, "reviewed-alpha", reviewed=True)
        _seed_candidate(tmp_path, "pending-beta", reviewed=False)
        _login(raw_client)
        r = raw_client.get("/admin/ui/skills", follow_redirects=False)
        assert r.status_code == 200
        assert "reviewed-alpha" in r.text
        # Default ?reviewed=true hides candidates without the flag.
        # The data-testid is the canonical anchor (the literal name might
        # appear in the unreviewed-banner counter, but the table row will not).
        assert 'data-testid="skill-row-pending-beta"' not in r.text

    def test_reviewed_false_shows_only_pending(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        _seed_candidate(tmp_path, "reviewed-alpha", reviewed=True)
        _seed_candidate(tmp_path, "pending-beta", reviewed=False)
        _login(raw_client)
        r = raw_client.get("/admin/ui/skills?reviewed=false", follow_redirects=False)
        assert r.status_code == 200
        assert 'data-testid="skill-row-pending-beta"' in r.text
        assert 'data-testid="skill-row-reviewed-alpha"' not in r.text

    def test_reviewed_any_shows_all_candidates(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        _seed_candidate(tmp_path, "reviewed-alpha", reviewed=True)
        _seed_candidate(tmp_path, "pending-beta", reviewed=False)
        _login(raw_client)
        r = raw_client.get("/admin/ui/skills?reviewed=any", follow_redirects=False)
        assert 'data-testid="skill-row-pending-beta"' in r.text
        assert 'data-testid="skill-row-reviewed-alpha"' in r.text

    def test_unreviewed_banner_count_independent_of_filter(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        # 2 pending + 1 reviewed
        _seed_candidate(tmp_path, "p1", reviewed=False)
        _seed_candidate(tmp_path, "p2", reviewed=False)
        _seed_candidate(tmp_path, "r1", reviewed=True)
        _login(raw_client)
        r = raw_client.get("/admin/ui/skills", follow_redirects=False)  # default filter
        # Banner mentions "2 candidate(s)" regardless of the active filter.
        assert 'data-testid="unreviewed-banner"' in r.text
        assert "<strong>2</strong>" in r.text

    def test_banner_absent_when_no_pending(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        _seed_candidate(tmp_path, "only-reviewed", reviewed=True)
        _login(raw_client)
        r = raw_client.get("/admin/ui/skills", follow_redirects=False)
        assert 'data-testid="unreviewed-banner"' not in r.text

    def test_active_skill_visible_regardless_of_reviewed_filter(self, raw_client, tmp_path, monkeypatch):
        """Active bucket skills are not subject to Layer 2-3 review."""
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        active_dir = tmp_path / ".claude/skills/active-x"
        active_dir.mkdir(parents=True)
        (active_dir / "SKILL.md").write_text("---\nname: active-x\ndescription: y\n---\nbody", encoding="utf-8")
        _login(raw_client)
        r = raw_client.get("/admin/ui/skills", follow_redirects=False)
        assert 'data-testid="skill-row-active-x"' in r.text


# ---------------------------------------------------------------------------
# POC 8 — update_target promote-replace flow via the HTTP route
# ---------------------------------------------------------------------------
class TestPromoteUpdateRoute:
    def test_promote_with_update_target_archives_old_via_route(self, raw_client, tmp_path, monkeypatch):
        monkeypatch.setenv("RATIS_REPO_ROOT", str(tmp_path))
        # Pre-existing active skill.
        active_dir = tmp_path / ".claude/skills/deploy"
        active_dir.mkdir(parents=True)
        (active_dir / "SKILL.md").write_text("---\nname: deploy\ndescription: old\n---\nold body", encoding="utf-8")
        # Candidate proposing to update it.
        _seed_candidate(
            tmp_path,
            "deploy-v2",
            reviewed=True,
            extra_fm={"update_target": "deploy", "update_reason": "Adds rollback"},
        )
        _login(raw_client, operator="alice")
        r = raw_client.post("/admin/ui/skills/deploy-v2/promote", follow_redirects=False)
        assert r.status_code == 303
        # New active version under the target name.
        assert (tmp_path / ".claude/skills/deploy/SKILL.md").is_file()
        new_content = (tmp_path / ".claude/skills/deploy/SKILL.md").read_text()
        assert "old body" not in new_content
        # Old version archived with timestamp suffix.
        archived = list((tmp_path / ".claude/skill-archive").glob("deploy-superseded-*"))
        assert len(archived) == 1
        # Audit captures the update.
        entries = _read_audit(tmp_path)
        assert any(e["action"] == "promote-update" for e in entries)
