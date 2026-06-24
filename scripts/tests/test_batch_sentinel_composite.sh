#!/usr/bin/env bash
# scripts/tests/test_batch_sentinel_composite.sh — tests for the
# report-batch-outcome composite action's inner bash script.
#
# Covers :
#   1. payload_shape_valid_json  — output is parseable JSON with required keys
#   2. hmac_golden_vector        — fixed secret + fixed body → fixed signature
#   3. timeout_swallow           — webhook on closed port → script still exits 0
#   4. secret_not_in_output      — distinctive secret never appears in stdout/stderr
#
# Run : ./scripts/tests/test_batch_sentinel_composite.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ACTION_YML="$REPO_ROOT/.github/actions/report-batch-outcome/action.yml"

PASS=0
FAIL=0
check() {
  local label="$1" cond="$2"
  if [[ "$cond" == "1" ]]; then echo "  ok  $label"; PASS=$((PASS+1));
  else echo "  FAIL  $label"; FAIL=$((FAIL+1)); fi
}

# Prefer `python3` from the environment ; fall back to `uv run python` so the
# test suite stays runnable both on bare CI runners (apt python3) and on the
# Mac mini dev-host (uv-managed Python 3.12).
if command -v python3 >/dev/null 2>&1 && python3 -c "import yaml" 2>/dev/null; then
  PY="python3"
elif command -v uvx >/dev/null 2>&1; then
  PY="uvx --from pyyaml --quiet python"
else
  echo "FATAL: need python3 with pyyaml OR uvx available" >&2
  exit 2
fi

# Extract the inner bash script from the action.yml `run: |` block.
EXTRACT_DIR="$(mktemp -d)"
trap 'rm -rf "$EXTRACT_DIR"' EXIT
INNER_SCRIPT="$EXTRACT_DIR/inner.sh"

$PY - "$ACTION_YML" "$INNER_SCRIPT" <<'PY'
import sys, yaml
action_path, out_path = sys.argv[1], sys.argv[2]
data = yaml.safe_load(open(action_path))
steps = data["runs"]["steps"]
assert len(steps) == 1, f"expected exactly 1 step, got {len(steps)}"
script = steps[0]["run"]
with open(out_path, "w") as f:
    f.write("#!/usr/bin/env bash\n")
    f.write(script)
PY
chmod +x "$INNER_SCRIPT"

# Common test env (the runtime env vars the composite expects).
SECRET_VALUE="SUPER_SECRET_XYZ_GOLDEN_VECTOR_123"
export SECRET="$SECRET_VALUE"
export CONCLUSION="failure"
export COMMIT_SHA="deadbeef1234567890abcdef"
export WORKFLOW_NAME="batch-test-fixture"
export RUN_ID="999999"
export RUN_ATTEMPT="1"
export SERVER_URL="https://github.com"
export REPO="Belladynn/ratis"
export ACTOR="github-actions[bot]"
export REF="refs/heads/main"
# STEP_START fixed so payload + signature are reproducible.
export STEP_START="1700000000"

# ---------------------------------------------------------------------------
# Test 1 : payload_shape_valid_json
# Run the inner script pointing WEBHOOK_URL at a local Python HTTP sink so we
# can capture the request body sent.
# ---------------------------------------------------------------------------
PORT=$((20000 + RANDOM % 10000))
NC_OUT="$EXTRACT_DIR/nc.out"
SINK_SCRIPT="$EXTRACT_DIR/sink.py"
cat > "$SINK_SCRIPT" <<'PY'
import sys, socket
port, out = int(sys.argv[1]), sys.argv[2]
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port))
s.listen(1)
s.settimeout(15)
conn, _ = s.accept()
conn.settimeout(5)
buf = b""
while True:
    try:
        chunk = conn.recv(4096)
    except socket.timeout:
        break
    if not chunk:
        break
    buf += chunk
    # Once headers + plausible body are in, send response and exit.
    if b"\r\n\r\n" in buf and len(buf) > buf.index(b"\r\n\r\n") + 4 + 50:
        break
try:
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
except OSError:
    pass
conn.close()
open(out, "wb").write(buf)
PY

