"""
Phase B — POST /rewards/events/action endpoint and qualifier-aware missions.

Tests the new ``trigger_action`` flow that supersedes the V0
``notify_scan_accepted`` legacy endpoint :

- Idempotency : caller-provided ``idempotency_key`` deduplicates retries.
- Qualifier filtering : missions only progress when the event qualifier
  matches (or both are NULL).
- ``scan_distinct`` : tracked_values JSONB array counts distinct values.
- Lazy-gen extension : the unique constraint now includes qualifier so
  multiple (action_type, NULL/qualifier) missions can coexist active.
- ``reward_events`` audit table : every event leaves a row.
- Caller migration : PA worker emits the right events for each scan_type.
- Legacy endpoint dropped : ``/rewards/events/scan_accepted`` returns 404.
- Rename : no row references ``barcode_scan`` after migration — every
  legacy reference uses ``product_identification`` instead.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from repositories.cab_repository import get_balance
from sqlalchemy import text

from tests.conftest import make_mission, make_user

TODAY = datetime.now(UTC).date()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post_action(
    client,
    *,
    user_id,
    action_type: str,
    qualifier: str | None = None,
    quantity: int = 1,
    idempotency_key: str | None = None,
    context: dict | None = None,
) -> dict:
    """Wrapper around the new POST /rewards/events/action helper."""
    body: dict = {
        "user_id": str(user_id),
        "action_type": action_type,
        "quantity": quantity,
    }
    if qualifier is not None:
        body["qualifier"] = qualifier
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    if context is not None:
        body["context"] = context
    return client.post("/api/v1/rewards/events/action", json=body)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_same_idempotency_key_processes_once(self, client, db):
        """Two calls with the same idempotency_key award CAB exactly once."""
        uid = make_user(db)
        key = "scan-abc-123"

        resp1 = _post_action(
            client,
            user_id=uid,
            action_type="receipt_scan",
            idempotency_key=key,
        )
        assert resp1.status_code == 200
        assert get_balance(db, uid) == 20  # cab_per_receipt_scan (V1.x recal)

        resp2 = _post_action(
            client,
            user_id=uid,
            action_type="receipt_scan",
            idempotency_key=key,
        )
        assert resp2.status_code == 200
        # Balance unchanged on the duplicate — single row in cabecoin_transactions.
        assert get_balance(db, uid) == 20

        n = db.execute(
            text("SELECT count(*) FROM cabecoin_transactions WHERE user_id = :uid AND reason = 'receipt_scan'"),
            {"uid": uid},
        ).scalar()
        assert n == 1

    def test_duplicate_marks_status_duplicate(self, client, db):
        """The second call leaves a reward_events row with status='duplicate'."""
        uid = make_user(db)
        key = "scan-dup-key"

        _post_action(client, user_id=uid, action_type="receipt_scan", idempotency_key=key)
        _post_action(client, user_id=uid, action_type="receipt_scan", idempotency_key=key)

        # Exactly one row with status='processed', the duplicate logged
        # for forensics — the implementation may either UPSERT a single
        # row or INSERT a second row marked 'duplicate'. Both shapes
        # satisfy the contract : at most ONE 'processed' row per key.
        processed = db.execute(
            text("SELECT count(*) FROM reward_events WHERE idempotency_key = :k AND status = 'processed'"),
            {"k": key},
        ).scalar()
        assert processed == 1

    def test_server_generates_key_when_omitted(self, client, db):
        """No idempotency_key supplied → server still inserts a reward_events row."""
        uid = make_user(db)

        resp = _post_action(
            client,
            user_id=uid,
            action_type="receipt_scan",
            context={"scan_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 200
        n = db.execute(
            text("SELECT count(*) FROM reward_events WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        assert n == 1


# ---------------------------------------------------------------------------
# Qualifier matching
# ---------------------------------------------------------------------------


class TestQualifierMatching:
    def test_qualifier_organic_matches_only_organic_events(self, client, db):
        """Mission qualifier=attribute:organic only progresses on organic events."""
        uid = make_user(db)
        mid = make_mission(
            db,
            action_type="product_identification",
            frequency="daily",
            difficulty="easy",
            target_count=2,
            cab_reward=10,
            qualifier="attribute:organic",
        )

        # Event without qualifier — should NOT progress the organic mission.
        _post_action(client, user_id=uid, action_type="product_identification")
        row = db.execute(
            text("SELECT current_count FROM user_missions WHERE user_id = :uid AND mission_id = :mid"),
            {"uid": uid, "mid": mid},
        ).first()
        assert row is None or row.current_count == 0

        # Event with matching qualifier — progresses.
        _post_action(
            client,
            user_id=uid,
            action_type="product_identification",
            qualifier="attribute:organic",
        )
        row = db.execute(
            text("SELECT current_count FROM user_missions WHERE user_id = :uid AND mission_id = :mid"),
            {"uid": uid, "mid": mid},
        ).first()
        assert row is not None
        assert row.current_count == 1

    def test_null_qualifier_mission_matches_any_qualifier_event(self, client, db):
        """Mission qualifier=NULL progresses on any event of that action_type."""
        uid = make_user(db)
        mid = make_mission(
            db,
            action_type="product_identification",
            frequency="daily",
            difficulty="easy",
            target_count=5,
            cab_reward=10,
        )
        db.commit()

        _post_action(client, user_id=uid, action_type="product_identification")
        _post_action(
            client,
            user_id=uid,
            action_type="product_identification",
            qualifier="attribute:organic",
        )

        row = db.execute(
            text("SELECT current_count FROM user_missions WHERE user_id = :uid AND mission_id = :mid"),
            {"uid": uid, "mid": mid},
        ).first()
        assert row is not None
        assert row.current_count == 2


# ---------------------------------------------------------------------------
# scan_distinct + tracked_values
# ---------------------------------------------------------------------------


class TestScanDistinct:
    def test_distinct_categories_tracked_in_jsonb(self, client, db):
        """scan_distinct mission counts unique values in tracked_values."""
        uid = make_user(db)
        mid = make_mission(
            db,
            action_type="scan_distinct",
            frequency="weekly",
            difficulty="easy",
            target_count=2,
            cab_reward=50,
            qualifier="category",
        )

        # Three events : 2 distinct categories — third one is a duplicate.
        _post_action(
            client,
            user_id=uid,
            action_type="scan_distinct",
            qualifier="category:dairy",
        )
        _post_action(
            client,
            user_id=uid,
            action_type="scan_distinct",
            qualifier="category:bakery",
        )
        _post_action(
            client,
            user_id=uid,
            action_type="scan_distinct",
            qualifier="category:dairy",
        )

        row = db.execute(
            text(
                "SELECT current_count, status, tracked_values "
                "FROM user_missions "
                "WHERE user_id = :uid AND mission_id = :mid"
            ),
            {"uid": uid, "mid": mid},
        ).first()
        assert row is not None
        # 2 distinct values — completes the target.
        assert row.current_count == 2
        assert row.status == "completed"
        # tracked_values is a JSONB array carrying both values, no duplicate.
        assert isinstance(row.tracked_values, list)
        assert sorted(row.tracked_values) == ["category:bakery", "category:dairy"]

    def test_store_distinct_qualifier(self, client, db):
        """scan_distinct, qualifier='store' tracks distinct store_ids."""
        uid = make_user(db)
        mid = make_mission(
            db,
            action_type="scan_distinct",
            frequency="weekly",
            difficulty="easy",
            target_count=2,
            cab_reward=50,
            qualifier="store",
        )

        store_a = uuid.uuid4()
        store_b = uuid.uuid4()
        _post_action(
            client,
            user_id=uid,
            action_type="scan_distinct",
            qualifier=f"store:{store_a}",
        )
        _post_action(
            client,
            user_id=uid,
            action_type="scan_distinct",
            qualifier=f"store:{store_b}",
        )
        # Duplicate store_a : should not advance.
        _post_action(
            client,
            user_id=uid,
            action_type="scan_distinct",
            qualifier=f"store:{store_a}",
        )

        row = db.execute(
            text("SELECT current_count, tracked_values FROM user_missions WHERE user_id = :uid AND mission_id = :mid"),
            {"uid": uid, "mid": mid},
        ).first()
        assert row.current_count == 2
        assert sorted(row.tracked_values) == sorted([f"store:{store_a}", f"store:{store_b}"])


# ---------------------------------------------------------------------------
# Lazy-gen extension : qualifier-aware
# ---------------------------------------------------------------------------


class TestLazyGenQualifierExtension:
    def test_two_missions_same_action_type_different_qualifiers_can_coexist(self, db, user_client):
        """The lazy-gen now permits two missions with the same (action_type,
        frequency, difficulty) when their qualifiers differ — the unique
        constraint includes qualifier (NULLS NOT DISTINCT)."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        # Same (action_type, frequency, difficulty) but different qualifier.
        # The phase B unique constraint includes qualifier, so these two
        # rows coexist.
        make_mission(
            db,
            action_type="product_identification",
            frequency="daily",
            difficulty="easy",
            target_count=1,
            cab_reward=5,
        )
        make_mission(
            db,
            action_type="product_identification",
            frequency="daily",
            difficulty="easy",
            target_count=1,
            cab_reward=5,
            qualifier="attribute:organic",
        )

        # GET /missions does the lazy gen ; it MUST tolerate the pair.
        # Real catalogue may select only one ; we only assert no crash.
        resp = client.get("/api/v1/gamification/missions")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Audit trail (reward_events)
