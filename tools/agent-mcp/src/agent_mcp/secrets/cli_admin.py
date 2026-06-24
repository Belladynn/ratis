"""ratis-admin CLI — open admin UI with OTT auth (Module 10, PR 5).

Sub-commands:

    ratis-admin open <path>                # open admin UI at <path>
    ratis-admin open <path> --service pa   # target PA (default)
    ratis-admin open <path> --service rw   # target RW
    ratis-admin open <path> --service au   # target AU
    ratis-admin open <path> --service http://host:8003  # custom URL

Security posture
----------------
* ADMIN_API_KEY is read from macOS Keychain (ratis-agent-mcp/admin-api-key).
  It is NEVER printed to stdout or stderr.
* The OTT (one-time JWT) is received from the service and passed directly to
  ``webbrowser.open`` — the raw key is never exposed in the browser URL bar.

Design notes
------------
* Uses ``httpx`` (already a dep of agent-mcp) for the POST.
* Delegates Keychain reads to the existing ``Keychain`` class (no new deps).
* ``_get_keychain()`` is module-level and replaceable for tests.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser

import httpx

from ..errors import KeychainMiss
from ..keychain import Keychain

# ---------------------------------------------------------------------------
# Service → host map
# ---------------------------------------------------------------------------

_SERVICE_HOSTS: dict[str, str] = {
    "pa": "http://localhost:8003",
    "rw": "http://localhost:8004",
    "au": "http://localhost:8001",
}

_KEYCHAIN_ACCOUNT = "admin-api-key"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Dependency injection — replaceable for tests
# ---------------------------------------------------------------------------


def _get_keychain() -> Keychain:
    """Return a Keychain instance. Replaceable for tests."""
    return Keychain()


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def cmd_open(path: str, host: str | None = None) -> int:
    """Open admin UI at ``path`` using OTT auth.

    1. Read ADMIN_API_KEY from Keychain ``ratis-agent-mcp/admin-api-key``.
    2. POST ``{host}/admin/session-bootstrap`` → {ott, redirect_url}.
    3. Open browser: ``redirect_url`` (never contains the raw key).
    4. Return 0 on success, 1 on error.

    ADMIN_API_KEY is NEVER printed to stdout or stderr.
    """
    # Resolve host.
    if host is None:
        resolved_host = _SERVICE_HOSTS["pa"]
    elif host in _SERVICE_HOSTS:
        resolved_host = _SERVICE_HOSTS[host]
    else:
        resolved_host = host  # treated as a full URL

    # Read ADMIN_API_KEY from Keychain.
    kc = _get_keychain()
    try:
        admin_key = kc.get(_KEYCHAIN_ACCOUNT)
    except KeychainMiss as exc:
        print(
            f"ratis-admin: ADMIN_API_KEY not found in Keychain.\n"
            f"  Store it first:  agent-mcp keychain set admin-api-key\n"
            f"  Detail: {exc}",
            file=sys.stderr,
        )
        return 1

    # POST to session-bootstrap.
    url = f"{resolved_host}/admin/session-bootstrap"
    try:
        resp = httpx.post(
            url,
            json={"redirect": path},
            headers={"Authorization": f"Bearer {admin_key}"},
            timeout=5.0,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        print(
            f"ratis-admin: session-bootstrap returned {exc.response.status_code}.",
            file=sys.stderr,
        )
        return 1
    except httpx.RequestError as exc:
        print(
            f"ratis-admin: could not reach {url} — {exc!r}",
            file=sys.stderr,
        )
        return 1

    body = resp.json()
    redirect_url = body["redirect_url"]

    # Open the browser. The ADMIN_API_KEY is NOT in the URL — only the OTT.
    webbrowser.open(redirect_url)
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse hierarchy for ratis-admin."""
    parser = argparse.ArgumentParser(
        prog="ratis-admin",
        description="ratis-admin — open admin UIs with OTT auth (Module 10).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # open
    open_p = sub.add_parser("open", help="Open admin UI path with OTT auth.")
    open_p.add_argument("path", help="Admin path to open (e.g. /admin/db-approvals).")
    open_p.add_argument(
        "--service",
        default=None,
        metavar="SERVICE",
        help=("Target service: pa (default, :8003), rw (:8004), au (:8001), or a full URL like http://host:8003."),
    )
    open_p.set_defaults(func=_run_open)

    return parser


def _run_open(args: argparse.Namespace) -> int:
    return cmd_open(path=args.path, host=args.service)


def main(argv: list[str] | None = None) -> int:
    """Console-script entry. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
