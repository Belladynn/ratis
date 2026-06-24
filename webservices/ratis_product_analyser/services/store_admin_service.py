"""Admin store-validation service — ARCH_admin_endpoints PR5.

Encapsulates the business logic for the admin store-management endpoints
(force-confirm, bulk validate, soft-disable, manual geocode). The route
module (:mod:`routes.admin.stores`) is a thin shell that translates
service-layer exceptions into HTTPException + composes the response
shape ; all DB writes and audit-log INSERTs happen here.

Design notes :

- Audit rows go into ``store_validation_history`` (the canonical table —
  same one written by the user-suggested confirm flow + the consensus
  batch). The columns are ``from_status`` / ``to_status`` / ``reason`` /
  ``triggered_by`` / ``meta`` (free JSONB). For admin actions, we always
  set ``triggered_by = 'admin:<operator>'``.

- Bulk validation is **atomic** : a single transaction wraps every
  INSERT history row + UPDATE stores. Any failure rolls back the whole
  batch — partial states would corrupt the audit trail. The function
  classifies the input ids into validated / skipped / not_found buckets
  and only mutates the validated bucket.

- ``disable`` does NOT change ``validation_status`` — disabling is
  orthogonal to the validation lifecycle (a confirmed store can be
  disabled because it physically closed ; a pending store can be
  disabled if flagged abusive). We log the transition anyway so the
  audit trail surfaces the action ; ``from_status == to_status`` here.

- ``geocode`` similarly does not change ``validation_status`` — the
  history row exists for traceability of the lat/lng correction.

The service layer raises plain exceptions (NotFound / Conflict from
``ratis_core.exceptions``) — the route translates them. This mirrors
the repository / service / route split (R03).
"""

from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal
from typing import Any

from ratis_core.exceptions import Conflict, NotFound
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------
def _insert_history(
    db: Session,
    *,
    store_id: uuid.UUID,
    from_status: str | None,
    to_status: str,
    reason: str,
    operator: str,
    meta: dict[str, Any] | None = None,
) -> None:
    """INSERT one ``store_validation_history`` row.

    ``meta`` is serialized to JSONB. Pass ``None`` to leave the column
    NULL (the schema allows it). ``triggered_by`` is built as
    ``'admin:<operator>'`` here so the call sites only need to pass
    the bare operator handle.
    """
    db.execute(
        text(
            "INSERT INTO store_validation_history "
            "(store_id, from_status, to_status, reason, triggered_by, meta) "
            "VALUES (:sid, :from_s, :to_s, :reason, :triggered_by, "
            "        CAST(:meta AS jsonb))"
        ),
        {
            "sid": str(store_id),
            "from_s": from_status,
            "to_s": to_status,
            "reason": reason,
            "triggered_by": f"admin:{operator}",
            "meta": json.dumps(meta) if meta is not None else None,
        },
    )


# ---------------------------------------------------------------------------
# validate_store — single force-confirm
# ---------------------------------------------------------------------------
def validate_store(
    db: Session,
    store_id: uuid.UUID,
    operator: str,
) -> dict[str, Any]:
    """Force-confirm one store ; raise if unknown or already confirmed.

    Audit row :
        from_status = current status (e.g. 'pending' / 'suspicious')
        to_status   = 'confirmed'
        reason      = 'admin_validate'

    Raises :
        NotFound("store_not_found")
        Conflict("store_already_confirmed")
    """
    row = db.execute(
        text("SELECT id, validation_status FROM stores WHERE id = :sid"),
        {"sid": str(store_id)},
    ).first()
    if row is None:
        raise NotFound("store_not_found")
    if row.validation_status == "confirmed":
        raise Conflict("store_already_confirmed")

    from_status = row.validation_status

    db.execute(
        text("UPDATE stores SET validation_status = 'confirmed' WHERE id = :sid"),
        {"sid": str(store_id)},
    )
    _insert_history(
        db,
        store_id=store_id,
        from_status=from_status,
        to_status="confirmed",
        reason="admin_validate",
        operator=operator,
    )
    return {
        "id": str(store_id),
        "validation_status": "confirmed",
        "from_status": from_status,
    }


