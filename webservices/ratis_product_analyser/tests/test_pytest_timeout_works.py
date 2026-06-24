"""Sanity probe : ensure `pytest-timeout` plugin is installed and loaded.

Without this plugin, the `[tool.pytest.ini_options] timeout = 60` config in
pyproject.toml is silently ignored — pytest will accept the unknown options and
run hung tests indefinitely. This regressed twice (PR #233 hung 85min on CI,
PR #234 hung 8min) before being diagnosed. See KP-43.

If this test fails, run :
    uv add --dev pytest-timeout

from the service directory.
"""


def test_pytest_timeout_plugin_is_loaded() -> None:
    """The plugin must be importable so that `timeout = 60` in pyproject is honored."""
    import pytest_timeout  # noqa: F401  — import is the assertion
