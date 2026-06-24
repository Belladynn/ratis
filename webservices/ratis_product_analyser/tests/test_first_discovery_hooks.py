"""Tests for the V1.1 first-discovery hooks wired into the scan-acceptance
code paths : ``scan_repository.create_scan``, ``barcode_repository.resolve_scan``.

KP-75 / DP-achievements-v1-followups item 1.

The wiring is small (one call per code path to ``claim_first_discovery``)
but critical for the achievement ``exp_unknown_10`` (Pionnier·e) to ever
unlock. These tests guard against silent removal of the hook (regression
guard) and confirm the idempotent / first-wins semantics flow end-to-end
through the repository layer.

The pure CAS semantics of the helper itself are exhaustively covered in
``ratis_core/tests/test_products_first_discovery.py`` ; this file only
verifies the *integration points*.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

# ---------------------------------------------------------------------------
# Helpers — local to keep tests self-contained
# ---------------------------------------------------------------------------


def _read_discoverer(db, ean: str) -> uuid.UUID | None:
    row = db.execute(
        text("SELECT first_discovered_by_user_id FROM products WHERE ean = :ean"),
        {"ean": ean},
    ).first()
    return row[0] if row else None


def _make_user(db, *, is_shadow_banned: bool = False) -> uuid.UUID:
    from ratis_core.identifiers import generate_support_id

    uid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO users (id, email, support_id, account_type, "
            "                  is_shadow_banned) "
            "VALUES (:id, :email, :sid, 'oauth', :banned)"
        ),
        {
            "id": uid,
            "email": f"u-{uid.hex[:8]}@test.com",
            "sid": generate_support_id(),
            "banned": is_shadow_banned,
        },
    )
    db.flush()
    return uid


def _make_product(db, *, ean: str | None = None) -> str:
    ean = ean or str(uuid.uuid4().int)[:13]
    db.execute(
        text("INSERT INTO products (ean, name, source) VALUES (:ean, 'p', 'off')"),
        {"ean": ean},
    )
    db.flush()
    return ean


def _make_store(db) -> uuid.UUID:
    sid = uuid.uuid4()
    db.execute(
        text("INSERT INTO stores (id, name, lat, lng, source, is_disabled) VALUES (:id, 'S', 0, 0, 'osm', false)"),
        {"id": sid},
    )
    db.flush()
    return sid


def _make_receipt(db, *, user_id: uuid.UUID, store_id: uuid.UUID):
    from datetime import date

    from ratis_core.models.scan import Receipt

    r = Receipt(
        id=uuid.uuid4(),
        user_id=user_id,
        store_id=store_id,
        store_status="confirmed",
        purchased_at=date(2026, 5, 10),
    )
    db.add(r)
    db.flush()
    return r


# ---------------------------------------------------------------------------
# create_scan — receipt pipeline v2 path
# ---------------------------------------------------------------------------


class TestCreateScanFirstDiscoveryHook:
    """``create_scan`` calls ``claim_first_discovery`` only on
    accepted/matched scans with a known product_ean.
    """

    def test_first_accepted_scan_claims_discovery(self, db):
        from repositories.scan_repository import create_scan

        ean = _make_product(db)
        user_id = _make_user(db)
        store_id = _make_store(db)
        receipt = _make_receipt(db, user_id=user_id, store_id=store_id)

        create_scan(
            db,
            receipt=receipt,
            scanned_name="Nutella",
            price=399,
            quantity=1.0,
            tva_amount=None,
            product_ean=ean,
            status="accepted",
        )

        assert _read_discoverer(db, ean) == user_id

    def test_unmatched_scan_does_not_claim(self, db):
        from repositories.scan_repository import create_scan

        ean = _make_product(db)
        user_id = _make_user(db)
        store_id = _make_store(db)
        receipt = _make_receipt(db, user_id=user_id, store_id=store_id)

        # status='unmatched' + no product_ean — the typical OCR-without-match path
        create_scan(
            db,
            receipt=receipt,
            scanned_name="Nutella",
            price=399,
            quantity=1.0,
            tva_amount=None,
            product_ean=None,
            status="unmatched",
        )

        assert _read_discoverer(db, ean) is None

    def test_second_user_does_not_overwrite_first(self, db):
        from repositories.scan_repository import create_scan

        ean = _make_product(db)
        first_uid = _make_user(db)
        second_uid = _make_user(db)
        store_id = _make_store(db)

        first_receipt = _make_receipt(db, user_id=first_uid, store_id=store_id)
        create_scan(
            db,
            receipt=first_receipt,
            scanned_name="Nutella",
            price=399,
            quantity=1.0,
            tva_amount=None,
            product_ean=ean,
            status="accepted",
        )

        second_receipt = _make_receipt(db, user_id=second_uid, store_id=store_id)
        create_scan(
            db,
            receipt=second_receipt,
            scanned_name="Nutella",
            price=399,
            quantity=1.0,
            tva_amount=None,
            product_ean=ean,
            status="accepted",
        )

        # First-discovery is permanent.
        assert _read_discoverer(db, ean) == first_uid

    def test_shadow_banned_user_does_not_claim(self, db):
        from repositories.scan_repository import create_scan

        ean = _make_product(db)
        banned = _make_user(db, is_shadow_banned=True)
        store_id = _make_store(db)
        receipt = _make_receipt(db, user_id=banned, store_id=store_id)

        create_scan(
            db,
            receipt=receipt,
            scanned_name="Nutella",
            price=399,
            quantity=1.0,
            tva_amount=None,
            product_ean=ean,
            status="accepted",
        )

        # Banned user is silently rejected by the helper.
        assert _read_discoverer(db, ean) is None


# ---------------------------------------------------------------------------
# resolve_scan — barcode rescue path
# ---------------------------------------------------------------------------


class TestResolveScanFirstDiscoveryHook:
    """``barcode_repository.resolve_scan`` (manual barcode rescue) also
    fires the first-discovery claim.
    """

    def _make_unmatched_scan(self, db, *, user_id: uuid.UUID, store_id: uuid.UUID):
        from datetime import date

        from ratis_core.models.scan import Receipt, Scan

        # CHECK ``receipt_required`` — seed sibling Receipt for the FK.
        r = Receipt(
            id=uuid.uuid4(),
            user_id=user_id,
            store_id=store_id,
            purchased_at=date.today(),
        )
        db.add(r)
        db.flush()
        scan = Scan(
            id=uuid.uuid4(),
            user_id=user_id,
            store_id=store_id,
            receipt_id=r.id,
            scan_type="receipt",
            status="unmatched",
            scanned_name="Unknown line",
            price=399,
            quantity=1.0,
            product_ean=None,
        )
        db.add(scan)
        db.flush()
        return scan

    def test_barcode_rescue_claims_discovery(self, db):
        from repositories.barcode_repository import resolve_scan

        ean = _make_product(db)
        user_id = _make_user(db)
        store_id = _make_store(db)
        scan = self._make_unmatched_scan(db, user_id=user_id, store_id=store_id)

        resolve_scan(db, scan, ean, match_method="barcode")

        assert _read_discoverer(db, ean) == user_id

    def test_barcode_rescue_idempotent_on_existing_attribution(self, db):
        from ratis_core.products import claim_first_discovery
        from repositories.barcode_repository import resolve_scan

        ean = _make_product(db)
        first = _make_user(db)
        second = _make_user(db)
        store_id = _make_store(db)

        # Pre-attribute via the helper directly — emulates a previous scan
        # by ``first`` having already claimed the slot.
        claim_first_discovery(db, ean, first)

        scan = self._make_unmatched_scan(db, user_id=second, store_id=store_id)
        resolve_scan(db, scan, ean, match_method="barcode")

        # second's barcode rescue must NOT overwrite first's attribution.
        assert _read_discoverer(db, ean) == first
