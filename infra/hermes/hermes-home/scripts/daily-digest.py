#!/usr/bin/env python3
"""Daily digest — Ratis morning briefing for Telegram.

Run via `hermes cron --no-agent --script daily-digest.sh --deliver telegram`.
Stdout is delivered verbatim to Telegram. Stays under Telegram's 4096 chars.

Collects:
  - GlitchTip unresolved issues per project (top 3 each)
  - GitHub open PRs (top 5) + their CI status
  - Hermes kanban tasks ready / blocked
  - Last postmortem timestamp

Reads creds from env (see ~/.hermes/.env DIGEST_* keys).
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


def _load_env_file(path: Path) -> None:
    """Inject `key=val` lines from `.env` into os.environ if not already set.

    Hermes reads ~/.hermes/.env into its own Python process but does not
    propagate those vars to subprocess children invoked via `hermes cron
    --no-agent --script`. We re-read defensively so DIGEST_* keys are visible.
    Robust to quoted values, blank lines, and comments.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(Path("/opt/data/.env"))

GH_TOKEN = os.environ.get("DIGEST_GITHUB_TOKEN", "")
GLT_TOKEN = os.environ.get("DIGEST_GLITCHTIP_TOKEN", "")
REPO = os.environ.get("DIGEST_REPO", "Belladynn/ratis")
GLT_API = os.environ.get("DIGEST_GLT_API", "http://ratis-glitchtip-web:8000/api/0").rstrip("/")
GLT_ORG = os.environ.get("DIGEST_GLT_ORG", "ratis")
POSTMORTEMS_DIR = "/opt/claude/postmortems"


def _get_json(url: str, headers: dict[str, str], timeout: int = 10):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        return {"_error": str(e)}


def section_glitchtip() -> str:
    if not GLT_TOKEN:
        return "🟦 *GlitchTip* — skip (token absent)"
    h = {"Authorization": f"Bearer {GLT_TOKEN}"}
    # List projects to know which ones to poll
    projects = _get_json(f"{GLT_API}/teams/{GLT_ORG}/{GLT_ORG}/projects/", h)
    if isinstance(projects, dict) and projects.get("_error"):
        return f"🟦 *GlitchTip* — erreur: {projects['_error']}"
    lines: list[str] = ["🟦 *GlitchTip incidents (unresolved)*"]
    total = 0
    for proj in projects[:5]:
        slug = proj.get("slug", "?")
        issues = _get_json(
            f"{GLT_API}/projects/{GLT_ORG}/{slug}/issues/?query=is:unresolved&limit=3",
            h,
        )
        if isinstance(issues, dict) and issues.get("_error"):
            lines.append(f"  • `{slug}` — erreur API")
            continue
        if not issues:
            continue
        lines.append(f"  *{slug}* ({len(issues)} issue{'s' if len(issues) > 1 else ''}):")
        for i in issues[:3]:
            title = (i.get("title") or "")[:80]
            count = i.get("count", "?")
            lines.append(f"    – {title} (×{count})")
        total += len(issues)
    if total == 0:
        return "🟦 *GlitchTip* — 0 issue unresolved ✓"
    return "\n".join(lines)


def section_github_prs() -> str:
    if not GH_TOKEN:
        return "🟪 *GitHub* — skip (token absent)"
    h = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    prs = _get_json(f"https://api.github.com/repos/{REPO}/pulls?state=open&per_page=10", h)
    if isinstance(prs, dict) and prs.get("_error"):
        return f"🟪 *GitHub* — erreur: {prs['_error']}"
    if not prs:
        return "🟪 *GitHub PRs* — 0 ouverte ✓"
    lines = [f"🟪 *GitHub PRs ouvertes* ({len(prs)})"]
    for pr in prs[:5]:
        num = pr.get("number")
        title = (pr.get("title") or "")[:60]
        draft = " [draft]" if pr.get("draft") else ""
        lines.append(f"  – #{num} {title}{draft}")
    return "\n".join(lines)


def section_kanban() -> str:
    try:
        out = subprocess.run(
            ["hermes", "kanban", "stats"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return f"📋 *Kanban* — erreur: {out.stderr[:80]}"
        # Parse "ready=N  todo=N  blocked=N  running=N" from stats output
        stats = out.stdout
        ready = blocked = running = "?"
        for line in stats.splitlines():
            ls = line.strip()
            if ls.startswith("ready"): ready = ls.split()[-1]
            elif ls.startswith("blocked"): blocked = ls.split()[-1]
            elif ls.startswith("running"): running = ls.split()[-1]
        return f"📋 *Kanban* — ready={ready} · running={running} · blocked={blocked}"
    except (subprocess.SubprocessError, OSError) as e:
        return f"📋 *Kanban* — erreur subprocess: {e}"


def section_postmortem() -> str:
    if not os.path.isdir(POSTMORTEMS_DIR):
        return "📓 *Postmortem* — dir absent"
    files = sorted(os.listdir(POSTMORTEMS_DIR), reverse=True)
    md_files = [f for f in files if f.endswith(".md")]
    if not md_files:
        return "📓 *Postmortem* — aucun"
    last = md_files[0]
    path = os.path.join(POSTMORTEMS_DIR, last)
    mtime = os.path.getmtime(path)
    age_h = (datetime.now(UTC).timestamp() - mtime) / 3600
    return f"📓 *Postmortem* — dernier: `{last}` (il y a {age_h:.1f}h)"


def main():
    now = datetime.now(UTC).astimezone()
    header = f"🌅 *Daily digest Ratis* — {now.strftime('%a %d %b %H:%M')}"
    sections = [
        header,
        "",
        section_glitchtip(),
        "",
        section_github_prs(),
        "",
        section_kanban(),
        section_postmortem(),
    ]
    text = "\n".join(sections)
    # Cap at Telegram limit (4096 chars)
    if len(text) > 4000:
        text = text[:4000] + "\n…(truncated)"
    print(text)


if __name__ == "__main__":
    main()
