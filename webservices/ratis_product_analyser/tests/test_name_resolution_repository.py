"""TDD coverage for the read-only name-resolution repository.

Bloc A (NRC) introduced the per-store consensus key. Bloc B (cross-
retailer) swaps the aggregation key from ``store_id`` to
``(retailer_id, source_type)`` — see
``ARCH_cross_retailer_consensus.md`` § "Cascade matcher" for the full
contract.

Functions under test :

- ``get_consensus_for_label(retailer_id, source_type, normalized_label)``
- ``find_fuzzy_verified_consensus(retailer_id, source_type, cleaned_label, …)``
- ``was_ever_verified(retailer_id, source_type, normalized_label)``
- ``list_divergent_labels`` — retailer-keyed
- ``list_unmatched_labels`` — retailer-keyed
- ``resolve_retailer_id`` — covered separately in
  ``test_retailer_resolution.py``

The deprecated ``*_by_store`` shims (transitional API used by Bloc C/D/F
call sites until they migrate) are NOT exhaustively tested here ; they
are pure passthroughs around ``resolve_retailer_id`` + canonical.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from ratis_core.models.name_resolution import ProductNameResolution
from ratis_core.models.retailer import Retailer
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.store import Store
from ratis_core.models.user import User
from repositories.consensus_state import ConsensusState
from sqlalchemy import text

# ============================================================
# Helpers
# ============================================================

LABEL_HIPRO = "HIPRO A BRE SAV VAN"
EAN_HIPRO_VAN = "7610113013175"
EAN_HIPRO_FRA = "7610113013182"
EAN_OTHER = "3017620422003"


def _make_user(db, suffix: str = "") -> User:
    # PG ``email_format`` CHECK requires no whitespace in the local part —
    # callers sometimes pass a label like ``"HIPRO A BRE"`` as suffix, so
    # strip spaces here before composing the email.
    safe_suffix = "".join(ch for ch in suffix if not ch.isspace())
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=f"{uid.hex[:8]}{safe_suffix}@ratis.fr",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    return u


def _make_retailer(db, slug_prefix: str = "intermarche") -> Retailer:
    r = Retailer(
        id=uuid.uuid4(),
        canonical_name=f"{slug_prefix.title()} Test {uuid.uuid4().hex[:6]}",
        slug=f"{slug_prefix}-{uuid.uuid4().hex[:8]}",
        country_code="FR",
    )
    db.add(r)
    db.flush()
    return r


def _make_store(
    db,
    *,
    retailer: Retailer | None,
    name: str = "Store",
    city: str = "Lyon",
) -> Store:
    s = Store(
        id=uuid.uuid4(),
        name=name,
        retailer=name.lower(),
        retailer_id=retailer.id if retailer else None,
        address="1 rue Test",
        city=city,
        postal_code="69001",
        lat=45.7640,
        lng=4.8357,
    )
    db.add(s)
    db.flush()
    return s


def _make_receipt(db, store, user) -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        store_id=store.id,
        user_id=user.id,
        purchased_at=date.today(),
        image_r2_key=f"{uuid.uuid4()}.jpg",
    )
    db.add(r)
    db.flush()
    return r


def _make_scan(db, store, user) -> Scan:
    # CHECK ``receipt_required`` — receipt scans need a sibling Receipt.
    r = _make_receipt(db, store, user)
    s = Scan(
        id=uuid.uuid4(),
        store_id=store.id,
        user_id=user.id,
        receipt_id=r.id,
        scan_type="receipt",
        status="unresolved",
        rejected_reason="awaiting_user_validation",
        scanned_name=LABEL_HIPRO,
        price=199,
        quantity=Decimal("1"),
    )
    db.add(s)
    db.flush()
    return s


def _add_resolution(
    db,
    *,
    scan: Scan,
    store_id: uuid.UUID,
    user_id: uuid.UUID,
    ean: str,
    method: str = "barcode",
    label: str = LABEL_HIPRO,
    source_type: str = "receipt",
) -> ProductNameResolution:
    pnr = ProductNameResolution(
        id=uuid.uuid4(),
        scan_id=scan.id,
        store_id=store_id,
        normalized_label=label,
        product_ean=ean,
        user_id=user_id,
        match_method=method,
        source_type=source_type,
    )
    db.add(pnr)
    db.flush()
    return pnr


# ============================================================
# get_consensus_for_label — retailer-keyed
# ============================================================


def test_get_consensus_returns_none_when_no_resolution(db, store, retailer):
    from repositories.name_resolution_repository import get_consensus_for_label

    result = get_consensus_for_label(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        normalized_label=LABEL_HIPRO,
    )
    assert result is None


def test_get_consensus_below_min_users_is_pending(db, store, retailer):
    """2 users distinct, all converging — should still be pending (default min=3)."""
    from repositories.name_resolution_repository import get_consensus_for_label

    u1 = _make_user(db, "1")
    u2 = _make_user(db, "2")
    s1 = _make_scan(db, store, u1)
    s2 = _make_scan(db, store, u2)
    _add_resolution(db, scan=s1, store_id=store.id, user_id=u1.id, ean=EAN_HIPRO_VAN)
    _add_resolution(db, scan=s2, store_id=store.id, user_id=u2.id, ean=EAN_HIPRO_VAN)

    result = get_consensus_for_label(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        normalized_label=LABEL_HIPRO,
    )
    assert result is not None
    assert result.ean == EAN_HIPRO_VAN
    assert result.distinct_validators == 2
    assert result.state == ConsensusState.PENDING


def test_get_consensus_verified_when_threshold_met_and_convergent(db, store, retailer):
    """3 users distinct (same store, same retailer), convergent → VERIFIED."""
    from repositories.name_resolution_repository import get_consensus_for_label

    for i in range(3):
        u = _make_user(db, str(i))
        sc = _make_scan(db, store, u)
        _add_resolution(db, scan=sc, store_id=store.id, user_id=u.id, ean=EAN_HIPRO_VAN)

    result = get_consensus_for_label(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        normalized_label=LABEL_HIPRO,
    )
    assert result is not None
    assert result.ean == EAN_HIPRO_VAN
    assert result.distinct_validators == 3
    assert result.top1_pct == pytest.approx(100.0)
    assert result.state == ConsensusState.VERIFIED


def test_get_consensus_retailer_wide_three_distinct_stores(db, retailer):
    """3 users in 3 distinct stores of the SAME retailer converge → VERIFIED.

    Bloc B happy path : the cross-retailer consensus aggregates across
    the retailer's stores, so a label that has 1 vote in each of 3
    Intermarché (Lyon, Marseille, Lille) reaches quorum even though no
    single store has 3 distinct users on it. Pre-Bloc B this would be
    PENDING per store (1 user each).
    """
    from repositories.name_resolution_repository import get_consensus_for_label

    store_lyon = _make_store(db, retailer=retailer, name="Intermarche Lyon", city="Lyon")
    store_marseille = _make_store(db, retailer=retailer, name="Intermarche Marseille", city="Marseille")
    store_lille = _make_store(db, retailer=retailer, name="Intermarche Lille", city="Lille")
    db.flush()

    for s in (store_lyon, store_marseille, store_lille):
        u = _make_user(db, str(s.id)[:6])
        sc = _make_scan(db, s, u)
        _add_resolution(db, scan=sc, store_id=s.id, user_id=u.id, ean=EAN_HIPRO_VAN)

    result = get_consensus_for_label(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        normalized_label=LABEL_HIPRO,
    )
    assert result is not None
    assert result.ean == EAN_HIPRO_VAN
    assert result.distinct_validators == 3
    assert result.state == ConsensusState.VERIFIED


def test_get_consensus_separates_source_type(db, store, retailer):
    """receipt and esl ledgers are independent — same retailer + label →
    two distinct consensus computations.
    """
    from repositories.name_resolution_repository import get_consensus_for_label

    # 3 receipt validators converging on EAN_VAN
    for i in range(3):
        u = _make_user(db, f"r{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(
            db,
            scan=sc,
            store_id=store.id,
            user_id=u.id,
            ean=EAN_HIPRO_VAN,
            source_type="receipt",
        )

    # 3 esl validators converging on EAN_FRA (different EAN)
    for i in range(3):
        u = _make_user(db, f"e{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(
            db,
            scan=sc,
            store_id=store.id,
            user_id=u.id,
            ean=EAN_HIPRO_FRA,
            method="esl",
            source_type="esl",
        )

    receipt_result = get_consensus_for_label(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        normalized_label=LABEL_HIPRO,
    )
    esl_result = get_consensus_for_label(
        db,
        retailer_id=retailer.id,
        source_type="esl",
        normalized_label=LABEL_HIPRO,
    )

    assert receipt_result is not None
    assert receipt_result.ean == EAN_HIPRO_VAN
    assert esl_result is not None
    assert esl_result.ean == EAN_HIPRO_FRA


def test_get_consensus_filters_null_retailer(db, store, retailer):
    """Rows whose ``retailer_id IS NULL`` (user-suggested store pending
    admin validation) must be excluded from the retailer-keyed consensus
    even when their label and store match.
    """
    from repositories.name_resolution_repository import get_consensus_for_label

    # 3 valid (retailer-attached) rows : converge on EAN_VAN
    for i in range(3):
        u = _make_user(db, f"v{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(db, scan=sc, store_id=store.id, user_id=u.id, ean=EAN_HIPRO_VAN)

    # Detached store (no retailer_id) — its rows write retailer_id=NULL
    detached_store = _make_store(db, retailer=None, name="UserSuggested")
    db.flush()
    for i in range(3):
        u = _make_user(db, f"n{i}")
        sc = _make_scan(db, detached_store, u)
        _add_resolution(
            db,
            scan=sc,
            store_id=detached_store.id,
            user_id=u.id,
            ean=EAN_HIPRO_FRA,
        )

    # Querying for the seeded retailer must NOT see the NULL rows.
    result = get_consensus_for_label(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        normalized_label=LABEL_HIPRO,
    )
    assert result is not None
    assert result.ean == EAN_HIPRO_VAN
    assert result.distinct_validators == 3
    assert result.state == ConsensusState.VERIFIED


def test_get_consensus_per_retailer_isolation(db, retailer):
    """Two retailers with the same label → consensus is per-retailer."""
    from repositories.name_resolution_repository import get_consensus_for_label

    other_retailer = _make_retailer(db, "carrefour")
    db.flush()

    store_a = _make_store(db, retailer=retailer, name="Inter A")
    _make_store(db, retailer=other_retailer, name="Carrefour B")
    db.flush()

    # 3 users on retailer A
    for i in range(3):
        u = _make_user(db, f"a{i}")
        sc = _make_scan(db, store_a, u)
        _add_resolution(db, scan=sc, store_id=store_a.id, user_id=u.id, ean=EAN_HIPRO_VAN)

    res_a = get_consensus_for_label(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        normalized_label=LABEL_HIPRO,
    )
    res_b = get_consensus_for_label(
        db,
        retailer_id=other_retailer.id,
        source_type="receipt",
        normalized_label=LABEL_HIPRO,
    )

    assert res_a is not None
    assert res_a.state == ConsensusState.VERIFIED
    assert res_b is None  # other retailer has zero validations


def test_get_consensus_admin_weight_applied(db, store, retailer):
    """1 admin (weight 5) + 2 user barcodes converging → VERIFIED."""
    from repositories.name_resolution_repository import get_consensus_for_label

    admin = _make_user(db, "admin")
    sc_admin = _make_scan(db, store, admin)
    _add_resolution(
        db,
        scan=sc_admin,
        store_id=store.id,
        user_id=admin.id,
        ean=EAN_HIPRO_VAN,
        method="manual_admin",
    )
    for i in range(2):
        u = _make_user(db, f"user{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(db, scan=sc, store_id=store.id, user_id=u.id, ean=EAN_HIPRO_VAN)

    result = get_consensus_for_label(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        normalized_label=LABEL_HIPRO,
    )
    assert result is not None
    assert result.ean == EAN_HIPRO_VAN
    assert result.distinct_validators == 3
    assert result.state == ConsensusState.VERIFIED


def test_get_consensus_excludes_non_validation_methods(db, store, retailer):
    """``fuzzy_pending`` rows are stored but must NOT contribute to consensus."""
    from repositories.name_resolution_repository import get_consensus_for_label

    for i in range(3):
        u = _make_user(db, f"fp{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(
            db,
            scan=sc,
            store_id=store.id,
            user_id=u.id,
            ean=EAN_HIPRO_VAN,
            method="fuzzy_pending",
        )

    result = get_consensus_for_label(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        normalized_label=LABEL_HIPRO,
    )
    assert result is None


def test_get_consensus_divergent_when_split(db, store, retailer):
    """7/3 split — quorum reached, top1_pct=70% < 80% → CONTROVERSE."""
    from repositories.name_resolution_repository import get_consensus_for_label

    for i in range(7):
        u = _make_user(db, f"a{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(db, scan=sc, store_id=store.id, user_id=u.id, ean=EAN_HIPRO_VAN)
    for i in range(3):
        u = _make_user(db, f"b{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(db, scan=sc, store_id=store.id, user_id=u.id, ean=EAN_HIPRO_FRA)

    result = get_consensus_for_label(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        normalized_label=LABEL_HIPRO,
    )
    assert result is not None
    assert result.distinct_validators == 10
    assert result.top1_pct == pytest.approx(70.0)
    assert result.state == ConsensusState.CONTROVERSE


# ============================================================
# was_ever_verified
# ============================================================


def test_was_ever_verified_returns_true_after_promotion(db, retailer):
    """Audit log carries a past ``to_state='verified'`` event for the
    ``(retailer_id, source_type, label)`` tuple → returns True.
    """
    from repositories.name_resolution_repository import was_ever_verified

    db.execute(
        text(
            "INSERT INTO pipeline_audit_log "
            "(phase, level, event, payload, created_at) "
            "VALUES ('match', 'normal', 'consensus_state_changed', "
            "        CAST(:payload AS jsonb), :created_at)"
        ),
        {
            "payload": json.dumps(
                {
                    "retailer_id": str(retailer.id),
                    "source_type": "receipt",
                    "normalized_label": LABEL_HIPRO,
                    "from_state": "pending",
                    "to_state": "verified",
                    "top1_ean": EAN_HIPRO_VAN,
                }
            ),
            "created_at": "2020-01-01T00:00:00+00:00",
        },
    )
    db.flush()

    assert (
        was_ever_verified(
            db,
            retailer_id=retailer.id,
            source_type="receipt",
            normalized_label=LABEL_HIPRO,
        )
        is True
    )


def test_was_ever_verified_returns_false_when_never_promoted(db, retailer):
    from repositories.name_resolution_repository import was_ever_verified

    assert (
        was_ever_verified(
            db,
            retailer_id=retailer.id,
            source_type="receipt",
            normalized_label=LABEL_HIPRO,
        )
        is False
    )


def test_was_ever_verified_separates_source_type(db, retailer):
    """An ESL promotion event must NOT count as a receipt promotion."""
    from repositories.name_resolution_repository import was_ever_verified

    db.execute(
        text(
            "INSERT INTO pipeline_audit_log "
            "(phase, level, event, payload, created_at) "
            "VALUES ('match', 'normal', 'consensus_state_changed', "
            "        CAST(:payload AS jsonb), :created_at)"
        ),
        {
            "payload": json.dumps(
                {
                    "retailer_id": str(retailer.id),
                    "source_type": "esl",
                    "normalized_label": LABEL_HIPRO,
                    "from_state": "pending",
                    "to_state": "verified",
                    "top1_ean": EAN_HIPRO_VAN,
                }
            ),
            "created_at": "2020-01-01T00:00:00+00:00",
        },
    )
    db.flush()

    assert (
        was_ever_verified(
            db,
            retailer_id=retailer.id,
            source_type="esl",
            normalized_label=LABEL_HIPRO,
        )
        is True
    )
    assert (
        was_ever_verified(
            db,
            retailer_id=retailer.id,
            source_type="receipt",
            normalized_label=LABEL_HIPRO,
        )
        is False
    )


def test_get_consensus_unverified_after_promotion_fall(db, store, retailer):
    """A pair that was VERIFIED then loses convergence → UNVERIFIED.

    Audit log seeded with a past ``to_state='verified'`` event for the
    retailer-keyed payload (Bloc B contract). Live ledger then contains
    a 5/5 split — quorum met but convergence fails. Expected state :
    UNVERIFIED (post-promotion fall) instead of CONTROVERSE.
    """
    from repositories.name_resolution_repository import get_consensus_for_label

    db.execute(
        text(
            "INSERT INTO pipeline_audit_log "
            "(phase, level, event, payload, created_at) "
            "VALUES ('match', 'normal', 'consensus_state_changed', "
            "        CAST(:payload AS jsonb), :created_at)"
        ),
        {
            "payload": json.dumps(
                {
                    "retailer_id": str(retailer.id),
                    "source_type": "receipt",
                    "normalized_label": LABEL_HIPRO,
                    "from_state": "pending",
                    "to_state": "verified",
                    "top1_ean": EAN_HIPRO_VAN,
                }
            ),
            "created_at": "2020-01-01T00:00:00+00:00",
        },
    )
    db.flush()

    for i in range(5):
        u = _make_user(db, f"unv_a{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(db, scan=sc, store_id=store.id, user_id=u.id, ean=EAN_HIPRO_VAN)
    for i in range(5):
        u = _make_user(db, f"unv_b{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(db, scan=sc, store_id=store.id, user_id=u.id, ean=EAN_HIPRO_FRA)

    result = get_consensus_for_label(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        normalized_label=LABEL_HIPRO,
    )
    assert result is not None
    assert result.distinct_validators == 10
    assert result.state == ConsensusState.UNVERIFIED


# ============================================================
# find_fuzzy_verified_consensus — retailer-wide pg_trgm
# ============================================================


def _seed_verified_consensus_retailer(
    db,
    *,
    store: Store,
    label: str,
    ean: str,
    n_users: int = 3,
    source_type: str = "receipt",
    method: str = "barcode",
) -> None:
    """Drop ``n_users`` distinct contributing rows on ``(store, label, ean)``
    so the consensus computation returns ``VERIFIED`` for that retailer.
    """
    for i in range(n_users):
        u = _make_user(db, f"{label[:6]}{ean[:4]}{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(
            db,
            scan=sc,
            store_id=store.id,
            user_id=u.id,
            ean=ean,
            label=label,
            source_type=source_type,
            method=method,
        )


def test_find_fuzzy_verified_consensus_finds_close_match(db, store, retailer):
    """``HIPROA BRE SAV FRSE`` (1-char OCR variant) matches a verified
    ``HIPRO BRE SAV FRSE`` in the retailer ledger.
    """
    from repositories.name_resolution_repository import find_fuzzy_verified_consensus

    _seed_verified_consensus_retailer(db, store=store, label="HIPRO BRE SAV FRSE", ean=EAN_HIPRO_FRA)

    result = find_fuzzy_verified_consensus(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        cleaned_label="HIPROA BRE SAV FRSE",
    )
    assert result is not None
    assert result.ean == EAN_HIPRO_FRA
    assert result.state == ConsensusState.VERIFIED


def test_find_fuzzy_verified_consensus_rejects_too_different(db, store, retailer):
    from repositories.name_resolution_repository import find_fuzzy_verified_consensus

    _seed_verified_consensus_retailer(db, store=store, label="HIPRO BRE SAV FRSE", ean=EAN_HIPRO_FRA)

    result = find_fuzzy_verified_consensus(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        cleaned_label="PEPSI MAX",
    )
    assert result is None


def test_find_fuzzy_verified_consensus_filters_pending(db, store, retailer):
    """Below quorum (PENDING) neighbour → fuzzy must skip it."""
    from repositories.name_resolution_repository import find_fuzzy_verified_consensus

    _seed_verified_consensus_retailer(db, store=store, label="HIPRO BRE SAV FRSE", ean=EAN_HIPRO_FRA, n_users=2)

    result = find_fuzzy_verified_consensus(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        cleaned_label="HIPROA BRE SAV FRSE",
    )
    assert result is None


def test_find_fuzzy_verified_consensus_filters_other_retailer(db, store, retailer):
    """A verified label on retailer X must NOT be returned when querying
    retailer Y, even if pg_trgm similarity is high.
    """
    from repositories.name_resolution_repository import find_fuzzy_verified_consensus

    other_retailer = _make_retailer(db, "carrefour")
    db.flush()
    other_store = _make_store(db, retailer=other_retailer, name="Carrefour Other")
    db.flush()

    _seed_verified_consensus_retailer(db, store=other_store, label="HIPRO BRE SAV FRSE", ean=EAN_HIPRO_FRA)

    # Our seeded retailer has no rows.
    result = find_fuzzy_verified_consensus(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        cleaned_label="HIPROA BRE SAV FRSE",
    )
    assert result is None


def test_find_fuzzy_verified_consensus_filters_other_source_type(db, store, retailer):
    """A verified ESL label must NOT match a receipt fuzzy query."""
    from repositories.name_resolution_repository import find_fuzzy_verified_consensus

    _seed_verified_consensus_retailer(
        db,
        store=store,
        label="HIPRO BRE SAV FRSE",
        ean=EAN_HIPRO_FRA,
        source_type="esl",
        method="esl",
    )

    result = find_fuzzy_verified_consensus(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        cleaned_label="HIPROA BRE SAV FRSE",
    )
    assert result is None


def test_find_fuzzy_verified_consensus_respects_len_diff_max(db, store, retailer):
    """A neighbour whose length differs by more than ``len_diff_max`` is
    rejected by the gate even if pg_trgm similarity is high.
    """
    from repositories.name_resolution_repository import find_fuzzy_verified_consensus

    _seed_verified_consensus_retailer(db, store=store, label="HIPRO BRE SAV FRSE", ean=EAN_HIPRO_FRA)

    result = find_fuzzy_verified_consensus(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        cleaned_label="HIPRO",
    )
    assert result is None


def test_find_fuzzy_verified_consensus_skips_exact_match(db, store, retailer):
    """The exact path is the caller's job ; the fuzzy helper must never
    return the verbatim label."""
    from repositories.name_resolution_repository import find_fuzzy_verified_consensus

    _seed_verified_consensus_retailer(db, store=store, label="HIPRO BRE SAV FRSE", ean=EAN_HIPRO_FRA)

    result = find_fuzzy_verified_consensus(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        cleaned_label="HIPRO BRE SAV FRSE",
    )
    assert result is None


def test_find_fuzzy_verified_consensus_aggregates_cross_store(db, retailer):
    """Fuzzy retailer-wide : a label promoted via 3 users in 3 distinct
    stores of the same retailer must be reachable by fuzzy lookup from a
    fourth store of the same retailer.
    """
    from repositories.name_resolution_repository import find_fuzzy_verified_consensus

    s1 = _make_store(db, retailer=retailer, name="A")
    s2 = _make_store(db, retailer=retailer, name="B")
    s3 = _make_store(db, retailer=retailer, name="C")
    db.flush()
    for s in (s1, s2, s3):
        u = _make_user(db, f"r{s.id}"[:8])
        sc = _make_scan(db, s, u)
        _add_resolution(
            db,
            scan=sc,
            store_id=s.id,
            user_id=u.id,
            ean=EAN_HIPRO_FRA,
            label="HIPRO BRE SAV FRSE",
        )

    result = find_fuzzy_verified_consensus(
        db,
        retailer_id=retailer.id,
        source_type="receipt",
        cleaned_label="HIPROA BRE SAV FRSE",
    )
    assert result is not None
    assert result.ean == EAN_HIPRO_FRA
    assert result.state == ConsensusState.VERIFIED


# ============================================================
# list_divergent_labels — retailer-keyed
# ============================================================


def test_list_divergent_labels_groups_by_retailer_source_label(db, store, retailer):
    """A divergent (retailer, source_type, label) triple surfaces ;
    verified and pending pairs do not.
    """
    from repositories.name_resolution_repository import list_divergent_labels

    # VERIFIED pair on retailer (3 users converging)
    for i in range(3):
        u = _make_user(db, f"v{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(
            db,
            scan=sc,
            store_id=store.id,
            user_id=u.id,
            ean=EAN_HIPRO_VAN,
            label="LABEL_VERIFIED",
        )

    # CONTROVERSE pair (7/3) — must surface.
    for i in range(7):
        u = _make_user(db, f"divA{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(
            db,
            scan=sc,
            store_id=store.id,
            user_id=u.id,
            ean=EAN_HIPRO_VAN,
            label="LABEL_DIVERGENT",
        )
    for i in range(3):
        u = _make_user(db, f"divB{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(
            db,
            scan=sc,
            store_id=store.id,
            user_id=u.id,
            ean=EAN_HIPRO_FRA,
            label="LABEL_DIVERGENT",
        )

    # PENDING pair (2 users, below quorum) — must NOT surface.
    for i in range(2):
        u = _make_user(db, f"pen{i}")
        sc = _make_scan(db, store, u)
        _add_resolution(
            db,
            scan=sc,
            store_id=store.id,
            user_id=u.id,
            ean=EAN_HIPRO_VAN,
            label="LABEL_PENDING",
        )

    result = list_divergent_labels(db, limit=10, offset=0)
    rows = {(r.retailer_id, r.source_type, r.normalized_label) for r in result}
    assert (retailer.id, "receipt", "LABEL_DIVERGENT") in rows
    assert all(r.normalized_label != "LABEL_VERIFIED" for r in result)
    assert all(r.normalized_label != "LABEL_PENDING" for r in result)


def test_list_divergent_labels_excludes_null_retailer(db, retailer):
    """Rows with ``retailer_id IS NULL`` are out of the consensus path
    entirely — they must NOT appear in the divergent queue.
    """
    from repositories.name_resolution_repository import list_divergent_labels

    detached_store = _make_store(db, retailer=None, name="UserSuggested")
    db.flush()

    for i in range(7):
        u = _make_user(db, f"a{i}")
        sc = _make_scan(db, detached_store, u)
        _add_resolution(
            db,
            scan=sc,
            store_id=detached_store.id,
            user_id=u.id,
            ean=EAN_HIPRO_VAN,
            label="LABEL_DETACHED",
        )
    for i in range(3):
        u = _make_user(db, f"b{i}")
        sc = _make_scan(db, detached_store, u)
        _add_resolution(
            db,
            scan=sc,
            store_id=detached_store.id,
            user_id=u.id,
            ean=EAN_HIPRO_FRA,
            label="LABEL_DETACHED",
        )

    result = list_divergent_labels(db, limit=50, offset=0)
    assert all(r.normalized_label != "LABEL_DETACHED" for r in result)


def test_list_divergent_labels_pagination(db, store, retailer):
    """Pagination via limit/offset returns disjoint slices."""
    from repositories.name_resolution_repository import list_divergent_labels

    for label_idx in range(3):
        label = f"DIVERGENT_{label_idx}"
        for i in range(7):
            u = _make_user(db, f"a{label_idx}{i}")
            sc = _make_scan(db, store, u)
            _add_resolution(
                db,
                scan=sc,
                store_id=store.id,
                user_id=u.id,
                ean=EAN_HIPRO_VAN,
                label=label,
            )
        for i in range(3):
            u = _make_user(db, f"b{label_idx}{i}")
            sc = _make_scan(db, store, u)
            _add_resolution(
                db,
                scan=sc,
                store_id=store.id,
                user_id=u.id,
                ean=EAN_HIPRO_FRA,
                label=label,
            )

    page1 = list_divergent_labels(db, limit=2, offset=0)
    page2 = list_divergent_labels(db, limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 1
    labels_p1 = {r.normalized_label for r in page1}
    labels_p2 = {r.normalized_label for r in page2}
    assert labels_p1.isdisjoint(labels_p2)


# ============================================================
# list_unmatched_labels — retailer-keyed
# ============================================================


def test_list_unmatched_labels_returns_scans_with_no_consensus(db, store, retailer):
    from repositories.name_resolution_repository import list_unmatched_labels

    u = _make_user(db, "u1")
    sc = _make_scan(db, store, u)
    sc.scanned_name = "MYSTERY ITEM"
    db.flush()

    result = list_unmatched_labels(db, limit=10, offset=0)
    rows = {(r.retailer_id, r.normalized_label) for r in result}
    assert (retailer.id, "MYSTERY ITEM") in rows


def test_list_unmatched_labels_excludes_labels_with_consensus(db, store, retailer):
    from repositories.name_resolution_repository import list_unmatched_labels

    u_resolver = _make_user(db, "resolver")
    sc_resolved = _make_scan(db, store, u_resolver)
    sc_resolved.scanned_name = "RESOLVED_LABEL"
    db.flush()
    _add_resolution(
        db,
        scan=sc_resolved,
        store_id=store.id,
        user_id=u_resolver.id,
        ean=EAN_HIPRO_VAN,
        label="RESOLVED_LABEL",
    )

    u_other = _make_user(db, "other")
    sc_other = _make_scan(db, store, u_other)
    sc_other.scanned_name = "RESOLVED_LABEL"
    db.flush()

    result = list_unmatched_labels(db, limit=10, offset=0)
    assert all(r.normalized_label != "RESOLVED_LABEL" for r in result)


def test_list_unmatched_labels_excludes_null_retailer(db, retailer):
    """Scans whose store has no retailer_id are out of the retailer-keyed
    consensus and must NOT show up in the queue (they belong to the
    store-validation track instead).
    """
    from repositories.name_resolution_repository import list_unmatched_labels

    detached_store = _make_store(db, retailer=None, name="UserSuggested")
    db.flush()

    u = _make_user(db, "u1")
    sc = _make_scan(db, detached_store, u)
    sc.scanned_name = "DETACHED_MYSTERY"
    db.flush()

    result = list_unmatched_labels(db, limit=50, offset=0)
    assert all(r.normalized_label != "DETACHED_MYSTERY" for r in result)
