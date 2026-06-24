"""Tests for the admin ``/admin/fraud_suspicions*`` endpoints — PR5.

Covered :
    GET  /admin/fraud_suspicions
        - returns ``pending`` by default (no resolution_status param)
        - filters by ``detection_signal``
        - filters by ``resolution_status``
        - filters by ``detected_after`` / ``detected_before``
        - filters by ``user_id`` (joined receipts.user_id)
        - pagination ``limit`` / ``offset`` + cap at 200
        - ordered by ``detected_at DESC``
        - 403 without ADMIN_API_KEY

    GET  /admin/fraud_suspicions/{id}
        - returns enriched detail (receipt + evidence_receipts)
        - 404 when suspicion id is unknown
        - 403 without ADMIN_API_KEY

    PATCH /admin/fraud_suspicions/{id}
        - resolves to confirmed_fraud / cleared / escalated_support
        - sets resolved_at + admin_operator
        - rejects ``pending`` as resolution_status (422)
        - rejects missing resolution_note (422)
        - rejects extra fields (422)
        - 400 ``operator_required`` when X-Admin-Operator absent
        - 404 when suspicion id is unknown
        - 409 ``already_resolved`` on a non-pending row
        - DB CHECK ``ck_fraud_suspicions_resolution_coherence`` holds
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

from ratis_core.models.fraud_suspicions import FraudSuspicion
from ratis_core.models.scan import Receipt
from ratis_core.models.user import User
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------
def _make_receipt(db, *, store, user, total_amount: int | None = 1530) -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        store_id=store.id,
        user_id=user.id,
        purchased_at=date.today(),
        image_r2_key="r.jpg",
        total_amount=total_amount,
    )
    db.add(r)
    db.flush()
    db.commit()
    return r


def _make_suspicion(
    db,
    *,
    receipt: Receipt,
    evidence_receipts: list[Receipt] | None = None,
    detection_signal: str = "phash",
    resolution_status: str = "pending",
    detected_at: datetime | None = None,
) -> FraudSuspicion:
    """INSERT a fraud_suspicion via raw SQL so we can control ``detected_at``
    (the ORM only honours server_default at INSERT, not on backdated tests).
    """
    sid = uuid.uuid4()
    evidence = evidence_receipts or []
    params: dict = {
        "id": str(sid),
        "rid": str(receipt.id),
        "ev": [str(r.id) for r in evidence],
        "sig": detection_signal,
        "stat": resolution_status,
    }
    if detected_at is None:
        db.execute(
            text(
                "INSERT INTO fraud_suspicions "
                "(id, receipt_id, evidence_receipt_ids, detection_signal, "
                " resolution_status, resolved_at, admin_operator) "
                "VALUES (:id, :rid, CAST(:ev AS uuid[]), :sig, :stat, "
                "        NULL, NULL)"
            ),
            params,
        )
    else:
        params["det"] = detected_at
        db.execute(
            text(
                "INSERT INTO fraud_suspicions "
                "(id, receipt_id, evidence_receipt_ids, detection_signal, "
                " detected_at, resolution_status, resolved_at, admin_operator) "
                "VALUES (:id, :rid, CAST(:ev AS uuid[]), :sig, :det, :stat, "
                "        NULL, NULL)"
            ),
            params,
        )
    db.commit()
    return db.get(FraudSuspicion, sid)


# ============================================================
# GET /admin/fraud_suspicions
# ============================================================
class TestListFraudSuspicions:
    def test_default_returns_only_pending(self, admin_client, db, store, user):
        r_pending = _make_receipt(db, store=store, user=user)
        r_cleared = _make_receipt(db, store=store, user=user)
        _make_suspicion(db, receipt=r_pending)
        # Insert as pending then UPDATE to a resolved state — the
        # ``ck_fraud_suspicions_resolution_coherence`` CHECK forbids
        # inserting non-pending without ``resolved_at`` (we want to
        # respect the schema invariant, not bypass it).
        s_cleared = _make_suspicion(db, receipt=r_cleared)
        db.execute(
            text(
                "UPDATE fraud_suspicions "
                "SET resolution_status = 'cleared', "
                "    resolved_at = now(), admin_operator = 'sys', "
                "    resolution_note = 'seed' "
                "WHERE id = :sid"
            ),
            {"sid": str(s_cleared.id)},
        )
        db.commit()

        r = admin_client.get("/api/v1/admin/fraud_suspicions")
        assert r.status_code == 200, r.text
        body = r.json()
        statuses = {item["resolution_status"] for item in body["items"]}
        assert statuses == {"pending"}
        assert all(item["receipt_id"] == str(r_pending.id) for item in body["items"])

    def test_filter_by_detection_signal(self, admin_client, db, store, user):
        r1 = _make_receipt(db, store=store, user=user)
        r2 = _make_receipt(db, store=store, user=user)
        _make_suspicion(db, receipt=r1, detection_signal="phash")
        _make_suspicion(db, receipt=r2, detection_signal="device_shared")
        r = admin_client.get("/api/v1/admin/fraud_suspicions?detection_signal=device_shared")
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["receipt_id"] == str(r2.id)
        assert body["items"][0]["detection_signal"] == "device_shared"

    def test_filter_invalid_detection_signal_422(self, admin_client):
        r = admin_client.get("/api/v1/admin/fraud_suspicions?detection_signal=bogus")
        assert r.status_code == 422

    def test_filter_by_resolution_status_non_default(self, admin_client, db, store, user):
        r1 = _make_receipt(db, store=store, user=user)
        r2 = _make_receipt(db, store=store, user=user)
        s1 = _make_suspicion(db, receipt=r1)  # pending
        _make_suspicion(db, receipt=r2)  # pending
        # Resolve s1
        db.execute(
            text(
                "UPDATE fraud_suspicions SET resolution_status='confirmed_fraud', "
                "    resolved_at=now(), admin_operator='alice' "
                "WHERE id = :sid"
            ),
            {"sid": str(s1.id)},
        )
        db.commit()
        r = admin_client.get("/api/v1/admin/fraud_suspicions?resolution_status=confirmed_fraud")
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["id"] == str(s1.id)

    def test_filter_by_detected_after_and_before(self, admin_client, db, store, user):
        r = _make_receipt(db, store=store, user=user)
        now = datetime.now(UTC)
        old = now - timedelta(days=10)
        recent = now - timedelta(hours=1)
        s_old = _make_suspicion(db, receipt=r, detected_at=old)
        s_recent = _make_suspicion(db, receipt=r, detected_at=recent)

        cutoff = quote((now - timedelta(days=3)).isoformat())
        r1 = admin_client.get(f"/api/v1/admin/fraud_suspicions?detected_after={cutoff}")
        assert r1.status_code == 200, r1.text
        ids = {it["id"] for it in r1.json()["items"]}
        assert str(s_recent.id) in ids
        assert str(s_old.id) not in ids

        r2 = admin_client.get(f"/api/v1/admin/fraud_suspicions?detected_before={cutoff}")
        assert r2.status_code == 200, r2.text
        ids = {it["id"] for it in r2.json()["items"]}
        assert str(s_old.id) in ids
        assert str(s_recent.id) not in ids

    def test_filter_by_user_id(self, admin_client, db, store, user):
        _other_uid = uuid.uuid4()
        other = User(
            id=_other_uid,
            email="other@ratis.fr",
            account_type="oauth",
            is_deleted=False,
        )
        db.add(other)
        db.commit()
        r_mine = _make_receipt(db, store=store, user=user)
        r_other = _make_receipt(db, store=store, user=other)
        s_mine = _make_suspicion(db, receipt=r_mine)
        s_other = _make_suspicion(db, receipt=r_other)

        r = admin_client.get(f"/api/v1/admin/fraud_suspicions?user_id={user.id}")
        assert r.status_code == 200
        ids = {it["id"] for it in r.json()["items"]}
        assert str(s_mine.id) in ids
        assert str(s_other.id) not in ids

    def test_ordered_by_detected_at_desc(self, admin_client, db, store, user):
        r = _make_receipt(db, store=store, user=user)
        now = datetime.now(UTC)
        s_old = _make_suspicion(db, receipt=r, detected_at=now - timedelta(hours=2))
        s_mid = _make_suspicion(db, receipt=r, detected_at=now - timedelta(hours=1))
        s_new = _make_suspicion(db, receipt=r, detected_at=now - timedelta(minutes=1))

        resp = admin_client.get("/api/v1/admin/fraud_suspicions")
        items = resp.json()["items"]
        ids_order = [it["id"] for it in items]
        # The 3 ids must appear in DESC creation order at the head of
        # the list — other tests' inserts may interleave at the tail.
        relevant = [i for i in ids_order if i in {str(s_old.id), str(s_mid.id), str(s_new.id)}]
        assert relevant == [str(s_new.id), str(s_mid.id), str(s_old.id)]

    def test_pagination_limit_offset(self, admin_client, db, store, user):
        r = _make_receipt(db, store=store, user=user)
        for _ in range(5):
            _make_suspicion(db, receipt=r)
        page1 = admin_client.get("/api/v1/admin/fraud_suspicions?limit=2&offset=0")
        page2 = admin_client.get("/api/v1/admin/fraud_suspicions?limit=2&offset=2")
        assert len(page1.json()["items"]) == 2
        assert len(page2.json()["items"]) == 2
        ids1 = {it["id"] for it in page1.json()["items"]}
        ids2 = {it["id"] for it in page2.json()["items"]}
        assert ids1.isdisjoint(ids2)

    def test_limit_caps_at_200(self, admin_client):
        r = admin_client.get("/api/v1/admin/fraud_suspicions?limit=1000")
        assert r.status_code == 422

    def test_unauth_without_admin_key_403(self, raw_client):
        r = raw_client.get("/api/v1/admin/fraud_suspicions")
        assert r.status_code == 403


# ============================================================
# GET /admin/fraud_suspicions/{id}
# ============================================================
class TestGetFraudSuspicion:
    def test_returns_enriched_detail(self, admin_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        evidence1 = _make_receipt(db, store=store, user=user, total_amount=999)
        evidence2 = _make_receipt(db, store=store, user=user, total_amount=2001)
        s = _make_suspicion(
            db,
            receipt=primary,
            evidence_receipts=[evidence1, evidence2],
            detection_signal="fp_global_strict",
        )

        r = admin_client.get(f"/api/v1/admin/fraud_suspicions/{s.id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == str(s.id)
        assert body["receipt_id"] == str(primary.id)
        assert body["detection_signal"] == "fp_global_strict"
        assert body["receipt"]["id"] == str(primary.id)
        assert body["receipt"]["total_amount"] == 1530
        ev_ids = {e["id"] for e in body["evidence_receipts"]}
        assert ev_ids == {str(evidence1.id), str(evidence2.id)}
        ev_totals = {e["total_amount"] for e in body["evidence_receipts"]}
        assert ev_totals == {999, 2001}

    def test_404_when_unknown(self, admin_client):
        r = admin_client.get(f"/api/v1/admin/fraud_suspicions/{uuid.uuid4()}")
        assert r.status_code == 404
        assert r.json()["detail"] == "fraud_suspicion_not_found"

    def test_unauth_without_admin_key_403(self, raw_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)
        r = raw_client.get(f"/api/v1/admin/fraud_suspicions/{s.id}")
        assert r.status_code == 403


# ============================================================
# PATCH /admin/fraud_suspicions/{id}
# ============================================================
class TestPatchFraudSuspicion:
    def test_resolve_to_confirmed_fraud(self, admin_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)

        r = admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={"resolution_status": "confirmed_fraud", "resolution_note": "dup OCR confirmed manually"},
            headers={"X-Admin-Operator": "alice"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resolution_status"] == "confirmed_fraud"
        assert body["admin_operator"] == "alice"
        assert body["resolved_at"] is not None
        assert body["resolution_note"] == "dup OCR confirmed manually"

    def test_resolve_to_cleared(self, admin_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)
        r = admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={"resolution_status": "cleared", "resolution_note": "legit pair"},
            headers={"X-Admin-Operator": "bob"},
        )
        assert r.status_code == 200
        assert r.json()["resolution_status"] == "cleared"

    def test_resolve_to_escalated_support(self, admin_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)
        r = admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={"resolution_status": "escalated_support", "resolution_note": "needs senior"},
            headers={"X-Admin-Operator": "alice"},
        )
        assert r.status_code == 200
        assert r.json()["resolution_status"] == "escalated_support"

    def test_pending_target_rejected_422(self, admin_client, db, store, user):
        """``pending`` is the initial state — can't be a resolve target."""
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)
        r = admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={"resolution_status": "pending", "resolution_note": "x"},
            headers={"X-Admin-Operator": "alice"},
        )
        assert r.status_code == 422

    def test_unknown_status_rejected_422(self, admin_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)
        r = admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={"resolution_status": "bogus", "resolution_note": "x"},
            headers={"X-Admin-Operator": "alice"},
        )
        assert r.status_code == 422

    def test_missing_note_rejected_422(self, admin_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)
        r = admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={"resolution_status": "cleared"},
            headers={"X-Admin-Operator": "alice"},
        )
        assert r.status_code == 422

    def test_extra_fields_rejected_422(self, admin_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)
        r = admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={
                "resolution_status": "cleared",
                "resolution_note": "ok",
                "rogue": True,
            },
            headers={"X-Admin-Operator": "alice"},
        )
        assert r.status_code == 422

    def test_empty_note_rejected_422(self, admin_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)
        r = admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={"resolution_status": "cleared", "resolution_note": ""},
            headers={"X-Admin-Operator": "alice"},
        )
        assert r.status_code == 422

    def test_missing_operator_header_400(self, admin_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)
        r = admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={"resolution_status": "cleared", "resolution_note": "ok"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_required"

    def test_blank_operator_header_400(self, admin_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)
        r = admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={"resolution_status": "cleared", "resolution_note": "ok"},
            headers={"X-Admin-Operator": "   "},
        )
        assert r.status_code == 400

    def test_404_when_unknown(self, admin_client):
        r = admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{uuid.uuid4()}",
            json={"resolution_status": "cleared", "resolution_note": "ok"},
            headers={"X-Admin-Operator": "alice"},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "fraud_suspicion_not_found"

    def test_409_when_already_resolved(self, admin_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)
        # First resolve succeeds
        r1 = admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={"resolution_status": "cleared", "resolution_note": "ok"},
            headers={"X-Admin-Operator": "alice"},
        )
        assert r1.status_code == 200
        # Second resolve attempt — even with different operator / target —
        # must reject single-shot.
        r2 = admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={"resolution_status": "confirmed_fraud", "resolution_note": "redo"},
            headers={"X-Admin-Operator": "bob"},
        )
        assert r2.status_code == 409
        assert r2.json()["detail"] == "already_resolved"

    def test_persists_db_state(self, admin_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)
        admin_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={
                "resolution_status": "confirmed_fraud",
                "resolution_note": "audit me",
            },
            headers={"X-Admin-Operator": "alice"},
        )
        db.expire_all()
        row = db.get(FraudSuspicion, s.id)
        assert row.resolution_status == "confirmed_fraud"
        assert row.admin_operator == "alice"
        assert row.resolved_at is not None
        assert row.resolution_note == "audit me"

    def test_unauth_without_admin_key_403(self, raw_client, db, store, user):
        primary = _make_receipt(db, store=store, user=user)
        s = _make_suspicion(db, receipt=primary)
        r = raw_client.patch(
            f"/api/v1/admin/fraud_suspicions/{s.id}",
            json={"resolution_status": "cleared", "resolution_note": "ok"},
            headers={"X-Admin-Operator": "alice"},
        )
        assert r.status_code == 403
