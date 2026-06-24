"""Pure unit tests for the optimization engine — no DB, no HTTP, no conftest deps."""

import uuid
from decimal import Decimal

from services.optimization_engine import (
    ItemPrice,
    StoreAssignment,
    assign_items_to_stores,
    cap_to_max_stores,
    redistribute_under_threshold,
)


def _uid():
    return uuid.uuid4()


class TestAssignItemsToStores:
    """Test the core assignment algorithm."""

    def test_simple_cheapest_store(self):
        """Each item goes to its cheapest store."""
        store_a, store_b = _uid(), _uid()
        ean_1, ean_2 = "1111111111111", "2222222222222"

        prices = {
            (store_a, ean_1): ItemPrice(price=Decimal("2.50"), source="consensus_local", trust_score=Decimal("90")),
            (store_b, ean_1): ItemPrice(price=Decimal("3.00"), source="consensus_local", trust_score=Decimal("85")),
            (store_a, ean_2): ItemPrice(price=Decimal("5.00"), source="consensus_local", trust_score=Decimal("90")),
            (store_b, ean_2): ItemPrice(price=Decimal("4.00"), source="consensus_local", trust_score=Decimal("85")),
        }

        items = [ean_1, ean_2]
        stores = [store_a, store_b]

        result = assign_items_to_stores(items, stores, prices, min_items_per_store=1)

        # ean_1 -> store_a (2.50 < 3.00), ean_2 -> store_b (4.00 < 5.00)
        assert result[ean_1].store_id == store_a
        assert result[ean_2].store_id == store_b

    def test_threshold_redistributes_small_stores(self):
        """Store with fewer items than threshold -> redistribute to others."""
        store_a, store_b, store_c = _uid(), _uid(), _uid()
        ean_1, ean_2, ean_3, ean_4 = (
            "1111111111111",
            "2222222222222",
            "3333333333333",
            "4444444444444",
        )

        # store_a is cheapest for ean_1, ean_2, ean_3 (3 items)
        # store_c is cheapest for ean_4 only (1 item, below threshold of 2)
        prices = {
            (store_a, ean_1): ItemPrice(price=Decimal("1.00"), source="consensus_local", trust_score=Decimal("90")),
            (store_b, ean_1): ItemPrice(price=Decimal("1.50"), source="consensus_local", trust_score=Decimal("80")),
            (store_c, ean_1): ItemPrice(price=Decimal("2.00"), source="consensus_local", trust_score=Decimal("70")),
            (store_a, ean_2): ItemPrice(price=Decimal("2.00"), source="consensus_local", trust_score=Decimal("90")),
            (store_b, ean_2): ItemPrice(price=Decimal("2.50"), source="consensus_local", trust_score=Decimal("80")),
            (store_c, ean_2): ItemPrice(price=Decimal("3.00"), source="consensus_local", trust_score=Decimal("70")),
            (store_a, ean_3): ItemPrice(price=Decimal("1.50"), source="consensus_local", trust_score=Decimal("90")),
            (store_b, ean_3): ItemPrice(price=Decimal("2.00"), source="consensus_local", trust_score=Decimal("80")),
            (store_c, ean_3): ItemPrice(price=Decimal("1.80"), source="consensus_local", trust_score=Decimal("70")),
            (store_a, ean_4): ItemPrice(price=Decimal("4.00"), source="consensus_local", trust_score=Decimal("90")),
            (store_b, ean_4): ItemPrice(price=Decimal("5.00"), source="consensus_local", trust_score=Decimal("80")),
            (store_c, ean_4): ItemPrice(price=Decimal("3.50"), source="consensus_local", trust_score=Decimal("70")),
        }

        items = [ean_1, ean_2, ean_3, ean_4]
        stores = [store_a, store_b, store_c]

        result = assign_items_to_stores(items, stores, prices, min_items_per_store=2)

        # store_c had only 1 item (ean_4) -> below threshold -> redistributed
        # ean_4 should go to store_a (4.00) since store_a already meets threshold
        assigned_stores = {a.store_id for a in result.values()}
        assert store_c not in assigned_stores
        assert result[ean_4].store_id == store_a  # cheapest among qualifying stores

    def test_all_items_same_store(self):
        """When one store is cheapest for everything, all items go there."""
        store_a, store_b = _uid(), _uid()
        ean_1, ean_2, ean_3 = "1111111111111", "2222222222222", "3333333333333"

        prices = {
            (store_a, ean_1): ItemPrice(price=Decimal("1.00"), source="consensus_local", trust_score=Decimal("90")),
            (store_b, ean_1): ItemPrice(price=Decimal("2.00"), source="consensus_local", trust_score=Decimal("80")),
            (store_a, ean_2): ItemPrice(price=Decimal("1.50"), source="consensus_local", trust_score=Decimal("90")),
            (store_b, ean_2): ItemPrice(price=Decimal("2.50"), source="consensus_local", trust_score=Decimal("80")),
            (store_a, ean_3): ItemPrice(price=Decimal("3.00"), source="consensus_local", trust_score=Decimal("90")),
            (store_b, ean_3): ItemPrice(price=Decimal("4.00"), source="consensus_local", trust_score=Decimal("80")),
        }

        result = assign_items_to_stores([ean_1, ean_2, ean_3], [store_a, store_b], prices, min_items_per_store=1)

        for ean in [ean_1, ean_2, ean_3]:
            assert result[ean].store_id == store_a

    def test_item_with_no_price_at_any_store(self):
        """Item not in the price matrix -> assigned with price=None, source=unknown."""
        store_a = _uid()
        ean_1, ean_unknown = "1111111111111", "9999999999999"

        prices = {
            (store_a, ean_1): ItemPrice(price=Decimal("2.00"), source="consensus_local", trust_score=Decimal("90")),
            # ean_unknown has no price anywhere
        }

        result = assign_items_to_stores([ean_1, ean_unknown], [store_a], prices, min_items_per_store=1)

        assert result[ean_1].store_id == store_a
        assert result[ean_unknown].price is None
        assert result[ean_unknown].source == "unknown"

    def test_national_average_preserved(self):
        """Items with national_average source keep that metadata."""
        store_a = _uid()
        ean_1 = "1111111111111"

        prices = {
            (store_a, ean_1): ItemPrice(price=Decimal("3.50"), source="national_average", trust_score=None),
        }

        result = assign_items_to_stores([ean_1], [store_a], prices, min_items_per_store=1)
        assert result[ean_1].source == "national_average"
        assert result[ean_1].trust_score is None

    def test_empty_items_returns_empty(self):
        """No items -> empty result."""
        result = assign_items_to_stores([], [_uid()], {}, min_items_per_store=1)
        assert result == {}

    def test_single_item_single_store(self):
        """Simplest case: 1 item, 1 store."""
        store_a = _uid()
        ean = "1111111111111"
        prices = {
            (store_a, ean): ItemPrice(price=Decimal("5.00"), source="consensus_local", trust_score=Decimal("95")),
        }
        result = assign_items_to_stores([ean], [store_a], prices, min_items_per_store=1)
        assert result[ean].store_id == store_a
        assert result[ean].price == Decimal("5.00")


