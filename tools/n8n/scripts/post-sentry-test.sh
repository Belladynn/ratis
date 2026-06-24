#!/usr/bin/env bash
# Post a Sentry-shaped JSON payload to the n8n webhook with valid HMAC-SHA256.
#
# Hashes the RAW file bytes and sends them via curl --data-binary, matching
# what real Sentry SaaS sends. n8n's Webhook node has options.rawBody=true so
# the body string is preserved verbatim for HMAC verification (no JSON.stringify
# round-trip mismatch).
#
# Usage:
#   tools/n8n/scripts/post-sentry-test.sh tools/n8n/sample-payloads/sentry-fatal-event.json
#
# Requires:
#   - $N8N_HOST env (e.g. mac-mini.tailXXXX.ts.net)
#   - $N8N_SENTRY_WEBHOOK_SECRET env (matches the n8n container env)
#   - openssl + curl on PATH

set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <path-to-payload.json>" >&2
    exit 2
fi

PAYLOAD_FILE="$1"
N8N_HOST="${N8N_HOST:?set N8N_HOST=mac-mini.tailXXXX.ts.net}"
SECRET="${N8N_SENTRY_WEBHOOK_SECRET:?set N8N_SENTRY_WEBHOOK_SECRET to match the n8n container}"

# Hash the raw file bytes — matches what curl --data-binary sends.
SIG=$(openssl dgst -sha256 -hmac "$SECRET" -hex < "$PAYLOAD_FILE" | awk '{print $2}')

curl -fsS -w "\nHTTP_STATUS:%{http_code}\n" -X POST "https://${N8N_HOST}/webhook/sentry-incoming" \
  -H "Content-Type: application/json" \
  -H "Sentry-Hook-Signature: ${SIG}" \
  -H "Sentry-Hook-Resource: event_alert" \
  --data-binary "@${PAYLOAD_FILE}"
