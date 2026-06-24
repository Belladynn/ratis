#!/usr/bin/env bash
# tools/agent-mcp/scripts/smoke.sh
#
# Live end-to-end smoke test for agent-mcp.
#
# Exercises three READ-ONLY tools against the real providers using the
# operator's seeded macOS Keychain entries. The point is to answer "are my
# Keychain credentials still valid against the live providers?" with a single
# command — useful post token-rotation, post Mac-restore, or after CI cache
# weirdness.
#
# Tools exercised :
#   - glitchtip_list_issues  (HTTP GET via httpx, against local GlitchTip)
#   - eas_list_builds        (subprocess shelling out to eas-cli)
#   - r2_list_objects        (boto3 S3-compat against Cloudflare R2)
#
# Pre-flight :
#   1. MCP_AUTH_TOKEN env var (ops or admin role token).
#   2. `agent-mcp` resolvable via `uv run --package ratis-agent-mcp agent-mcp --version`.
#   3. `jq` on PATH (used to parse + shape-check tool responses).
#   4. `agent-mcp keychain check` — required accounts (admin-glitchtip, eas,
#      r2-*) MUST be present. Optional accounts (github, stripe) only emit a
#      warning if missing — they are not in the V0 smoke set.
#
# Override env vars (all optional) :
#   RATIS_SMOKE_GLITCHTIP_PROJECT  GlitchTip project slug (default: ratis-backend)
#   RATIS_SMOKE_EAS_PLATFORM       EAS platform filter    (default: android)
#   RATIS_SMOKE_R2_PREFIX          R2 prefix to list      (default: empty = root)
#
# Exit codes :
#   0 — all 3 tests PASS
#   1 — one or more FAIL
#   2 — pre-flight aborted (token/CLI/jq/keychain)
#
# Timing :
#   Uses Bash 5's `EPOCHREALTIME` when available, falls back to a one-shot
#   `python3` call (Mac mini's stock `/usr/bin/python3` is fine — no deps).
#   The default `/bin/bash` on macOS is 3.2 ; this script tolerates that.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration

REQUIRED_ACCOUNTS=("admin-glitchtip" "eas" "r2-access-key-id" "r2-secret-access-key" "r2-endpoint-url")
OPTIONAL_ACCOUNTS=("github" "stripe")

GLITCHTIP_PROJECT="${RATIS_SMOKE_GLITCHTIP_PROJECT:-ratis-backend}"
EAS_PLATFORM="${RATIS_SMOKE_EAS_PLATFORM:-android}"
R2_PREFIX="${RATIS_SMOKE_R2_PREFIX:-}"

# ---------------------------------------------------------------------------
# Color helpers — only when stdout is a TTY with >=8 colors.

if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    C_GREEN="$(tput setaf 2)"
    C_RED="$(tput setaf 1)"
    C_YELLOW="$(tput setaf 3)"
    C_DIM="$(tput dim)"
    C_BOLD="$(tput bold)"
    C_RESET="$(tput sgr0)"
else
    C_GREEN=""
    C_RED=""
    C_YELLOW=""
    C_DIM=""
    C_BOLD=""
    C_RESET=""
fi

# ---------------------------------------------------------------------------
# Tempfile management — one stderr capture per test, all cleaned up on EXIT.

