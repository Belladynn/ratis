"""TDD coverage for the name-resolution ledger write paths (NRC bloc C).

See ``ARCH_name_resolution_consensus.md`` § "Promotion / Détection /
États" + Bloc C checklist for the full contract.

Three concentric layers :

1. Unit — ``record_resolution`` / ``evaluate_state_transition`` /
   ``emit_consensus_state_changed_event`` / ``was_ever_verified``
   exercised against the ORM.
2. Integration — three call sites :
   - ``services.barcode_service.scan_barcode``
   - ``worker.receipt_task`` v2 phase pipeline (matched item path)
   - ``routes.admin.scans.patch_scan_override`` (manual_admin)
3. Schema — partial index ``idx_pal_consensus_state_changed`` exists
   after the bloc C migration upgrade.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

from ratis_core.models.name_resolution import ProductNameResolution
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.user import User
from repositories.consensus_state import ConsensusState
from repositories.name_resolution_repository import (
    # Bloc B (cross-retailer) renamed the canonical signatures to
    # retailer-keyed. These tests still target the legacy ``store_id``
    # API ; alias to the transitional ``*_by_store`` wrappers until
    # Bloc C/D migrate them to the new contract.
    get_consensus_for_label_by_store as get_consensus_for_label,
)
from repositories.name_resolution_repository import (
    was_ever_verified_by_store as was_ever_verified,
)
from repositories.name_resolution_writes import (
    evaluate_state_transition,
    record_resolution,
)
from sqlalchemy import text

LABEL = "HIPRO A BRE SAV VAN"
EAN_A = "7610113013175"
EAN_B = "7610113013182"


# ============================================================
# helpers
# ============================================================


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


def _make_scan(db, store, user, *, scanned_name: str = LABEL) -> Scan:
    # CHECK ``receipt_required`` : a ``scan_type='receipt'`` row MUST have
    # a non-NULL ``receipt_id``. Seed a sibling Receipt for the FK.
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
        scanned_name=scanned_name,
        price=199,
        quantity=Decimal("1"),
    )
    db.add(s)
    db.flush()
    return s


def _audit_events(db, store_id: uuid.UUID, label: str) -> list[dict]:
    """Return all ``consensus_state_changed`` events for a (store, label)
    in chronological order. Payload is JSON-decoded for ergonomics."""
    rows = db.execute(
        text(
            """
            SELECT payload, created_at FROM pipeline_audit_log
            WHERE event = 'consensus_state_changed'
              AND payload->>'store_id' = :sid
              AND payload->>'normalized_label' = :label
            ORDER BY created_at ASC, id ASC
            """
        ),
        {"sid": str(store_id), "label": label},
    ).fetchall()
    return [dict(r.payload) for r in rows]


def _seed_verified_event(db, store_id: uuid.UUID, label: str, *, top1_ean: str = EAN_A) -> None:
    """Plant a synthetic ``to_state='verified'`` event in the audit log
    so ``was_ever_verified()`` returns True without the test having to
    drive the full PENDING→VERIFIED transition.

    ``created_at`` is explicitly pinned to a clearly-past timestamp so
    challenger detection (``resolved_at > seed.created_at``) works
    deterministically — within a single PG transaction ``now()`` is
    constant, so a default-now() seed would tie with subsequent
    ``record_resolution`` inserts.
    """
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
                    "store_id": str(store_id),
                    "normalized_label": label,
                    "from_state": "pending",
                    "to_state": "verified",
                    "top1_ean": top1_ean,
                }
            ),
            "created_at": "2020-01-01T00:00:00+00:00",
        },
    )
    db.flush()


# ============================================================
# record_resolution — happy + idempotent
# ============================================================


def test_record_resolution_inserts_row(db, store):
    u = _make_user(db, "ins1")
    sc = _make_scan(db, store, u)

    res = record_resolution(
        db,
        scan_id=sc.id,
        store_id=store.id,
        normalized_label=LABEL,
        product_ean=EAN_A,
        user_id=u.id,
        match_method="barcode",
    )

    assert res is not None
    assert res.scan_id == sc.id
    assert res.product_ean == EAN_A
    assert res.match_method == "barcode"

    persisted = db.query(ProductNameResolution).filter(ProductNameResolution.scan_id == sc.id).one()
    assert persisted.id == res.id


def test_record_resolution_idempotent_on_duplicate_scan_label(db, store):
    """A second call with the same (scan_id, label) is a NO-OP and
    returns the original row."""
    u = _make_user(db, "idem")
    sc = _make_scan(db, store, u)

    first = record_resolution(
        db,
        scan_id=sc.id,
        store_id=store.id,
        normalized_label=LABEL,
        product_ean=EAN_A,
        user_id=u.id,
        match_method="barcode",
    )
    second = record_resolution(
        db,
        scan_id=sc.id,
        store_id=store.id,
        normalized_label=LABEL,
        product_ean=EAN_B,  # different EAN — must NOT update the row
        user_id=u.id,
        match_method="manual_admin",
    )

    assert second.id == first.id
    assert second.product_ean == EAN_A  # original wins (append-only)
    assert second.match_method == "barcode"

    count = db.query(ProductNameResolution).filter(ProductNameResolution.scan_id == sc.id).count()
    assert count == 1


def test_record_resolution_no_op_on_duplicate_does_not_emit_event(db, store):
    """The conflict path must not re-trigger evaluate_state_transition
    (state cannot have changed when no row was added)."""
    u = _make_user(db, "noev")
    sc = _make_scan(db, store, u)

    # Seed a single PENDING-yielding row + capture audit count.
    record_resolution(
        db,
        scan_id=sc.id,
        store_id=store.id,
        normalized_label=LABEL,
        product_ean=EAN_A,
        user_id=u.id,
        match_method="barcode",
    )
    initial_events = len(_audit_events(db, store.id, LABEL))

    # Re-run — duplicate, should NOT emit a new audit event.
    record_resolution(
        db,
        scan_id=sc.id,
        store_id=store.id,
        normalized_label=LABEL,
        product_ean=EAN_A,
        user_id=u.id,
        match_method="barcode",
    )

    assert len(_audit_events(db, store.id, LABEL)) == initial_events


# ============================================================
# evaluate_state_transition — emit on transitions
# ============================================================


def test_evaluate_emits_event_from_none_to_pending(db, store):
    u = _make_user(db, "p1")
    sc = _make_scan(db, store, u)

    # Insert a single row first (no transition emitted yet — the
    # caller would normally do it via record_resolution).
    pnr = ProductNameResolution(
        id=uuid.uuid4(),
        scan_id=sc.id,
        store_id=store.id,
        normalized_label=LABEL,
        product_ean=EAN_A,
        user_id=u.id,
        match_method="barcode",
    )
    db.add(pnr)
    db.flush()

    evaluate_state_transition(db, store_id=store.id, normalized_label=LABEL, triggered_by_scan_id=sc.id)

    events = _audit_events(db, store.id, LABEL)
    assert len(events) == 1
    assert events[0]["from_state"] is None
    assert events[0]["to_state"] == "pending"
    assert events[0]["top1_ean"] == EAN_A
    assert events[0]["distinct_validators"] == 1
    assert events[0]["triggered_by_scan_id"] == str(sc.id)


def test_evaluate_emits_event_pending_to_verified(db, store):
    """3 distinct validators converging — must record PENDING then VERIFIED."""
    # First validator → PENDING.
    u1 = _make_user(db, "v1")
    sc1 = _make_scan(db, store, u1)
    record_resolution(
        db,
        scan_id=sc1.id,
        store_id=store.id,
        normalized_label=LABEL,
        product_ean=EAN_A,
        user_id=u1.id,
        match_method="barcode",
    )

    # Second validator → still PENDING (no event emitted).
    u2 = _make_user(db, "v2")
    sc2 = _make_scan(db, store, u2)
    record_resolution(
        db,
        scan_id=sc2.id,
        store_id=store.id,
        normalized_label=LABEL,
        product_ean=EAN_A,
        user_id=u2.id,
        match_method="barcode",
    )

    # Third validator → quorum + convergence → VERIFIED.
    u3 = _make_user(db, "v3")
    sc3 = _make_scan(db, store, u3)
    record_resolution(
        db,
        scan_id=sc3.id,
        store_id=store.id,
        normalized_label=LABEL,
        product_ean=EAN_A,
        user_id=u3.id,
        match_method="barcode",
    )

    events = _audit_events(db, store.id, LABEL)
    states = [(e["from_state"], e["to_state"]) for e in events]
    assert (None, "pending") in states
    assert ("pending", "verified") in states


def test_evaluate_pending_to_controverse_never_verified(db, store):
    """3 validators, split 2/1 — CONTROVERSE (cold-start, no prior verified)."""
    u1 = _make_user(db, "c1")
    u2 = _make_user(db, "c2")
    u3 = _make_user(db, "c3")
    sc1 = _make_scan(db, store, u1)
    sc2 = _make_scan(db, store, u2)
    sc3 = _make_scan(db, store, u3)

    record_resolution(
        db,
        scan_id=sc1.id,
        store_id=store.id,
        normalized_label=LABEL,
        product_ean=EAN_A,
        user_id=u1.id,
        match_method="barcode",
    )
    record_resolution(
        db,
        scan_id=sc2.id,
        store_id=store.id,
        normalized_label=LABEL,
        product_ean=EAN_A,
        user_id=u2.id,
        match_method="barcode",
    )
    record_resolution(
        db,
        scan_id=sc3.id,
        store_id=store.id,
        normalized_label=LABEL,
        product_ean=EAN_B,
        user_id=u3.id,
        match_method="barcode",
    )

    events = _audit_events(db, store.id, LABEL)
    last = events[-1]
    assert last["to_state"] == "controverse"
    assert last["challengers"] is None


def test_evaluate_verified_to_unverified_emits_unverified_with_challengers(db, store):
    """A previously-verified pair that loses convergence → UNVERIFIED + challengers.

    Setup :
    1. Seed an audit ``to_state=verified`` event so ``was_ever_verified``
       returns True.
    2. Pre-populate 5 ``EAN_A`` + 5 ``EAN_B`` ledger rows DIRECTLY
       (bypassing ``record_resolution``) so no intermediate state
       events fire and the snapshot at transition time captures every
       challenger.
    3. Trigger ``evaluate_state_transition`` once — the live
       computation now reads as UNVERIFIED and emits exactly one event.
    """
    _seed_verified_event(db, store.id, LABEL, top1_ean=EAN_A)

    # Pre-populate both EAN groups directly (no record_resolution → no
    # interim events). Distinct user IDs feed the quorum + challenger
    # set.
    for i in range(5):
        u = _make_user(db, f"unv_a{i}")
        sc = _make_scan(db, store, u)
        db.add(
            ProductNameResolution(
                id=uuid.uuid4(),
                scan_id=sc.id,
                store_id=store.id,
                normalized_label=LABEL,
                product_ean=EAN_A,
                user_id=u.id,
                match_method="barcode",
            )
        )
    challenger_users: list[User] = []
    for i in range(5):
        u = _make_user(db, f"unv_b{i}")
        challenger_users.append(u)
        sc = _make_scan(db, store, u)
        db.add(
            ProductNameResolution(
                id=uuid.uuid4(),
                scan_id=sc.id,
                store_id=store.id,
                normalized_label=LABEL,
                product_ean=EAN_B,
                user_id=u.id,
                match_method="barcode",
            )
        )
    db.flush()

    evaluate_state_transition(db, store_id=store.id, normalized_label=LABEL)

    unverified_events = [e for e in _audit_events(db, store.id, LABEL) if e["to_state"] == "unverified"]
    assert len(unverified_events) == 1
    event = unverified_events[0]
    assert event["challengers"] is not None
    challenger_user_ids = {c["user_id"] for c in event["challengers"]}
    expected_user_ids = {str(u.id) for u in challenger_users}
    assert challenger_user_ids == expected_user_ids
    for c in event["challengers"]:
        assert c["voted_ean"] == EAN_B  # all challengers voted for the other EAN


def test_evaluate_pending_to_controverse_emits_no_challengers(db, store):
    """Cold-start CONTROVERSE must NOT carry challengers (only UNVERIFIED does)."""
    for ean, n, prefix in ((EAN_A, 2, "ctv_a"), (EAN_B, 1, "ctv_b")):
        for i in range(n):
            u = _make_user(db, f"{prefix}{i}")
            sc = _make_scan(db, store, u)
            record_resolution(
                db,
                scan_id=sc.id,
                store_id=store.id,
                normalized_label=LABEL,
                product_ean=ean,
                user_id=u.id,
                match_method="barcode",
            )

    events = _audit_events(db, store.id, LABEL)
    final = events[-1]
    assert final["to_state"] == "controverse"
    assert final["challengers"] is None


# ============================================================
# was_ever_verified
# ============================================================


def test_was_ever_verified_true_after_verified_event(db, store):
    _seed_verified_event(db, store.id, LABEL, top1_ean=EAN_A)
    assert was_ever_verified(db, store.id, LABEL) is True


def test_was_ever_verified_false_when_audit_empty(db, store):
    assert was_ever_verified(db, store.id, LABEL) is False


def test_was_ever_verified_persists_through_subsequent_unverified(db, store):
    """Append-only audit log — a verified event remains visible even
    after an UNVERIFIED transition is later recorded."""
    _seed_verified_event(db, store.id, LABEL, top1_ean=EAN_A)

    # Plant a later UNVERIFIED-state event manually (simulating a fall).
    db.execute(
        text(
            "INSERT INTO pipeline_audit_log "
            "(phase, level, event, payload) "
            "VALUES ('match', 'normal', 'consensus_state_changed', "
            "        CAST(:payload AS jsonb))"
        ),
        {
            "payload": json.dumps(
                {
                    "store_id": str(store.id),
                    "normalized_label": LABEL,
                    "from_state": "verified",
                    "to_state": "unverified",
                    "top1_ean": EAN_A,
                }
            )
        },
    )
    db.flush()

    assert was_ever_verified(db, store.id, LABEL) is True


# ============================================================
# _evaluate behaviour through get_consensus_for_label
# ============================================================


def test_evaluate_returns_unverified_when_was_ever_verified_and_divergent(db, store):
    """Live computation = CONTROVERSE-shape + audit verified ⇒ UNVERIFIED.

    Inserts ledger rows directly so the only audit history present is
    the explicit ``_seed_verified_event`` — keeps ``was_ever_verified``
    behaviour isolated from the writer's side effects.
    """
    _seed_verified_event(db, store.id, LABEL, top1_ean=EAN_A)

    for ean, n, prefix in ((EAN_A, 3, "ev_unv_a"), (EAN_B, 3, "ev_unv_b")):
        for i in range(n):
            u = _make_user(db, f"{prefix}{i}")
            sc = _make_scan(db, store, u)
            db.add(
                ProductNameResolution(
                    id=uuid.uuid4(),
                    scan_id=sc.id,
                    store_id=store.id,
                    normalized_label=LABEL,
                    product_ean=ean,
                    user_id=u.id,
                    match_method="barcode",
                )
            )
    db.flush()

    result = get_consensus_for_label(db, store_id=store.id, normalized_label=LABEL)
    assert result is not None
    assert result.state == ConsensusState.UNVERIFIED


def test_evaluate_returns_controverse_when_never_verified_and_divergent(db, store):
    """Live computation = divergent + no audit history ⇒ CONTROVERSE.

    Inserts ledger rows directly (bypassing ``record_resolution``) so
    no audit events are emitted along the way and ``was_ever_verified``
    stays ``False``.
    """
    for ean, n, prefix in ((EAN_A, 3, "ev_ctv_a"), (EAN_B, 3, "ev_ctv_b")):
        for i in range(n):
            u = _make_user(db, f"{prefix}{i}")
            sc = _make_scan(db, store, u)
            db.add(
                ProductNameResolution(
                    id=uuid.uuid4(),
                    scan_id=sc.id,
                    store_id=store.id,
                    normalized_label=LABEL,
                    product_ean=ean,
                    user_id=u.id,
                    match_method="barcode",
                )
            )
    db.flush()

    result = get_consensus_for_label(db, store_id=store.id, normalized_label=LABEL)
    assert result is not None
    assert result.state == ConsensusState.CONTROVERSE


# ============================================================
# emit_consensus_state_changed_event payload shape
# ============================================================


def test_emit_payload_carries_full_schema(db, store):
    """The payload schema is the contract consumed by frontend (bloc E)
    and admin queue (bloc D) — assert every field is populated."""
    u = _make_user(db, "pl1")
    sc = _make_scan(db, store, u)
    record_resolution(
        db,
        scan_id=sc.id,
        store_id=store.id,
        normalized_label=LABEL,
        product_ean=EAN_A,
        user_id=u.id,
        match_method="barcode",
    )

    events = _audit_events(db, store.id, LABEL)
    assert len(events) == 1
    e = events[0]
    assert set(e.keys()) >= {
        "event",
        "store_id",
        "normalized_label",
        "from_state",
        "to_state",
        "top1_ean",
        "distinct_validators",
        "convergence_pct",
        "triggered_by_scan_id",
        "challengers",
    }
    assert e["event"] == "consensus_state_changed"
    assert e["store_id"] == str(store.id)
    assert e["normalized_label"] == LABEL


# ============================================================
# Integration — barcode_service hook
# ============================================================


def _setup_barcode_scenario(db, user, store) -> tuple[Receipt, Scan]:
    """Create a receipt + unmatched receipt scan owned by ``user`` at
    ``store`` so the barcode-resolve flow can target it."""
    rec = Receipt(
        id=uuid.uuid4(),
        store_id=store.id,
        user_id=user.id,
        purchased_at=date.today(),
        image_r2_key=f"{uuid.uuid4()}.jpg",
    )
    db.add(rec)
    db.flush()
    scan = Scan(
        id=uuid.uuid4(),
        store_id=store.id,
        user_id=user.id,
        receipt_id=rec.id,
        scan_type="receipt",
        status="unresolved",
        rejected_reason="awaiting_user_validation",
        scanned_name=LABEL,
        price=199,
        quantity=Decimal("1"),
    )
    db.add(scan)
    db.flush()
    db.commit()
    return rec, scan


def test_barcode_resolve_creates_ledger_row_and_emits_event(client, db, user, store):
    """Hitting POST /scan/barcode triggers record_resolution +
    consensus_state_changed event."""
    from ratis_core.models.product import Product

    from tests.conftest import make_token

    p = Product(ean=EAN_A, name="Hipro Vanille", source="off")
    db.add(p)
    db.flush()
    db.commit()

    _, scan = _setup_barcode_scenario(db, user, store)

    response = client.post(
        "/api/v1/scan/barcode",
        json={"ean": EAN_A, "scan_id": str(scan.id)},
        headers={"Authorization": f"Bearer {make_token(user.id)}"},
    )
    assert response.status_code == 200, response.text

    pnrs = db.query(ProductNameResolution).filter(ProductNameResolution.scan_id == scan.id).all()
    assert len(pnrs) == 1
    assert pnrs[0].match_method == "barcode"
    assert pnrs[0].product_ean == EAN_A

    events = _audit_events(db, store.id, pnrs[0].normalized_label)
    assert len(events) == 1
    assert events[0]["to_state"] == "pending"


# ============================================================
# Integration — admin override hook
# ============================================================


def test_admin_override_creates_ledger_row_with_manual_admin(admin_client, db, user, store):
    """PATCH /admin/scans/{id} with status=matched + product_ean produces
    a ledger row at match_method='manual_admin'."""
    from ratis_core.models.product import Product

    p = Product(ean=EAN_A, name="Hipro Vanille", source="off")
    db.add(p)
    db.flush()
    db.commit()

    _, scan = _setup_barcode_scenario(db, user, store)

    response = admin_client.patch(
        f"/api/v1/admin/scans/{scan.id}",
        json={"status": "matched", "product_ean": EAN_A},
        headers={"X-Admin-Operator": "alice"},
    )
    assert response.status_code == 200, response.text

    pnrs = db.query(ProductNameResolution).filter(ProductNameResolution.scan_id == scan.id).all()
    assert len(pnrs) == 1
    assert pnrs[0].match_method == "manual_admin"
    assert pnrs[0].product_ean == EAN_A


# ============================================================
# Integration — receipt_task pipeline path
# ============================================================
#
# Removed 2026-05-03 : ``test_receipt_task_match_writes_ledger_for_observed_or_fuzzy``
# tested the v2 receipt-task predicate that wrote the ledger when the
# cascade returned ``observed_name`` / ``fuzzy_pending``. The pipeline
# consensus-only refonte (2026-05-02) dropped ``MatchResult`` /
# ``observed_name`` / ``fuzzy_pending`` ; the matcher now only emits
# ``consensus_match`` which by construction comes from the ledger — no
# defensive write needed. The barcode call-site (above) and the
# ``patch_scan_override`` route (also above) remain the only ledger
# write paths.


# Migration coverage for ``idx_pal_consensus_state_changed`` lives in
# ``alembic/tests/test_nrc_c_audit_idx_migration.py`` (real upgrade /
# downgrade cycle against ``ratis_migration_test``).
