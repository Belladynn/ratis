"""Skills review service — admin_ui § /admin/ui/skills.

Lists and mutates the three skill buckets produced by the Hermes
``claude-code-postmortem`` skill (POC 8) :

- ``.claude/skills/<name>/SKILL.md``           — active (versioned)
- ``.claude/skill-candidates/<name>/SKILL.md`` — pending review (gitignored)
- ``.claude/skill-archive/<name>/SKILL.md``    — rejected (versioned)

Each ``SKILL.md`` carries a YAML frontmatter delimited by ``---``
fences. The frontmatter is parsed defensively : malformed files still
surface in the listing under ``bucket`` with whatever metadata could
be extracted (``description`` / ``status`` may fall back to
``"unknown"``).

Three mutating actions are exposed :

- :func:`promote_skill`  : candidate → active
- :func:`archive_skill`  : candidate | active → archive
- :func:`drop_skill`     : candidate → /dev/null (destructive, candidates only)

Every action appends a JSONL audit entry in
``.claude/skill-review-audit.jsonl`` (gitignored, see ``.gitignore``).
The audit log is append-only — no deletes, no rewrites — so the
operator trail is preserved even after a skill is dropped.

The repository root is resolved via :func:`resolve_repo_root` (env var
override ``RATIS_REPO_ROOT`` for test/CI flexibility, otherwise the
worktree containing this file). No hardcoded paths (R33).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

Bucket = Literal["active", "candidate", "archived"]
RoiVerdict = Literal["promote", "review", "archive"]

_BUCKETS: tuple[tuple[Bucket, str], ...] = (
    ("active", ".claude/skills"),
    ("candidate", ".claude/skill-candidates"),
    ("archived", ".claude/skill-archive"),
)

_AUDIT_LOG_REL = ".claude/skill-review-audit.jsonl"
_BODY_EXCERPT_LEN = 200


@dataclass(frozen=True)
class SkillMetadata:
    """One row in the skills-review listing.

    ``frontmatter_raw`` is the as-parsed dict (best-effort) — useful
    for the template to render extra fields without a service-layer
    change. ``body_excerpt`` is the first ``_BODY_EXCERPT_LEN`` chars
    of the markdown body (frontmatter stripped) so the operator gets
    a glance of the skill content in the table.

    The ``reviewed_by_claude`` / ``security_assessment`` / ``verdict_v2``
    fields are populated by the Hermes ``claude-skill-reviewer`` skill
    (POC 8 Layer 2-3) — see ``.claude/skills/claude-skill-reviewer/``.

    The ``update_target`` field carries the "candidate replaces an existing
    active skill" semantic : when set, :func:`promote_skill` performs a
    replace-and-archive instead of a plain move.
    """

    name: str
    description: str
    bucket: Bucket
    roi_verdict: RoiVerdict | None
    source_session: str | None
    generated_by: str | None
    path: Path
    frontmatter_raw: dict[str, Any]
    body_excerpt: str
    # POC 8 Layer 2-3 review surface
    reviewed_by_claude: bool = False
    security_assessment: str | None = None
    verdict_v2: str | None = None
    quality_score: int | None = None
    injection_patterns_detected: int | None = None
    # "update candidate" pattern — see § promote_skill
    update_target: str | None = None
    update_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Jinja-friendly dict — ``Path`` → str so templates do not
        accidentally call filesystem methods through the value."""
        d = asdict(self)
        d["path"] = str(self.path)
        return d


# ---------------------------------------------------------------------------
# Repo root resolution
# ---------------------------------------------------------------------------
def resolve_repo_root(override: Path | None = None) -> Path:
    """Find the repo root containing the three ``.claude/skill*`` dirs.

    Priority :

    1. Explicit ``override`` argument (tests inject ``tmp_path``).
    2. ``RATIS_REPO_ROOT`` env var (deploys with a non-default layout).
    3. Walk up from this file until a directory containing ``.claude``
       is found. Falls back to 5 parents (worktree → repo root) which
       matches the production layout.
    """
    if override is not None:
        return override
    env = os.environ.get("RATIS_REPO_ROOT")
    if env:
        return Path(env)
    # webservices/ratis_product_analyser/admin_ui/skills_admin_service.py
    # → 4 .parent hops reach the repo root.
    here = Path(__file__).resolve()
    for parent in (here.parents[i] for i in range(2, 7)):
        if (parent / ".claude").is_dir():
            return parent
    # Last-resort : the 4-hop default (worktree root for the SA dev layout).
    return here.parents[4]


