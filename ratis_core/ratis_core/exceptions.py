"""Domain exceptions for Ratis services.

Use these instead of fastapi.HTTPException in service layers.
Each main.py registers exception handlers that translate these to HTTP responses.
"""

from __future__ import annotations


class NotFound(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class Conflict(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class Forbidden(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class ServiceUnavailable(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class UnprocessableEntity(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class BadRequest(Exception):
    """HTTP 400 — malformed or semantically invalid request input."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class PaymentRequired(Exception):
    """HTTP 402 — used by Buffer/Burst to signal an action requires
    additional progress / portions before the user can claim. Not
    related to monetary payment in the Ratis economy.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class Gone(Exception):
    """HTTP 410 — the resource is no longer available (e.g. mission
    period extended deadline elapsed).
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)