TMPDIR_SMOKE="$(mktemp -d -t agent-mcp-smoke.XXXXXX)"
cleanup() {
    rm -rf "$TMPDIR_SMOKE"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Portable millisecond clock.
#
# On Bash 5+ : EPOCHREALTIME is "<seconds>.<microseconds>", we drop precision
# to ms with shell arithmetic.
# On Bash 3.2 (macOS default) : EPOCHREALTIME is unset, fall back to a python3
# one-liner which is fast enough for our purpose (~30ms overhead per call).

now_ms() {
    if [ -n "${EPOCHREALTIME:-}" ]; then
        # EPOCHREALTIME = "1714912345.123456" — chop to ms.
        # Replace dot, take first 13 chars (10 sec digits + 3 ms digits).
        local raw="${EPOCHREALTIME/./}"
        printf '%s\n' "${raw:0:13}"
    else
        python3 -c 'import time; print(int(time.time()*1000))'
    fi
}

# ---------------------------------------------------------------------------
# Pre-flight checks. Each failure prints a clear line and exits 2.

preflight_fail() {
    printf '%sagent-mcp smoke: pre-flight failed — %s%s\n' "$C_RED" "$1" "$C_RESET" >&2
    exit 2
}

# 1. MCP_AUTH_TOKEN
if [ -z "${MCP_AUTH_TOKEN:-}" ]; then
    preflight_fail "set MCP_AUTH_TOKEN to your ops or admin token before running smoke."
fi

# 2. agent-mcp resolvable
if ! uv run --package ratis-agent-mcp agent-mcp --version >/dev/null 2>&1; then
    preflight_fail "\`uv run --package ratis-agent-mcp agent-mcp --version\` failed. Run \`uv sync --package ratis-agent-mcp\` first."
fi

# 3. jq present
if ! command -v jq >/dev/null 2>&1; then
    preflight_fail "jq is required for response parsing. Install via \`brew install jq\`."
fi

# 4. Keychain accounts — required must all be present, optional only warn.
KC_OUTPUT_FILE="$TMPDIR_SMOKE/keychain_check.txt"
if ! uv run --package ratis-agent-mcp agent-mcp keychain check >"$KC_OUTPUT_FILE" 2>&1; then
    # Non-zero exit just means SOMETHING is missing — that's normal if the
    # operator hasn't seeded the optional providers. We still need to verify
    # nothing in REQUIRED is missing.
    :
fi

missing_required=()
missing_optional=()
for account in "${REQUIRED_ACCOUNTS[@]}"; do
    # Tolerate trailing whitespace ; match exact account name then "missing".
    if grep -E "^${account}[[:space:]]+missing$" "$KC_OUTPUT_FILE" >/dev/null 2>&1; then
        missing_required+=("$account")
    fi
done
for account in "${OPTIONAL_ACCOUNTS[@]}"; do
    if grep -E "^${account}[[:space:]]+missing$" "$KC_OUTPUT_FILE" >/dev/null 2>&1; then
        missing_optional+=("$account")
    fi
done

if [ "${#missing_optional[@]}" -gt 0 ]; then
    printf '%sagent-mcp smoke: warning — optional Keychain accounts missing (not in smoke set): %s%s\n' \
        "$C_YELLOW" "${missing_optional[*]}" "$C_RESET" >&2
fi

if [ "${#missing_required[@]}" -gt 0 ]; then
    preflight_fail "required Keychain accounts missing: ${missing_required[*]} — run \`agent-mcp keychain set <name>\` for each."
fi

# ---------------------------------------------------------------------------
# Test execution.
#
# Per test we capture :
#   - stdout (parseable JSON on success, indeterminate on failure)
#   - stderr (agent-mcp error envelope on failure)
#   - exit code
#   - elapsed wall time in ms
#
# We then judge PASS / FAIL using :
#   exit_code == 0  AND  stdout parses as JSON  AND  jq -e 'type == "array"'

# Parallel arrays — results to print after all 3 tests have run.
RESULT_NAMES=()
RESULT_STATUSES=()
RESULT_DURATIONS=()
RESULT_DETAILS=()
RESULT_STDERRS=()

PASS_COUNT=0
FAIL_COUNT=0

run_test() {
    local name="$1"
    local args_json="$2"

    local stdout_file="$TMPDIR_SMOKE/${name}.stdout"
    local stderr_file="$TMPDIR_SMOKE/${name}.stderr"

    local started ended duration
    started="$(now_ms)"

    local exit_code=0
    # Capture stdout to file, stderr to file, exit code to var. We DON'T want
    # `set -e` to abort us on a tool failure — we want to log it and continue.
    set +e
    uv run --package ratis-agent-mcp agent-mcp call "$name" "$args_json" \
        >"$stdout_file" 2>"$stderr_file"
    exit_code=$?
    set -e

    ended="$(now_ms)"
    duration=$((ended - started))

    local status detail stderr_snippet
    stderr_snippet=""

    if [ "$exit_code" -ne 0 ]; then
        status="FAIL"
        # Pull the first non-empty stderr line for the summary.
        detail="exit=$exit_code"
        stderr_snippet="$(grep -E '.+' "$stderr_file" | head -1 || true)"
        if [ -n "$stderr_snippet" ]; then
            detail="$stderr_snippet"
        fi
    elif ! jq -e . "$stdout_file" >/dev/null 2>&1; then
        status="FAIL"
        detail="response is not valid JSON"
    elif ! jq -e 'type == "array"' "$stdout_file" >/dev/null 2>&1; then
        status="FAIL"
        local actual_type
        actual_type="$(jq -r 'type' "$stdout_file" 2>/dev/null || echo unknown)"
        detail="response shape mismatch (expected array, got $actual_type)"
    else
        status="PASS"
        local count
        count="$(jq 'length' "$stdout_file")"
        local noun="items"
        case "$name" in
            glitchtip_list_issues) noun="issues" ;;
            eas_list_builds)       noun="builds" ;;
            r2_list_objects)       noun="objects" ;;
        esac
        detail="$count $noun returned"
    fi

    if [ "$status" = "PASS" ]; then
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    RESULT_NAMES+=("$name")
    RESULT_STATUSES+=("$status")
    RESULT_DURATIONS+=("$duration")
    RESULT_DETAILS+=("$detail")
    RESULT_STDERRS+=("$stderr_snippet")
}

