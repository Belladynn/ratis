from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal  # kept for trust_score (NUMERIC) and consensus calculations

import ratis_core.geo as geo
from ratis_core.consensus import compute_trust_score, find_dominant_price
from ratis_core.models.price import PriceConsensus, PriceConsensusHistory, PriceConsensusScans
from ratis_core.models.product import Product
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.store import Store
from ratis_core.products import claim_first_discovery, pick_display_name
from ratis_core.settings import load_settings
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from worker.ocr.normalize import normalize_text

_CONSENSUS_CFG: dict = load_settings()["consensus"]


def get_active_store(db: Session, store_id: uuid.UUID) -> Store | None:
    return db.scalar(select(Store).where(Store.id == store_id, Store.is_disabled.is_(False)))


def get_nearest_store(db: Session, lat: float, lng: float, radius_km: int) -> tuple[uuid.UUID | None, bool]:
    """Return (store_id, unambiguous) — nearest active store within radius_km.

    ``unambiguous`` is True when exactly one store is within the radius, OR when
    the closest store is clearly nearer than the second candidate (>= 2x factor).
    Otherwise False — caller should treat the match as ambiguous (``pending``).

    Proximité résolue via ``ratis_core.geo`` (PostGIS, index GIST).
    """
    matches = geo.nearest_stores(db, lat, lng, k=2, max_radius_km=radius_km)
    if not matches:
        return None, False
    closest = matches[0]
    if len(matches) == 1:
        return closest.store.id, True
    # Non ambigu si le plus proche est au moins 2× plus près que le suivant.
    runner_up = matches[1].distance_km
    unambiguous = runner_up >= closest.distance_km * 2 if closest.distance_km > 0 else True
    return closest.store.id, unambiguous


def check_photo_hash_receipt(db: Session, photo_hash: str) -> bool:
    """Return True if this SHA-256 hash is already stored on a receipt."""
    return db.scalar(select(Receipt.id).where(Receipt.photo_hash == photo_hash)) is not None


def check_photo_hash_scan(db: Session, photo_hash: str) -> bool:
    """Return True if this SHA-256 hash is already stored on a label scan."""
    return (
        db.scalar(
            select(Scan.id).where(
                Scan.photo_hash == photo_hash,
                Scan.scan_type == "electronic_label",
            )
        )
        is not None
    )


def get_receipt_by_idempotency_key(db: Session, *, user_id: uuid.UUID, idempotency_key: uuid.UUID) -> Receipt | None:
    """Return the receipt previously created by this user with this
    client-generated idempotency key, or ``None`` if no replay exists."""
    return db.scalar(
        select(Receipt).where(
            Receipt.user_id == user_id,
            Receipt.idempotency_key == idempotency_key,
        )
    )


def create_receipt(
    db: Session,
    *,
    receipt_id: uuid.UUID,
    user_id: uuid.UUID,
    image_r2_key: str,
    store_id: uuid.UUID | None = None,
    photo_hash: str | None = None,
    idempotency_key: uuid.UUID | None = None,
) -> Receipt:
    """Create a receipt row. ``store_id`` is optional — when absent, the OCR
    worker resolves the store via barcode detection (DA-18) and sets
    ``store_status`` accordingly. Default initial status is ``'unknown'``."""
    receipt = Receipt(
        id=receipt_id,
        store_id=store_id,
        user_id=user_id,
        purchased_at=date(1970, 1, 1),
        image_r2_key=image_r2_key,
        image_uploaded_at=datetime.now(UTC),
        photo_hash=photo_hash,
        idempotency_key=idempotency_key,
        store_status="confirmed" if store_id is not None else "unknown",
    )
    db.add(receipt)
    db.flush()
    return receipt


def get_receipt(db: Session, receipt_id: uuid.UUID) -> Receipt | None:
    return db.get(Receipt, receipt_id)


