"""Liveness probe — GET /health is unauthenticated and returns 200 + JSON.

The ``client`` fixture applies no auth (DB override only), so reaching /health
with no Authorization header proves the route lives outside the JWT perimeter :
the Docker HEALTHCHECK has no credentials to present.
"""

from __future__ import annotations


def test_health_returns_200_without_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "ratis_list_optimiser"}
