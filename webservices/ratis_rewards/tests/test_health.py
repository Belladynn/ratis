"""Liveness probe — GET /health is unauthenticated and returns 200 + JSON.

Uses raw_client (no auth bypass) to prove the route answers with NO
credentials : the Docker HEALTHCHECK has no JWT/INTERNAL_API_KEY to present.
"""

from __future__ import annotations


def test_health_returns_200_without_auth(raw_client):
    resp = raw_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "ratis_rewards"}
