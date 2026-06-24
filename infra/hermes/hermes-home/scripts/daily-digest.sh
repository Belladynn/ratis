#!/usr/bin/env bash
# Hermes cron requires --script under ~/.hermes/scripts/. Wrapper exec's the .py.
exec python3 /opt/data/scripts/daily-digest.py "$@"
