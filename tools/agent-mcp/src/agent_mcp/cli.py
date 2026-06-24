"""CLI entry points for ratis-agent-mcp.

Sub-commands :

    agent-mcp serve                   # MCP stdio runtime (used by Claude Code)
    agent-mcp init                    # generate admin/ops MCP tokens, write tokens.env
    agent-mcp keychain set <name>     # prompt for secret, store in macOS Keychain
    agent-mcp keychain rm  <name>     # delete a Keychain entry (with confirmation)
    agent-mcp keychain check          # report present/missing for every required provider
    agent-mcp keychain get <name>     # print one provider secret on stdout (raw)
    agent-mcp call <tool> [json_args] # one-shot in-process Dispatcher invocation
    agent-mcp tokens rotate --role admin|ops   # regenerate one MCP role token

Design notes :
    * `init` and `tokens rotate` print each generated token EXACTLY ONCE.
      The user copies them into `~/.claude/mcp.json` ; we don't keep them
      in any clipboard or transient buffer.
    * `keychain set` reads the value via `getpass.getpass()` (no echo) so
      the secret never appears in shell history or terminal scrollback.
    * `keychain get` IS allowed to print the secret on stdout — it's
      strictly equivalent to `security find-generic-password -s ratis-agent-mcp -a <x> -w`
      but stays inside our known-good service name. A stderr warning is
      emitted by default (silenceable with `--no-warn` for piping).
    * `call` reuses the in-process `Dispatcher` so auth / audit / provider
      error wrapping behave EXACTLY like the MCP server. It is NOT a
      shortcut around the security model.
    * `tokens rotate` writes an audit-log line `status="token_rotated"` so
      the rotation is traceable post-hoc (DA-48).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from .audit import AuditLog
from .auth import ADMIN_ENV, OPS_ENV
from .config import audit_log_file, config_dir, ensure_dir, tokens_file
from .errors import KeychainMiss
from .keychain import Keychain

if TYPE_CHECKING:  # pragma: no cover — types only.
    from .server import Dispatcher

REQUIRED_PROVIDER_ACCOUNTS: tuple[str, ...] = (
    "admin-glitchtip",  # was "sentry" pre-DA-47 (Sentry SaaS sunset → GlitchTip self-hosted).
    "eas",
    "github",
    # Notion — REMOVED 2026-05-31. Sunset → GlitchTip self-hosted.
    # Le wrapper CLI `glt` (~/glitchtip/bin/glt) gère l'API en plus du MCP tool.
    "stripe",
    "r2-access-key-id",
    "r2-secret-access-key",
    "r2-endpoint-url",
)
"""Canonical Keychain accounts the MCP needs to be fully operational.

Hardcoded (rather than introspected from the tool modules) to keep the CLI
fast and import-light — `keychain check` must not pay the cost of importing
httpx / boto3 / pydantic just to enumerate names.

Kept in sync with the `KEYCHAIN_ACCOUNT` / `KEYCHAIN_ACCESS_KEY` /
`KEYCHAIN_SECRET_KEY` / `KEYCHAIN_ENDPOINT` constants in `agent_mcp.tools.*`.
"""

TOKEN_BYTES = 32
"""Length of generated MCP role tokens — 32 bytes ≈ 43 chars urlsafe."""


def _generate_token() -> str:
    """Return a cryptographically-random URL-safe MCP role token."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def _write_tokens_env(admin_token: str, ops_token: str, path: Path) -> None:
    """Write `tokens.env` with mode 600. Overwrites any existing file."""
    ensure_dir(path.parent, mode=0o700)
    body = (
        "# ratis-agent-mcp — caller tokens (DA-44).\n"
        "# DO NOT COMMIT. chmod 600 enforced by the CLI.\n"
        f"{ADMIN_ENV}={admin_token}\n"
        f"{OPS_ENV}={ops_token}\n"
    )
    # Open with O_CREAT|O_TRUNC|O_WRONLY at mode 0600 so the file never has
    # looser perms even briefly. `os.open` bypasses the umask.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, body.encode("utf-8"))
    finally:
        os.close(fd)
    # Re-chmod in case the file already existed under a different mode.
    os.chmod(path, 0o600)


