#!/usr/bin/env python3
"""Generate docs/reference/ENDPOINTS.md inventory from FastAPI route decorators.

Scans `webservices/*/routes/**/*.py` for `@router.<method>(...)` decorators,
resolves URL prefixes from each service's `main.py` (`app.include_router(router, prefix=...)`),
extracts the first line of each route's docstring as the purpose, and writes
`docs/reference/ENDPOINTS.md`.

Rerun whenever routes change:
    python scripts/generate-endpoints-inventory.py

CI fails if `docs/reference/ENDPOINTS.md` is out of date (see `.github/workflows/doc-inventories.yml`).

Agent workflow — per `CLAUDE.md` rules:
    Before any session that may propose a new endpoint, run this script
    and read the resulting `docs/reference/ENDPOINTS.md` to avoid reinventing existing ones.
"""

from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
WEBSERVICES = REPO_ROOT / "webservices"
OUTPUT = REPO_ROOT / "docs" / "reference" / "ENDPOINTS.md"

METHOD_DECORATORS = {"get", "post", "put", "patch", "delete", "head", "options"}


def extract_router_prefixes(main_py: Path) -> dict[str, str]:
    """Map router variable name → URL prefix by parsing `app.include_router(...)` in main.py."""
    try:
        tree = ast.parse(main_py.read_text(encoding="utf-8"))
    except (SyntaxError, FileNotFoundError):
        return {}

    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "include_router"
        ):
            continue
        if not node.args:
            continue
        router_arg = node.args[0]
        router_name = getattr(router_arg, "id", None)
        if not router_name:
            continue
        prefix = ""
        for kw in node.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                prefix = str(kw.value.value)
        mapping[router_name] = prefix
    return mapping


def extract_router_sources(main_py: Path) -> dict[str, Path]:
    """Map router variable name → routes file path, by scanning imports in main.py.

    Handles: `from .routes.auth import router as auth_router`
         and: `from .routes.rewards.referral import router as referral_router`
    """
    try:
        tree = ast.parse(main_py.read_text(encoding="utf-8"))
    except (SyntaxError, FileNotFoundError):
        return {}

    svc_dir = main_py.parent
    mapping: dict[str, Path] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        # Only care about imports from .routes.*
        if "routes" not in module.split("."):
            continue
        # Resolve path relative to service dir (leading dots already elided by ast)
        parts = module.split(".")
        # parts like ['routes', 'auth'] or ['routes', 'rewards', 'referral']
        if parts[0] != "routes":
            continue
        # Module file: routes/auth.py
        rel_path = svc_dir.joinpath(*parts).with_suffix(".py")
        # Package fallback: routes/admin/__init__.py — when ``from routes.admin
        # import router`` resolves to a package, the inventory needs to find
        # the aggregator router exported by ``__init__.py`` and follow its
        # ``include_router(...)`` calls back to the per-submodule files.
        if not rel_path.exists():
            pkg_init = svc_dir.joinpath(*parts) / "__init__.py"
            if pkg_init.exists():
                rel_path = pkg_init
            else:
                continue
        for alias in node.names:
            local_name = alias.asname or alias.name
            mapping[local_name] = rel_path
    return mapping