def handle_barcode_rescan(
    db: Session,
    barcode: str,
    new_receipt_id: uuid.UUID,
) -> uuid.UUID | None:
    """Check if a receipt barcode already exists on another receipt.

    If found (rescan of the same physical ticket):
    - Reject all active scans of the old receipt (``superseded_rescan``)
    - Clear the old receipt's barcode (frees the unique index)

    Returns the old receipt ID if rescan detected, ``None`` otherwise.
    """
    old = db.scalar(
        select(Receipt)
        .where(
            Receipt.receipt_barcode == barcode,
            Receipt.id != new_receipt_id,
        )
        .with_for_update()
    )
    if old is None:
        return None

    # Supersede old receipt's active scans.
    # Active statuses span both v2 (accepted/unmatched/pending) and v3
    # (matched/unresolved). v3's 'rejected' is already terminal — leave
    # those alone so the audit trail (rejected_reason) stays intact.
    db.execute(
        text(
            "UPDATE scans SET status = 'rejected', rejected_reason = 'superseded_rescan' "
            "WHERE receipt_id = :rid "
            "  AND status IN ('accepted', 'unmatched', 'pending', 'matched', 'unresolved')"
        ),
        {"rid": str(old.id)},
    )
    # Free barcode on old receipt so unique index allows the new one
    old.receipt_barcode = None
    db.flush()
    return old.id


def get_receipt_scan_summary(db: Session, receipt_id: uuid.UUID) -> dict:
    """Return scan counts grouped by status for a given receipt."""
    rows = db.execute(
        select(Scan.status, func.count().label("n")).where(Scan.receipt_id == receipt_id).group_by(Scan.status)
    ).all()
    return {row.status: row.n for row in rows}


def create_scan(
    db: Session,
    *,
    receipt: Receipt,
    scanned_name: str,
    price: int,
    quantity: float,
    tva_amount: int | None,
    product_ean: str | None,
    status: str | None = None,
    match_method: str | None = None,
    rejected_reason: str | None = None,
) -> Scan:
    status = status or ("accepted" if product_ean else "unmatched")
    scan = Scan(
        id=uuid.uuid4(),
        store_id=receipt.store_id,
        user_id=receipt.user_id,
        receipt_id=receipt.id,
        scan_type="receipt",
        status=status,
        scanned_name=scanned_name,
        price=price,
        quantity=quantity,
        tva_amount=tva_amount,
        product_ean=product_ean,
        match_method=match_method,
        rejected_reason=rejected_reason,
        image_url=None,  # never stored — RGPD
    )
    db.add(scan)
    db.flush()
    # V1.1 first-discovery attribution (KP-75) — only on accepted/matched
    # scans with a known product_ean, and only for non-banned/deleted users
    # (the helper enforces these gates atomically). No-op on duplicates so
    # safe to call unconditionally.
    if status in ("accepted", "matched") and product_ean and receipt.user_id:
        claim_first_discovery(db, product_ean, receipt.user_id)
    return scan


def finalize_receipt(
    db: Session,
    receipt: Receipt,
    total_amount: int | None,
    purchased_at: date | None = None,
    purchased_at_with_time: datetime | None = None,
    total_lines_detected: int | None = None,
) -> None:
    """Update receipt total_amount, purchased_at, purchased_at_with_time, and
    total_lines_detected after OCR processing."""
    if total_amount is not None:
        receipt.total_amount = total_amount
    if purchased_at is not None:
        receipt.purchased_at = purchased_at
    if purchased_at_with_time is not None:
        receipt.purchased_at_with_time = purchased_at_with_time
    if total_lines_detected is not None:
        receipt.total_lines_detected = total_lines_detected
    db.flush()


