"""Liveness probe — GET /health is unauthenticated and returns 200 + JSON.

NT's /notify route is INTERNAL_API_KEY-gated. raw_client has no auth bypass, so
a 200 on /health proves the probe lives OUTSIDE that perimeter — the Docker
HEALTHCHECK presents no credentials.
"""

from __future__ import annotations


def test_health_returns_200_without_auth(raw_client):
    resp = raw_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "ratis_notifier"}