$PY "$SINK_SCRIPT" "$PORT" "$NC_OUT" &
SINK_PID=$!
sleep 0.3

export WEBHOOK_URL="http://127.0.0.1:${PORT}/webhook/batch-outcome"
"$INNER_SCRIPT" >/dev/null 2>&1 || true
wait "$SINK_PID" 2>/dev/null || true

# Extract body from raw HTTP request (after first \r\n\r\n).
BODY_FILE="$EXTRACT_DIR/body.bin"
$PY - "$NC_OUT" "$BODY_FILE" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
data = open(src, "rb").read()
idx = data.find(b"\r\n\r\n")
body = data[idx + 4:] if idx >= 0 else b""
open(dst, "wb").write(body)
PY

VALID_JSON=$($PY - "$BODY_FILE" <<'PY'
import json, sys
body = open(sys.argv[1], "rb").read().decode("utf-8", "replace")
try:
    j = json.loads(body)
    required = ['workflow_name','conclusion','run_id','run_url','started_at','completed_at','duration_s','commit_sha','attempt','actor','ref']
    missing = [k for k in required if k not in j]
    print('1' if not missing else '0')
except Exception:
    print('0')
PY
)
check "payload_shape_valid_json (all 11 fields present, parseable)" "$VALID_JSON"

# ---------------------------------------------------------------------------
# Test 2 : hmac_golden_vector
# With the fixed env above, the body JSON is deterministic, and so is the
# signature. We re-compute it independently and compare to the X-Signature-256
# header that was POSTed (captured in NC_OUT).
# ---------------------------------------------------------------------------
GOLDEN_SIG=$($PY - "$NC_OUT" <<'PY'
import sys
data = open(sys.argv[1], "rb").read().decode("latin1", "replace")
for line in data.split("\r\n"):
    if line.lower().startswith("x-signature-256:"):
        print(line.split(":", 1)[1].strip())
        break
PY
)

EXPECTED_SIG=$(SECRET_VALUE="$SECRET_VALUE" $PY - "$BODY_FILE" <<'PY'
import hmac, hashlib, os, sys
secret = os.environ["SECRET_VALUE"].encode()
payload = open(sys.argv[1], "rb").read()
sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()
print(f"sha256={sig}")
PY
)

if [ "$GOLDEN_SIG" = "$EXPECTED_SIG" ]; then
  check "hmac_golden_vector (recomputed HMAC matches header)" "1"
else
  echo "    got     : $GOLDEN_SIG"
  echo "    expected: $EXPECTED_SIG"
  check "hmac_golden_vector" "0"
fi

# ---------------------------------------------------------------------------
# Test 3 : timeout_swallow
# Point WEBHOOK_URL at a closed port. curl will fail with connection refused
# OR timeout. The inner script MUST exit 0 anyway (best-effort).
# ---------------------------------------------------------------------------
export WEBHOOK_URL="http://127.0.0.1:1/closed-port"
EXIT_CODE=0
"$INNER_SCRIPT" >/dev/null 2>&1 || EXIT_CODE=$?
if [ "$EXIT_CODE" = "0" ]; then
  check "timeout_swallow (exit 0 on connection refused)" "1"
else
  check "timeout_swallow (exit=$EXIT_CODE)" "0"
fi

# ---------------------------------------------------------------------------
# Test 4 : secret_not_in_output
# Re-run with a distinctive secret, capture combined stdout+stderr, grep.
# ---------------------------------------------------------------------------
export WEBHOOK_URL="http://127.0.0.1:1/closed-port"
COMBINED="$EXTRACT_DIR/combined.out"
"$INNER_SCRIPT" >"$COMBINED" 2>&1 || true
if grep -q "$SECRET_VALUE" "$COMBINED"; then
  echo "    leaked output:"
  sed 's/^/    > /' "$COMBINED"
  check "secret_not_in_output (grep found $SECRET_VALUE in output)" "0"
else
  check "secret_not_in_output (secret never echoed)" "1"
fi

echo
echo "=== $PASS passed / $FAIL failed ==="
[ "$FAIL" = "0" ]
