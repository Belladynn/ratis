"""Thin wrapper around the Hermes CLI (Codex via ChatGPT Plus OAuth).

The skill used to POST directly to the OpenAI Chat Completions API with an
``OPENAI_API_KEY``. That model required us to manage a dedicated API key with
metered billing on top of the operator's existing ChatGPT Plus subscription.

We now shell out to Hermes instead: ``docker exec ratis-hermes hermes chat -Q
-q '<prompt>'``. Hermes is already configured with the OAuth ChatGPT Plus
credentials (provider ``openai-codex``, default model ``gpt-5.5``), so we reuse
the seat that's already paid for and remove one secret from the system.

Wireformat reminder (cf. ``hermes status`` + manual probing 2026-05-31):

- With ``-Q/--quiet`` the banner / spinner / box-drawing is suppressed. stdout
  is the model's reply, **plain text**. ``session_id: <id>`` may appear as the
  first OR last line of stderr depending on timing — we ignore stderr.
- Without ``-Q`` Hermes wraps the reply in ``╭─ ⚕ Hermes ─...─╮ ... ╰...─╯`` and
  indents each body line with 4 spaces. The legacy box-drawing parser below
  exists as a defensive fallback in case ``-Q`` ever stops being honoured.

The ``--no-llm`` stub path is preserved unchanged so ``--dry-run --no-llm``
keeps working without Docker or Hermes installed (CI, unit tests).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_MODEL = os.environ.get("HERMES_POSTMORTEM_MODEL", "gpt-5.5")
DEFAULT_CONTAINER = os.environ.get("HERMES_POSTMORTEM_CONTAINER", "ratis-hermes")
DEFAULT_TIMEOUT = int(os.environ.get("HERMES_POSTMORTEM_TIMEOUT", "300"))
DEFAULT_DOCKER_BIN = os.environ.get("HERMES_POSTMORTEM_DOCKER_BIN", "docker")
DEFAULT_HERMES_BIN = os.environ.get("HERMES_POSTMORTEM_HERMES_BIN", "hermes")


def _running_in_container() -> bool:
    """True when this script is executing INSIDE the ratis-hermes container.

    Detection priority:
    1. ``HERMES_POSTMORTEM_IN_CONTAINER`` env override (``1``/``true``)
    2. ``/.dockerenv`` marker file (standard Docker indicator)

    Why this matters: when the script lives on the host, we shell out via
    ``docker exec ratis-hermes hermes chat`` to reach the LLM (Hermes holds the
    Codex OAuth creds inside the container). When the script lives inside the
    container itself (e.g. invoked by `hermes cron --no-agent --script`),
    ``docker`` is not available — we must call ``hermes chat`` directly.
    """
    override = os.environ.get("HERMES_POSTMORTEM_IN_CONTAINER", "").strip().lower()
    if override in {"1", "true", "yes"}:
        return True
    if override in {"0", "false", "no"}:
        return False
    return Path("/.dockerenv").exists()


_IN_CONTAINER = _running_in_container()

# Prompt-file directory. When we run on the host, files land in
# ~/.hermes/state/postmortem-prompts/ which the container sees as
# /opt/data/state/postmortem-prompts/ via the bind mount. When we run *inside*
# the container, both sides of the bind mount collapse to the same path —
# /opt/data/state/postmortem-prompts/ — so we keep using it directly.
_DEFAULT_PROMPT_DIR_HOST = Path.home() / ".hermes" / "state" / "postmortem-prompts"
_DEFAULT_PROMPT_DIR_CONTAINER = Path("/opt/data/state/postmortem-prompts")
DEFAULT_HOST_PROMPT_DIR = Path(
    os.environ.get(
        "HERMES_POSTMORTEM_HOST_PROMPT_DIR",
        str(_DEFAULT_PROMPT_DIR_CONTAINER if _IN_CONTAINER else _DEFAULT_PROMPT_DIR_HOST),
    )
)
DEFAULT_CONTAINER_PROMPT_DIR = os.environ.get(
    "HERMES_POSTMORTEM_CONTAINER_PROMPT_DIR",
    "/opt/data/state/postmortem-prompts",
)

# Heuristic: anything below this many chars stays as an argv ``-q`` (one less
# round-trip via the filesystem). Above, we materialize the prompt to a file
# and ``cat`` it in via ``sh -c``. 64K is well under both Linux argv limits
# and the empirical docker-exec ceiling.
ARGV_PROMPT_THRESHOLD = int(
    os.environ.get("HERMES_POSTMORTEM_ARGV_THRESHOLD", "65536")
)

# Hermes prints a usage hint after each ``-Q`` call on stderr. We do not parse
# stderr for the response, but the debug logs (when ``-v`` is set) carry a
# ``Token usage: prompt=X, completion=Y`` line we can opportunistically lift.
_TOKEN_USAGE_RE = re.compile(
    r"Token usage:\s*prompt=([\d,]+),\s*completion=([\d,]+)"
)

# Defensive fallback parser for the box-drawing format (if a future Hermes
# version drops the ``-Q`` contract).
_BOX_TOP_RE = re.compile(r"^\s*╭[─\s].*?Hermes\b.*[─\s]╮\s*$")
_BOX_BOTTOM_RE = re.compile(r"^\s*╰[─\s]+╯\s*$")


@dataclass
class LLMResponse:
    """Normalized LLM response — same shape as the legacy OpenAI client.

    ``tokens_in`` / ``tokens_out`` may be zero when Hermes does not surface them
    (we only get them when ``-v`` is enabled and the debug log makes it through
    to stderr). Counting purely for audit visibility — not used for billing.
    """

    content: str
    tokens_in: int
    tokens_out: int
    model: str

    def parse_json(self) -> dict[str, Any]:
        """Parse the assistant content as JSON, stripping markdown fences if any."""
        body = self.content.strip()
        # Strip ```json ... ``` fences if the model wrapped the JSON.
        if body.startswith("```"):
            lines = body.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            body = "\n".join(lines).strip()
        return json.loads(body)


def _strip_box_drawing(text: str) -> str:
    """Remove the ``╭─ Hermes ─╮ ... ╰─╯`` wrapper if Hermes ever falls back to it.

    Behaviour:

    - Find a top-border line matching ``╭...Hermes...╮``.
    - Find the next bottom-border line ``╰...╯``.
    - Return the lines between, with the leading 4-space indent stripped.
    - If no top border is found, return the input untouched.
    """
    lines = text.splitlines()
    top = None
    for i, ln in enumerate(lines):
        if _BOX_TOP_RE.match(ln):
            top = i
            break
    if top is None:
        return text

    body: list[str] = []
    for ln in lines[top + 1 :]:
        if _BOX_BOTTOM_RE.match(ln):
            break
        # Body lines are indented by 4 spaces in the Hermes box; strip that.
        body.append(ln[4:] if ln.startswith("    ") else ln)
    return "\n".join(body).rstrip()


def _is_noise_line(line: str) -> bool:
    """Return True if ``line`` is a Hermes status/spinner line we should drop.

    Even in ``-Q`` mode Hermes leaks some live status lines to stdout when the
    response triggers context compaction (the transcript can exceed the model's
    50% compaction threshold). Observed examples:

    - ``  ⟳ compacting context…``
    - ``  ⠋ thinking…`` (when spinner glyphs slip past quiet)
    - ``session_id: 20260531_...``

    These have no semantic content for the caller — strip them.
    """
    s = line.strip()
    if not s:
        return False
    if s.startswith("session_id:"):
        return True
    # Spinner / status glyphs Hermes uses while waiting on the model.
    if s.startswith(("⟳", "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")):
        return True
    # Belt-and-suspenders: status keywords prefixed by spinner glyphs.
    return bool(" compacting context" in s or " thinking…" in s)


def _clean_quiet_stdout(text: str) -> str:
    """Strip Hermes status/spinner lines and ``session_id:`` from stdout."""
    out_lines = [ln for ln in text.splitlines() if not _is_noise_line(ln)]
    return "\n".join(out_lines).strip()


def parse_hermes_output(stdout: str) -> str:
    """Extract the model's reply from a Hermes CLI invocation.

    Handles both wire formats:

    1. ``-Q`` (preferred) — stdout is the reply, possibly prefixed/suffixed by
       a stray ``session_id:`` line.
    2. Box-drawing fallback — find ``╭ Hermes ╮`` / ``╰ ╯`` and extract body.
    """
    cleaned = _strip_box_drawing(stdout)
    return _clean_quiet_stdout(cleaned)


def _extract_token_counts(stderr: str) -> tuple[int, int]:
    """Pull ``prompt=X, completion=Y`` from a verbose Hermes stderr if present."""
    m = _TOKEN_USAGE_RE.search(stderr)
    if not m:
        return (0, 0)
    try:
        return (
            int(m.group(1).replace(",", "")),
            int(m.group(2).replace(",", "")),
        )
    except ValueError:
        return (0, 0)


class CodexClient:
    """Hermes-backed Codex client.

    A single ``call`` shells out ``docker exec <container> hermes chat -Q -q
    <prompt>``. Stateless — each invocation is an independent Hermes session.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        container: str = DEFAULT_CONTAINER,
        timeout: int = DEFAULT_TIMEOUT,
        docker_bin: str = DEFAULT_DOCKER_BIN,
        hermes_bin: str = DEFAULT_HERMES_BIN,
        host_prompt_dir: Path = DEFAULT_HOST_PROMPT_DIR,
        container_prompt_dir: str = DEFAULT_CONTAINER_PROMPT_DIR,
    ) -> None:
        self.model = model
        self.container = container
        self.timeout = timeout
        self.docker_bin = docker_bin
        self.hermes_bin = hermes_bin
        self.host_prompt_dir = host_prompt_dir
        self.container_prompt_dir = container_prompt_dir.rstrip("/")

    def is_available(self) -> bool:
        """In-container : check the ``hermes`` binary itself.

        On the host : ``docker`` must be on PATH; the container check happens
        lazily on first ``call``. If the container is down we surface the
        stderr to the audit log and return an error.
        """
        if _IN_CONTAINER:
            return shutil.which(self.hermes_bin) is not None
        return shutil.which(self.docker_bin) is not None

    # ----- argv builders ------------------------------------------------

    def _argv_prefix(self) -> list[str]:
        """Argv prefix that gets us to ``hermes chat`` — either directly (when
        running inside the container) or via ``docker exec <container>`` (when
        running on the host).
        """
        if _IN_CONTAINER:
            return [self.hermes_bin]
        return [self.docker_bin, "exec", self.container, self.hermes_bin]

    def _build_argv_inline(self, prompt: str) -> list[str]:
        """Short-prompt path — pass the prompt as a single argv to ``hermes -q``.

        Why no ``-v``? Because Hermes with ``-v`` duplicates its init banner to
        **both** stdout and stderr (instead of stderr only), polluting the
        clean JSON we expect on stdout. Token counts are nice-to-have, not
        load-bearing — we leave ``tokens_in/out`` at 0 and rely on Hermes'
        own session log for accounting.
        """
        return [*self._argv_prefix(), "chat", "-Q", "-q", prompt]

    def _build_argv_via_file(self, container_path: str) -> list[str]:
        """Large-prompt path — instruct Hermes to ``read_file`` the prompt itself.

        Why not ``sh -c 'hermes -q "$(cat ...)"'``? Because Linux enforces a
        per-arg ``MAX_ARG_STRLEN`` ceiling (128KB on most kernels), so even with
        ``ARG_MAX`` at 2MB a 200KB prompt blows up the inner ``hermes -q`` call
        with ``Argument list too long``. We sidestep the kernel entirely by
        keeping the argv tiny and letting Hermes' built-in ``read_file`` tool
        ingest the prompt body from the bind-mounted file. Hermes has the
        ``file`` toolset enabled by default (see ``hermes status``).

        The tiny instruction we DO send via argv tells Hermes exactly how to
        find and consume the file — no agentic exploration required.
        """
        instruction = (
            f"Use the read_file tool to read the file at the absolute path "
            f"{container_path}. That file contains a system prompt followed by a "
            f"user prompt with an explicit JSON output schema. Follow the "
            f"instructions literally and return ONLY the JSON object the user "
            f"prompt asks for — no markdown fence, no commentary, no preamble."
        )
        return [*self._argv_prefix(), "chat", "-Q", "-q", instruction]

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format_json: bool = True,
    ) -> LLMResponse:
        """One-shot completion through Hermes.

        We concatenate ``system_prompt`` + ``user_prompt`` into a single query
        — Hermes CLI does not expose a distinct system role through ``-q`` so we
        prepend the system instructions as the first line.

        ``response_format_json`` is accepted for compatibility with the legacy
        client; Hermes does not have a JSON-mode flag for the CLI, so we rely
        on the prompt itself to instruct the model (which is already what the
        caller does — ``_build_user_prompt`` ends with an explicit JSON schema).
        """
        prompt = (
            f"{system_prompt.strip()}\n\n"
            f"{user_prompt.strip()}"
        )

        # Choose argv-inline vs file-passthrough based on prompt size.
        # ``docker exec`` has an effective argv ceiling well below ARG_MAX
        # (empirically ~128KB on Docker Desktop / macOS), so anything sizeable
        # goes via a bind-mounted file rather than the argv.
        prompt_file_host: Path | None = None
        if len(prompt) >= ARGV_PROMPT_THRESHOLD:
            self.host_prompt_dir.mkdir(parents=True, exist_ok=True)
            # Use NamedTemporaryFile with delete=False so we can pass its name
            # to Docker and clean up after the call completes.
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=str(self.host_prompt_dir),
                prefix=f"prompt-{uuid.uuid4().hex[:8]}-",
                suffix=".txt",
                encoding="utf-8",
                delete=False,
            ) as tmp:
                tmp.write(prompt)
                prompt_file_host = Path(tmp.name)
            container_path = (
                f"{self.container_prompt_dir}/{prompt_file_host.name}"
            )
            argv = self._build_argv_via_file(container_path)
        else:
            argv = self._build_argv_inline(prompt)

        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"docker binary not found ({self.docker_bin!r}): {e}"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"Hermes call timed out after {self.timeout}s "
                f"(prompt {len(prompt)} chars)"
            ) from e
        finally:
            # Best-effort cleanup; leaving a stale prompt is harmless but noisy.
            if prompt_file_host is not None:
                with contextlib.suppress(OSError):
                    prompt_file_host.unlink()

        if proc.returncode != 0:
            raise RuntimeError(
                f"Hermes CLI exit {proc.returncode}: "
                f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
            )

        content = parse_hermes_output(proc.stdout)
        if not content:
            raise RuntimeError(
                "Hermes returned an empty response after parsing. "
                f"stdout={proc.stdout!r} stderr={proc.stderr[-500:]!r}"
            )

        tokens_in, tokens_out = _extract_token_counts(proc.stderr)
        return LLMResponse(
            content=content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=self.model,
        )


def stub_response_for_dry_run(transcript_excerpt: str) -> LLMResponse:
    """Return a deterministic stub used in ``--no-llm`` mode for E2E pipeline tests."""
    payload = {
        "summary": "[dry-run stub] transcript was parsed but no LLM call was made.",
        "outcomes": ["dry-run"],
        "outcome_reason": "--no-llm flag set",
        "tools_used": [],
        "patterns_observed": [],
        "skill_match": [],
        "skill_candidates": [],
        "warnings": [],
        "_dry_run": True,
        "_excerpt_first_120_chars": transcript_excerpt[:120],
    }
    return LLMResponse(
        content=json.dumps(payload),
        tokens_in=0,
        tokens_out=0,
        model="stub",
    )


__all__ = [
    "DEFAULT_MODEL",
    "CodexClient",
    "LLMResponse",
    "parse_hermes_output",
    "stub_response_for_dry_run",
]