class TestRedistributeUnderThreshold:
    """Test the redistribution step directly."""

    def test_no_redistribution_when_all_meet_threshold(self):
        """If all stores meet the threshold, nothing changes."""
        store_a, store_b = _uid(), _uid()
        assignments = {
            "1111111111111": StoreAssignment(
                store_id=store_a,
                price=Decimal("1.00"),
                source="consensus_local",
                trust_score=Decimal("90"),
            ),
            "2222222222222": StoreAssignment(
                store_id=store_a,
                price=Decimal("2.00"),
                source="consensus_local",
                trust_score=Decimal("90"),
            ),
            "3333333333333": StoreAssignment(
                store_id=store_b,
                price=Decimal("1.50"),
                source="consensus_local",
                trust_score=Decimal("85"),
            ),
            "4444444444444": StoreAssignment(
                store_id=store_b,
                price=Decimal("2.50"),
                source="consensus_local",
                trust_score=Decimal("85"),
            ),
        }

        result = redistribute_under_threshold(assignments, {}, min_items_per_store=2)

        # Nothing should change — both stores have 2 items
        assert result["1111111111111"].store_id == store_a
        assert result["2222222222222"].store_id == store_a
        assert result["3333333333333"].store_id == store_b
        assert result["4444444444444"].store_id == store_b

    def test_cascading_redistribution(self):
        """Removing one store may cause another to fall below threshold."""
        store_a, store_b, store_c = _uid(), _uid(), _uid()
        ean_1, ean_2, ean_3, ean_4, ean_5 = (
            "1111111111111",
            "2222222222222",
            "3333333333333",
            "4444444444444",
            "5555555555555",
        )

        # store_a: 3 items, store_b: 1 item, store_c: 1 item
        # With threshold=2, store_b and store_c get redistributed
        assignments = {
            ean_1: StoreAssignment(
                store_id=store_a,
                price=Decimal("1.00"),
                source="consensus_local",
                trust_score=Decimal("90"),
            ),
            ean_2: StoreAssignment(
                store_id=store_a,
                price=Decimal("2.00"),
                source="consensus_local",
                trust_score=Decimal("90"),
            ),
            ean_3: StoreAssignment(
                store_id=store_a,
                price=Decimal("1.50"),
                source="consensus_local",
                trust_score=Decimal("90"),
            ),
            ean_4: StoreAssignment(
                store_id=store_b,
                price=Decimal("3.00"),
                source="consensus_local",
                trust_score=Decimal("80"),
            ),
            ean_5: StoreAssignment(
                store_id=store_c,
                price=Decimal("2.50"),
                source="consensus_local",
                trust_score=Decimal("70"),
            ),
        }

        prices = {
            (store_a, ean_4): ItemPrice(price=Decimal("3.50"), source="consensus_local", trust_score=Decimal("90")),
            (store_a, ean_5): ItemPrice(price=Decimal("3.00"), source="consensus_local", trust_score=Decimal("90")),
        }

        result = redistribute_under_threshold(assignments, prices, min_items_per_store=2)

        # Both store_b and store_c items should end up at store_a
        assigned_stores = {a.store_id for a in result.values()}
        assert store_b not in assigned_stores
        assert store_c not in assigned_stores
        assert all(a.store_id == store_a for a in result.values())

    def test_item_stays_if_no_qualifying_alternative(self):
        """If no qualifying store has a price for the item, it stays put."""
        store_a, store_b = _uid(), _uid()
        ean_1 = "1111111111111"
        ean_2 = "2222222222222"

        # store_a has 1 item (below threshold=2), store_b has 1 item
        # Neither qualifies, so no redistribution possible
        assignments = {
            ean_1: StoreAssignment(
                store_id=store_a,
                price=Decimal("1.00"),
                source="consensus_local",
                trust_score=Decimal("90"),
            ),
            ean_2: StoreAssignment(
                store_id=store_b,
                price=Decimal("2.00"),
                source="consensus_local",
                trust_score=Decimal("80"),
            ),
        }

        # No cross-store prices available
        prices = {}

        result = redistribute_under_threshold(assignments, prices, min_items_per_store=3)

        # Nothing can be redistributed — both stay in place
        assert result[ean_1].store_id == store_a
        assert result[ean_2].store_id == store_b


