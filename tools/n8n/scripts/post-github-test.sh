#!/usr/bin/env bash
# Post a GitHub-shaped JSON payload to the n8n github-pr-merged-closer webhook
# with valid HMAC-SHA256 (X-Hub-Signature-256: sha256=hex).
#
# Hashes the RAW file bytes and sends them via curl --data-binary, matching
# what real GitHub sends. n8n's Webhook node has options.rawBody=true so the
# body string is preserved verbatim for HMAC verification.
#
# Usage:
#   tools/n8n/scripts/post-github-test.sh tools/n8n/sample-payloads/github-pr-merged.json
#
# Requires:
#   - $N8N_HOST
#   - $N8N_GITHUB_WEBHOOK_SECRET
#   - openssl + curl

set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <path-to-payload.json>" >&2
    exit 2
fi

PAYLOAD_FILE="$1"
N8N_HOST="${N8N_HOST:?set N8N_HOST=mac-mini.tailXXXX.ts.net}"
SECRET="${N8N_GITHUB_WEBHOOK_SECRET:?set N8N_GITHUB_WEBHOOK_SECRET to match the n8n container}"

SIG="sha256=$(openssl dgst -sha256 -hmac "$SECRET" -hex < "$PAYLOAD_FILE" | awk '{print $2}')"

curl -fsS -w "\nHTTP_STATUS:%{http_code}\n" -X POST "https://${N8N_HOST}/webhook/github-pr-merged" \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: ${SIG}" \
  -H "X-GitHub-Event: pull_request" \
  --data-binary "@${PAYLOAD_FILE}"
