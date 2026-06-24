"""GET /rewards/settings/public — whitelisted runtime settings for the client.

Public (no JWT) read endpoint. Returns a flat dict keyed by dotted path
``<section>.<key>``. The whitelist of exposed keys lives in
:mod:`services.public_settings_service` (single source of truth — see
``PUBLIC_SETTINGS_WHITELIST`` for additions).

Cache-Control 5 min — settings change infrequently and the values are
non-personalised, so a CDN / Caddy edge cache is allowed.

Cf F-10 in the V1.1 usage-stats sprint.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from ratis_core.database import get_db
from services.public_settings_service import get_public_settings
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/rewards/settings/public")
def read_public_settings(
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the whitelisted runtime settings.

    No auth — values are public-by-design (display-time constants the
    client needs to render the UI consistently with backend logic).

    The Cache-Control header lets the client / CDN cache for 5 min ;
    the client-side React Query layer also keeps a 5 min stale time so
    multiple components reading the same settings share a single
    network call per session.
    """
    response.headers["Cache-Control"] = "public, max-age=300"
    return get_public_settings(db)
