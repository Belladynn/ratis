"""
TDD tests for the notification_outbox pattern (DA-15).

Covers:
  - enqueue_notification — inserts a row within the active transaction
  - process_outbox_batch — reads unsent rows, dispatches via notify_user, marks sent_at
  - maybe_increment_challenge + milestone threshold → outbox row inserted
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

from repositories.notification_repository import enqueue_notification, process_outbox_batch
from sqlalchemy import text

from tests.conftest import make_user

# ---------------------------------------------------------------------------
# enqueue_notification
# ---------------------------------------------------------------------------


class TestEnqueueNotification:
    def test_enqueue_inserts_row(self, db):
        """enqueue_notification inserts one row in notification_outbox."""
        uid = make_user(db)

        enqueue_notification(db, uid, "challenge_milestone_unlocked", {"label": "Palier 1"})
        db.commit()

        row = db.execute(
            text("SELECT type, data, sent_at FROM notification_outbox WHERE user_id = :uid"),
            {"uid": uid},
        ).first()

        assert row is not None
        assert row.type == "challenge_milestone_unlocked"
        assert row.data["label"] == "Palier 1"
        assert row.sent_at is None  # not dispatched yet

    def test_enqueue_defaults_empty_data(self, db):
        """enqueue_notification with data=None stores '{}'."""
        uid = make_user(db)

        enqueue_notification(db, uid, "some_event", None)
        db.commit()

        data = db.execute(
            text("SELECT data FROM notification_outbox WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        assert data == {}

    def test_multiple_enqueues_same_user(self, db):
        """Multiple notifications for the same user all stored as separate rows."""
        uid = make_user(db)

        enqueue_notification(db, uid, "event_a", {"x": 1})
        enqueue_notification(db, uid, "event_b", {"x": 2})
        db.commit()

        count = db.execute(
            text("SELECT COUNT(*) FROM notification_outbox WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        assert count == 2


# ---------------------------------------------------------------------------
# process_outbox_batch
# ---------------------------------------------------------------------------


class TestProcessOutboxBatch:
    def _insert_outbox(
        self,
        db,
        user_id: uuid.UUID,
        notif_type: str = "test_event",
        data: dict | None = None,
    ) -> uuid.UUID:
        """Helper: insert a raw outbox row (bypasses enqueue_notification)."""
        import json

        row_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO notification_outbox (id, user_id, type, data) "
                "VALUES (:id, :uid, :type, CAST(:data AS jsonb))"
            ),
            {"id": row_id, "uid": user_id, "type": notif_type, "data": json.dumps(data or {})},
        )
        db.commit()
        return row_id

    def test_dispatches_unsent_rows(self, db):
        """process_outbox_batch calls notify_user for each unsent row."""
        uid = make_user(db)
        self._insert_outbox(db, uid, "test_event", {"k": "v"})

        with patch("repositories.notification_repository.notify_user") as mock_notify:
            dispatched = process_outbox_batch(db)

        assert dispatched == 1
        mock_notify.assert_called_once_with(uid, "test_event", {"k": "v"})

    def test_marks_sent_at_after_dispatch(self, db):
        """sent_at is set after successful dispatch."""
        uid = make_user(db)
        row_id = self._insert_outbox(db, uid, "test_event")

        with patch("repositories.notification_repository.notify_user"):
            process_outbox_batch(db)

        sent_at = db.execute(
            text("SELECT sent_at FROM notification_outbox WHERE id = :id"),
            {"id": row_id},
        ).scalar()
        assert sent_at is not None

    def test_skips_already_sent_rows(self, db):
        """Rows with sent_at already set are not dispatched again."""
        uid = make_user(db)
        # Insert and mark as sent directly
        row_id = self._insert_outbox(db, uid, "already_sent")
        db.execute(
            text("UPDATE notification_outbox SET sent_at = now() WHERE id = :id"),
            {"id": row_id},
        )
        db.commit()

        with patch("repositories.notification_repository.notify_user") as mock_notify:
            dispatched = process_outbox_batch(db)

        assert dispatched == 0
        mock_notify.assert_not_called()

    def test_notify_error_leaves_row_unsent(self, db):
        """When notify_user raises, sent_at stays NULL so the row is retried."""
        uid = make_user(db)
        row_id = self._insert_outbox(db, uid, "flaky_event")

        with patch(
            "repositories.notification_repository.notify_user",
            side_effect=RuntimeError("notifier down"),
        ):
            dispatched = process_outbox_batch(db)

        assert dispatched == 0

        sent_at = db.execute(
            text("SELECT sent_at FROM notification_outbox WHERE id = :id"),
            {"id": row_id},
        ).scalar()
        assert sent_at is None  # not marked sent — will be retried

    def test_batch_size_limits_rows_processed(self, db):
        """process_outbox_batch respects batch_size limit."""
        uid = make_user(db)
        for i in range(5):
            self._insert_outbox(db, uid, f"event_{i}")

        with patch("repositories.notification_repository.notify_user"):
            dispatched = process_outbox_batch(db, batch_size=3)

        assert dispatched == 3

        remaining = db.execute(
            text("SELECT COUNT(*) FROM notification_outbox WHERE user_id = :uid AND sent_at IS NULL"),
            {"uid": uid},
        ).scalar()
        assert remaining == 2


# ---------------------------------------------------------------------------
# maybe_increment_challenge → outbox integration
# ---------------------------------------------------------------------------


class TestChallengeOutboxIntegration:
    def test_milestone_crossed_enqueues_notification(self, db):
        """
        When maybe_increment_challenge crosses a milestone threshold,
        a notification_outbox row is inserted in the same transaction.
        """
        import json

        from repositories.challenge_repository import maybe_increment_challenge

        uid = make_user(db)

        # Create challenge with threshold=1 — first increment should trigger it
        challenge_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO community_challenges "
                "    (id, title, description, action_type, action_filter, objective, "
                "     starts_at, ends_at, grace_period_days, is_active) "
                "VALUES (:id, 'Test', NULL, 'receipt_scan', NULL, 10, "
                "        now() - interval '1 day', now() + interval '7 days', 3, TRUE)"
            ),
            {"id": challenge_id},
        )
        db.execute(
            text("INSERT INTO community_challenge_progress (challenge_id, current_count) VALUES (:cid, 0)"),
            {"cid": challenge_id},
        )
        milestone_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO community_challenge_milestones "
                "    (id, challenge_id, threshold, reward_type, reward_value, label, sort_order) "
                "VALUES (:id, :cid, 1, 'cab', CAST(:rval AS jsonb), 'Palier 1', 1)"
            ),
            {"id": milestone_id, "cid": challenge_id, "rval": json.dumps({"amount": 100})},
        )
        db.commit()

        maybe_increment_challenge(db, uid, "receipt_scan")
        db.commit()

        row = db.execute(
            text("SELECT type, data FROM notification_outbox WHERE user_id = :uid"),
            {"uid": uid},
        ).first()

        assert row is not None
        assert row.type == "challenge_milestone_unlocked"
        assert row.data["label"] == "Palier 1"

    def test_no_outbox_row_when_threshold_not_crossed(self, db):
        """
        When increment does NOT cross a threshold, no outbox row is inserted.
        """
        import json

        from repositories.challenge_repository import maybe_increment_challenge

        uid = make_user(db)

        challenge_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO community_challenges "
                "    (id, title, description, action_type, action_filter, objective, "
                "     starts_at, ends_at, grace_period_days, is_active) "
                "VALUES (:id, 'Test', NULL, 'receipt_scan', NULL, 10, "
                "        now() - interval '1 day', now() + interval '7 days', 3, TRUE)"
            ),
            {"id": challenge_id},
        )
        db.execute(
            text("INSERT INTO community_challenge_progress (challenge_id, current_count) VALUES (:cid, 0)"),
            {"cid": challenge_id},
        )
        # threshold=5 — first increment gives count=1, not 5
        db.execute(
            text(
                "INSERT INTO community_challenge_milestones "
                "    (id, challenge_id, threshold, reward_type, reward_value, label, sort_order) "
                "VALUES (:id, :cid, 5, 'cab', CAST(:rval AS jsonb), 'Palier 1', 1)"
            ),
            {"id": uuid.uuid4(), "cid": challenge_id, "rval": json.dumps({"amount": 100})},
        )
        db.commit()

        maybe_increment_challenge(db, uid, "receipt_scan")
        db.commit()

        count = db.execute(
            text("SELECT COUNT(*) FROM notification_outbox WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        assert count == 0
