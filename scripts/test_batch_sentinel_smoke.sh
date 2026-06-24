#!/usr/bin/env bash
# ============================================================
# test_batch_sentinel_smoke.sh — smoke test n8n batch-sentinel
# ============================================================
# POST un payload fake signé HMAC contre le webhook batch-sentinel
# local (n8n must be up via `docker compose -f infra/itops/docker-compose.yml up -d n8n`)
# et assert HTTP 200.
#
# LOCAL-ONLY : n8n n'est pas câblé en CI (instance auto-hébergée sur le Mac
# mini). Ce script est destiné à être exécuté à la main par l'opérateur après
# `docker compose up -d n8n` côté infra/itops/.
#
# Usage :
#   N8N_BATCH_SENTINEL_WEBHOOK_SECRET=<valeur> ./scripts/test_batch_sentinel_smoke.sh
#   # optionnel :
#   WEBHOOK_URL=http://localhost:5678/webhook/batch-outcome \
#       N8N_BATCH_SENTINEL_WEBHOOK_SECRET=<valeur> \
#       ./scripts/test_batch_sentinel_smoke.sh
#
# Prérequis :
#   - n8n up sur http://localhost:5678 (cf infra/itops/docker-compose.yml)
#   - workflow batch-sentinel.json importé + activé dans n8n UI
#   - env var N8N_BATCH_SENTINEL_WEBHOOK_SECRET définie (même valeur que dans
#     infra/itops/.env)
# ============================================================

set -euo pipefail

WEBHOOK_URL="${WEBHOOK_URL:-http://localhost:5678/webhook/batch-outcome}"
SECRET="${N8N_BATCH_SENTINEL_WEBHOOK_SECRET:-}"

if [ -z "$SECRET" ]; then
  echo "ERROR: N8N_BATCH_SENTINEL_WEBHOOK_SECRET is not set." >&2
  echo "Hint : source infra/itops/.env or export it manually." >&2
  exit 2
fi

NOW_EPOCH="$(date -u +%s)"
COMPLETED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STARTED_AT="$(date -u -r $((NOW_EPOCH - 5)) +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
  || python3 -c "import datetime;print(datetime.datetime.utcfromtimestamp($NOW_EPOCH - 5).strftime('%Y-%m-%dT%H:%M:%SZ'))")"

# Build JSON body sorted/compact to match the composite action format.
BODY=$(python3 -c "
import json
payload = {
    'actor': 'smoke',
    'attempt': 1,
    'commit_sha': 'deadbeef1234567890abcdef',  # pragma: allowlist secret
    'completed_at': '$COMPLETED_AT',
    'conclusion': 'success',
    'duration_s': 5,
    'ref': 'refs/heads/smoke',
    'run_id': '999999',
    'run_url': 'https://example.com/runs/999999',
    'started_at': '$STARTED_AT',
    'workflow_name': 'batch-smoke-test',
}
print(json.dumps(payload, separators=(',', ':'), sort_keys=True), end='')
")

SIG_HEX=$(printf "%s" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | awk '{print $2}')
SIG_HEADER="sha256=${SIG_HEX}"

echo "→ POST ${WEBHOOK_URL}"
echo "  body=${BODY}"
echo "  X-Timestamp=${NOW_EPOCH}"
echo "  X-Signature-256=${SIG_HEADER}"
echo

HTTP_CODE=$(curl -sS -m 10 -o /tmp/batch_sentinel_smoke_response.json -w "%{http_code}" \
  -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -H "X-Signature-256: $SIG_HEADER" \
  -H "X-Timestamp: $NOW_EPOCH" \
  -H "User-Agent: ratis-batch-sentinel-smoke/1.0" \
  --data-binary "$BODY") || HTTP_CODE="curl_failed"

echo "→ Response : HTTP $HTTP_CODE"
if [ -s /tmp/batch_sentinel_smoke_response.json ]; then
  echo "  body : $(cat /tmp/batch_sentinel_smoke_response.json)"
fi

if [ "$HTTP_CODE" != "200" ]; then
  echo
  echo "FAIL : expected HTTP 200, got $HTTP_CODE."
  echo "Vérifie : (a) n8n up sur ${WEBHOOK_URL%/webhook/batch-outcome} (b) workflow batch-sentinel.json importé + activé (c) le secret côté .env matche celui passé à ce script."
  exit 1
fi

echo
echo "OK : webhook batch-sentinel a accepté le payload signé (200)."
