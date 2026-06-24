"""Step 2 — LLM review via Claude API."""

from __future__ import annotations

import os
from pathlib import Path

import anthropic

REVIEWER_PROMPT_PATH = Path(__file__).parent.parent.parent / "CLAUDE_reviewer.md"
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096


def _load_system_prompt() -> str:
    """Load CLAUDE_reviewer.md as system prompt."""
    return REVIEWER_PROMPT_PATH.read_text(encoding="utf-8")


def review(
    diff: str,
    yellow_warnings: list[dict] | None = None,
    false_positives: list[dict] | None = None,
) -> dict:
    """Run LLM review on the PR diff.

    Args:
        diff: Full unified diff of the PR.
        yellow_warnings: Optional list of yellow-severity warnings from regex step
                         to include as context.
        false_positives: Optional list of findings annotated as false-positive by the
                         PR author or reviewer, with justification.

    Returns:
        dict with keys: "approved" (bool), "body" (str — markdown report).
    """
    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = _load_system_prompt()

    user_content = "Voici le diff de la PR a analyser :\n\n```diff\n" + diff + "\n```"

    if yellow_warnings:
        warnings_text = "\n".join(
            f"- {w['rule_id']} | {w['file']}:{w['line']} | {w['message']}" for w in yellow_warnings
        )
        user_content += "\n\nWarnings regex (severite jaune) detectes en amont :\n" + warnings_text

    if false_positives:
        fp_text = "\n".join(
            f"- finding: {fp['finding']} | file: {fp['file']} | reason: {fp['reason']}" for fp in false_positives
        )
        user_content += (
            "\n\nFaux positifs signales par l'auteur (a prendre en compte dans ton analyse) :\n"
            + fp_text
            + "\n\nPour chaque faux positif, verifie la justification sur le diff. "
            "Si elle est correcte, ne bloque pas sur ce point."
        )

    user_content += (
        "\n\nAnalyse ce diff selon les conventions du projet. "
        "Reponds avec le rapport au format decrit dans tes instructions. "
        "Si aucune violation : approuve. Sinon : bloque avec le detail."
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    body = response.content[0].text

    approved = "PR APPROUVEE" in body.upper() or "APPROUVEE" in body.upper()

    return {"approved": approved, "body": body}
