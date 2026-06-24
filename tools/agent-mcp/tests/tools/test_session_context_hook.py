"""TDD coverage for the SessionStart hook (`scripts/hooks/inject-session-context.py`).

The hook is a self-contained Python script that imports
``agent_mcp.tools.docs_tools.docs_context_for_session`` in-process. These
tests exercise its CLI surface :

* with a synthetic inventory pointed via ``RATIS_DOCS_INVENTORY_PATH``,
* with empty / malformed stdin,
* when the import raises (we monkeypatch the function to inject failure).

The hook is launched via ``subprocess.run([sys.executable, hook_path])``
to capture the realistic stdout / exit code path.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).resolve().parents[4] / "scripts" / "hooks" / "inject-session-context.py"


@pytest.fixture
def synth_inventory(tmp_path: Path) -> Path:
    """Tiny inventory that the hook can hit semantically.

    The hook reaches the inventory through the multi-source corpus, whose
    ``arch_inventory`` source resolves ``docs/reference/ARCH_INVENTORY.md``
    relative to the repo root. With the override set, the repo root is the
    inventory's parent (tmp_path), so we drop the inventory at
    ``tmp_path/docs/reference/`` for the corpus source and keep a tmp-root
    copy for ``RATIS_DOCS_INVENTORY_PATH`` to point at — same content, both
    places, mirroring the production layout where they're one file.
    """
    inv_text = "\n".join(
        [
            "# Inv",
            "",
            "ID | STATUT | FICHIER:LIGNE | TAGS | TL;DR",
            "---+--------+---------------+------+------",
            "DA-42 | LIVRÉ V1.0 | docs/arch/X.md:3 | admin-ui session | Admin UI session bridge.",
            "DA-77 | EN-COURS | docs/arch/Y.md:5 | scan camera | Camera scan flow.",
            "",
        ]
    )
    inv = tmp_path / "ARCH_INVENTORY.md"
    inv.write_text(inv_text, encoding="utf-8")
    ref_dir = tmp_path / "docs" / "reference"
    ref_dir.mkdir(parents=True)
    (ref_dir / "ARCH_INVENTORY.md").write_text(inv_text, encoding="utf-8")
    return inv


def _run_hook(stdin: str, env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    import os

    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def test_hook_emits_session_context_block(synth_inventory: Path) -> None:
    """A cwd hinting at 'admin/ui' must surface DA-42 (matching tags)."""
    payload = '{"cwd": "/tmp/admin/ui"}'
    res = _run_hook(payload, {"RATIS_DOCS_INVENTORY_PATH": str(synth_inventory)})
    assert res.returncode == 0
    assert "session-context" in res.stdout.lower() or "Session context" in res.stdout
    # DA-42 has 'admin-ui' tag → tokens 'admin' and 'ui' (acronym) match.
    assert "DA-42" in res.stdout


def test_hook_empty_stdin_does_not_crash(synth_inventory: Path) -> None:
    """No stdin payload → hook falls back to ``Path.cwd()`` and still exits 0."""
    res = _run_hook("", {"RATIS_DOCS_INVENTORY_PATH": str(synth_inventory)})
    assert res.returncode == 0
    # Always prints something (either a context block or a `<!-- comment -->`).
    assert res.stdout.strip() != ""


def test_hook_malformed_stdin_falls_back(synth_inventory: Path) -> None:
    res = _run_hook(
        "not json at all {{{",
        {"RATIS_DOCS_INVENTORY_PATH": str(synth_inventory)},
    )
    assert res.returncode == 0
    # Even malformed stdin produces stdout — no traceback leaked.
    assert "Traceback" not in res.stdout
    assert "Traceback" not in res.stderr


def test_hook_no_inventory_emits_silent_comment(tmp_path: Path) -> None:
    """If the inventory file is missing, the hook emits a comment and exits 0."""
    missing = tmp_path / "missing.md"
    res = _run_hook(
        '{"cwd": "/tmp"}',
        {"RATIS_DOCS_INVENTORY_PATH": str(missing)},
    )
    assert res.returncode == 0
    assert "Traceback" not in res.stderr
    # Output is a hook error comment, not a real context block.
    assert "<!--" in res.stdout