def process_pending_items(db: Session, receipt: Receipt) -> list[Scan]:
    """
    Process items stored in receipt.pending_items and create Scan records.

    Precondition: receipt.store_id must not be None — raises ValueError if it is.
    Called after a store is resolved (OSM, user suggestion, or admin).

    Returns: list of created Scan objects (may be empty if pending_items was None/empty).
    Side effect: sets receipt.pending_items = None after processing.
    Does NOT call db.commit() — caller owns the transaction.
    """
    if receipt.store_id is None:
        raise ValueError("process_pending_items called with no store_id")

    if not receipt.pending_items:
        return []

    # Lazy import — repositories used here pull SQLAlchemy models which
    # are heavy at import time, and the legacy consensus-only matcher
    # is only consulted on the rare ``pending_items`` resolution path.
    from repositories.consensus_state import ConsensusState
    from repositories.name_resolution_repository import (
        find_fuzzy_verified_consensus_by_store,
        get_consensus_for_label_by_store,
    )

    created: list[Scan] = []
    for item in receipt.pending_items:
        scanned_name: str = item["scanned_name"]
        price: int = item["price"]
        quantity: float = item["quantity"]

        # Consensus-only resolution (refonte 2026-05-02). Run the OCR
        # cleanup, then check the ledger for a VERIFIED consensus.
        # Bloc B (cross-retailer) shipped retailer-keyed canonicals ;
        # this path uses the transitional ``*_by_store`` wrappers until
        # Bloc C migrates the matcher cascade to retailer-keyed lookups.
        cleaned_label = normalize_text(db, scanned_name)
        consensus = get_consensus_for_label_by_store(db, store_id=receipt.store_id, normalized_label=cleaned_label)
        product_ean: str | None = None
        match_method: str | None = None
        if consensus is not None and consensus.state == ConsensusState.VERIFIED:
            product_ean = consensus.ean
            match_method = "consensus_match"
        else:
            fuzzy = find_fuzzy_verified_consensus_by_store(db, store_id=receipt.store_id, cleaned_label=cleaned_label)
            if fuzzy is not None:
                product_ean = fuzzy.ean
                match_method = "consensus_match"

        scan = create_scan(
            db,
            receipt=receipt,
            scanned_name=scanned_name,
            price=price,
            quantity=quantity,
            tva_amount=None,
            product_ean=product_ean,
            match_method=match_method,
        )
        if product_ean is not None and receipt.store_status == "confirmed":
            upsert_price_consensus(db, scan)

        created.append(scan)

    receipt.pending_items = None

    # Backfill purchased_at if still at sentinel (1970-01-01) — happens when the receipt
    # was processed without a known store (Option A path) and the date was never set.
    # Using today as the resolution date is the best available approximation.
    _SENTINEL_DATE = date(1970, 1, 1)
    if receipt.purchased_at == _SENTINEL_DATE:
        from datetime import datetime as _dt

        receipt.purchased_at = _dt.now(tz=UTC).date()

    return created


def upsert_price_consensus(db: Session, scan: Scan) -> None:
    """
    Update price_consensus for an accepted scan with a known product_ean.

    Per ARCH_consensus:
    - Ticket age check: receipt scans older than ticket_max_age_days are skipped.
    - Min creation gate: consensus only created after 2 concordant scans from 2 distinct users.
    - Price quarantine: price ±30% outside consensus → scan skipped.
    - Freeze: 3 concordant scans in last 24h → frozen_until set; while frozen scans are
      recorded but trust_score is not recalculated.
    - Trust score: weighted ratio over window of 20 most recent scans,
      weight = max(scan_weight_floor, 1.0 - age_days × scan_weight_decay_per_day).
    """
    if scan.product_ean is None or scan.status != "accepted":
        return

    cfg = _CONSENSUS_CFG
    now = datetime.now(UTC)

    # 1. Ticket age check (receipt scans only)
    if scan.receipt_id is not None:
        receipt = db.get(Receipt, scan.receipt_id)
        if receipt and receipt.purchased_at:
            age_days = (now.date() - receipt.purchased_at).days
            if age_days > cfg["ticket_max_age_days"]:
                return

    consensus = db.scalar(
        select(PriceConsensus).where(
            PriceConsensus.store_id == scan.store_id,
            PriceConsensus.product_ean == scan.product_ean,
        )
    )

    if consensus is None:
        _try_create_consensus(db, scan, cfg, now)
        return

    # 2. Price quarantine: skip if price deviates more than ±30% from consensus
    if not _price_in_range(scan.price, consensus.price, cfg["price_quarantine_pct"]):
        return

    # 3. Record scan in price_consensus_scans (always, even when frozen)
    db.execute(
        pg_insert(PriceConsensusScans)
        .values(id=uuid.uuid4(), consensus_id=consensus.id, scan_id=scan.id)
        .on_conflict_do_nothing(index_elements=["consensus_id", "scan_id"])
    )
    db.flush()

    # 4. If frozen → don't recalculate trust_score
    if consensus.frozen_until and consensus.frozen_until > now:
        return

    # 5. Recalculate trust_score and detect price basculement
    window = _get_window_scans(db, consensus.id, cfg["window_size"])
    current_trust_score = compute_trust_score(window, consensus.price, now, cfg)
    dominant_price, dominant_score = find_dominant_price(window, now, cfg)
    # Strict inequality: ties keep the current price (no spurious switch on equal weight)
    basculement = (
        dominant_price is not None and dominant_price != consensus.price and dominant_score > current_trust_score
    )

    if basculement:
        # Archive current consensus to history before switching price
        db.add(
            PriceConsensusHistory(
                id=uuid.uuid4(),
                consensus_id=consensus.id,
                store_id=consensus.store_id,
                product_ean=consensus.product_ean,
                price=consensus.price,
                trust_score=current_trust_score,
                first_seen_at=consensus.first_seen_at,
                last_seen_at=now,
            )
        )
        consensus.price = dominant_price
        consensus.trust_score = dominant_score
        consensus.first_seen_at = now  # end of old consensus = start of new
        consensus.frozen_until = None
    else:
        consensus.trust_score = current_trust_score
        if _should_freeze(window, consensus.price, now, cfg):
            consensus.frozen_until = now + timedelta(hours=cfg["freeze_duration_hours"])

    consensus.last_seen_at = now
    db.flush()


