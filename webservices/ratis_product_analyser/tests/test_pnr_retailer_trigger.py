"""Tests for the cross-retailer schema trigger + UNIQUE migration (bloc A).

Exercises the runtime behaviour installed by Alembic migration
``20260502_1900_xretail`` (and mirrored via SQLAlchemy DDL events for
``Base.metadata.create_all`` in tests — see
``ratis_core/models/name_resolution.py``) :

- ``fn_sync_pnr_retailer_id`` BEFORE INSERT → fills ``retailer_id`` from
  ``stores.retailer_id`` when the application omits it.
- BEFORE UPDATE OF ``store_id`` → re-syncs ``retailer_id`` when a row is
  reparented to a different store.
- Application-provided ``retailer_id`` is left untouched (defensive).
- UNIQUE ``(scan_id, source_type, normalized_label)`` accepts one
  ``receipt`` + one ``esl`` row for the same ``(scan, label)`` but
  rejects duplicates within the same source.
- CHECK ``pnr_match_method_check`` accepts ``'esl'`` and
  ``'cross_source_esl_exact'``.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from ratis_core.models.name_resolution import ProductNameResolution
from ratis_core.models.retailer import Retailer
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.store import Store
from ratis_core.models.user import User
from sqlalchemy.exc import IntegrityError

LABEL = "HIPRO A BRE SAV VAN"
EAN = "7610113013175"


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_retailer(db, slug: str) -> Retailer:
    r = Retailer(
        id=uuid.uuid4(),
        canonical_name=slug.capitalize(),
        slug=slug,
        country_code="FR",
        is_verified=True,
    )
    db.add(r)
    db.flush()
    return r


def _make_store(db, *, retailer_id: uuid.UUID | None, name: str = "Store") -> Store:
    s = Store(
        id=uuid.uuid4(),
        name=name,
        retailer_id=retailer_id,
        address="1 rue Test",
        city="Paris",
        postal_code="75001",
        lat=Decimal("48.85"),
        lng=Decimal("2.35"),
        is_disabled=False,
    )
    db.add(s)
    db.flush()
    return s


def _make_user(db, suffix: str = "") -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=f"{uid.hex[:8]}{suffix}@ratis.fr",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    return u


def _make_scan(db, store: Store, user: User) -> Scan:
    # CHECK ``receipt_required`` — receipt scans need a sibling Receipt.
    r = Receipt(
        id=uuid.uuid4(),
        store_id=store.id,
        user_id=user.id,
        purchased_at=date.today(),
    )
    db.add(r)
    db.flush()
    s = Scan(
        id=uuid.uuid4(),
        store_id=store.id,
        user_id=user.id,
        receipt_id=r.id,
        scan_type="receipt",
        status="unresolved",
        rejected_reason="awaiting_user_validation",
        scanned_name=LABEL,
        price=199,
        quantity=Decimal("1"),
    )
    db.add(s)
    db.flush()
    return s


def _add_pnr(
    db,
    *,
    scan: Scan,
    store: Store,
    user: User,
    label: str = LABEL,
    ean: str = EAN,
    method: str = "barcode",
    source_type: str = "receipt",
    retailer_id: uuid.UUID | None = None,
    flush: bool = True,
) -> ProductNameResolution:
    """Add a PNR row to the session.

    By default flushes immediately so trigger-driven tests can `db.refresh()`
    on the returned row. Pass ``flush=False`` for constraint-violation tests
    that must wrap the flush in ``with pytest.raises(IntegrityError)``.
    """
    pnr = ProductNameResolution(
        id=uuid.uuid4(),
        scan_id=scan.id,
        store_id=store.id,
        retailer_id=retailer_id,
        normalized_label=label,
        product_ean=ean,
        user_id=user.id,
        match_method=method,
        source_type=source_type,
    )
    db.add(pnr)
    if flush:
        db.flush()
    return pnr


# ── trigger : INSERT denorm ──────────────────────────────────────────────────


def test_trigger_fills_retailer_id_on_insert_when_omitted(db):
    """INSERT without retailer_id → trigger reads stores.retailer_id."""
    retailer = _make_retailer(db, "intermarche-trig")
    store = _make_store(db, retailer_id=retailer.id, name="IM Lyon")
    user = _make_user(db, "1")
    scan = _make_scan(db, store, user)

    pnr = _add_pnr(db, scan=scan, store=store, user=user, retailer_id=None)
    db.refresh(pnr)
    assert pnr.retailer_id == retailer.id


def test_trigger_does_not_overwrite_explicit_retailer_id(db):
    """Defensive : if app code writes retailer_id explicitly, the trigger
    leaves it alone (IF NEW.retailer_id IS NULL guard)."""
    retailer_a = _make_retailer(db, "carrefour-trig")
    retailer_b = _make_retailer(db, "leclerc-trig")
    store = _make_store(db, retailer_id=retailer_a.id, name="CRF Paris")
    user = _make_user(db, "2")
    scan = _make_scan(db, store, user)

    # Explicit retailer_id=B even though the store points at A — the
    # trigger MUST NOT rewrite this. (No production code path does this,
    # but the defensive guard exists.)
    pnr = _add_pnr(db, scan=scan, store=store, user=user, retailer_id=retailer_b.id)
    db.refresh(pnr)
    assert pnr.retailer_id == retailer_b.id


def test_trigger_leaves_retailer_id_null_when_store_has_none(db):
    """Store without retailer_id (user-suggested, unvalidated) →
    retailer_id stays NULL ; row is excluded from consensus path."""
    store = _make_store(db, retailer_id=None, name="User-suggested store")
    user = _make_user(db, "3")
    scan = _make_scan(db, store, user)

    pnr = _add_pnr(db, scan=scan, store=store, user=user, retailer_id=None)
    db.refresh(pnr)
    assert pnr.retailer_id is None


# ── trigger : UPDATE OF store_id ─────────────────────────────────────────────


def test_trigger_refreshes_retailer_id_on_update_of_store_id(db):
    """UPDATE OF store_id → re-sync from the new store's retailer."""
    retailer_a = _make_retailer(db, "auchan-trig")
    retailer_b = _make_retailer(db, "monoprix-trig")
    store_a = _make_store(db, retailer_id=retailer_a.id, name="Auchan")
    store_b = _make_store(db, retailer_id=retailer_b.id, name="Monoprix")
    user = _make_user(db, "4")
    scan = _make_scan(db, store_a, user)

    pnr = _add_pnr(db, scan=scan, store=store_a, user=user, retailer_id=None)
    db.flush()
    db.refresh(pnr)
    assert pnr.retailer_id == retailer_a.id

    # Reparent the row to store_b. The trigger fires only on UPDATE OF
    # store_id, so we set retailer_id = NULL too (otherwise the IF
    # NEW.retailer_id IS NULL guard skips the resync — by design).
    pnr.store_id = store_b.id
    pnr.retailer_id = None
    db.flush()
    db.refresh(pnr)
    assert pnr.retailer_id == retailer_b.id


