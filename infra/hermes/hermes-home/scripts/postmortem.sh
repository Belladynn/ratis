#!/usr/bin/env bash
# Wrapper — Hermes cron requires script under ~/.hermes/scripts/ ; canonical
# postmortem.py lives in ~/.hermes/skills/ratis/claude-code-postmortem/scripts/
# (source-of-truth, versioned). This wrapper just delegates.
exec python3 /opt/data/skills/ratis/claude-code-postmortem/scripts/postmortem.py "$@"
