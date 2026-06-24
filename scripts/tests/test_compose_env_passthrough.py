"""
Regression guard for ``require_env(...) ↔ docker-compose.prod.yml`` drift.

Inception : Bug 5 (PR #399, RGPD anon completeness) added
``require_env("RGPD_ANONYMIZE_SALT", ...)`` to ``webservices/ratis_auth/main.py``
but the matching ``RGPD_ANONYMIZE_SALT: ${RGPD_ANONYMIZE_SALT:?...}`` line was
NOT added to auth's ``environment:`` block in ``docker-compose.prod.yml``.
The next prod deploy crashed auth at boot with::

    RuntimeError: Missing required environment variables: RGPD_ANONYMIZE_SALT
    — aborting

Same root cause hit notifier (REDIS_URL — that one was a pre-existing drift,
surfaced when require_env feedback became visible). To prevent recurrence,
this test walks every user-facing service's ``main.py``, parses each top-level
``require_env(...)`` call with AST, and asserts every required env var has a
matching mapping line in that service's ``environment:`` block in
``docker-compose.prod.yml``.

The test is intentionally permissive : it does NOT enforce particular default
syntax (``${VAR:?...}`` vs ``${VAR:-default}``) — only that the VAR exists as a
key in the environment mapping. The choice of failure-mode is a per-var
decision (cf. SA_DEV.md R20).

Run :
    uv run pytest scripts/tests/test_compose_env_passthrough.py -x -v
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.prod.yml"

# (compose service name, service main.py path relative to repo root)
# Only user-facing FastAPI services are checked here ; Celery workers reuse
# the same image but ship their own command (worker-only require_env coverage
# would need parsing the worker entrypoint, out of scope for V0).
SERVICES = [
    ("auth", "webservices/ratis_auth/main.py"),
    ("product_analyser", "webservices/ratis_product_analyser/main.py"),
    ("list_optimiser", "webservices/ratis_list_optimiser/main.py"),
    ("rewards", "webservices/ratis_rewards/main.py"),
    ("notifier", "webservices/ratis_notifier/main.py"),
]


def _extract_required_env_vars(main_py: Path) -> set[str]:
    """Parse ``main.py`` AST and return the union of every literal string passed
    to ``require_env(...)`` or ``require_env_min_length(name, _)``.

    Non-literal arguments (variables, f-strings, splats) are silently skipped —
    this guard targets the dominant case of hard-coded var names.
    """
    src = main_py.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(main_py))
    required: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match both ``require_env`` and ``require_env_min_length``
        if isinstance(func, ast.Name):
            fname = func.id
        elif isinstance(func, ast.Attribute):
            fname = func.attr
        else:
            continue
        if fname == "require_env":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    required.add(arg.value)
        # signature: require_env_min_length(name, min_length)
        elif (
            fname == "require_env_min_length"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            required.add(node.args[0].value)
    return required


def _load_compose_env(service_name: str) -> set[str]:
    """Return the set of env-var keys defined in the given compose service's
    ``environment:`` block. Supports the map form (used throughout
    docker-compose.prod.yml) ; list form is unused but tolerated."""
    with COMPOSE_FILE.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    svc = data["services"].get(service_name)
    if svc is None:
        raise AssertionError(f"Service {service_name!r} not found in {COMPOSE_FILE}")
    env = svc.get("environment", {}) or {}
    if isinstance(env, dict):
        return set(env.keys())
    # list form : ["VAR=value", "VAR2"]
    keys: set[str] = set()
    for entry in env:
        if isinstance(entry, str):
            keys.add(entry.split("=", 1)[0])
    return keys


@pytest.mark.parametrize(
    "compose_service,main_rel_path",
    SERVICES,
    ids=[s[0] for s in SERVICES],
)
def test_require_env_has_compose_passthrough(compose_service: str, main_rel_path: str) -> None:
    """Every var passed to ``require_env(...)`` in a service main.py MUST have a
    corresponding key in that service's compose ``environment:`` block."""
    main_py = REPO_ROOT / main_rel_path
    assert main_py.is_file(), f"main.py missing: {main_py}"

    required = _extract_required_env_vars(main_py)
    compose_env = _load_compose_env(compose_service)

    missing = sorted(required - compose_env)
    assert not missing, (
        f"Service {compose_service!r} declares require_env(...) for "
        f"{missing!r} but those keys are missing from "
        f"docker-compose.prod.yml services.{compose_service}.environment. "
        f"Add e.g.  '{missing[0]}: ${{{missing[0]}:?{missing[0]} is required}}'  "
        f"(fail-fast) or  '{missing[0]}: ${{{missing[0]}:-<default>}}'  "
        f"(default) to that block."
    )


def test_compose_file_parses() -> None:
    """Sanity : the compose file itself must parse cleanly. Catches accidental
    YAML breakage before the parametrised tests run."""
    with COMPOSE_FILE.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict)
    assert "services" in data
    for name, _ in SERVICES:
        assert name in data["services"], f"compose missing service {name!r}"


if __name__ == "__main__":  # pragma: no cover — convenience for ad-hoc runs
    sys.exit(pytest.main([__file__, "-v"]))