# ── UNIQUE (scan_id, source_type, normalized_label) ──────────────────────────


def test_unique_index_accepts_receipt_and_esl_for_same_scan_label(db):
    """One receipt + one ESL row for the same (scan, label) must coexist."""
    retailer = _make_retailer(db, "franprix-trig")
    store = _make_store(db, retailer_id=retailer.id, name="Franprix")
    user = _make_user(db, "5")
    scan = _make_scan(db, store, user)

    _add_pnr(db, scan=scan, store=store, user=user, source_type="receipt")
    _add_pnr(
        db,
        scan=scan,
        store=store,
        user=user,
        source_type="esl",
        method="esl",
    )
    db.flush()
    # No exception → both rows persisted.
    rows = db.query(ProductNameResolution).filter_by(scan_id=scan.id, normalized_label=LABEL).all()
    assert {r.source_type for r in rows} == {"receipt", "esl"}


def test_unique_index_rejects_two_receipts_same_scan_label(db):
    retailer = _make_retailer(db, "casino-trig")
    store = _make_store(db, retailer_id=retailer.id, name="Casino")
    user = _make_user(db, "6")
    scan = _make_scan(db, store, user)

    _add_pnr(db, scan=scan, store=store, user=user, source_type="receipt")
    # Stage the duplicate WITHOUT flushing — let pytest.raises catch the
    # IntegrityError when the flush actually fires inside the block.
    _add_pnr(db, scan=scan, store=store, user=user, source_type="receipt", flush=False)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# ── CHECK match_method ───────────────────────────────────────────────────────


def test_check_accepts_esl_method(db):
    retailer = _make_retailer(db, "lidl-trig")
    store = _make_store(db, retailer_id=retailer.id, name="Lidl")
    user = _make_user(db, "7")
    scan = _make_scan(db, store, user)

    _add_pnr(
        db,
        scan=scan,
        store=store,
        user=user,
        method="esl",
        source_type="esl",
    )
    db.flush()  # no IntegrityError


def test_check_accepts_cross_source_esl_exact_method(db):
    retailer = _make_retailer(db, "biocoop-trig")
    store = _make_store(db, retailer_id=retailer.id, name="Biocoop")
    user = _make_user(db, "8")
    scan = _make_scan(db, store, user)

    _add_pnr(
        db,
        scan=scan,
        store=store,
        user=user,
        method="cross_source_esl_exact",
    )
    db.flush()


def test_check_rejects_unknown_match_method(db):
    retailer = _make_retailer(db, "metro-trig")
    store = _make_store(db, retailer_id=retailer.id, name="Metro")
    user = _make_user(db, "9")
    scan = _make_scan(db, store, user)

    _add_pnr(db, scan=scan, store=store, user=user, method="bogus_method", flush=False)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# ── CHECK source_type ────────────────────────────────────────────────────────


def test_check_rejects_unknown_source_type(db):
    retailer = _make_retailer(db, "naturalia-trig")
    store = _make_store(db, retailer_id=retailer.id, name="Naturalia")
    user = _make_user(db, "10")
    scan = _make_scan(db, store, user)

    _add_pnr(db, scan=scan, store=store, user=user, source_type="bogus", flush=False)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
