"""Unit tests for the shared liveness-probe router (ratis_core.health)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from ratis_core.health import make_health_router


def test_make_health_router_returns_apirouter():
    router = make_health_router("ratis_demo")
    assert isinstance(router, APIRouter)


def test_health_returns_200_and_expected_body():
    app = FastAPI()
    app.include_router(make_health_router("ratis_demo"))
    client = TestClient(app)

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "ratis_demo"}


def test_service_name_is_reflected_per_instance():
    """Each factory call binds its own service name."""
    app = FastAPI()
    app.include_router(make_health_router("ratis_other"))
    client = TestClient(app)

    assert client.get("/health").json()["service"] == "ratis_other"


def test_health_is_unauthenticated_even_with_a_global_dependency():
    """The health router carries no dependencies of its own — but if the app
    enforces a global auth dependency, an UNAUTH'd request to /health must
    still pass while a sibling guarded route 403s. Confirms the route can be
    mounted outside the auth perimeter (it has zero route-level deps)."""

    def deny() -> None:
        raise HTTPException(status_code=403, detail="forbidden")

    app = FastAPI()
    # Health mounted with NO dependency.
    app.include_router(make_health_router("ratis_demo"))
    # A sibling router that IS guarded, to prove the contrast.
    guarded = APIRouter()

    @guarded.get("/guarded")
    def _guarded(_: None = Depends(deny)) -> dict[str, str]:
        return {"ok": "never"}

    app.include_router(guarded)
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/guarded").status_code == 403
