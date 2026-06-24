"""Anti-fraud V1 — verify shadow-banned users :

- have ``weight_override = 0`` written into ``product_name_resolutions``
- do not contribute to the consensus weight nor to ``distinct_validators``

See ``ARCH_anti_fraud.md`` § "Hook ledger : weight_override".
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from ratis_core.models.name_resolution import ProductNameResolution
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.user import User
from repositories.consensus_state import ConsensusState
from repositories.name_resolution_repository import (
    # Bloc B (cross-retailer) renamed the canonical signature to
    # retailer-keyed ; this test still uses the legacy ``store_id``
    # API via the transitional ``_by_store`` wrapper until Bloc C
    # migrates the matcher cascade.
    get_consensus_for_label_by_store as get_consensus_for_label,
)
from repositories.name_resolution_writes import record_resolution
from sqlalchemy import text

LABEL = "HIPRO LABEL"
EAN_A = "1234567890123"
EAN_B = "9999999999999"


def _make_user(db, *, shadow_banned: bool = False) -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=f"{uid.hex[:8]}@ratis.fr",
        account_type="oauth",
        is_deleted=False,
        is_shadow_banned=shadow_banned,
    )
    db.add(u)
    db.flush()
    return u


def _make_scan(db, store, user) -> Scan:
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


class TestRecordResolutionShadowBan:
    def test_normal_user_writes_null_weight_override(self, db, store):
        u = _make_user(db)
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
        row = db.get(ProductNameResolution, res.id)
        assert row.weight_override is None

    def test_shadow_banned_user_writes_zero_weight_override(self, db, store):
        u = _make_user(db, shadow_banned=True)
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
        row = db.get(ProductNameResolution, res.id)
        assert row.weight_override == 0


class TestConsensusIgnoresZeroWeight:
    def _seed_user_vote(self, db, store, *, ean: str, shadow_banned: bool) -> None:
        u = _make_user(db, shadow_banned=shadow_banned)
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

    def test_banned_vote_does_not_add_to_distinct_validators(self, db, store):
        # 1 normal user votes EAN_A.
        self._seed_user_vote(db, store, ean=EAN_A, shadow_banned=False)
        # 1 banned user also votes EAN_A — should not bring distinct count up.
        self._seed_user_vote(db, store, ean=EAN_A, shadow_banned=True)

        res = get_consensus_for_label(db, store_id=store.id, normalized_label=LABEL)
        assert res is not None
        assert res.distinct_validators == 1

    def test_banned_vote_carries_zero_weight(self, db, store):
        # 4 normal users vote EAN_A → quorum + convergence reached.
        for _ in range(4):
            self._seed_user_vote(db, store, ean=EAN_A, shadow_banned=False)
        # 1 banned user spams EAN_B — should not flip the consensus.
        self._seed_user_vote(db, store, ean=EAN_B, shadow_banned=True)

        res = get_consensus_for_label(db, store_id=store.id, normalized_label=LABEL)
        assert res is not None
        assert res.ean == EAN_A
        # 4 of 4 effective votes are EAN_A → 100%.
        assert res.top1_pct == 100.0
        assert res.state == ConsensusState.VERIFIED

    def test_banned_vote_row_still_exists_in_audit_trail(self, db, store):
        u = _make_user(db, shadow_banned=True)
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
        # Append-only respected — the row IS persisted, just with weight 0.
        rows = db.execute(
            text("SELECT weight_override FROM product_name_resolutions WHERE scan_id = :sid"),
            {"sid": str(sc.id)},
        ).fetchall()
        assert len(rows) == 1
        assert rows[0].weight_override == 0
