"""
Shared middleware for all Ratis services.

RequestIDMiddleware
-------------------
Reads X-Request-ID from the incoming request header.
If absent OR not a well-formed UUID, generates a UUID v4. Untrusted client
input is never reflected verbatim — that would allow log forgery (newline
injection) or oversized header values.
Always echoes the resolved value back in the response header.

Usage in any FastAPI main.py:
    from ratis_core.middleware import RequestIDMiddleware
    app.add_middleware(RequestIDMiddleware)
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_HEADER = "X-Request-ID"


def _resolve_request_id(raw: str | None) -> str:
    """Return the client value only if it is a well-formed UUID, else a fresh one."""
    if raw:
        try:
            return str(uuid.UUID(raw))
        except ValueError:
            pass
    return str(uuid.uuid4())


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = _resolve_request_id(request.headers.get(_HEADER))
        response = await call_next(request)
        response.headers[_HEADER] = request_id
        return response
