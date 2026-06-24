"""End-to-end test for the Phase C-2 french qualifier enrichment.

Verifies that ``reconcile_unknown_scans_for_receipt`` emits the
``qualifier='attribute:french'`` ``trigger_action`` call when the
resolved product carries a French-origin signal in OFF
``origins_tags``, and *no* french emit otherwise.

These tests mirror the Phase C-1 organic E2E shape exactly — they
share the fixture machinery (real DB + monkey-patched
``trigger_action``) and only assert on the **french branch** plus the
combined matrix when a product is both organic AND French.

The 3 ``product_identification + attribute:french`` mission templates
remain ``is_active=false`` after this PR — the helper + emit are wired
now so progress accrues silently; the operator flips the missions
live after the ``ratis_batch_origins_backfill`` batch confirms
≥80% coverage in prod (cf PROD_CHECKLIST.md § Missions Phase C-2).
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

# ── Fixtures (mirrors test_organic_enrichment_e2e.py for parity) ──────


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
    origins_tags: list[str] | None = None,
    labels_tags: list[str] | None = None,
) -> Product:
    p = Product(
        ean=ean,
        name=f"Product {ean}",
        source="off",
        origins_tags=origins_tags,
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
    reconciliation flow will attach to the target store. Mirror of the
    helper in ``test_organic_enrichment_e2e``."""
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


def _capture(monkeypatch) -> list[dict]:
    """Replace the rewards client's ``trigger_action`` with an in-memory
    capture list. Returns the list — tests inspect / filter it."""
    captured: list[dict] = []
    monkeypatch.setattr(
        recon_module,
        "trigger_action",
        lambda user_id, action_type, **kwargs: captured.append(
            {"user_id": user_id, "action_type": action_type, **kwargs}
        ),
    )
    return captured


# ── Tests ─────────────────────────────────────────────────────────────


def test_dual_emit_when_product_is_french(db, user, target_store, monkeypatch):
    """French product → vanilla (no qualifier) + french
    (``attribute:french``) emits. Distinct idempotency_keys (suffix
    ``:french``) ensure the ``reward_events`` UNIQUE survives both rows.

    Phase C-3 also fires the ``scan_distinct + store:<uuid>`` event
    (resolved store always seeds a store-distinct emit for a scan
    carrying a product_ean). Filtered out here.
    """
    _make_product(db, ean="3017620499010", origins_tags=["en:france"])
    _make_reconcilable_scan(db, user, product_ean="3017620499010")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured = _capture(monkeypatch)

    result = reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    assert result is not None
    assert result.reconciled_count == 1

    pi_events = [c for c in captured if c["action_type"] == "product_identification"]
    assert len(pi_events) == 2, f"Expected dual-emit (vanilla + french), got {len(pi_events)}: {pi_events!r}"

    user_ids = {c["user_id"] for c in pi_events}
    assert user_ids == {user.id}

    qualifiers = sorted((c.get("qualifier") or "") for c in pi_events)
    assert qualifiers == ["", "attribute:french"]

    # Distinct idempotency_keys — vanilla uses bare scan_id, french
    # appends the ``:french`` suffix. Reward_events UNIQUE survives.
    keys = sorted(c["idempotency_key"] for c in pi_events)
    assert len(set(keys)) == 2
    assert keys[1].endswith(":french"), keys
    assert keys[1] == f"{keys[0]}:french"


def test_triple_emit_when_product_is_organic_and_french(db, user, target_store, monkeypatch):
    """A product carrying BOTH organic labels AND French origin tags
    must produce 3 ``product_identification`` events :
    vanilla + organic + french. Each with a distinct idempotency_key.
    """
    _make_product(
        db,
        ean="3017620499011",
        labels_tags=["en:organic", "fr:bio"],
        origins_tags=["en:france", "en:european-union"],
    )
    _make_reconcilable_scan(db, user, product_ean="3017620499011")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured = _capture(monkeypatch)

    reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    pi_events = [c for c in captured if c["action_type"] == "product_identification"]
    assert len(pi_events) == 3, (
        f"Expected triple-emit (vanilla + organic + french), got {len(pi_events)}: {pi_events!r}"
    )

    qualifiers = sorted((c.get("qualifier") or "") for c in pi_events)
    assert qualifiers == ["", "attribute:french", "attribute:organic"]

    # All 3 idempotency_keys distinct (the reward_events UNIQUE
    # (user_id, reference_type, reference_id) requires this).
    keys = [c["idempotency_key"] for c in pi_events]
    assert len(set(keys)) == 3, keys
    assert any(k.endswith(":organic") for k in keys), keys
    assert any(k.endswith(":french") for k in keys), keys


def test_single_emit_when_product_has_non_french_origins(db, user, target_store, monkeypatch):
    """Non-French origin tags (e.g. ``en:germany``) → vanilla emit only
    on the product_identification action_type. No french emit."""
    _make_product(db, ean="3017620499012", origins_tags=["en:germany", "en:european-union"])
    _make_reconcilable_scan(db, user, product_ean="3017620499012")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured = _capture(monkeypatch)

    reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    pi_events = [c for c in captured if c["action_type"] == "product_identification"]
    assert len(pi_events) == 1, pi_events
    assert pi_events[0].get("qualifier") is None


def test_single_emit_when_product_has_no_origins(db, user, target_store, monkeypatch):
    """``origins_tags=None`` (pre-backfill row) → vanilla emit only on
    the product_identification action_type. No french emit. This is the
    expected steady state until the prod backfill batch runs — and the
    safe default if the OFF API ever drops the field."""
    _make_product(db, ean="3017620499013", origins_tags=None)
    _make_reconcilable_scan(db, user, product_ean="3017620499013")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured = _capture(monkeypatch)

    reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    pi_events = [c for c in captured if c["action_type"] == "product_identification"]
    assert len(pi_events) == 1, pi_events
    assert pi_events[0].get("qualifier") is None


def test_single_emit_when_product_has_empty_origins(db, user, target_store, monkeypatch):
    """Empty array (OFF row without origin metadata, post-backfill) is
    semantically equivalent to None — no french emit."""
    _make_product(db, ean="3017620499014", origins_tags=[])
    _make_reconcilable_scan(db, user, product_ean="3017620499014")
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    captured = _capture(monkeypatch)

    reconcile_unknown_scans_for_receipt(db, receipt)
    db.commit()

    pi_events = [c for c in captured if c["action_type"] == "product_identification"]
    assert len(pi_events) == 1, pi_events
    assert pi_events[0].get("qualifier") is None


def test_french_active_missions_count_after_migration(db):
    """Sanity assertion : Phase C-2 migration does NOT re-flip the 3
    ``product_identification + attribute:french`` templates. They stay
    DISABLED until the operator runs the manual one-row migration AFTER
    the prod backfill batch completes (PROD_CHECKLIST.md).

    Skipped when the test schema does not carry the seeded mission
    templates."""
    has_seeded = db.execute(text("SELECT COUNT(*) FROM missions WHERE qualifier = 'attribute:french'")).scalar()
    if has_seeded == 0:
        pytest.skip(
            "Test schema does not include the seeded missions catalogue ; "
            "this assertion is exercised by tests/test_migrations.py instead."
        )

    french_active = db.execute(
        text("SELECT COUNT(*) FROM missions WHERE qualifier = 'attribute:french'   AND is_active = TRUE")
    ).scalar()
    assert french_active == 0, (
        f"attribute:french missions must stay disabled until operator flip ; got {french_active} active"
    )