# ---------------------------------------------------------------------------
# Frontmatter parser (defensive — pyyaml NOT a dep)
# ---------------------------------------------------------------------------
def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown blob into ``(frontmatter_dict, body)``.

    Recognises the standard fence ``---\\n...\\n---\\n`` at the top of
    the file. Anything that does not match returns
    ``({}, text)`` — the body is the full text in that case.

    Parses **only** scalar ``key: value`` pairs. Block scalars
    (``description: >-``) are folded into a single space-joined string
    by reading subsequent indented lines until the next top-level key
    or the closing fence. No nested mappings, no lists — the Hermes
    skill format is intentionally flat.

    Quotes (single or double) wrapping a value are stripped. Unknown
    constructs are kept as raw strings to keep the parser resilient.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    # Find the closing fence.
    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text
    body = "\n".join(lines[end_idx + 1 :]).lstrip("\n")
    fm_lines = lines[1:end_idx]
    fm: dict[str, Any] = {}
    i = 0
    while i < len(fm_lines):
        raw = fm_lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if ":" not in stripped:
            i += 1
            continue
        key, _, rest = stripped.partition(":")
        key = key.strip()
        value = rest.strip()
        # Block-scalar indicators (``>``, ``|``, optionally ``-``/``+``).
        # We fold everything indented below into a single space-joined string.
        if value in (">", ">-", ">+", "|", "|-", "|+"):
            collected: list[str] = []
            j = i + 1
            while j < len(fm_lines):
                cont = fm_lines[j]
                if not cont.strip():
                    j += 1
                    continue
                # Indented continuation : at least one leading whitespace char.
                if cont.startswith((" ", "\t")):
                    collected.append(cont.strip())
                    j += 1
                else:
                    break
            fm[key] = " ".join(collected)
            i = j
            continue
        # Strip wrapping quotes (single or double).
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        fm[key] = value
        i += 1
    return fm, body


def _read_skill_md(path: Path) -> tuple[dict[str, Any], str]:
    """Read a ``SKILL.md`` and parse its frontmatter. Logs + returns
    empty on read failure — a malformed skill never crashes the list."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("skills_admin: failed to read %s", path, exc_info=True)
        return {}, ""
    try:
        return _parse_frontmatter(text)
    except Exception:
        logger.warning("skills_admin: failed to parse frontmatter for %s", path, exc_info=True)
        return {}, text


def _parse_bool(v: Any) -> bool:
    """Coerce a YAML scalar (string ``"true"``, real bool, …) to ``bool``.

    The defensive frontmatter parser stores everything as ``str`` — this
    helper accepts the conventional truthy spellings.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1", "on")
    return False


