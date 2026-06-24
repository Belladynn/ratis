#!/usr/bin/env bash
# Start the Ratis self-hosted GitHub Actions runners.
#
# The runner PAT is NOT kept in a .env file — it lives in the macOS
# Keychain (generic-password item, service `ratis-runner-pat`), mirroring
# the Keychain-backed token pattern of tools/agent-mcp (cf ARCH_agent_mcp.md).
# This wrapper reads the token at launch and hands it to docker compose as
# ACCESS_TOKEN (interpolated by docker-compose.yml). The token therefore
# never touches a file inside the repo.
#
# One-time setup — store the PAT (GitHub classic PAT, scope `repo`) :
#   security add-generic-password -a "$USER" -s ratis-runner-pat -U \
#     -D "GitHub PAT for Ratis CI runners" -w "<paste-PAT>"
# Rotating the PAT later = re-run that command with the new value.
#
# Usage :
#   ./start.sh             → docker compose up -d (16 runners)
#   ./start.sh --build     → extra args are forwarded to `docker compose up`
#
# Stop / logs / status do NOT need the token — use docker compose directly :
#   docker compose down · docker compose logs -f · docker compose ps
set -euo pipefail

cd "$(dirname "$0")"

ACCESS_TOKEN="$(security find-generic-password -s ratis-runner-pat -w 2>/dev/null || true)"
if [[ -z "$ACCESS_TOKEN" ]]; then
  echo "ERROR: Keychain item 'ratis-runner-pat' not found or empty." >&2
  echo "Store the runner PAT (GitHub classic PAT, scope 'repo') first:" >&2
  echo "  security add-generic-password -a \"\$USER\" -s ratis-runner-pat -U \\" >&2
  echo "    -D 'GitHub PAT for Ratis CI runners' -w '<paste-PAT>'" >&2
  exit 1
fi
export ACCESS_TOKEN

exec docker compose up -d "$@"