class TestCapToMaxStores:
    """LO-19 — cap the number of stores in a route."""

    def test_no_cap_when_under_limit(self):
        """Routes with <= max_stores are returned untouched."""
        store_a, store_b = _uid(), _uid()
        assignments = {
            "1111111111111": StoreAssignment(
                store_id=store_a,
                price=Decimal("1.00"),
                source="consensus_local",
                trust_score=Decimal("90"),
            ),
            "2222222222222": StoreAssignment(
                store_id=store_b,
                price=Decimal("2.00"),
                source="consensus_local",
                trust_score=Decimal("85"),
            ),
        }
        result = cap_to_max_stores(assignments, {}, max_stores=4)
        assert result["1111111111111"].store_id == store_a
        assert result["2222222222222"].store_id == store_b

    def test_caps_to_four_stores(self):
        """A route with 6 candidate stores collapses to at most 4."""
        stores = [_uid() for _ in range(6)]
        # One item per store -> 6 distinct stores
        assignments = {}
        prices = {}
        for i, sid in enumerate(stores):
            ean = f"{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}"
            assignments[ean] = StoreAssignment(
                store_id=sid,
                price=Decimal("1.00"),
                source="consensus_local",
                trust_score=Decimal("90"),
            )
            # Every store has a price for every item so redistribution works
            for j, other in enumerate(stores):
                prices[(other, ean)] = ItemPrice(
                    price=Decimal("1.00") + Decimal(j),
                    source="consensus_local",
                    trust_score=Decimal("90"),
                )

        result = cap_to_max_stores(assignments, prices, max_stores=4)
        assigned_stores = {a.store_id for a in result.values()}
        assert len(assigned_stores) <= 4
        # No item lost
        assert set(result.keys()) == set(assignments.keys())

    def test_keeps_largest_stores(self):
        """The kept stores are the ones holding the most items."""
        big, small_a, small_b = _uid(), _uid(), _uid()
        ean_big = ["a" * 13, "b" * 13, "c" * 13]
        ean_sa = ["d" * 13]
        ean_sb = ["e" * 13]
        assignments = {}
        for e in ean_big:
            assignments[e] = StoreAssignment(
                store_id=big,
                price=Decimal("1.00"),
                source="consensus_local",
                trust_score=Decimal("90"),
            )
        for e in ean_sa:
            assignments[e] = StoreAssignment(
                store_id=small_a,
                price=Decimal("1.00"),
                source="consensus_local",
                trust_score=Decimal("90"),
            )
        for e in ean_sb:
            assignments[e] = StoreAssignment(
                store_id=small_b,
                price=Decimal("1.00"),
                source="consensus_local",
                trust_score=Decimal("90"),
            )
        prices = {}
        for e in assignments:
            for sid in (big, small_a, small_b):
                prices[(sid, e)] = ItemPrice(
                    price=Decimal("1.00"),
                    source="consensus_local",
                    trust_score=Decimal("90"),
                )

        result = cap_to_max_stores(assignments, prices, max_stores=1)
        assigned_stores = {a.store_id for a in result.values()}
        # Only the biggest store survives
        assert assigned_stores == {big}