def extract_router_local_prefix(tree: ast.AST) -> str:
    """Find an `APIRouter(prefix="...")` assignment to the `router` variable in this file."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "router" for t in node.targets):
            continue
        if not (isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        is_apirouter = (isinstance(func, ast.Name) and func.id == "APIRouter") or (
            isinstance(func, ast.Attribute) and func.attr == "APIRouter"
        )
        if not is_apirouter:
            continue
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
    return ""


def extract_routes_from_file(py_file: Path) -> tuple[str, list[tuple[str, str, str]]]:
    """Return (router_local_prefix, list of (method, relative_path, first_line_docstring))."""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return "", []

    local_prefix = extract_router_local_prefix(tree)
    routes: list[tuple[str, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            if not (isinstance(dec.func, ast.Attribute) and dec.func.attr in METHOD_DECORATORS):
                continue
            method = dec.func.attr.upper()

            # Path: first positional arg OR kwarg `path=...`
            path: str | None = None
            if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                path = dec.args[0].value
            else:
                for kw in dec.keywords:
                    if kw.arg == "path" and isinstance(kw.value, ast.Constant):
                        path = str(kw.value.value)
                        break
            if path is None:
                continue

            # Purpose: first non-empty line of docstring (function body first stmt)
            purpose = ""
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                first_line = node.body[0].value.value.strip().split("\n")[0].strip()
                # Remove leading ">" quote markers, trailing punctuation
                purpose = re.sub(r"^>+\s*", "", first_line).rstrip(".")
            routes.append((method, path, purpose))
    return local_prefix, routes


def generate() -> str:
    """Build the ENDPOINTS.md content."""
    services = sorted(d for d in WEBSERVICES.iterdir() if d.is_dir() and (d / "main.py").exists())

    lines = [
        "# Ratis Endpoints Inventory",
        "",
        "> **Auto-generated — do not edit manually.**",
        "> Regenerate: `python scripts/generate-endpoints-inventory.py`",
        "> CI enforces freshness on every PR (see `.github/workflows/doc-inventories.yml`).",
        "",
        "**Agent rule (CLAUDE.md §3)**: run this script and read this file BEFORE any "
        "brainstorm or code session that may propose a new endpoint. Reuse existing "
        "endpoints rather than inventing duplicates.",
        "",
        "**Columns** : Method · Path (with prefix applied) · Purpose (first-line docstring) · Source file.",
        "",
    ]

    total = 0
    for svc in services:
        main_py = svc / "main.py"
        routes_dir = svc / "routes"
        if not routes_dir.exists():
            continue

        router_prefix = extract_router_prefixes(main_py)
        router_source = extract_router_sources(main_py)

        # Build: for each router file found, collect routes with their full (prefixed) path.
        # Full path = app_mount_prefix + router_local_prefix + route_rel_path
        by_file: dict[Path, list[tuple[str, str, str, str]]] = defaultdict(list)
        for router_name, src_file in router_source.items():
            app_prefix = router_prefix.get(router_name, "")
            # Package router: when ``src_file`` is a package's ``__init__.py``,
            # there are no route decorators directly — the ``__init__.py``
            # aggregates submodule routers via ``router.include_router(...)``.
            # Walk those submodule imports and harvest routes from each.
            sub_files: list[Path] = []
            if src_file.name == "__init__.py":
                pkg_dir = src_file.parent
                try:
                    pkg_tree = ast.parse(src_file.read_text(encoding="utf-8"))
                except SyntaxError:
                    pkg_tree = None
                if pkg_tree is not None:
                    for n in ast.walk(pkg_tree):
                        if not isinstance(n, ast.ImportFrom):
                            continue
                        # ``from .debug import router as debug_router`` →
                        # node.module='debug', level=1, look up ``pkg_dir/debug.py``.
                        mod = n.module or ""
                        if not mod or n.level == 0:
                            continue
                        candidate = pkg_dir.joinpath(*mod.split(".")).with_suffix(".py")
                        if candidate.exists():
                            sub_files.append(candidate)

            if sub_files:
                for sub in sub_files:
                    local_prefix, routes = extract_routes_from_file(sub)
                    combined_prefix = app_prefix.rstrip("/") + "/" + local_prefix.strip("/")
                    combined_prefix = "/" + combined_prefix.strip("/")
                    for method, rel_path, purpose in routes:
                        full_path = (combined_prefix.rstrip("/") + "/" + rel_path.lstrip("/")).rstrip("/") or "/"
                        by_file[sub].append((method, full_path, purpose, router_name))
                # Mark the package init as visited to avoid the fallback loop below.
                by_file.setdefault(src_file, [])
                continue

            local_prefix, routes = extract_routes_from_file(src_file)
            combined_prefix = app_prefix.rstrip("/") + "/" + local_prefix.strip("/")
            combined_prefix = "/" + combined_prefix.strip("/")
            for method, rel_path, purpose in routes:
                full_path = (combined_prefix.rstrip("/") + "/" + rel_path.lstrip("/")).rstrip("/") or "/"
                by_file[src_file].append((method, full_path, purpose, router_name))

        # Fallback: any routes file that wasn't mounted via include_router (shouldn't happen)
        for route_file in sorted(routes_dir.rglob("*.py")):
            if route_file.name == "__init__.py" or route_file in by_file:
                continue
            _local, routes = extract_routes_from_file(route_file)
            for method, rel_path, purpose in routes:
                by_file[route_file].append((method, rel_path + " [UNMOUNTED?]", purpose, "?"))

        if not by_file:
            continue

        lines.append(f"## {svc.name}")
        lines.append("")
        lines.append("| Method | Path | Purpose | Source |")
        lines.append("|---|---|---|---|")

        for route_file in sorted(by_file.keys()):
            entries = sorted(by_file[route_file], key=lambda e: (e[1], e[0]))
            rel = route_file.relative_to(REPO_ROOT).as_posix()
            for method, full_path, purpose, _router_name in entries:
                purpose_display = purpose if purpose else "—"
                lines.append(f"| `{method}` | `{full_path}` | {purpose_display} | `{rel}` |")
                total += 1

        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"**Total endpoints: {total}** across {len(services)} services.")
    lines.append("")
    return "\n".join(lines)


def main(check_only: bool = False) -> int:
    new_content = generate()
    if check_only:
        if not OUTPUT.exists():
            print(f"ERROR: {OUTPUT.name} missing. Run scripts/generate-endpoints-inventory.py", file=sys.stderr)
            return 1
        # Normalize CRLF → LF on read for cross-platform comparison.
        current = OUTPUT.read_text(encoding="utf-8").replace("\r\n", "\n")
        if current != new_content:
            print(f"ERROR: {OUTPUT.name} is out of date. Run scripts/generate-endpoints-inventory.py", file=sys.stderr)
            return 1
        print(f"OK: {OUTPUT.name} is up to date.")
        return 0

    # Always write LF (not CRLF) — cross-platform consistency for CI --check mode.
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="\n") as f:
        f.write(new_content)
    size = OUTPUT.stat().st_size
    count = new_content.count("| `")
    print(f"Wrote {OUTPUT.name} ({size} bytes, {count} endpoints).")
    return 0


if __name__ == "__main__":
    check = "--check" in sys.argv
    sys.exit(main(check_only=check))