# ---------------------------------------------------------------------------


class TestRewardEventsAuditTrail:
    def test_payload_persisted(self, client, db):
        """The context payload is persisted in reward_events.payload."""
        uid = make_user(db)
        scan_id = str(uuid.uuid4())
        _post_action(
            client,
            user_id=uid,
            action_type="receipt_scan",
            context={"scan_id": scan_id, "store_id": "abc"},
        )
        row = db.execute(
            text("SELECT payload, status, processed_at FROM reward_events WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row is not None
        assert row.status == "processed"
        assert row.processed_at is not None
        assert row.payload is not None
        assert row.payload.get("scan_id") == scan_id

    def test_chronological_index(self, client, db):
        """Events are queryable by user/action_type with a time order."""
        uid = make_user(db)
        for i in range(3):
            _post_action(
                client,
                user_id=uid,
                action_type="receipt_scan",
                idempotency_key=f"k-{i}",
            )
        rows = db.execute(
            text(
                "SELECT created_at FROM reward_events "
                "WHERE user_id = :uid AND action_type = 'receipt_scan' "
                "ORDER BY created_at ASC"
            ),
            {"uid": uid},
        ).fetchall()
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# Quantity multiplier
# ---------------------------------------------------------------------------


class TestQuantityMultiplier:
    def test_quantity_multiplies_cab_award(self, client, db):
        """quantity=N multiplies the base CAB award by N."""
        uid = make_user(db)
        resp = _post_action(
            client,
            user_id=uid,
            action_type="receipt_scan",
            quantity=3,
        )
        assert resp.status_code == 200
        # Base receipt = 20 CAB (V1.x recal). Quantity 3 → 60 CAB.
        assert get_balance(db, uid) == 60

    def test_quantity_must_be_positive(self, client, db):
        """quantity=0 is rejected by Pydantic validation (422)."""
        uid = make_user(db)
        resp = _post_action(
            client,
            user_id=uid,
            action_type="receipt_scan",
            quantity=0,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Diminishing returns — F-RW-gamif-4 (ARCH_cab_economy § "Diminishing
# returns journaliers")
# ---------------------------------------------------------------------------


class TestDiminishingReturns:
    """CAB earned beyond the daily threshold is halved (×0.5).

    ARCH_cab_economy : label_scan halves after 20 scans/day,
    fill_product_field halves after 10 fields/day. Thresholds &
    multiplier live in ratis_settings.json (rewards.diminishing_returns).
    Base CAB : cab_per_label_scan=3, cab_per_fill_product_field=5.
    """

    def test_label_scan_under_threshold_full_rate(self, client, db):
        """quantity exactly at the threshold → no diminishing applied."""
        uid = make_user(db)
        resp = _post_action(
            client,
            user_id=uid,
            action_type="label_scan",
            quantity=20,
        )
        assert resp.status_code == 200
        # 20 × 3 = 60, all under threshold.
        assert get_balance(db, uid) == 60

    def test_label_scan_over_threshold_halves_excess(self, client, db):
        """quantity 24, threshold 20 → 20 full + 4 halved.

        20 × 3 + round(4 × 3 × 0.5) = 60 + 6 = 66.
        """
        uid = make_user(db)
        resp = _post_action(
            client,
            user_id=uid,
            action_type="label_scan",
            quantity=24,
        )
        assert resp.status_code == 200
        assert get_balance(db, uid) == 66

    def test_fill_product_field_over_threshold_halves_excess(self, client, db):
        """quantity 14, threshold 10 → 10 full + 4 halved.

        10 × 5 + round(4 × 5 × 0.5) = 50 + 10 = 60.
        """
        uid = make_user(db)
        resp = _post_action(
            client,
            user_id=uid,
            action_type="fill_product_field",
            quantity=14,
        )
        assert resp.status_code == 200
        assert get_balance(db, uid) == 60

    def test_diminishing_counts_across_calls(self, client, db):
        """Today's prior label_scan rows count toward the threshold.

        Call 1 : quantity 20 → 60 CAB (all under).
        Call 2 : quantity 2  → already 20 done today → both halved →
                 round(2 × 3 × 0.5) = 3 CAB.
        Total = 63.
        """
        uid = make_user(db)
        r1 = _post_action(
            client,
            user_id=uid,
            action_type="label_scan",
            quantity=20,
            idempotency_key="dr-call-1",
        )
        assert r1.status_code == 200
        r2 = _post_action(
            client,
            user_id=uid,
            action_type="label_scan",
            quantity=2,
            idempotency_key="dr-call-2",
        )
        assert r2.status_code == 200
        assert get_balance(db, uid) == 63

    def test_receipt_scan_unaffected_by_diminishing(self, client, db):
        """receipt_scan has no diminishing config → always full rate."""
        uid = make_user(db)
        resp = _post_action(
            client,
            user_id=uid,
            action_type="receipt_scan",
            quantity=50,
        )
        assert resp.status_code == 200
        # 50 × 20 = 1000, no halving (receipt_scan not in config).
        assert get_balance(db, uid) == 1000


# ---------------------------------------------------------------------------
# Migration / catalogue invariants
# ---------------------------------------------------------------------------


class TestMigrationInvariants:
    """Phase B migration guarantees on the seeded catalogue."""

    @pytest.fixture
    def reseeded(self, db):
        """Reset the catalogue and apply the V1 (now phase-B-aware) seed."""
        from ratis_core.seed.missions_v1 import seed_missions_catalog_v1

        db.execute(text("DELETE FROM user_missions"))
        db.execute(text("DELETE FROM missions"))
        db.flush()
        seed_missions_catalog_v1(db)
        db.flush()
        return db

    def test_no_legacy_barcode_scan_action_type_after_migration(self, reseeded):
        """After phase B, no row uses the legacy 'barcode_scan' name —
        all renamed to 'product_identification'."""
        n = reseeded.execute(text("SELECT count(*) FROM missions WHERE action_type = 'barcode_scan'")).scalar()
        assert n == 0

    def test_qualifiers_are_prefixed(self, reseeded):
        """Every non-NULL qualifier carries its type prefix
        (attribute: / category / store)."""
        rows = reseeded.execute(text("SELECT DISTINCT qualifier FROM missions WHERE qualifier IS NOT NULL")).fetchall()
        for r in rows:
            q = r.qualifier
            assert q in {"category", "store"} or q.startswith(("attribute:", "category:", "store:")), (
                f"unexpected qualifier shape : {q!r}"
            )

    def test_all_41_templates_active_post_phase_b(self, reseeded):
        """Phase B unlocks every template, EXCEPT the 9 attribute-qualifier
        rows held back until phase C (PA worker qualifier enrichment).
        Catalogue holds 41 total / 32 active. See
        ``test_qualifier_attribute_templates_inactive`` in
        ``test_missions_catalog_v1.py`` for the canonical assertion on
        the 9 deactivated rows."""
        n_active = reseeded.execute(text("SELECT count(*) FROM missions WHERE is_active = TRUE")).scalar()
        n_total = reseeded.execute(text("SELECT count(*) FROM missions")).scalar()
        assert n_total == 41
        assert n_active == 32


# ---------------------------------------------------------------------------
# Legacy endpoint removal
# ---------------------------------------------------------------------------


class TestLegacyEndpointRemoved:
    def test_scan_accepted_endpoint_returns_404(self, client, db):
        """The V0 endpoint is removed — POSTing it now returns 404."""
        uid = make_user(db)
        resp = client.post(
            "/api/v1/rewards/events/scan_accepted",
            json={
                "user_id": str(uid),
                "scan_id": str(uuid.uuid4()),
                "scan_type": "receipt",
            },
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Caller side : rewards_client.trigger_action
# ---------------------------------------------------------------------------


class TestRewardsClientHelper:
    def test_trigger_action_signature(self):
        """ratis_core.rewards_client exposes trigger_action with the
        documented kwargs."""
        from ratis_core import rewards_client

        assert hasattr(rewards_client, "trigger_action")

    def test_notify_scan_accepted_removed(self):
        """The legacy fire-and-forget helper has been deleted."""
        from ratis_core import rewards_client

        assert not hasattr(rewards_client, "notify_scan_accepted")
