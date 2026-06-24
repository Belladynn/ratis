"""Tests for ratis_core.middleware.RequestIDMiddleware.

A client-supplied X-Request-ID is reflected into logs and the response
header. Reflecting it unvalidated lets a caller inject newlines / control
chars (log forgery) or oversized values. The middleware must only echo a
well-formed UUID; anything else is replaced by a freshly generated one.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from ratis_core.middleware import RequestIDMiddleware

_HEADER = "X-Request-ID"


def _make_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/ping")
    def _ping():
        return {"ok": True}

    return TestClient(app)


def test_absent_header_generates_uuid4():
    resp = _make_client().get("/ping")
    parsed = uuid.UUID(resp.headers[_HEADER])
    assert parsed.version == 4


def test_valid_uuid_is_echoed():
    custom = str(uuid.uuid4())
    resp = _make_client().get("/ping", headers={_HEADER: custom})
    assert resp.headers[_HEADER] == custom


def test_non_uuid_value_is_replaced():
    """A garbage value must NOT be reflected — a fresh UUID is issued."""
    resp = _make_client().get("/ping", headers={_HEADER: "not-a-uuid"})
    out = resp.headers[_HEADER]
    assert out != "not-a-uuid"
    uuid.UUID(out)  # raises if not a valid UUID


def test_injection_payload_is_replaced():
    """Newline / control-char injection (log forgery) must be discarded."""
    resp = _make_client().get("/ping", headers={_HEADER: "abc\r\nX-Evil: injected"})
    out = resp.headers[_HEADER]
    assert "\n" not in out
    assert "\r" not in out
    uuid.UUID(out)


def test_oversized_value_is_replaced():
    """An oversized value must not be reflected verbatim."""
    resp = _make_client().get("/ping", headers={_HEADER: "a" * 500})
    out = resp.headers[_HEADER]
    assert len(out) == len(str(uuid.uuid4()))
    uuid.UUID(out)