# ---------------------------------------------------------------------------
# validate_stores_bulk — atomic force-confirm of many stores
# ---------------------------------------------------------------------------
def validate_stores_bulk(
    db: Session,
    ids: list[uuid.UUID],
    operator: str,
) -> dict[str, list[str]]:
    """Atomically validate a list of store ids.

    Classifies each id into one of three buckets :
        - validated : was non-confirmed → flipped to confirmed (history logged)
        - skipped_already_confirmed : was already confirmed (no-op, no history)
        - not_found : no row with that id

    All UPDATEs and INSERTs run in the caller's transaction ; the route
    calls ``db.commit()`` at the end. If any single INSERT raises, the
    whole transaction rolls back (route layer). Returns lists of stringified
    UUIDs (JSON-serialisable).
    """
    if not ids:
        return {"validated": [], "skipped_already_confirmed": [], "not_found": []}

    # Single SELECT to classify all ids in one round-trip.
    rows = db.execute(
        text("SELECT id, validation_status FROM stores WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(i) for i in ids]},
    ).fetchall()

    by_id = {str(row.id): row.validation_status for row in rows}

    validated: list[str] = []
    already: list[str] = []
    not_found: list[str] = []

    for sid in ids:
        sid_str = str(sid)
        status = by_id.get(sid_str)
        if status is None:
            not_found.append(sid_str)
        elif status == "confirmed":
            already.append(sid_str)
        else:
            validated.append(sid_str)

    if validated:
        db.execute(
            text("UPDATE stores SET validation_status = 'confirmed' WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": validated},
        )
        for sid_str in validated:
            _insert_history(
                db,
                store_id=uuid.UUID(sid_str),
                from_status=by_id[sid_str],
                to_status="confirmed",
                reason="admin_validate_bulk",
                operator=operator,
            )

    return {
        "validated": validated,
        "skipped_already_confirmed": already,
        "not_found": not_found,
    }


# ---------------------------------------------------------------------------
# disable_store — soft-delete
# ---------------------------------------------------------------------------
def disable_store(
    db: Session,
    store_id: uuid.UUID,
    reason: str,
    operator: str,
) -> dict[str, Any]:
    """Soft-disable a store : set ``is_disabled=true`` + ``disabled_at=now()``.

    Does NOT change ``validation_status``. Audit row records the action
    with ``from_status == to_status`` (the validation lifecycle is
    untouched) and the human-supplied ``reason`` in ``meta``.

    Raises :
        NotFound("store_not_found")
        Conflict("store_already_disabled")
    """
    row = db.execute(
        text("SELECT id, validation_status, is_disabled FROM stores WHERE id = :sid"),
        {"sid": str(store_id)},
    ).first()
    if row is None:
        raise NotFound("store_not_found")
    if row.is_disabled:
        raise Conflict("store_already_disabled")

    db.execute(
        text("UPDATE stores SET is_disabled = true, disabled_at = now() WHERE id = :sid"),
        {"sid": str(store_id)},
    )
    _insert_history(
        db,
        store_id=store_id,
        from_status=row.validation_status,
        to_status=row.validation_status,
        reason="admin_disable",
        operator=operator,
        meta={"disable_reason": reason},
    )
    return {
        "id": str(store_id),
        "is_disabled": True,
        "validation_status": row.validation_status,
    }


# ---------------------------------------------------------------------------
# geocode_store — manual lat/lng correction
# ---------------------------------------------------------------------------
def geocode_store(
    db: Session,
    store_id: uuid.UUID,
    lat: float,
    lng: float,
    operator: str,
) -> dict[str, Any]:
    """Set ``lat`` / ``lng`` on a store (typically a user-suggested one
    that arrived with placeholder ``0/0`` coords).

    Range validation is done by the Pydantic model in the route layer
    (lat ∈ [-90, 90], lng ∈ [-180, 180]). The service trusts its inputs.

    Raises :
        NotFound("store_not_found")
    """
    row = db.execute(
        text("SELECT id, validation_status, lat, lng FROM stores WHERE id = :sid"),
        {"sid": str(store_id)},
    ).first()
    if row is None:
        raise NotFound("store_not_found")

    db.execute(
        text("UPDATE stores SET lat = :lat, lng = :lng WHERE id = :sid"),
        {
            "lat": Decimal(str(lat)),
            "lng": Decimal(str(lng)),
            "sid": str(store_id),
        },
    )
    _insert_history(
        db,
        store_id=store_id,
        from_status=row.validation_status,
        to_status=row.validation_status,
        reason="admin_geocode",
        operator=operator,
        meta={
            "lat": lat,
            "lng": lng,
            "previous_lat": float(row.lat) if row.lat is not None else None,
            "previous_lng": float(row.lng) if row.lng is not None else None,
        },
    )
    return {
        "id": str(store_id),
        "lat": lat,
        "lng": lng,
        "validation_status": row.validation_status,
    }