def _read_tokens_env(path: Path) -> dict[str, str]:
    """Read existing tokens.env into a dict ; empty dict if absent."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def cmd_init(_args: argparse.Namespace) -> int:
    """`agent-mcp init` — generate the two MCP role tokens.

    Idempotent in the sense that re-running the command always produces a
    fresh pair (it overwrites). The previous tokens are invalidated. Use
    `tokens rotate` if you want to rotate just one role.
    """
    path = tokens_file()
    if path.exists():
        print(
            f"agent-mcp: {path} already exists. Use 'agent-mcp tokens rotate' "
            f"to rotate one role, or remove the file to start over.",
            file=sys.stderr,
        )
        return 1
    admin = _generate_token()
    ops = _generate_token()
    _write_tokens_env(admin, ops, path)
    print(
        f"agent-mcp: wrote {path} (mode 600).\n"
        f"\n"
        f"Copy ONE of the tokens below into ~/.claude/mcp.json env "
        f"`MCP_AUTH_TOKEN` :\n"
        f"\n"
        f"  {ADMIN_ENV} (interactive sessions, full scope) :\n"
        f"    {admin}\n"
        f"\n"
        f"  {OPS_ENV} (Claude SAs, n8n automation, restricted scope) :\n"
        f"    {ops}\n"
        f"\n"
        f"These tokens will NOT be shown again. They are also persisted to "
        f"{path} for the runtime to read."
    )
    return 0


def cmd_keychain_set(args: argparse.Namespace) -> int:
    """Prompt for a secret value (no echo) and store it in the Keychain.

    Round-trip verified : after `set` we immediately `get` and compare. If
    the round-trip fails we report it so the operator notices a Keychain
    permission issue early.
    """
    account = args.provider
    kc = Keychain()
    try:
        existing_check = kc.get(account)
        prompt = (
            f"agent-mcp: a secret already exists for account '{account}'. Type a new value to UPDATE it (no echo) :\n"
        )
        # We don't print the existing value — only acknowledge presence.
        del existing_check
    except KeychainMiss:
        prompt = f"agent-mcp: enter the secret to store under account '{account}' (no echo) :\n"

    value = getpass.getpass(prompt)
    if not value:
        print("agent-mcp: empty value — aborting.", file=sys.stderr)
        return 1

    try:
        kc.set(account, value)
    except (KeychainMiss, ValueError) as exc:
        print(f"agent-mcp: keychain set failed: {exc}", file=sys.stderr)
        return 1

    # Round-trip verification — bypass the cache to confirm the value
    # actually landed on disk.
    kc.invalidate_cache(account)
    try:
        round_trip = kc.get(account)
    except KeychainMiss as exc:
        print(
            f"agent-mcp: round-trip read failed after write: {exc}",
            file=sys.stderr,
        )
        return 2
    if round_trip != value:
        print(
            "agent-mcp: round-trip value mismatch — keychain content does not match what was written.",
            file=sys.stderr,
        )
        return 2

    print(f"agent-mcp: stored secret for account '{account}'.")
    return 0


def cmd_keychain_rm(args: argparse.Namespace) -> int:
    """Delete a Keychain entry after explicit confirmation."""
    account = args.provider
    if not args.yes:
        confirm = input(f"agent-mcp: delete keychain entry for '{account}' ? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("agent-mcp: aborted.", file=sys.stderr)
            return 1
    try:
        Keychain().delete(account)
    except KeychainMiss as exc:
        print(f"agent-mcp: keychain delete failed: {exc}", file=sys.stderr)
        return 1
    print(f"agent-mcp: removed account '{account}'.")
    return 0


def cmd_tokens_rotate(args: argparse.Namespace) -> int:
    """Rotate one MCP role token (admin or ops). Audit-logged."""
    role = args.role
    env_var = ADMIN_ENV if role == "admin" else OPS_ENV
    path = tokens_file()
    existing = _read_tokens_env(path)
    if not existing:
        print(
            "agent-mcp: tokens.env not found — run 'agent-mcp init' first.",
            file=sys.stderr,
        )
        return 1
    new_token = _generate_token()
    existing[env_var] = new_token
    # Ensure both keys are present even if only one was being rotated.
    existing.setdefault(ADMIN_ENV, _generate_token())
    existing.setdefault(OPS_ENV, _generate_token())
    _write_tokens_env(existing[ADMIN_ENV], existing[OPS_ENV], path)

    # Best-effort audit log entry — failure here doesn't fail the command,
    # the operator already saw the token printed.
    try:
        AuditLog(audit_log_file()).write(
            caller="cli",
            tool="tokens_rotate",
            args_redacted={"role": role},
            status="token_rotated",
            latency_ms=0,
            error=None,
        )
    except Exception as exc:
        print(f"agent-mcp: warning — audit write failed: {exc}", file=sys.stderr)

    print(
        f"agent-mcp: rotated {role} token. New value (copy into "
        f"~/.claude/mcp.json env MCP_AUTH_TOKEN) :\n\n  {new_token}\n\n"
        f"This token will NOT be shown again."
    )
    return 0


def cmd_keychain_check(args: argparse.Namespace) -> int:
    """`agent-mcp keychain check` — audit Keychain for every required provider.

    Iterates `REQUIRED_PROVIDER_ACCOUNTS`, reports `present` / `missing` per
    account on stdout, exits 0 if all present, 1 if any missing.

    The `Keychain` instance can be injected via `args._keychain` for tests
    that need to bypass the real `security` CLI (KP-58 — Linux CI has no
    `security` binary so production code must accept a runner).
    """
    kc: Keychain = getattr(args, "_keychain", None) or Keychain()
    rows: list[tuple[str, str]] = []
    any_missing = False
    for account in REQUIRED_PROVIDER_ACCOUNTS:
        try:
            kc.get(account)
            rows.append((account, "present"))
        except KeychainMiss:
            rows.append((account, "missing"))
            any_missing = True

    # Compute alignment width — keep formatting plain so it's grep-friendly.
    name_w = max(len(name) for name, _ in rows)
    print(f"{'account'.ljust(name_w)}  status")
    print(f"{'-' * name_w}  ------")
    for account, status in rows:
        print(f"{account.ljust(name_w)}  {status}")

    return 1 if any_missing else 0


def cmd_keychain_get(args: argparse.Namespace) -> int:
    """`agent-mcp keychain get <provider>` — print one secret on stdout (raw).

    Security posture (intentional, scoped to the operator's own TTY) :
        * No label, no decoration — strictly equivalent to
          `security find-generic-password -s ratis-agent-mcp -a <provider> -w`
          but stays inside the canonical service name so operators don't have
          to remember the keyspec.
        * No trailing newline on stdout — the value can be piped directly
          (`agent-mcp keychain get sentry | curl -H "Authorization: Bearer $(cat)"`).
        * A visible warning is emitted on stderr by default ; `--no-warn`
          silences it for clean piping.

    Exit 0 on success, 1 if the account is absent (clean stderr message
    naming the missing account so the operator knows what to seed).
    """
    account = args.provider
    kc: Keychain = getattr(args, "_keychain", None) or Keychain()
    try:
        value = kc.get(account)
    except KeychainMiss:
        print(
            f"agent-mcp: secret missing for account '{account}' — run `agent-mcp keychain set {account}` to seed it.",
            file=sys.stderr,
        )
        return 1

    # Stdout : raw secret, no trailing newline. We use `sys.stdout.write` +
    # `flush` rather than `print` because `print` always appends `\n`.
    sys.stdout.write(value)
    sys.stdout.flush()

    if not args.no_warn:
        print(
            "agent-mcp: secret printed to stdout — use `--no-warn` to silence.",
            file=sys.stderr,
        )

    return 0


def _build_call_dispatcher() -> Dispatcher:
    """Production factory for the in-process Dispatcher used by `agent-mcp call`.

    Imports tool modules and registers every built-in tool, then returns a
    fresh Dispatcher. Tests override this via `monkeypatch.setattr` so the
    in-process path stays hermetic (no real audit log, no real provider).
    """
    # Lazy imports — keep the CLI's startup path light when `call` is unused.
    from .server import Dispatcher, load_builtin_tools

    load_builtin_tools()
    return Dispatcher()


def cmd_call(args: argparse.Namespace) -> int:
    """`agent-mcp call <tool> [json_args]` — one-shot in-process invocation.

    Reads `MCP_AUTH_TOKEN` from env (same contract as `serve`). Parses
    `[json_args]` (defaults to `{}` if omitted). Dispatches through the same
    `Dispatcher.dispatch()` pipeline as the MCP server so auth, audit and
    provider error handling all behave identically.

    On success, the JSON-encoded tool result is printed on stdout.
    On any non-`ok` status (forbidden_tool, keychain_miss, provider_error,
    tool_not_registered, ...) the function exits 1 with the status + error
    message on stderr.
    """
    raw_args = args.json_args
    if raw_args is None or raw_args == "":
        arguments: dict[str, object] = {}
    else:
        try:
            arguments = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            print(
                f"agent-mcp: invalid JSON args: {exc}",
                file=sys.stderr,
            )
            return 1
        if not isinstance(arguments, dict):
            print(
                f"agent-mcp: JSON args must be an object (got {type(arguments).__name__}).",
                file=sys.stderr,
            )
            return 1

    factory = getattr(args, "_dispatcher_factory", None) or _build_call_dispatcher
    dispatcher = factory()

    presented_token = os.environ.get("MCP_AUTH_TOKEN")
    # Run the async dispatcher synchronously — this command is one-shot.
    import asyncio

    outcome = asyncio.run(
        dispatcher.dispatch(
            tool_name=args.tool_name,
            arguments=arguments,
            presented_token=presented_token,
        )
    )

    if outcome.status != "ok":
        # Surface a clean single-line error envelope on stderr. We include
        # `status` so scripts can grep for `forbidden_tool` etc.
        print(
            f"agent-mcp: {outcome.status}: {outcome.error or '(no error message)'}",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            outcome.result,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )
    return 0


def cmd_serve(_args: argparse.Namespace) -> int:
    """Run the MCP stdio server. Blocks until stdin closes / Ctrl+C."""
    # Import lazily so `agent-mcp init` etc. don't require `mcp` on PATH.
    from .server import run_serve

    run_serve()
    return 0


def cmd_paths(_args: argparse.Namespace) -> int:
    """Diagnostics — print resolved config / state paths and their state."""
    cfg = config_dir()
    tokens = tokens_file()
    audit = audit_log_file()
    print(f"agent-mcp version : {__version__}")
    print(f"config dir        : {cfg}  exists={cfg.exists()}")
    print(f"tokens.env        : {tokens}  exists={tokens.exists()}")
    if tokens.exists():
        st = tokens.stat()
        mode = stat.filemode(st.st_mode)
        print(f"  permissions     : {mode}  ({oct(st.st_mode & 0o777)})")
    print(f"audit log         : {audit}  exists={audit.exists()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse hierarchy. Pure function — easy to unit-test."""
    parser = argparse.ArgumentParser(
        prog="agent-mcp",
        description="ratis-agent-mcp control plane — MCP server + admin CLI.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="Run the MCP stdio server.").set_defaults(func=cmd_serve)
    sub.add_parser(
        "init",
        help="Generate MCP admin + ops tokens (writes ~/.config/...).",
    ).set_defaults(func=cmd_init)
    sub.add_parser("paths", help="Show resolved paths (diagnostics).").set_defaults(func=cmd_paths)

    keychain = sub.add_parser("keychain", help="Manage macOS Keychain entries.")
    kcsub = keychain.add_subparsers(dest="kc_action", required=True)

    kc_set = kcsub.add_parser("set", help="Set/update a provider secret.")
    kc_set.add_argument("provider", help="Account name, e.g. 'admin-glitchtip', 'eas'.")
    kc_set.set_defaults(func=cmd_keychain_set)

    kc_rm = kcsub.add_parser("rm", help="Delete a provider secret.")
    kc_rm.add_argument("provider", help="Account name to remove.")
    kc_rm.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt.")
    kc_rm.set_defaults(func=cmd_keychain_rm)

    kc_check = kcsub.add_parser(
        "check",
        help="Report present/missing for every required provider account.",
    )
    kc_check.set_defaults(func=cmd_keychain_check)

    kc_get = kcsub.add_parser(
        "get",
        help="Print one provider secret on stdout (raw — for piping).",
    )
    kc_get.add_argument("provider", help="Account name to read.")
    kc_get.add_argument(
        "--no-warn",
        action="store_true",
        help="Silence the stderr 'secret printed to stdout' warning.",
    )
    kc_get.set_defaults(func=cmd_keychain_get)

    call = sub.add_parser(
        "call",
        help="One-shot in-process tool dispatch (uses MCP_AUTH_TOKEN env).",
    )
    call.add_argument("tool_name", help="Registered tool name (e.g. glitchtip_list_issues).")
    call.add_argument(
        "json_args",
        nargs="?",
        default=None,
        help="JSON-encoded arguments object (default: {}).",
    )
    call.set_defaults(func=cmd_call)

    tokens = sub.add_parser("tokens", help="Manage MCP role tokens (admin/ops).")
    tksub = tokens.add_subparsers(dest="tk_action", required=True)
    rotate = tksub.add_parser("rotate", help="Regenerate one role token.")
    rotate.add_argument("--role", choices=("admin", "ops"), required=True, help="Which role to rotate.")
    rotate.set_defaults(func=cmd_tokens_rotate)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Console-script entry. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
