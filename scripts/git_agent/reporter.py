"""Format and post reports as GitHub PR comments."""

from __future__ import annotations

import os

from github import Github

from .regex_validator import SEVERITY_EMOJI, Violation


def _format_regex_report(violations: list[Violation], blocked: bool) -> str:
    """Format the regex validation section of the report."""
    if not violations:
        return "### Etape 1 — Validation regex : :white_check_mark: PASSE\n\nAucune violation detectee.\n"

    header = "### Etape 1 — Validation regex : :x: BLOQUE\n\n"

    table = "| ID | Sev | Fichier | Ligne | Probleme |\n"
    table += "|---|---|---|---|---|\n"
    for v in violations:
        emoji = SEVERITY_EMOJI.get(v.severity, "")
        table += f"| {v.rule_id} | {emoji} | {v.file} | {v.line} | {v.message} |\n"

    footer = "\n**LLM review non lancee — corriger les violations ci-dessus.**\n"

    return header + table + footer


def _format_llm_report(llm_result: dict) -> str:
    """Format the LLM review section of the report."""
    status = ":white_check_mark: APPROUVE" if llm_result["approved"] else ":x: BLOQUE"
    header = f"### Etape 2 — LLM Review : {status}\n\n"
    return header + llm_result["body"] + "\n"


def build_report(
    violations: list[Violation],
    blocked_by_regex: bool,
    llm_result: dict | None = None,
) -> str:
    """Build the full markdown report."""
    report = "## :robot: Ratis Git Agent — Rapport\n\n"
    report += _format_regex_report(violations, blocked_by_regex)

    if llm_result is not None:
        report += "\n---\n\n"
        report += _format_llm_report(llm_result)

    return report


def post_comment(report: str) -> None:
    """Post the report as a PR comment on GitHub."""
    gh_token = os.environ["GH_TOKEN"]
    repo_name = os.environ["REPO"]
    pr_number = int(os.environ["PR_NUMBER"])

    gh = Github(gh_token)
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    # Delete previous agent comments to avoid spam
    for comment in pr.get_issue_comments():
        if comment.body.startswith("## :robot: Ratis Git Agent"):
            comment.delete()

    pr.create_issue_comment(report)


def post_review(approved: bool) -> None:
    """Submit a PR review (approve or request changes).

    Dismisses any prior reviews from the same bot before creating the new one
    so a follow-up APPROVE correctly clears a previous REQUEST_CHANGES.
    """
    gh_token = os.environ["GH_TOKEN"]
    repo_name = os.environ["REPO"]
    pr_number = int(os.environ["PR_NUMBER"])

    gh = Github(gh_token)
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    # Dismiss previous reviews from this bot to avoid stale REQUEST_CHANGES blocking APPROVE
    # GITHUB_ACTOR is set by Actions; for GITHUB_TOKEN it resolves to "github-actions[bot]"
    bot_login = os.environ.get("GITHUB_ACTOR", "github-actions[bot]")
    for review in pr.get_reviews():
        if review.user.login == bot_login and review.state in ("CHANGES_REQUESTED", "APPROVED"):
            try:
                review.dismiss("Superseded by updated review.")
            except Exception as exc:
                print(f"  Warning: could not dismiss prior review {review.id}: {exc}")

    event = "APPROVE" if approved else "REQUEST_CHANGES"
    body = "Ratis Git Agent — review automatique."
    try:
        pr.create_review(body=body, event=event)
    except Exception as exc:
        print(f"  Warning: could not submit PR review ({event}): {exc}")
