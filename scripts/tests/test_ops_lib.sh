#!/usr/bin/env bash
# scripts/tests/test_ops_lib.sh — basic syntactic + behavioral tests for ops_lib.sh.
#
# These tests do NOT touch the network. They verify :
#   1. The library sources cleanly.
#   2. _resolve_ssh_bin returns "ssh.exe" on simulated git-bash, "ssh" otherwise.
#   3. COMPOSE_PROD is exported with the expected --env-file flag.
#
# Run : ./scripts/tests/test_ops_lib.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$SCRIPT_DIR/../ops_lib.sh"

if [[ ! -f "$LIB" ]]; then
  echo "FAIL: $LIB not found" >&2
  exit 1
fi

PASS=0
FAIL=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    echo "  ok  $label"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  $label"
    echo "        expected : $expected"
    echo "        actual   : $actual"
    FAIL=$((FAIL + 1))
  fi
}

assert_contains() {
  local label="$1" haystack="$2" needle="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    echo "  ok  $label"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  $label (missing : $needle)"
    echo "        haystack : $haystack"
    FAIL=$((FAIL + 1))
  fi
}

# --- 1) source-cleanly test ------------------------------------------------
echo "== sourcing ops_lib.sh =="
# shellcheck source=/dev/null
source "$LIB"
echo "  ok  source"
PASS=$((PASS + 1))

# --- 2) COMPOSE_PROD constant ----------------------------------------------
echo "== COMPOSE_PROD =="
assert_contains "contains -f docker-compose.prod.yml" "$COMPOSE_PROD" "-f docker-compose.prod.yml"
assert_contains "contains --env-file .env.prod"       "$COMPOSE_PROD" "--env-file .env.prod"

# --- 3) _resolve_ssh_bin on Linux/macOS ------------------------------------
echo "== _resolve_ssh_bin (linux/macOS path) =="
# Force the non-Windows branch : unset MSYSTEM in a sub-shell.
out=$(MSYSTEM='' _resolve_ssh_bin)
assert_eq "returns 'ssh' when MSYSTEM is empty" "ssh" "$out"

# --- 4) _resolve_ssh_bin on git-bash with ssh.exe present ------------------
echo "== _resolve_ssh_bin (git-bash path) =="
# Create a fake ssh.exe in a tmp dir, prepend to PATH, set MSYSTEM, call.
TMPDIR_FAKE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_FAKE"' EXIT

# Write a no-op ssh.exe (executable). Bash's `command -v ssh.exe` only checks
# existence + executability, so we don't need real Windows behavior.
cat > "$TMPDIR_FAKE/ssh.exe" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMPDIR_FAKE/ssh.exe"

out=$(PATH="$TMPDIR_FAKE:$PATH" MSYSTEM="MINGW64" _resolve_ssh_bin)
assert_eq "returns 'ssh.exe' when MSYSTEM set + ssh.exe on PATH" "ssh.exe" "$out"

# --- 5) _resolve_ssh_bin on git-bash WITHOUT ssh.exe (graceful fallback) ---
echo "== _resolve_ssh_bin (git-bash, no ssh.exe) =="
# Reset PATH to a minimal prefix that excludes ssh.exe. We can't easily strip
# every dir containing ssh.exe portably, so we just rely on the temp dir
# being clean and the test docs the intended fallback (Linux CI typically
# has no ssh.exe).
if ! command -v ssh.exe >/dev/null 2>&1; then
  out=$(MSYSTEM="MINGW64" _resolve_ssh_bin)
  assert_eq "falls back to 'ssh' when ssh.exe not on PATH" "ssh" "$out"
else
  echo "  skip  (ssh.exe present on this host PATH — can't simulate fallback)"
fi

# --- summary ---------------------------------------------------------------
echo
echo "== summary =="
echo "  pass : $PASS"
echo "  fail : $FAIL"

if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