# ============================================================
# Consensus creation
# ============================================================


def _try_create_consensus(db: Session, scan: Scan, cfg: dict, now: datetime) -> None:
    """Create a new consensus if min conditions are met (2 concordant scans, 2 distinct users)."""
    cutoff = now - timedelta(days=cfg["ticket_max_age_days"])
    concordant = db.execute(
        select(Scan.id, Scan.user_id, Scan.scanned_at).where(
            Scan.store_id == scan.store_id,
            Scan.product_ean == scan.product_ean,
            Scan.price == scan.price,
            Scan.status == "accepted",
            Scan.scanned_at >= cutoff,
        )
    ).all()

    distinct_users = {row.user_id for row in concordant}
    if len(concordant) < cfg["min_scans_to_create"] or len(distinct_users) < cfg["min_distinct_users"]:
        return

    # Race-safe: INSERT ON CONFLICT DO NOTHING — first worker wins
    created_ats = [row.scanned_at for row in concordant]
    inserted = db.execute(
        pg_insert(PriceConsensus)
        .values(
            id=uuid.uuid4(),
            store_id=scan.store_id,
            product_ean=scan.product_ean,
            price=scan.price,
            trust_score=Decimal("50.00"),
            first_seen_at=min(created_ats),
            last_seen_at=now,
        )
        .on_conflict_do_nothing(index_elements=["store_id", "product_ean"])
        .returning(PriceConsensus.id)
    )
    db.flush()
    if inserted.first() is None:
        return  # another worker beat us — nothing to do

    consensus = db.scalar(
        select(PriceConsensus).where(
            PriceConsensus.store_id == scan.store_id,
            PriceConsensus.product_ean == scan.product_ean,
        )
    )
    if consensus is None:
        return

    # Link all concordant scans in a single bulk INSERT
    db.execute(
        text("""
            INSERT INTO price_consensus_scans (id, consensus_id, scan_id)
            VALUES (:id, :consensus_id, :scan_id)
            ON CONFLICT (consensus_id, scan_id) DO NOTHING
        """),
        [{"id": str(uuid.uuid4()), "consensus_id": str(consensus.id), "scan_id": str(row.id)} for row in concordant],
    )
    db.flush()

    # Compute initial trust_score
    window = _get_window_scans(db, consensus.id, cfg["window_size"])
    consensus.trust_score = compute_trust_score(window, consensus.price, now, cfg)
    db.flush()


# ============================================================
# Trust score helpers
# ============================================================


def _get_window_scans(db: Session, consensus_id: uuid.UUID, window_size: int) -> list[tuple[int, datetime]]:
    """Return the most recent window_size (price, scanned_at) pairs for this consensus."""
    rows = db.execute(
        select(Scan.price, Scan.scanned_at)
        .join(PriceConsensusScans, PriceConsensusScans.scan_id == Scan.id)
        .where(PriceConsensusScans.consensus_id == consensus_id)
        .order_by(Scan.scanned_at.desc())
        .limit(window_size)
    ).all()
    return [(row.price, row.scanned_at) for row in rows]


def _should_freeze(window: list[tuple[int, datetime]], consensus_price: int, now: datetime, cfg: dict) -> bool:
    """Return True if >= freeze_threshold_scans concordant scans occurred in the last 24h."""
    threshold: int = cfg["freeze_threshold_scans"]
    concordant_24h = 0
    for price, scanned_at in window:
        if (now - scanned_at).total_seconds() <= 86400 and price == consensus_price:
            concordant_24h += 1
    return concordant_24h >= threshold


