"""Tests for POST /api/v1/admin/db-approvals — n8n proposal registration (SP6).

The workflow ``db-write-pipeline`` POSTs a proposal reaching the human
gate ; the endpoint INSERTs a ``db_write_approvals`` row in ``pending``.
Auth is Bearer ``ADMIN_API_KEY`` (machine call n8n→PA).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from ratis_core.models.db_write_approval import DbWriteApproval, DbWriteApprovalStatus


def _proposal_body(submission_id: str) -> dict:
    return {
        "submission_id": submission_id,
        "payload": {
            "procedure": "support_credit_cab",
            "args": {"user_id": 7, "amount": 500},
            "rationale": "ticket #42 — CAB manquants",
            "client_message": "Je n'ai pas reçu mes CAB.",
            "investigation": "reward_event jamais émis",
            "checks": [{"type": "rowcount", "expect": 1}],
            "llm_feedback": [{"pass": "intent", "verdict": "ok"}],
        },
        "touches_money_tables": True,
        "llm_unavailable": False,
        "resume_url": "https://n8n.example/webhook-waiting/abc",
    }


def test_register_proposal_inserts_pending_row(admin_client, db):
    sid = str(uuid.uuid4())
    resp = admin_client.post("/api/v1/admin/db-approvals", json=_proposal_body(sid))
    assert resp.status_code == 200
    assert resp.json() == {"status": "registered", "submission_id": sid}
    row = db.query(DbWriteApproval).filter_by(submission_id=uuid.UUID(sid)).one()
    assert row.status == DbWriteApprovalStatus.PENDING
    assert row.touches_money_tables is True
    assert row.llm_unavailable is False
    assert row.resume_url == "https://n8n.example/webhook-waiting/abc"
    assert row.payload["procedure"] == "support_credit_cab"
    assert row.operator is None
    assert row.decided_at is None


def test_register_proposal_requires_admin_key(raw_client):
    sid = str(uuid.uuid4())
    resp = raw_client.post("/api/v1/admin/db-approvals", json=_proposal_body(sid))
    assert resp.status_code == 403


def test_expire_marks_pending_row(admin_client, db):
    sid = str(uuid.uuid4())
    admin_client.post("/api/v1/admin/db-approvals", json=_proposal_body(sid))
    resp = admin_client.post(f"/api/v1/admin/db-approvals/{sid}/expire")
    assert resp.status_code == 200
    assert resp.json() == {"status": "expired", "submission_id": sid}
    row = db.query(DbWriteApproval).filter_by(submission_id=uuid.UUID(sid)).one()
    assert row.status == DbWriteApprovalStatus.EXPIRED
    assert row.decided_at is not None
    assert isinstance(row.decided_at, datetime)


def test_expire_unknown_returns_404(admin_client):
    resp = admin_client.post(f"/api/v1/admin/db-approvals/{uuid.uuid4()}/expire")
    assert resp.status_code == 404


def test_expire_already_decided_is_noop(admin_client, db):
    sid = str(uuid.uuid4())
    admin_client.post("/api/v1/admin/db-approvals", json=_proposal_body(sid))
    row = db.query(DbWriteApproval).filter_by(submission_id=uuid.UUID(sid)).one()
    row.status = DbWriteApprovalStatus.APPROVED
    db.commit()
    resp = admin_client.post(f"/api/v1/admin/db-approvals/{sid}/expire")
    assert resp.status_code == 200
    assert resp.json() == {"status": "noop", "submission_id": sid}
