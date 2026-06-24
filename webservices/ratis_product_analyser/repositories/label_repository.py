from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ratis_core.models.scan import LabelSession, Scan
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def create_label_session(
    db: Session,
    *,
    user_id: uuid.UUID,
    store_id: uuid.UUID | None,
    scan_count: int,
) -> LabelSession:
    """Create a label session.

    ``store_id`` is optional: when no store is matched in the user's radius,
    the session is persisted with ``store_id=NULL`` (paired with scans in
    ``store_status='unknown'``). Part B will reconcile these against future
    receipt scans.
    """
    session = LabelSession(
        id=uuid.uuid4(),
        user_id=user_id,
        store_id=store_id,
        scan_count=scan_count,
    )
    db.add(session)
    db.flush()
    return session


def get_label_scan(db: Session, scan_id: uuid.UUID) -> Scan | None:
    return db.get(Scan, scan_id)


def get_label_session(db: Session, session_id: uuid.UUID) -> LabelSession | None:
    return db.get(LabelSession, session_id)


def get_label_session_scan_summary(db: Session, session_id: uuid.UUID) -> dict:
    """Return scan counts grouped by status for a given label session."""
    rows = db.execute(
        select(Scan.status, func.count().label("n")).where(Scan.label_session_id == session_id).group_by(Scan.status)
    ).all()
    return {row.status: row.n for row in rows}


def create_label_scan(
    db: Session,
    *,
    scan_id: uuid.UUID,
    store_id: uuid.UUID | None,
    user_id: uuid.UUID,
    label_r2_key: str,
    label_session_id: uuid.UUID | None = None,
    photo_hash: str | None = None,
    store_status: str = "confirmed",
    user_lat: Decimal | None = None,
    user_lng: Decimal | None = None,
) -> Scan:
    """Insert a label scan.

    When ``store_id`` is None, ``store_status`` must be ``'unknown'`` (the
    CHECK constraint ``ck_scans_store_status_consistency`` enforces this).
    In that case, ``user_lat`` and ``user_lng`` are expected so Part B can
    reconcile the scan against a future receipt matched by geo.
    """
    scan = Scan(
        id=scan_id,
        store_id=store_id,
        user_id=user_id,
        scan_type="electronic_label",
        status="pending",
        price=Decimal("0"),  # placeholder — updated by worker
        quantity=Decimal("1"),
        label_session_id=label_session_id,
        label_r2_key=label_r2_key,
        photo_hash=photo_hash,
        image_url=None,
        label_image_expires_at=datetime.now(UTC) + timedelta(hours=72),
        store_status=store_status,
        user_lat=user_lat,
        user_lng=user_lng,
    )
    db.add(scan)
    db.flush()
    return scan


def update_label_scan_result(
    db: Session,
    scan: Scan,
    *,
    scanned_name: str,
    price: Decimal,
    product_ean: str | None,
    match_method: str | None,
    status: str,
    rejected_reason: str | None = None,
) -> None:
    scan.scanned_name = scanned_name
    scan.price = price
    scan.product_ean = product_ean
    scan.match_method = match_method
    scan.status = status
    scan.rejected_reason = rejected_reason
    db.flush()
