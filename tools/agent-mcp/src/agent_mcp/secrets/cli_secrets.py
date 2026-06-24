"""ratis-secret CLI — operator tool for secrets vault (Module 10, PR 4 + PR 6 + PR 8).

Sub-commands:

    ratis-secret list                              # list all managed secrets (metadata only)
    ratis-secret new <name> [--format FORMAT] \
        [--length N] [--description DESC]          # generate a new Cat-A secret
    ratis-secret use <name> --cmd ...              # inject secret into env and run a command
    ratis-secret revoke <lease_id>                 # revoke a lease
    ratis-secret audit                             # tail last 20 audit chain entries
    ratis-secret rotate <name> [--format FORMAT]   # rotate a secret (create v+1)
    ratis-secret import <name> --category C \
        --expires-at 2027-01-01 \
        --description "Stripe live key"            # import Cat-C secret (prompted, no echo)

Security posture
----------------
* ``use`` NEVER prints the secret value to stdout or stderr.
* ``use`` injects the value via ``os.environ`` copy → subprocess env only.
* ``audit`` outputs ``ts action name`` — no values, no lease IDs.

Design notes
------------
* Delegates to the existing MCP tool functions (``secret_list``, ``secret_get``,
  ``secret_revoke``) — the same security model (Keychain + SQLite) applies.
* The audit tail reads directly from ``SecretsAuditChain.tail()`` so it respects
  the HMAC-chained log format.
* ``_get_audit_chain()`` is module-level and replaceable for tests.
"""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys

from ..config import secrets_audit_dir
from ..keychain import Keychain
from ..secrets.audit_chain import SecretsAuditChain
from ..tools.secrets_tools import secret_generate, secret_get, secret_import, secret_list, secret_revoke, secret_rotate

# ---------------------------------------------------------------------------
# Dependency injection — replaceable for tests
# ---------------------------------------------------------------------------


def _get_audit_chain() -> SecretsAuditChain:
    """Return a SecretsAuditChain for the current audit dir."""
    kc = Keychain()
    return SecretsAuditChain(log_dir=secrets_audit_dir(), keychain=kc)


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


_FORMAT_CHOICES = ("urlsafe", "hex", "base64", "alphanumeric", "numeric", "uuid")


def cmd_new(args: argparse.Namespace) -> int:
    """`ratis-secret new <name> [--format FORMAT] [--length N] [--description DESC]`

    Generate a new Cat-A secret and store it in the vault.
    The secret value is NEVER printed to stdout or stderr.
    """
    result = secret_generate(
        name=args.name,
        format=args.format,
        length=args.length,
        description=args.description,
    )

    if "error" in result:
        print(f"ratis-secret: generate failed: {result.get('detail', result['error'])}", file=sys.stderr)
        return 1

    print(
        f"ratis-secret: generated '{args.name}' v{result['version']} "
        f"format={result['format']} account={result['keychain_account']}"
    )
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    """`ratis-secret list` — print all managed secrets (metadata only)."""
    results = secret_list()
    if not results:
        print("(no secrets found)")
        return 0

    for r in results:
        name = r.get("name", "")
        cat = r.get("category", "?")
        version = r.get("version", 0)
        issued_at = (r.get("issued_at") or "")[:10]
        print(f"{name:<30} cat={cat} v{version} issued={issued_at}")
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    """`ratis-secret use <name> --cmd <shell_cmd>` — inject secret into env and run.

    The secret value is NEVER printed to stdout or stderr.
    It is injected as ``NAME.upper().replace("-","_")`` in the subprocess env.
    """
    result = secret_get(args.name)
    if "error" in result:
        print(
            f"ratis-secret: secret '{args.name}' not found in vault.",
            file=sys.stderr,
        )
        return 1

    value = result["value"]
    env_key = args.name.upper().replace("-", "_")
    env = {**os.environ, env_key: value}

    # Run the command with the injected env. Value never appears on stdout/stderr.
    proc = subprocess.run(args.cmd, env=env, shell=True)  # noqa: S602 — operator-provided cmd
    return proc.returncode


def cmd_revoke(args: argparse.Namespace) -> int:
    """`ratis-secret revoke <lease_id>` — revoke a lease immediately."""
    result = secret_revoke(args.lease_id)
    if "error" in result:
        print(
            f"ratis-secret: lease '{args.lease_id}' not found.",
            file=sys.stderr,
        )
        return 1

    print(f"ratis-secret: lease {result['lease_id']} revoked.")
    return 0


