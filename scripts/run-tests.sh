#!/usr/bin/env bash
# scripts/run-tests.sh — SA-friendly pytest wrapper with minimal output.
#
# Why : pytest output is verbose by default (50-200 KB on full suite).
# When the Bash tool captures that, token cost explodes for the SA. This
# wrapper boils the result down to 1-15 lines : just the pass/fail
# summary + first 10 failing tests with their assertion line.
#
# Usage :
#   ./scripts/run-tests.sh <target>           # sync, prints PASSED/FAILED summary
#   ./scripts/run-tests.sh <target> --silent  # sync, no stdout, exit code only
#   ./scripts/run-tests.sh <target> --collect # collect-only (no execution)
#
# Examples :
#   ./scripts/run-tests.sh webservices/ratis_product_analyser/tests/test_cluster_blocks.py
#   ./scripts/run-tests.sh webservices/ratis_product_analyser/tests/test_X.py::TestY::test_z
#   ./scripts/run-tests.sh ratis_core/tests/
#
# Async mode : run with the Bash tool's `run_in_background: true` parameter.
# The tool returns immediately with a shell ID ; SA reads output later when ready.
#
# Output format :
#   PASSED <n> in <time>s         (success, ~1 line)
#   FAILED <n>:                   (failure header)
#     <file>::<test> — <error>    (one line per failed test, max 10)
#     E <assertion>               (max 5 assertion details)
#
# Exit codes :
#   0 : all tests passed (or collect-only succeeded)
#   1 : some tests failed
#   2 : usage error (missing target, unknown flag)
#   3 : env error (cannot detect package, uv not found)

set -u

if [ $# -lt 1 ]; then
    echo "Usage: $0 <target_path> [--silent|--collect]" >&2
    echo "  <target_path>  : path to a test file/dir/single test" >&2
    echo "  --silent       : exit code only (no stdout)" >&2
    echo "  --collect      : collect-only, no execution" >&2
    exit 2
fi

TARGET="$1"
SILENT=false
COLLECT=false
case "${2:-}" in
    --silent)  SILENT=true ;;
    --collect) COLLECT=true ;;
    "")        ;;
    *)         echo "Unknown flag: $2" >&2; exit 2 ;;
esac

# Detect package from the target path
detect_package() {
    case "$1" in
        webservices/ratis_product_analyser/*|*/ratis_product_analyser/*) echo "ratis_product_analyser" ;;
        webservices/ratis_auth/*|*/ratis_auth/*) echo "ratis_auth" ;;
        webservices/ratis_list_optimiser/*|*/ratis_list_optimiser/*) echo "ratis_list_optimiser" ;;
        webservices/ratis_rewards/*|*/ratis_rewards/*) echo "ratis_rewards" ;;
        webservices/ratis_notifier/*|*/ratis_notifier/*) echo "ratis_notifier" ;;
        ratis_core/*|*/ratis_core/*) echo "ratis-core" ;;
        batch/ratis_batch_*|*/batch/ratis_batch_*) echo "${1%%/tests/*}" ;;  # less common
        *) echo "" ;;
    esac
}

PKG=$(detect_package "$TARGET")
if [ -z "$PKG" ]; then
    echo "ERROR: cannot detect package from path '$TARGET'" >&2
    echo "  Path must contain one of : ratis_product_analyser, ratis_auth, ratis_list_optimiser, ratis_rewards, ratis_notifier, ratis_core" >&2
    exit 3
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not found in PATH" >&2
    exit 3
fi

LOG=$(mktemp -t pytest-XXXXXX.log)
trap "rm -f $LOG" EXIT

# Run pytest with maximum quiet :
#   -q          : minimal output (no progress dots, just summary line)
#   --tb=line   : 1 line per traceback (instead of full stack)
#   --no-header : skip pytest version banner
# Stderr is merged into stdout so we don't lose error messages.
PYTEST_FLAGS=(-q --tb=line --no-header)
if $COLLECT; then
    PYTEST_FLAGS+=(--collect-only)
fi

uv run --package "$PKG" pytest "$TARGET" "${PYTEST_FLAGS[@]}" >"$LOG" 2>&1
EXIT=$?

if $SILENT; then
    exit "$EXIT"
fi

if [ "$EXIT" -eq 0 ]; then
    if $COLLECT; then
        # Extract collected test count : "X tests collected in Y.YYs"
        SUMMARY=$(grep -oE '[0-9]+ tests? collected' "$LOG" | head -1)
        echo "COLLECTED ${SUMMARY:-(no summary)}"
    else
        # Extract pass summary : "X passed in Y.YYs" (or "X passed, Y skipped in Z.ZZs")
        SUMMARY=$(grep -oE '[0-9]+ passed[^,]*(, [0-9]+ skipped)?[^=]*' "$LOG" | tail -1 | sed 's/=*$//')
        echo "PASSED ${SUMMARY:-(no summary)}"
    fi
else
    # Failure : list failing tests + their assertion line
    FAIL_LINES=$(grep "^FAILED " "$LOG" | head -10)
    FAIL_COUNT=$(echo "$FAIL_LINES" | grep -c "^FAILED " || true)

    if [ "$FAIL_COUNT" -gt 0 ]; then
        echo "FAILED $FAIL_COUNT:"
        echo "$FAIL_LINES" | sed 's/^FAILED /  /'
        # First few assertion error lines (E prefix in pytest -q --tb=line)
        ERRORS=$(grep -E "^E +" "$LOG" | head -5)
        if [ -n "$ERRORS" ]; then
            echo "$ERRORS" | sed 's/^E /    /'
        fi
    else
        # No FAILED lines but non-zero exit : probably setup error or env issue.
        # Print last 20 lines of log so SA can debug.
        echo "EXIT=$EXIT (no FAILED lines, possible setup/env error). Last log lines :"
        tail -20 "$LOG"
    fi
fi

exit "$EXIT"
