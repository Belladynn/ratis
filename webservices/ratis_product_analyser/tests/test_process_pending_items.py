from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import patch

import pytest
from ratis_core.models.scan import Receipt, Scan
from repositories.scan_repository import process_pending_items

# ── helpers ───────────────────────────────────────────────────────────────────


def make_receipt(
    db,
    store,
    user,
    store_status: str = "confirmed",
    pending_items=None,
) -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        store_id=store.id,
        user_id=user.id,
        purchased_at=date(1970, 1, 1),
        store_status=store_status,
        pending_items=pending_items,
    )
    db.add(r)
    db.flush()
    return r


# ── tests ────────────────────────────────────────────────────────────────────


class TestProcessPendingItems:
    def test_raises_if_no_store_id(self, db, user):
        """ValueError if receipt.store_id is None."""
        r = Receipt(
            id=uuid.uuid4(),
            store_id=None,
            user_id=user.id,
            purchased_at=date(1970, 1, 1),
            store_status="pending",
            pending_items=[{"scanned_name": "PAIN", "price": 100, "quantity": 1.0}],
        )
        db.add(r)
        db.flush()
        with pytest.raises(ValueError, match="process_pending_items called with no store_id"):
            process_pending_items(db, r)

    def test_returns_empty_if_no_pending_items(self, db, store, user):
        """Returns [] and clears nothing if pending_items is None."""
        r = make_receipt(db, store, user, pending_items=None)
        result = process_pending_items(db, r)
        assert result == []
        assert r.pending_items is None

    def test_returns_empty_if_empty_list(self, db, store, user):
        """Returns [] for pending_items = []."""
        r = make_receipt(db, store, user, pending_items=[])
        result = process_pending_items(db, r)
        assert result == []

    def test_creates_scans_for_each_item(self, db, store, user):
        """Creates one Scan per item in pending_items."""
        items = [
            {"scanned_name": "PAIN", "price": 180, "quantity": 1.0},
            {"scanned_name": "CROISSANT", "price": 90, "quantity": 2.0},
        ]
        r = make_receipt(db, store, user, pending_items=items)
        with patch(
            "repositories.name_resolution_repository.get_consensus_for_label",
            return_value=None,
        ):
            scans = process_pending_items(db, r)
        assert len(scans) == 2
        names = {s.scanned_name for s in scans}
        assert names == {"PAIN", "CROISSANT"}

    def test_clears_pending_items_after_processing(self, db, store, user):
        """receipt.pending_items is None after call."""
        items = [{"scanned_name": "BEURRE", "price": 250, "quantity": 1.0}]
        r = make_receipt(db, store, user, pending_items=items)
        with patch(
            "repositories.name_resolution_repository.get_consensus_for_label",
            return_value=None,
        ):
            process_pending_items(db, r)
        assert r.pending_items is None

    def test_scan_price_is_centimes(self, db, store, user):
        """Scan.price matches the centimes value from pending_items."""
        items = [{"scanned_name": "LAIT", "price": 99, "quantity": 1.0}]
        r = make_receipt(db, store, user, pending_items=items)
        with patch(
            "repositories.name_resolution_repository.get_consensus_for_label",
            return_value=None,
        ):
            scans = process_pending_items(db, r)
        assert len(scans) == 1
        assert scans[0].price == 99

    def test_matched_product_upserts_consensus_when_confirmed(self, db, store, user, product):
        """If product matched (VERIFIED consensus exists) AND store_status='confirmed'
        → price consensus upserted."""
        items = [{"scanned_name": "Nutella 400g", "price": 350, "quantity": 1.0}]
        r = make_receipt(db, store, user, store_status="confirmed", pending_items=items)
        from repositories.consensus_state import ConsensusState
        from repositories.name_resolution_repository import ConsensusResult

        verified = ConsensusResult(
            ean=product.ean,
            distinct_validators=2,
            top1_pct=100.0,
            state=ConsensusState.VERIFIED,
        )
        with (
            patch(
                "repositories.name_resolution_repository.get_consensus_for_label",
                return_value=verified,
            ),
            patch(
                "repositories.scan_repository.upsert_price_consensus",
            ) as mock_upsert,
        ):
            scans = process_pending_items(db, r)
        assert len(scans) == 1
        mock_upsert.assert_called_once_with(db, scans[0])

    def test_no_consensus_when_store_pending(self, db, store, user, product):
        """store_status='pending' → no price consensus upserted even when
        a VERIFIED name-resolution consensus exists."""
        items = [{"scanned_name": "Nutella 400g", "price": 350, "quantity": 1.0}]
        r = make_receipt(db, store, user, store_status="pending", pending_items=items)
        from repositories.consensus_state import ConsensusState
        from repositories.name_resolution_repository import ConsensusResult

        verified = ConsensusResult(
            ean=product.ean,
            distinct_validators=2,
            top1_pct=100.0,
            state=ConsensusState.VERIFIED,
        )
        with (
            patch(
                "repositories.name_resolution_repository.get_consensus_for_label",
                return_value=verified,
            ),
            patch(
                "repositories.scan_repository.upsert_price_consensus",
            ) as mock_upsert,
        ):
            process_pending_items(db, r)
        mock_upsert.assert_not_called()

    def test_returns_scans_list(self, db, store, user):
        """Return value is a list of Scan objects (not None)."""
        items = [{"scanned_name": "CAFE", "price": 499, "quantity": 1.0}]
        r = make_receipt(db, store, user, pending_items=items)
        with patch(
            "repositories.name_resolution_repository.get_consensus_for_label",
            return_value=None,
        ):
            result = process_pending_items(db, r)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], Scan)

    def test_backfills_purchased_at_when_sentinel(self, db, store, user):
        """purchased_at sentinel (1970-01-01) is backfilled to today after processing.

        When a receipt is processed without a known store, purchased_at stays at the
        sentinel value 1970-01-01.  Once the store is resolved and process_pending_items
        is called, the date should be corrected to today.
        """
        items = [{"scanned_name": "PAIN", "price": 100, "quantity": 1.0}]
        r = make_receipt(db, store, user, pending_items=items)
        assert r.purchased_at == date(1970, 1, 1)
        with patch("repositories.name_resolution_repository.get_consensus_for_label", return_value=None):
            process_pending_items(db, r)
        assert r.purchased_at == date.today()

    def test_does_not_backfill_purchased_at_when_already_set(self, db, store, user):
        """A real purchased_at date is NOT overwritten by process_pending_items."""
        real_date = date(2026, 1, 15)
        r = Receipt(
            id=uuid.uuid4(),
            store_id=store.id,
            user_id=user.id,
            purchased_at=real_date,
            store_status="confirmed",
            pending_items=[{"scanned_name": "PAIN", "price": 100, "quantity": 1.0}],
        )
        db.add(r)
        db.flush()
        with patch("repositories.name_resolution_repository.get_consensus_for_label", return_value=None):
            process_pending_items(db, r)
        assert r.purchased_at == real_date