def cmd_audit(_args: argparse.Namespace) -> int:
    """`ratis-secret audit` — tail last 20 entries of the audit chain."""
    chain = _get_audit_chain()
    entries = chain.tail(20)
    if not entries:
        print("(audit log empty)")
        return 0

    for entry in entries:
        ts = entry.get("ts", "")
        action = entry.get("action", "")
        name = entry.get("name", "")
        print(f"{ts}  {action:<10}  {name}")
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    """`ratis-secret rotate <name> [--format FORMAT] [--window N]`

    Rotate a secret: create version N+1 with a new value, keep the old version
    alive for ``window`` minutes before revoking it automatically.
    The new value is NEVER printed to stdout or stderr.
    """
    format_arg: str = getattr(args, "format", "urlsafe")
    window: int = getattr(args, "window", 60)
    result = secret_rotate(name=args.name, format=format_arg, window_minutes=window)

    if "error" in result:
        print(
            f"ratis-secret: rotate failed: {result.get('detail', result['error'])}",
            file=sys.stderr,
        )
        return 1

    print(
        f"ratis-secret: rotated '{args.name}' "
        f"v{result['old_version']} → v{result['new_version']} "
        f"window_expires={result['window_expires_at']}"
    )
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """`ratis-secret import <name> --category C --expires-at YYYY-MM-DD --description ...`

    Prompts for the secret value without echo, then stores it in the vault.
    The value is NEVER printed to stdout or stderr.
    """
    value: str
    if hasattr(args, "_value_override") and args._value_override is not None:
        # Test injection hook — allows tests to bypass interactive prompt.
        value = args._value_override
    else:
        try:
            value = getpass.getpass(f"Secret value for '{args.name}' (no echo): ")
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.", file=sys.stderr)
            return 1

    if not value:
        print("ratis-secret: value cannot be empty.", file=sys.stderr)
        return 1

    result = secret_import(
        name=args.name,
        value=value,
        category=args.category,
        expires_at=args.expires_at,
        description=args.description,
    )

    if "error" in result:
        print(f"ratis-secret: import failed: {result['error']}", file=sys.stderr)
        return 1

    expires_display = result.get("expires_at") or "no expiry"
    print(
        f"ratis-secret: imported '{args.name}' cat={result['category']} v{result['version']} expires={expires_display}"
    )
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse hierarchy for ratis-secret."""
    parser = argparse.ArgumentParser(
        prog="ratis-secret",
        description="ratis-agent-mcp secrets vault operator CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    sub.add_parser("list", help="List all managed secrets (metadata only).").set_defaults(func=cmd_list)

    # new — generate a Cat-A secret
    new_p = sub.add_parser(
        "new",
        help="Generate a new Cat-A secret and store it in the vault (value never printed).",
    )
    new_p.add_argument("name", help="Logical secret name (e.g. 'stripe-test-key').")
    new_p.add_argument(
        "--format",
        default="urlsafe",
        choices=_FORMAT_CHOICES,
        help="Token format (default: urlsafe). Choices: urlsafe hex base64 alphanumeric numeric uuid.",
    )
    new_p.add_argument(
        "--length",
        type=int,
        default=32,
        help="Token length (default: 32). Meaning depends on format — see secret_generate docs.",
    )
    new_p.add_argument(
        "--description",
        default="",
        help="Human-readable description for audit purposes.",
    )
    new_p.set_defaults(func=cmd_new)

    # use
    use_p = sub.add_parser("use", help="Inject a secret into a subprocess environment.")
    use_p.add_argument("name", help="Secret name as stored in the vault.")
    use_p.add_argument(
        "--cmd",
        required=True,
        dest="cmd",
        help="Shell command to run with the secret injected as NAME_UPPER env var.",
    )
    use_p.set_defaults(func=cmd_use)

    # revoke
    revoke_p = sub.add_parser("revoke", help="Revoke a lease immediately.")
    revoke_p.add_argument("lease_id", help="The lease_id to revoke.")
    revoke_p.set_defaults(func=cmd_revoke)

    # audit
    sub.add_parser("audit", help="Tail last 20 entries from the audit chain.").set_defaults(func=cmd_audit)

    # rotate
    rotate_p = sub.add_parser("rotate", help="Rotate a secret: create v+1 with a new value.")
    rotate_p.add_argument("name", help="Secret name to rotate.")
    rotate_p.add_argument(
        "--format",
        default="urlsafe",
        choices=_FORMAT_CHOICES,
        help="Token format for the new version (default: urlsafe).",
    )
    rotate_p.add_argument(
        "--window",
        type=int,
        default=60,
        metavar="MINUTES",
        help="Grace window in minutes before old version is revoked (default: 60).",
    )
    rotate_p.set_defaults(func=cmd_rotate)

    # import (PR 6 — Cat-C secret registration)
    import_p = sub.add_parser(
        "import",
        help="Import an externally-managed secret (Cat-C). Prompts for value without echo.",
    )
    import_p.add_argument("name", help="Logical secret name (e.g. 'stripe-live-key').")
    import_p.add_argument(
        "--category",
        default="C",
        choices=["A", "B", "C"],
        help="Secret category (default: C for external secrets).",
    )
    import_p.add_argument(
        "--expires-at",
        dest="expires_at",
        default=None,
        metavar="YYYY-MM-DD",
        help="Expiry date in ISO8601 format (e.g. '2027-01-01'). Omit if unknown.",
    )
    import_p.add_argument(
        "--description",
        default="",
        help="Human-readable description for audit purposes.",
    )
    import_p.set_defaults(func=cmd_import, _value_override=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Console-script entry. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
