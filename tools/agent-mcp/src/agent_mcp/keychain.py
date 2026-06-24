"""macOS Keychain wrapper around the built-in `security` CLI (DA-43).

The runtime never embeds a provider token in source or in argv. Reads :

```
security find-generic-password -s ratis-agent-mcp -a <provider> -w
```

Writes (CRITICAL — value passed via stdin, never argv) :

```
echo -n <secret> | security add-generic-password -U \
    -s ratis-agent-mcp -a <provider> -w  (reads from stdin when -w lacks an argument
```

Note on macOS quirks :
    `security add-generic-password` traditionally expects `-w <value>` inline,
    but doing that leaks the secret to `ps`. We use the `-w` flag without a
    value AND pipe the secret on stdin — this is supported and is the only
    safe pattern. On non-macOS hosts (e.g. CI Linux runners), every Keychain
    method raises `KeychainMiss` so tests must mock `subprocess.run` (the
    `_runner` injection point makes that trivial).

Cache :
    A 60-second in-memory positive cache reduces `security` invocations
    during a hot dispatch (each tool call may need 1-2 lookups). The cache
    is per-process — when the MCP exits the cache dies with it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from .config import KEYCHAIN_SERVICE
from .errors import KeychainMiss

# Type for the subprocess runner injection — typed loosely so tests can
# substitute either the real `subprocess.run` or a custom callable.
SecurityRunner = Callable[..., "subprocess.CompletedProcess[str]"]

DEFAULT_CACHE_TTL_SEC = 60.0
KEYCHAIN_NOT_FOUND_EXIT = 44  # `errSecItemNotFound` propagated by `security`.


@dataclass(slots=True)
class _CacheEntry:
    value: str
    expires_at: float


class Keychain:
    """Read / write secrets in the login Keychain under a single service name.

    Default service is `ratis-agent-mcp` (DA-43) ; override via constructor
    is permitted only for tests.
    """

    def __init__(
        self,
        *,
        service: str = KEYCHAIN_SERVICE,
        cache_ttl_sec: float = DEFAULT_CACHE_TTL_SEC,
        runner: SecurityRunner | None = None,
    ) -> None:
        self.service = service
        self.cache_ttl_sec = cache_ttl_sec
        self._runner: SecurityRunner = runner or subprocess.run
        self._cache: dict[str, _CacheEntry] = {}

    def get(self, account: str) -> str:
        """Return the secret for `account` ; cached for `cache_ttl_sec`.

        Raises `KeychainMiss` if the entry is absent (exit 44) or unreadable.
        Other non-zero exit codes (locked keychain, denied prompt, OS error)
        are surfaced as `KeychainMiss` with the captured stderr — the caller
        is expected to fall back gracefully.
        """
        cached = self._cache.get(account)
        if cached and cached.expires_at > monotonic():
            return cached.value

        result = self._run(
            [
                "security",
                "find-generic-password",
                "-s",
                self.service,
                "-a",
                account,
                "-w",
            ],
            input_data=None,
        )
        if result.returncode == 0:
            value = result.stdout.rstrip("\n")
            self._cache[account] = _CacheEntry(value=value, expires_at=monotonic() + self.cache_ttl_sec)
            return value
        if result.returncode == KEYCHAIN_NOT_FOUND_EXIT:
            raise KeychainMiss(f"keychain account '{account}' not found in service '{self.service}'")
        # Any other non-zero is unexpected — locked keychain, denied prompt,
        # IO error. Surface as miss so the caller can prompt re-setup.
        raise KeychainMiss(f"keychain read failed (exit={result.returncode}): {(result.stderr or '').strip()}")

    def set(self, account: str, value: str) -> None:
        """Create or update the secret for `account`.

        The `-U` flag tells `security` to update an existing entry rather than
        fail. Critical : we DO NOT pass `value` in argv. The `-w` flag with no
        argument makes `security` read the secret from stdin, which is then
        invisible in `ps`.
        """
        if not value:
            raise ValueError("keychain value must be non-empty")

        result = self._run(
            [
                "security",
                "add-generic-password",
                "-U",  # update if already present
                "-s",
                self.service,
                "-a",
                account,
                "-w",  # no value here — read from stdin
            ],
            input_data=value,
        )
        if result.returncode != 0:
            raise KeychainMiss(f"keychain write failed (exit={result.returncode}): {(result.stderr or '').strip()}")
        # Invalidate any stale cache entry for this account.
        self._cache.pop(account, None)

    def delete(self, account: str) -> None:
        """Remove the secret for `account`. No-op if it doesn't exist.

        Returns silently when `security` reports `errSecItemNotFound` (44),
        so the operation is idempotent — re-running `keychain rm` is safe.
        """
        result = self._run(
            [
                "security",
                "delete-generic-password",
                "-s",
                self.service,
                "-a",
                account,
            ],
            input_data=None,
        )
        if result.returncode not in (0, KEYCHAIN_NOT_FOUND_EXIT):
            raise KeychainMiss(f"keychain delete failed (exit={result.returncode}): {(result.stderr or '').strip()}")
        self._cache.pop(account, None)

    def invalidate_cache(self, account: str | None = None) -> None:
        """Drop one (or all) entries from the in-memory positive cache."""
        if account is None:
            self._cache.clear()
        else:
            self._cache.pop(account, None)

    # --- internal --------------------------------------------------------

    def _run(self, argv: list[str], *, input_data: str | None) -> subprocess.CompletedProcess[Any]:
        """Run the `security` CLI with stdin-piped input when needed.

        Captures both stdout and stderr as text. We never set `shell=True`
        and we never include the secret in argv — this is the security
        boundary of this whole module.
        """
        return self._runner(
            argv,
            input=input_data,
            capture_output=True,
            text=True,
            check=False,
        )
