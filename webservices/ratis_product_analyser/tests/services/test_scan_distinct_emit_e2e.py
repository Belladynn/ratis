"""End-to-end test for the Phase C-3 scan_distinct qualifier emit.

Verifies that ``reconcile_unknown_scans_for_receipt`` fans out
``trigger_action`` calls for the ``scan_distinct`` action_type with the
``category:<slug>`` and ``store:<uuid>`` qualifiers expected by the 8
active scan_distinct missions in the V1 catalogue.

Combined with the Phase C-1 organic dual-emit, a single scan can now
produce up to 4 ``trigger_action`` calls :

  1. Vanilla event (e.g. ``product_identification``, qualifier=None)
  2. Organic event (qualifier=``attribute:organic``) — C-1
  3. scan_distinct + ``category:<tag>``                  — C-3
  4. scan_distinct + ``store:<uuid>``                    — C-3

Each carries a distinct ``idempotency_key`` derived from the scan_id
plus a qualifier-aware suffix, so the
``reward_events UNIQUE(user_id, reference_type, reference_id)`` never
collides.

Strategy
========
Drive ``reconcile_unknown_scans_for_receipt`` end-to-end with a real
DB + real product row carrying ``categories_tags`` and (optionally)
``labels_tags``. Monkey-patch the ``trigger_action`` symbol imported
into the service module so the emits are captured in a list.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import services.reconciliation_service as recon_module
from ratis_core.models.product import Product
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.store import Store
from ratis_core.models.user import User
from services.reconciliation_service import reconcile_unknown_scans_for_receipt

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def target_store(db) -> Store:
    s = Store(
        id=uuid.uuid4(),
        name="Monoprix République",
        retailer="monoprix",
        address="21 place de la République, Paris",
        city="Paris",
        postal_code="75003",
        lat=Decimal("48.8676"),
        lng=Decimal("2.3631"),
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _make_receipt(db, user: User, store: Store) -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id,
        store_status="confirmed",
        purchased_at=datetime.now(UTC).date(),
    )
    db.add(r)
    db.flush()
    return r


def _make_product(
    db,
    *,
    ean: str,
    categories_tags: list[str] | None,
    labels_tags: list[str] | None = None,
) -> Product:
    p = Product(
        ean=ean,
        name=f"Product {ean}",
        source="off",
        categories_tags=categories_tags,
        labels_tags=labels_tags,
    )
    db.add(p)
    db.flush()
    db.commit()
    return p


def _make_reconcilable_scan(
    db,
    user: User,
    *,
    product_ean: str | None,
    scan_type: str = "manual",
    lat: Decimal = Decimal("48.86770"),
    lng: Decimal = Decimal("2.36320"),
) -> Scan:
    scan = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=None,
        store_status="unknown",
        scan_type=scan_type,
        scanned_name=(None if (scan_type == "manual" and product_ean) else "FALLBACK NAME"),
        product_ean=product_ean,
        match_method="barcode_ean" if product_ean else None,
        status="accepted" if product_ean else "pending",
        price=299,
        quantity=1.0,
        user_lat=lat,
        user_lng=lng,
        scanned_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(scan)
    db.flush()
    return scan


def _capture_trigger_action(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    monkeypatch.setattr(
        recon_module,
        "trigger_action",
        lambda user_id, action_type, **kwargs: captured.append(
            {"user_id": user_id, "action_type": action_type, **kwargs}
        ),
    )
    return captured


# ── Tests — the 4-emit matrix ─────────────────────────────────────────


def test_quadruple_emit_when_product_has_categories_and_store(db, user, target_store, monkeypatch):
    """Product with ``categories_tags`` + organic labels + reconciled
    store → 4 ``trigger_action`` calls :
      1. vanilla product_identification
      2. product_identification + attribute:organic   (C-1)
      3. scan_distinct + category:<tag>               (C-3)
      4. scan_distinct + store:<uuid>                 (C-3)

    All four share the same scan_id as base idempotency key, with
    distinct suffixes (``""`` / ``:organic`` / ``:distinct:category:..`` /
    ``:distinct:store:..``) so the rewards UNIQUE constraint holds.
    """
    _make_product(
        db,
        ean="3017620422003",
        categories_tags=["en:dairies", "en:fresh-milks"],
        labels_tags=["en:organic"],
    )
    _make_reconcilable_scan(db, user, product_ean="3017620422003")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured = _capture_trigger_action(monkeypatch)

    result = reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    assert result is not None
    assert result.reconciled_count == 1

    assert len(captured) == 4, (
        f"Expected 4 emits (vanilla + organic + 2 scan_distinct), got {len(captured)} : {captured!r}"
    )

    # Group by (action_type, qualifier).
    pairs = sorted((c["action_type"], c.get("qualifier") or "") for c in captured)
    assert pairs == sorted(
        [
            ("product_identification", ""),
            ("product_identification", "attribute:organic"),
            ("scan_distinct", "category:en:dairies"),
            ("scan_distinct", f"store:{target_store.id}"),
        ]
    )

    # Distinct idempotency_keys — UNIQUE constraint must survive.
    keys = [c["idempotency_key"] for c in captured]
    assert len(set(keys)) == 4, keys


def test_three_emits_when_no_categories(db, user, target_store, monkeypatch):
    """Product without ``categories_tags`` (None) → 3 emits :
    vanilla + organic + scan_distinct.store. No scan_distinct.category."""
    _make_product(
        db,
        ean="3017620422010",
        categories_tags=None,
        labels_tags=["en:organic"],
    )
    _make_reconcilable_scan(db, user, product_ean="3017620422010")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured = _capture_trigger_action(monkeypatch)

    reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    pairs = sorted((c["action_type"], c.get("qualifier") or "") for c in captured)
    assert pairs == sorted(
        [
            ("product_identification", ""),
            ("product_identification", "attribute:organic"),
            ("scan_distinct", f"store:{target_store.id}"),
        ]
    )


def test_three_emits_when_no_organic_signal(db, user, target_store, monkeypatch):
    """Non-organic product with categories + store → 3 emits :
    vanilla + 2 × scan_distinct. No organic emit (Phase C-1 condition
    not met)."""
    _make_product(
        db,
        ean="3017620422027",
        categories_tags=["en:fruits"],
        labels_tags=["en:fair-trade"],
    )
    _make_reconcilable_scan(db, user, product_ean="3017620422027")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured = _capture_trigger_action(monkeypatch)

    reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    pairs = sorted((c["action_type"], c.get("qualifier") or "") for c in captured)
    assert pairs == sorted(
        [
            ("product_identification", ""),
            ("scan_distinct", "category:en:fruits"),
            ("scan_distinct", f"store:{target_store.id}"),
        ]
    )


def test_single_emit_when_scan_has_no_product(db, user, target_store, monkeypatch):
    """Unmatched scan (no product_ean) → 1 emit only : vanilla.

    No product means no labels_tags (no organic) AND no categories_tags
    (no scan_distinct.category). The reconciled store IS available, so
    in principle a ``scan_distinct.store`` could fire — but Phase C-3
    deliberately keeps the store emit tied to a *resolved product* :
    the V1 design intent for scan_distinct is "distinct categories /
    stores **of products scanned**", and an unmatched scan does not
    contribute a tangible product. Revisiting if user-research shows
    unmatched scans should also count store diversity.
    """
    _make_reconcilable_scan(db, user, product_ean=None, scan_type="electronic_label")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured = _capture_trigger_action(monkeypatch)

    reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    assert len(captured) == 1, captured
    assert captured[0]["action_type"] == "label_scan"
    assert captured[0].get("qualifier") is None


def test_scan_distinct_idempotency_keys_carry_qualifier_suffix(db, user, target_store, monkeypatch):
    """The 2 scan_distinct emits carry idempotency keys of shape
    ``<scan_id>:distinct:<qualifier>`` so a Celery retry hitting
    reconciliation a second time dedups server-side via the
    ``reward_events UNIQUE`` constraint."""
    _make_product(
        db,
        ean="3017620422034",
        categories_tags=["en:dairies"],
        labels_tags=None,
    )
    scan = _make_reconcilable_scan(db, user, product_ean="3017620422034")
    scan_id = scan.id
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured = _capture_trigger_action(monkeypatch)

    reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    distinct_events = [c for c in captured if c["action_type"] == "scan_distinct"]
    assert len(distinct_events) == 2

    keys = sorted(e["idempotency_key"] for e in distinct_events)
    assert keys == sorted(
        [
            f"{scan_id}:distinct:category:en:dairies",
            f"{scan_id}:distinct:store:{target_store.id}",
        ]
    )

    # Context payload carries the tracked_value for forensics / admin
    # audit (the rewards service persists this in reward_events.payload).
    for e in distinct_events:
        assert "tracked_value" in e["context"], e
        # tracked_value is the qualifier minus the type-tag prefix
        # (everything after the first colon).
        assert e["context"]["tracked_value"] == e["qualifier"].split(":", 1)[1]


def test_scan_distinct_emits_quantity_one_each(db, user, target_store, monkeypatch):
    """Each scan_distinct emit carries ``quantity=1`` — the runtime
    branch B (distinct missions) ignores quantity and counts array
    length anyway, but the API contract requires quantity ≥ 1."""
    _make_product(
        db,
        ean="3017620422041",
        categories_tags=["en:fruits"],
        labels_tags=None,
    )
    _make_reconcilable_scan(db, user, product_ean="3017620422041")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured = _capture_trigger_action(monkeypatch)

    reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    distinct_events = [c for c in captured if c["action_type"] == "scan_distinct"]
    for e in distinct_events:
        assert e.get("quantity") == 1, e
