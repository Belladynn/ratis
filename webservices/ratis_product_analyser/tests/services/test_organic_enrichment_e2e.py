"""End-to-end test for the Phase C-1 organic qualifier enrichment.

Verifies that ``reconcile_unknown_scans_for_receipt`` emits the
``qualifier='attribute:organic'`` ``trigger_action`` call when the
resolved product is OFF-tagged organic, and *no* organic emit otherwise.

Phase C-3 (2026-05-11) — the reconciliation flow now ALSO emits up to
2 ``scan_distinct`` events per scan (one for the category, one for the
store). These tests focus on the **organic branch** specifically and
filter out the C-3 emits when asserting count/shape — the full 4-emit
matrix is covered by ``test_scan_distinct_emit_e2e.py``.

Strategy
========
Drive ``reconcile_unknown_scans_for_receipt`` end-to-end with a
real DB + real product row carrying ``labels_tags``. Monkey-patch the
``trigger_action`` symbol imported into the service module so the
emits are captured in a list instead of hitting RW.
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
from sqlalchemy import text

# ── Fixtures (mirrors test_reconciliation_service.py to keep parity) ──


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


def _make_product(db, *, ean: str, labels_tags: list[str] | None) -> Product:
    p = Product(
        ean=ean,
        name=f"Product {ean}",
        source="off",
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
    """Build an ``unknown`` scan with a resolved product_ean that the
    reconciliation flow will attach to the target store."""
    scan = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=None,
        store_status="unknown",
        scan_type=scan_type,
        # ``manual_no_scanned_name`` invariant : a manual scan that
        # carries a product_ean must NOT carry a scanned_name. For
        # ``electronic_label`` scans we let the helper synthesize a name
        # to keep the row valid against legacy V2 checks.
        scanned_name=(None if (scan_type == "manual" and product_ean) else "FALLBACK NAME"),
        product_ean=product_ean,
        # ``barcode_ean`` is the v3 enum value for an EAN-resolved match.
        # See ``ck_scans_match_method_v3`` in ratis_core.models.scan.
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


# ── Tests ─────────────────────────────────────────────────────────────


def test_dual_emit_when_product_is_organic(db, user, target_store, monkeypatch):
    """Organic product → vanilla (no qualifier) + organic
    (``attribute:organic``) emits. Distinct idempotency_keys (suffix
    ``:organic``) ensure the rewards ``reward_events`` UNIQUE survives
    both rows.

    Phase C-3 also fires the ``scan_distinct + store:<uuid>`` event
    (the resolved store always seeds a store-distinct emit when the scan
    carries a product_ean). Filtered out here ; ``test_scan_distinct_
    emit_e2e.py`` covers the full 4-emit matrix.
    """
    _make_product(db, ean="3017620422003", labels_tags=["en:organic"])
    _make_reconcilable_scan(db, user, product_ean="3017620422003")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured: list[dict] = []
    monkeypatch.setattr(
        recon_module,
        "trigger_action",
        lambda user_id, action_type, **kwargs: captured.append(
            {"user_id": user_id, "action_type": action_type, **kwargs}
        ),
    )

    result = reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    assert result is not None
    assert result.reconciled_count == 1

    # Filter to the product_identification events (C-1 scope) ; the
    # C-3 scan_distinct emits are out of scope for this test.
    pi_events = [c for c in captured if c["action_type"] == "product_identification"]

    assert len(pi_events) == 2, f"Expected dual-emit (vanilla + organic), got {len(pi_events)}: {pi_events!r}"

    # Both events target the same user
    user_ids = {c["user_id"] for c in pi_events}
    assert user_ids == {user.id}

    # Exactly one vanilla (qualifier=None / absent) and one organic
    qualifiers = sorted((c.get("qualifier") or "") for c in pi_events)
    assert qualifiers == ["", "attribute:organic"]

    # Distinct idempotency_keys — vanilla uses bare scan_id, organic
    # appends the ``:organic`` suffix. The reward_events UNIQUE
    # (user_id, reference_type, reference_id) cannot be violated.
    keys = sorted(c["idempotency_key"] for c in pi_events)
    assert len(set(keys)) == 2
    assert keys[1].endswith(":organic"), keys
    # Vanilla key is a prefix of the organic key
    assert keys[1] == f"{keys[0]}:organic"


def test_single_emit_when_product_has_non_organic_labels(db, user, target_store, monkeypatch):
    """Non-organic labels (e.g. ``en:fair-trade``) → vanilla emit only
    on the product_identification action_type. No organic emit."""
    _make_product(db, ean="3017620422010", labels_tags=["en:fair-trade"])
    _make_reconcilable_scan(db, user, product_ean="3017620422010")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured: list[dict] = []
    monkeypatch.setattr(
        recon_module,
        "trigger_action",
        lambda user_id, action_type, **kwargs: captured.append(
            {"user_id": user_id, "action_type": action_type, **kwargs}
        ),
    )

    reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    pi_events = [c for c in captured if c["action_type"] == "product_identification"]
    assert len(pi_events) == 1, pi_events
    assert pi_events[0].get("qualifier") is None


def test_single_emit_when_product_has_no_labels(db, user, target_store, monkeypatch):
    """``labels_tags=None`` → vanilla emit only on the
    product_identification action_type. No organic emit."""
    _make_product(db, ean="3017620422027", labels_tags=None)
    _make_reconcilable_scan(db, user, product_ean="3017620422027")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured: list[dict] = []
    monkeypatch.setattr(
        recon_module,
        "trigger_action",
        lambda user_id, action_type, **kwargs: captured.append(
            {"user_id": user_id, "action_type": action_type, **kwargs}
        ),
    )

    reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    pi_events = [c for c in captured if c["action_type"] == "product_identification"]
    assert len(pi_events) == 1, pi_events
    assert pi_events[0].get("qualifier") is None


def test_single_emit_when_scan_has_no_product_ean(db, user, target_store, monkeypatch):
    """Scan without a resolved product_ean cannot be assessed for organic.
    Defensive : vanilla emit fires (label_scan), no organic decoration,
    and no scan_distinct emits (Phase C-3 gates these on a resolved
    product — cf ``test_scan_distinct_emit_e2e``).
    """
    _make_reconcilable_scan(db, user, product_ean=None, scan_type="electronic_label")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured: list[dict] = []
    monkeypatch.setattr(
        recon_module,
        "trigger_action",
        lambda user_id, action_type, **kwargs: captured.append(
            {"user_id": user_id, "action_type": action_type, **kwargs}
        ),
    )

    reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    assert len(captured) == 1, captured
    assert captured[0].get("qualifier") is None
    assert captured[0]["action_type"] == "label_scan"


def test_active_organic_missions_count_after_migration(db):
    """Sanity assertion : Phase B + disqual + Phase C-1 migrations
    produce exactly 3 active ``product_identification + attribute:organic``
    templates (daily/easy + weekly/easy + weekly/medium).

    The 3 ``fill_product_field + attribute:organic`` templates remain
    DISABLED — they depend on the C-5 contribute endpoint not yet shipped.
    The 3 ``*+attribute:french`` templates also remain DISABLED — they
    depend on the C-2 origins enrichment.

    Skipped when the test schema does not carry the seeded mission
    templates (Pattern A applies migrations against a fresh DB, so the
    count check is only meaningful in that ground-truth path)."""
    has_seeded = db.execute(text("SELECT COUNT(*) FROM missions WHERE qualifier = 'attribute:organic'")).scalar()
    if has_seeded == 0:
        pytest.skip(
            "Test schema does not include the seeded missions catalogue ; "
            "this assertion is exercised by tests/test_migrations.py instead."
        )

    organic_active = db.execute(
        text(
            "SELECT COUNT(*) FROM missions "
            "WHERE qualifier = 'attribute:organic' "
            "  AND action_type = 'product_identification' "
            "  AND is_active = TRUE"
        )
    ).scalar()
    assert organic_active == 3, organic_active

    fill_active = db.execute(
        text(
            "SELECT COUNT(*) FROM missions "
            "WHERE qualifier = 'attribute:organic' "
            "  AND action_type = 'fill_product_field' "
            "  AND is_active = TRUE"
        )
    ).scalar()
    assert fill_active == 0, fill_active

    french_active = db.execute(
        text("SELECT COUNT(*) FROM missions WHERE qualifier = 'attribute:french'   AND is_active = TRUE")
    ).scalar()
    assert french_active == 0, french_active