def _parse_int_or_none(v: Any) -> int | None:
    """Parse a YAML scalar into ``int`` or return ``None`` on failure."""
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _build_metadata(name: str, bucket: Bucket, path: Path) -> SkillMetadata:
    """Compose one :class:`SkillMetadata` from a directory entry."""
    skill_md = path / "SKILL.md"
    fm, body = _read_skill_md(skill_md)
    fm_name = str(fm.get("name") or name)
    description = str(fm.get("description") or "unknown")
    roi_raw = fm.get("roi_verdict")
    roi_verdict: RoiVerdict | None = None
    if roi_raw in ("promote", "review", "archive"):
        roi_verdict = roi_raw
    source_session = fm.get("source_session")
    generated_by = fm.get("generated_by")
    excerpt = body.strip()[:_BODY_EXCERPT_LEN]
    # POC 8 Layer 2-3 review surface.
    reviewed_by_claude = _parse_bool(fm.get("reviewed_by_claude"))
    sec_raw = fm.get("security_assessment")
    security_assessment = str(sec_raw) if sec_raw in ("safe", "suspect", "malicious") else None
    v2_raw = fm.get("verdict_v2")
    verdict_v2 = str(v2_raw) if v2_raw in ("promote", "promote-as-update", "archive", "hold-for-improvement") else None
    quality_score = _parse_int_or_none(fm.get("quality_score"))
    injection_patterns_detected = _parse_int_or_none(fm.get("injection_patterns_detected"))
    update_target_raw = fm.get("update_target")
    update_target = (
        str(update_target_raw).strip()
        if update_target_raw and str(update_target_raw).strip().lower() not in ("none", "null", "")
        else None
    )
    update_reason_raw = fm.get("update_reason")
    update_reason = str(update_reason_raw) if update_reason_raw else None
    return SkillMetadata(
        name=fm_name,
        description=description,
        bucket=bucket,
        roi_verdict=roi_verdict,
        source_session=str(source_session) if source_session else None,
        generated_by=str(generated_by) if generated_by else None,
        path=skill_md,
        frontmatter_raw=fm,
        body_excerpt=excerpt,
        reviewed_by_claude=reviewed_by_claude,
        security_assessment=security_assessment,
        verdict_v2=verdict_v2,
        quality_score=quality_score,
        injection_patterns_detected=injection_patterns_detected,
        update_target=update_target,
        update_reason=update_reason,
    )


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------
def list_skills_all(repo_root: Path | None = None) -> list[SkillMetadata]:
    """Enumerate every skill across the three buckets.

    Skills are sorted bucket-first (candidate → active → archived,
    matching the operator's review priority) then name-ascending. A
    directory without a ``SKILL.md`` is silently skipped — the bucket
    dir itself may exist while empty.
    """
    root = resolve_repo_root(repo_root)
    out: list[SkillMetadata] = []
    for bucket, rel in _BUCKETS:
        bucket_dir = root / rel
        if not bucket_dir.is_dir():
            continue
        for entry in sorted(bucket_dir.iterdir()):
            if not entry.is_dir():
                continue
            if not (entry / "SKILL.md").is_file():
                continue
            out.append(_build_metadata(entry.name, bucket, entry))
    # Bucket order : candidate first (most actionable), then active, then archived.
    bucket_rank = {"candidate": 0, "active": 1, "archived": 2}
    out.sort(key=lambda m: (bucket_rank[m.bucket], m.name.lower()))
    return out


def _find_skill_dir(repo_root: Path, name: str, expected: tuple[Bucket, ...]) -> tuple[Bucket, Path]:
    """Locate a skill across the allowed source buckets.

    Returns ``(bucket, dir_path)``. Raises :class:`HTTPException` 404
    if the skill is in none of ``expected``.
    """
    for bucket, rel in _BUCKETS:
        if bucket not in expected:
            continue
        candidate = repo_root / rel / name
        if (candidate / "SKILL.md").is_file():
            return bucket, candidate
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="skill_not_found",
    )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def _append_audit(
    repo_root: Path,
    *,
    operator: str,
    action: str,
    skill_name: str,
    details: dict[str, Any],
) -> None:
    """Append one JSONL line to ``.claude/skill-review-audit.jsonl``.

    Best-effort : a failed audit append logs a warning but does not
    raise — the filesystem mutation already happened, refusing to
    surface the operator's action would be worse than a missing
    audit line. The log file is gitignored ; operators can rotate /
    inspect it offline.
    """
    log_path = repo_root / _AUDIT_LOG_REL
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "operator": operator,
        "action": action,
        "skill": skill_name,
        "details": details,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("skills_admin: failed to append audit entry %s", entry, exc_info=True)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------