def _price_in_range(new_price: int, consensus_price: int, pct: int) -> bool:
    """Return True if new_price is within ±pct% of consensus_price."""
    if consensus_price == 0:
        return True
    deviation = abs(new_price - consensus_price) / consensus_price * 100
    return deviation <= pct


# ============================================================
# User scan history
# ============================================================


def list_user_history_entries(
    db: Session,
    *,
    user_id: uuid.UUID,
    limit: int,
    cursor_activity_at: datetime | None = None,
    cursor_disambiguator: str | None = None,
) -> list[dict]:
    """Return unified history entries for ``user_id`` — receipts + label groups.

    Each entry is a dict with at least the keys ``type`` (``'receipt'`` or
    ``'label_group'``), ``latest_activity_at`` (``datetime``, UTC), and
    ``disambiguator`` (``receipt_id`` UUID as str for receipts,
    ``"<store_id>|<YYYY-MM-DD>"`` for label groups).

    For ``receipt`` entries the additional keys are:
        receipt_id, store_id, store_name, store_status,
        total_amount_cents, matched_count, unmatched_count, pending_count

    For ``label_group`` entries:
        store_id, date (``date``), store_name, accepted_count

    ``status='rejected'`` scans are always excluded from counts and groupings.
    Groups with 0 accepted scans are dropped (HAVING clause).
    Ordered ``(latest_activity_at, disambiguator)`` DESC. Returns ``limit + 1``
    rows so the caller can detect "has next page".
    """
    # Receipts: latest_activity = MAX(scans.scanned_at) for non-rejected scans.
    # Receipts without any non-rejected scan still appear (counts all zero) —
    # in that case we fall back to receipts.created_at via COALESCE.
    sql = text("""
        WITH receipt_entries AS (
            SELECT
                'receipt'::text AS type,
                r.id AS receipt_id,
                r.store_id AS store_id,
                st.name AS store_name,
                r.store_status AS store_status,
                r.total_amount AS total_amount_cents,
                COALESCE(MAX(s.scanned_at) FILTER (WHERE s.status <> 'rejected'),
                         r.created_at) AS latest_activity_at,
                -- Pipeline_v3 (deployed 2026-04-30) renamed v2 'accepted' →
                -- 'matched' and v2 'unmatched' → 'unresolved'. During the
                -- transition both vocabularies coexist on disk : count both
                -- so the receipt header surfaces the correct totals whether
                -- the row was persisted by v2 or v3. A future cleanup bloc
                -- (drop legacy) will collapse the lists to v3 only.
                COUNT(*) FILTER (WHERE s.status IN ('accepted', 'matched'))
                    AS matched_count,
                COUNT(*) FILTER (WHERE s.status IN ('unmatched', 'unresolved'))
                    AS unmatched_count,
                COUNT(*) FILTER (WHERE s.status = 'pending') AS pending_count,
                r.id::text AS disambiguator
            FROM receipts r
            LEFT JOIN scans s ON s.receipt_id = r.id
            LEFT JOIN stores st ON st.id = r.store_id
            WHERE r.user_id = :user_id
            GROUP BY r.id, st.name
        ),
        label_group_entries AS (
            SELECT
                'label_group'::text AS type,
                NULL::uuid AS receipt_id,
                s.store_id AS store_id,
                st.name AS store_name,
                NULL::text AS store_status,
                NULL::integer AS total_amount_cents,
                MAX(s.scanned_at) AS latest_activity_at,
                -- See receipt_entries CTE — same v2/v3 transition rule.
                COUNT(*) FILTER (WHERE s.status IN ('accepted', 'matched'))
                    AS matched_count,
                0 AS unmatched_count,
                0 AS pending_count,
                (s.store_id::text || '|' ||
                 to_char(DATE(s.scanned_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD')) AS disambiguator
            FROM scans s
            LEFT JOIN stores st ON st.id = s.store_id
            WHERE s.user_id = :user_id
              AND s.scan_type = 'electronic_label'
              AND s.status <> 'rejected'
              AND s.store_id IS NOT NULL
            GROUP BY s.store_id, DATE(s.scanned_at AT TIME ZONE 'UTC'), st.name
            HAVING COUNT(*) FILTER (WHERE s.status IN ('accepted', 'matched')) > 0
        ),
        merged AS (
            SELECT * FROM receipt_entries
            UNION ALL
            SELECT * FROM label_group_entries
        )
        SELECT * FROM merged
        WHERE (CAST(:cursor_activity_at AS timestamptz) IS NULL
               OR (latest_activity_at, disambiguator)
                  < (CAST(:cursor_activity_at AS timestamptz),
                     CAST(:cursor_disambiguator AS text)))
        ORDER BY latest_activity_at DESC, disambiguator DESC
        LIMIT :limit_plus_one
    """)
    rows = (
        db.execute(
            sql,
            {
                "user_id": str(user_id),
                "cursor_activity_at": cursor_activity_at,
                "cursor_disambiguator": cursor_disambiguator,
                "limit_plus_one": limit + 1,
            },
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def get_receipt_items(db: Session, *, receipt_id: uuid.UUID) -> list[dict]:
    """Return non-rejected scans of a receipt with product name, ordered ASC.

    Keys returned per row:
        scan_id, scanned_name, product_name, display_name, product_ean,
        quantity, price_cents, status, match_method, rejected_reason

    ``product_name`` is ``products.name`` (the historical raw OFF best-of) —
    kept for backward compatibility with FE clients that still read it.
    ``display_name`` is the new canonical FE-facing label, composed by
    ``ratis_core.products.pick_display_name`` from the OFF multi-field columns
    (``product_name_fr`` / ``generic_name_fr`` / ``brands_text`` …) — the
    frontend should prefer ``display_name`` and fall back to ``product_name``
    only for older rows. ``rejected_reason`` is populated for v3 ``unresolved``
    rows (legacy v2 ``unmatched`` rows return NULL).
    """
    stmt = (
        select(
            Scan.id.label("scan_id"),
            Scan.scanned_name.label("scanned_name"),
            Product.name.label("product_name"),
            # OFF multi-field enrichment columns — read so the helper can
            # compose ``display_name`` without a second round-trip.
            Product.product_name_fr.label("product_name_fr"),
            Product.generic_name_fr.label("generic_name_fr"),
            Product.brands_text.label("brands_text"),
            Product.quantity_text.label("quantity_text"),
            Scan.product_ean.label("product_ean"),
            Scan.quantity.label("quantity"),
            Scan.price.label("price_cents"),
            Scan.status.label("status"),
            Scan.match_method.label("match_method"),
            Scan.rejected_reason.label("rejected_reason"),
            Scan.scanned_at.label("scanned_at"),
        )
        .select_from(Scan)
        .outerjoin(Product, Scan.product_ean == Product.ean)
        .where(
            Scan.receipt_id == receipt_id,
            Scan.status != "rejected",
        )
        .order_by(Scan.scanned_at.asc(), Scan.id.asc())
    )
    rows = db.execute(stmt).mappings().all()

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        # display_name is None for rows where the scan didn't match a product
        # (no Product row joined — all *_name columns are NULL). Picking on
        # such a row would yield "" — keep None to mirror ``product_name``.
        if d.get("product_name") is None and d.get("product_name_fr") is None:
            d["display_name"] = None
        else:
            d["display_name"] = pick_display_name(d)
        out.append(d)
    return out


def get_label_group_items(
    db: Session,
    *,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    day: date,
) -> list[dict]:
    """Return accepted electronic_label scans for (user, store, date).

    Ordered ``scanned_at`` ASC. Unmatched/rejected excluded (see ARCH).
    Keys : scan_id, product_name, product_ean, price_cents, match_method, scanned_at
    """
    stmt = (
        select(
            Scan.id.label("scan_id"),
            Product.name.label("product_name"),
            Scan.product_ean.label("product_ean"),
            Scan.price.label("price_cents"),
            Scan.match_method.label("match_method"),
            Scan.scanned_at.label("scanned_at"),
        )
        .select_from(Scan)
        .outerjoin(Product, Scan.product_ean == Product.ean)
        .where(
            Scan.user_id == user_id,
            Scan.store_id == store_id,
            Scan.scan_type == "electronic_label",
            Scan.status == "accepted",
            func.date(func.timezone("UTC", Scan.scanned_at)) == day,
        )
        .order_by(Scan.scanned_at.asc(), Scan.id.asc())
    )
    rows = db.execute(stmt).mappings().all()
    return [dict(r) for r in rows]
