"""TDD tests for the Expo push-receipt polling batch.

The batch reads unchecked ``push_receipt_tickets`` rows, polls Expo's
getReceipts endpoint, and for every ``DeviceNotRegistered`` receipt deletes
the matching ``user_push_tokens`` row. Every polled ticket is marked
``checked_at`` so it is not re-polled.

Expo HTTP is mocked throughout — no network. The SAVEPOINT isolation
fixture (conftest) rolls every test back.
"""

from __future__ import annotations

from unittest.mock import patch

import push_receipts
from sqlalchemy import text


def _device_not_registered(ticket_id: str) -> dict:
    return {
        ticket_id: {
            "status": "error",
            "message": "...",
            "details": {"error": "DeviceNotRegistered"},
        }
    }


def _ok(ticket_id: str) -> dict:
    return {ticket_id: {"status": "ok"}}


class TestDeadTokenCleanup:
    def test_device_not_registered_deletes_the_token(self, session_factory, db, make_user, add_token, add_ticket):
        """A DeviceNotRegistered receipt → the matching push token is removed."""
        user = make_user()
        token = add_token(user)
        ticket_id = "expo-ticket-dead"
        add_ticket(user, token, ticket_id)

        with patch.object(
            push_receipts,
            "fetch_receipts",
            return_value=_device_not_registered(ticket_id),
        ):
            counters = push_receipts.poll_receipts(session_factory, dry_run=False)

        assert counters["dead_tokens_removed"] == 1
        remaining = db.execute(
            text("SELECT 1 FROM user_push_tokens WHERE token = :t"),
            {"t": token},
        ).first()
        assert remaining is None

    def test_ok_receipt_keeps_the_token(self, session_factory, db, make_user, add_token, add_ticket):
        """A status='ok' receipt → the token is left intact."""
        user = make_user()
        token = add_token(user)
        ticket_id = "expo-ticket-ok"
        add_ticket(user, token, ticket_id)

        with patch.object(
            push_receipts,
            "fetch_receipts",
            return_value=_ok(ticket_id),
        ):
            counters = push_receipts.poll_receipts(session_factory, dry_run=False)

        assert counters["dead_tokens_removed"] == 0
        remaining = db.execute(
            text("SELECT 1 FROM user_push_tokens WHERE token = :t"),
            {"t": token},
        ).first()
        assert remaining is not None

    def test_only_the_dead_token_is_removed(self, session_factory, db, make_user, add_token, add_ticket):
        """User with two tokens — only the DeviceNotRegistered one is removed."""
        user = make_user()
        dead = add_token(user, "ExponentPushToken[dead]")
        alive = add_token(user, "ExponentPushToken[alive]")
        add_ticket(user, dead, "ticket-dead")
        add_ticket(user, alive, "ticket-alive")

        receipts = {
            **_device_not_registered("ticket-dead"),
            **_ok("ticket-alive"),
        }
        with patch.object(push_receipts, "fetch_receipts", return_value=receipts):
            push_receipts.poll_receipts(session_factory, dry_run=False)

        tokens = {
            r.token
            for r in db.execute(
                text("SELECT token FROM user_push_tokens WHERE user_id = :u"),
                {"u": str(user)},
            )
        }
        assert tokens == {alive}


class TestCheckedAtMarking:
    def test_polled_tickets_are_marked_checked(self, session_factory, db, make_user, add_token, add_ticket):
        """Every polled ticket gets checked_at set so it is not re-polled."""
        user = make_user()
        token = add_token(user)
        tid = add_ticket(user, token, "ticket-x")

        with patch.object(
            push_receipts,
            "fetch_receipts",
            return_value=_ok("ticket-x"),
        ):
            push_receipts.poll_receipts(session_factory, dry_run=False)

        checked = db.execute(
            text("SELECT checked_at FROM push_receipt_tickets WHERE id = :i"),
            {"i": str(tid)},
        ).scalar()
        assert checked is not None

    def test_already_checked_tickets_are_skipped(self, session_factory, make_user, add_token, add_ticket):
        """Tickets with checked_at set are not re-polled."""
        user = make_user()
        token = add_token(user)
        add_ticket(user, token, "ticket-old", checked=True)

        with patch.object(push_receipts, "fetch_receipts") as mock_fetch:
            counters = push_receipts.poll_receipts(session_factory, dry_run=False)

        mock_fetch.assert_not_called()
        assert counters["tickets_polled"] == 0

    def test_missing_receipt_still_marks_checked(self, session_factory, db, make_user, add_token, add_ticket):
        """Expo did not return a receipt for the ticket (retention expired)
        → the ticket is still marked checked, never re-polled."""
        user = make_user()
        token = add_token(user)
        tid = add_ticket(user, token, "ticket-missing")

        with patch.object(push_receipts, "fetch_receipts", return_value={}):
            push_receipts.poll_receipts(session_factory, dry_run=False)

        checked = db.execute(
            text("SELECT checked_at FROM push_receipt_tickets WHERE id = :i"),
            {"i": str(tid)},
        ).scalar()
        assert checked is not None


class TestDryRun:
    def test_dry_run_deletes_nothing_and_marks_nothing(self, session_factory, db, make_user, add_token, add_ticket):
        user = make_user()
        token = add_token(user)
        tid = add_ticket(user, token, "ticket-dry")

        with patch.object(
            push_receipts,
            "fetch_receipts",
            return_value=_device_not_registered("ticket-dry"),
        ):
            counters = push_receipts.poll_receipts(session_factory, dry_run=True)

        # Reports the would-be deletion but persists nothing.
        assert counters["dead_tokens_removed"] == 1
        assert db.execute(text("SELECT 1 FROM user_push_tokens WHERE token = :t"), {"t": token}).first() is not None
        assert (
            db.execute(
                text("SELECT checked_at FROM push_receipt_tickets WHERE id = :i"),
                {"i": str(tid)},
            ).scalar()
            is None
        )


class TestNoWork:
    def test_no_unchecked_tickets_is_a_noop(self, session_factory):
        with patch.object(push_receipts, "fetch_receipts") as mock_fetch:
            counters = push_receipts.poll_receipts(session_factory, dry_run=False)
        mock_fetch.assert_not_called()
        assert counters == {
            "tickets_polled": 0,
            "receipts_received": 0,
            "dead_tokens_removed": 0,
        }


class TestFetchReceiptsChunking:
    def test_fetch_receipts_chunks_requests_at_the_expo_limit(self):
        """>1000 ticket IDs → multiple getReceipts POSTs, merged result."""
        ticket_ids = [f"t{i}" for i in range(2500)]
        seen_chunks: list[int] = []

        def fake_post(url, json, timeout):
            seen_chunks.append(len(json["ids"]))

            class _Resp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"data": {tid: {"status": "ok"} for tid in json["ids"]}}

            return _Resp()

        with patch("push_receipts.httpx.post", side_effect=fake_post):
            out = push_receipts.fetch_receipts("https://expo.test/getReceipts", ticket_ids)

        assert seen_chunks == [1000, 1000, 500]
        assert len(out) == 2500