def _move_skill_dir(src: Path, dst: Path) -> None:
    """``mv src → dst`` with parent-mkdir. Refuses to overwrite — if
    a name collision occurs the caller has to handle it (it indicates
    a duplicate skill, not a normal flow)."""
    if dst.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="skill_destination_exists",
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def promote_skill(name: str, operator: str, repo_root: Path | None = None) -> SkillMetadata:
    """Move a skill from ``skill-candidates/`` to ``skills/``.

    404 if the skill is not currently in the candidate bucket
    (promotion from active or archived is a no-op by design).

    **Update-via-candidate flow** : if the candidate's frontmatter
    declares ``update_target: <existing-active-skill>`` then the active
    target is **first archived** with a timestamp suffix
    (``.claude/skill-archive/<target>-superseded-<UTC>/``) and the
    candidate is renamed to take its place (``<target>``). This keeps
    active skills immutable — every evolution leaves an audit-trail copy
    of the previous version. If the declared ``update_target`` does not
    exist in active, the promote falls back to a plain candidate→active
    move and records a warning in the audit log (the candidate's intent
    is honoured but the operator is alerted).
    """
    root = resolve_repo_root(repo_root)
    _bucket, src_dir = _find_skill_dir(root, name, expected=("candidate",))
    meta = _build_metadata(name, "candidate", src_dir)
    if meta.update_target:
        target_name = meta.update_target
        target_dir = root / ".claude/skills" / target_name
        target_skill_md = target_dir / "SKILL.md"
        if target_skill_md.is_file():
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            archive_dir = root / ".claude/skill-archive" / f"{target_name}-superseded-{ts}"
            _move_skill_dir(target_dir, archive_dir)
            dst_dir = root / ".claude/skills" / target_name
            _move_skill_dir(src_dir, dst_dir)
            _append_audit(
                root,
                operator=operator,
                action="promote-update",
                skill_name=name,
                details={
                    "from": "candidate",
                    "to": "active",
                    "update_target": target_name,
                    "superseded_archive": archive_dir.name,
                },
            )
            return _build_metadata(target_name, "active", dst_dir)
        # update_target declared but does not exist : promote-as-add + warn.
        dst_dir = root / ".claude/skills" / name
        _move_skill_dir(src_dir, dst_dir)
        _append_audit(
            root,
            operator=operator,
            action="promote",
            skill_name=name,
            details={
                "from": "candidate",
                "to": "active",
                "warning": "update_target_not_found",
                "declared_update_target": target_name,
            },
        )
        return _build_metadata(name, "active", dst_dir)
    dst_dir = root / ".claude/skills" / name
    _move_skill_dir(src_dir, dst_dir)
    _append_audit(
        root,
        operator=operator,
        action="promote",
        skill_name=name,
        details={"from": "candidate", "to": "active"},
    )
    return _build_metadata(name, "active", dst_dir)


def archive_skill(name: str, operator: str, repo_root: Path | None = None) -> SkillMetadata:
    """Move a skill from candidate-or-active to ``skill-archive/``.

    Source bucket is auto-detected. 404 if not in either source bucket
    (archive of an already-archived skill is a no-op).
    """
    root = resolve_repo_root(repo_root)
    src_bucket, src_dir = _find_skill_dir(root, name, expected=("candidate", "active"))
    dst_dir = root / ".claude/skill-archive" / name
    _move_skill_dir(src_dir, dst_dir)
    _append_audit(
        root,
        operator=operator,
        action="archive",
        skill_name=name,
        details={"from": src_bucket, "to": "archived"},
    )
    return _build_metadata(name, "archived", dst_dir)


def drop_skill(name: str, operator: str, repo_root: Path | None = None) -> None:
    """Permanently delete a candidate skill (destructive).

    Only valid for the ``candidate`` bucket — dropping an active or
    archived skill would lose audit-relevant history. 400 if attempted
    on the wrong bucket, 404 if the candidate does not exist.
    """
    root = resolve_repo_root(repo_root)
    # Forbid drop on active / archived BEFORE the 404 check on candidate, so
    # the operator sees the "you cannot drop this bucket" error instead of
    # a misleading "not found".
    for _forbidden_bucket, forbidden_rel in (("active", ".claude/skills"), ("archived", ".claude/skill-archive")):
        if (root / forbidden_rel / name / "SKILL.md").is_file():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="drop_only_candidates",
            )
    _bucket, src_dir = _find_skill_dir(root, name, expected=("candidate",))
    shutil.rmtree(src_dir)
    _append_audit(
        root,
        operator=operator,
        action="drop",
        skill_name=name,
        details={"from": "candidate", "to": None},
    )