# Build R2 args JSON — the prefix may be empty.
R2_ARGS="$(jq -nc --arg p "$R2_PREFIX" '{prefix: $p, limit: 5}')"
GLITCHTIP_ARGS="$(jq -nc --arg proj "$GLITCHTIP_PROJECT" '{project: $proj, limit: 3}')"
EAS_ARGS="$(jq -nc --arg plat "$EAS_PLATFORM" '{platform: $plat, limit: 3}')"

run_test "glitchtip_list_issues" "$GLITCHTIP_ARGS"
run_test "eas_list_builds"       "$EAS_ARGS"
run_test "r2_list_objects"       "$R2_ARGS"

# ---------------------------------------------------------------------------
# Render summary.

# Compute the longest test name for column alignment.
name_width=0
for n in "${RESULT_NAMES[@]}"; do
    if [ "${#n}" -gt "$name_width" ]; then
        name_width="${#n}"
    fi
done

printf '%sagent-mcp smoke test%s\n' "$C_BOLD" "$C_RESET"
printf '====================\n'

i=0
total="${#RESULT_NAMES[@]}"
while [ "$i" -lt "$total" ]; do
    name="${RESULT_NAMES[$i]}"
    status="${RESULT_STATUSES[$i]}"
    duration="${RESULT_DURATIONS[$i]}"
    detail="${RESULT_DETAILS[$i]}"
    stderr_snippet="${RESULT_STDERRS[$i]}"

    if [ "$status" = "PASS" ]; then
        color="$C_GREEN"
    else
        color="$C_RED"
    fi

    # Pad name to common width so columns align.
    padded_name="$(printf '%-*s' "$name_width" "$name")"
    printf '%s  %s[%s]%s  %sms  %s\n' \
        "$padded_name" "$color" "$status" "$C_RESET" \
        "$(printf '%6d' "$duration")" "$detail"

    if [ "$status" = "FAIL" ] && [ -n "$stderr_snippet" ]; then
        # Subtle indent + dim color for the stderr context line.
        printf '%s%*s  └─ stderr: %s%s\n' \
            "$C_DIM" "$name_width" "" "$stderr_snippet" "$C_RESET"
    fi

    i=$((i + 1))
done

printf '====================\n'
printf '%d/%d passed.\n' "$PASS_COUNT" "$total"

if [ "$FAIL_COUNT" -eq 0 ]; then
    exit 0
else
    exit 1
fi
