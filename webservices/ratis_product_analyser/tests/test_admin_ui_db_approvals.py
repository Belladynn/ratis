"""Tests for the admin UI DB-approval pages (SP6).

Covers :

- ``GET  /admin/ui/db-approvals``                       — list of pending
- ``GET  /admin/ui/db-approvals/{submission_id}``       — detail
- ``POST /admin/ui/db-approvals/{submission_id}/approve`` — approve flows
- ``POST /admin/ui/db-approvals/{submission_id}/reject``  — reject flows
- idempotency : a decision on an already-decided row is a no-op.
- n8n-failure path : decision committed even when resume POST returns False.

``_post_resume`` is monkeypatched directly with an async stub so no real
network call happens. Auth is the cookie session pattern shared by the
rest of the admin UI.
"""

from __future__ import annotations

import uuid

import pytest
from ratis_core.models.db_write_approval import DbWriteApproval, DbWriteApprovalStatus


def _login(raw_client, api_key="test-admin-key-padded-to-32-chars-min", operator="tester"):
    return raw_client.post(
        "/admin/ui/login",
        data={"api_key": api_key, "operator": operator},
        follow_redirects=False,
    )


def _make_approval(
    db,
    *,
    touches_money_tables=False,
    llm_unavailable=False,
    status=DbWriteApprovalStatus.PENDING,
    procedure="support_credit_cab",
) -> uuid.UUID:
    sid = uuid.uuid4()
    row = DbWriteApproval(
        submission_id=sid,
        payload={
            "procedure": procedure,
            "args": {"user_id": 7, "amount": 500},
            "rationale": "ticket #42",
            "client_message": "Je n'ai pas reçu mes CAB.",
            "investigation": "reward_event jamais émis — bug RW.",
            "checks": [{"type": "rowcount", "expect": 1}],
            "llm_feedback": [{"pass": "intent", "verdict": "ok"}],
            "break_glass": False,
        },
        status=status,
        touches_money_tables=touches_money_tables,
        llm_unavailable=llm_unavailable,
        resume_url="https://n8n.example/webhook-waiting/abc",
    )
    db.add(row)
    db.commit()
    return sid


@pytest.fixture
def mock_resume(monkeypatch):
    """Stub ``_post_resume`` so no real HTTP call is made.

    Returns a list of recorded call dicts ``{"url": ..., "json": ...}``.
    The stub returns ``True`` (success) by default.
    """
    from admin_ui import db_approvals as mod

    calls = []

    async def _stub(url, body):
        calls.append({"url": url, "json": body})
        return True

    monkeypatch.setattr(mod, "_post_resume", _stub)
    return calls


def test_list_shows_pending(raw_client, db):
    _login(raw_client)
    _make_approval(db)
    resp = raw_client.get("/admin/ui/db-approvals")
    assert resp.status_code == 200
    assert "support_credit_cab" in resp.text


def test_list_auth_gate(raw_client, db):
    resp = raw_client.get("/admin/ui/db-approvals", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/ui/login"


def test_detail_renders_support_context(raw_client, db):
    _login(raw_client)
    sid = _make_approval(db)
    resp = raw_client.get(f"/admin/ui/db-approvals/{sid}")
    assert resp.status_code == 200
    # Jinja2 auto-escapes apostrophes as &#39; in HTML templates.
    assert "Je n&#39;ai pas re" in resp.text
    assert "reward_event jamais" in resp.text


def _approve_with_challenge(raw_client, db, sid, secret, amount_cents=10000):
    """Helper HSP3 : POST /approve avec challenge + HMAC valides.

    Adapts SP6 tests to the new challenge-based approve flow (HSP3 M1+M2).
    Le challenge pour ``support_credit_cab`` avec ``amount_cents`` est
    ``it_cab <amount_cents>``.
    """
    import json
    import time as _t

    body = json.dumps(
        {
            "submission_id": str(sid),
            "decision": "approve",
            "challenge": f"it_cab {amount_cents}",
            "ts": int(_t.time()),
        }
    ).encode()
    from admin_ui.human_secret import compute_decision_hmac

    mac = compute_decision_hmac(secret, body)
    return raw_client.post(
        f"/admin/ui/db-approvals/{sid}/approve",
        content=body,
        headers={"content-type": "application/json", "x-human-mac": mac},
        follow_redirects=False,
    )


def test_approve_happy_path(raw_client, db, mock_resume):
    """HSP3 — approve heureux : challenge correct + HMAC valide → 303 approved.

    Adapté depuis SP6 : l'ancien test POSTait un form vide. Le nouveau flow
    requiert un JSON body + X-Human-Mac + human_approval_session cookie.
    Intent préservé : vérifier qu'une proposition non-monétaire est approuvée,
    que l'opérateur et decided_at sont renseignés, et que le resume n8n est
    appellé.
    Justification modification (R01) : SP6 form-based approve remplacé par
    HSP3 challenge M1 — le chemin de code form n'existe plus.
    """
    _login(raw_client)
    _setup_human_session_sp6 = "test-secret-sp6"
    _seed_human_secret_hash(db, _setup_human_session_sp6)
    raw_client.post(
        "/admin/ui/db-approvals/unlock",
        data={"secret": _setup_human_session_sp6},
        follow_redirects=False,
    )
    sid = uuid.uuid4()
    _seed_pending_approval_with_manifest(db, sid, procedure="support_credit_cab")
    resp = _approve_with_challenge(raw_client, db, sid, _setup_human_session_sp6)
    assert resp.status_code == 303
    db.expire_all()
    row = db.query(DbWriteApproval).filter_by(submission_id=sid).one()
    assert row.status == DbWriteApprovalStatus.APPROVED
    assert row.operator == "tester"
    assert row.decided_at is not None
    assert len(mock_resume) == 1


def test_approve_money_table_requires_challenge(raw_client, db, mock_resume):
    """HSP3 — sans human_approval_session → 401 secret_session_expired.

    Adapté depuis SP6 ``test_approve_money_table_requires_confirmation`` :
    l'ancien test vérifiait que sans ``confirm_procedure``, l'approbation
    était refusée. HSP3 remplace ce mécanisme par le cookie de session +
    challenge ; sans cookie, on obtient 401 (pas 303 redirect).
    Justification modification (R01) : SP6 confirm_procedure supprimé,
    HSP3 gate est le cookie + challenge.
    """
    _login(raw_client)
    # Pas de _setup_human_session → pas de cookie.
    sid = uuid.uuid4()
    _seed_pending_approval_with_manifest(db, sid, procedure="support_credit_cab")
    import json
    import time as _t

    body = json.dumps(
        {
            "submission_id": str(sid),
            "decision": "approve",
            "challenge": "it_cab 10000",
            "ts": int(_t.time()),
        }
    ).encode()
    from admin_ui.human_secret import compute_decision_hmac

    mac = compute_decision_hmac("x", body)
    resp = raw_client.post(
        f"/admin/ui/db-approvals/{sid}/approve",
        content=body,
        headers={"content-type": "application/json", "x-human-mac": mac},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    db.expire_all()
    row = db.query(DbWriteApproval).filter_by(submission_id=sid).one()
    assert row.status == DbWriteApprovalStatus.PENDING
    assert len(mock_resume) == 0


def test_approve_money_table_wrong_challenge(raw_client, db, mock_resume):
    """HSP3 — challenge incorrect → 422 challenge_mismatch, row reste pending.

    Adapté depuis SP6 ``test_approve_money_table_wrong_confirmation`` :
    l'ancien test envoyait un mauvais ``confirm_procedure``. HSP3 remplace
    avec un mauvais challenge.
    Justification modification (R01) : SP6 confirm_procedure → HSP3 challenge.
    """
    _login(raw_client)
    secret = "test-secret-sp6"
    _seed_human_secret_hash(db, secret)
    raw_client.post(
        "/admin/ui/db-approvals/unlock",
        data={"secret": secret},
        follow_redirects=False,
    )
    sid = uuid.uuid4()
    _seed_pending_approval_with_manifest(db, sid, procedure="support_credit_cab")
    import json
    import time as _t

    body = json.dumps(
        {
            "submission_id": str(sid),
            "decision": "approve",
            "challenge": "wrong_challenge",  # mauvais challenge
            "ts": int(_t.time()),
        }
    ).encode()
    from admin_ui.human_secret import compute_decision_hmac

    mac = compute_decision_hmac(secret, body)
    resp = raw_client.post(
        f"/admin/ui/db-approvals/{sid}/approve",
        content=body,
        headers={"content-type": "application/json", "x-human-mac": mac},
        follow_redirects=False,
    )
    assert resp.status_code == 422
    db.expire_all()
    row = db.query(DbWriteApproval).filter_by(submission_id=sid).one()
    assert row.status == DbWriteApprovalStatus.PENDING
    assert len(mock_resume) == 0


def test_approve_money_table_correct_challenge(raw_client, db, mock_resume):
    """HSP3 — challenge correct → 303 + row approved.

    Adapté depuis SP6 ``test_approve_money_table_correct_confirmation`` :
    l'ancien test envoyait ``confirm_procedure=support_credit_cab``. HSP3
    remplace par le challenge ``it_cab 10000``.
    Justification modification (R01) : SP6 confirm_procedure → HSP3 challenge.
    """
    _login(raw_client)
    secret = "test-secret-sp6"
    _seed_human_secret_hash(db, secret)
    raw_client.post(
        "/admin/ui/db-approvals/unlock",
        data={"secret": secret},
        follow_redirects=False,
    )
    sid = uuid.uuid4()
    _seed_pending_approval_with_manifest(db, sid, procedure="support_credit_cab")
    resp = _approve_with_challenge(raw_client, db, sid, secret)
    assert resp.status_code == 303
    db.expire_all()
    row = db.query(DbWriteApproval).filter_by(submission_id=sid).one()
    assert row.status == DbWriteApprovalStatus.APPROVED
    assert len(mock_resume) == 1


def test_reject_requires_reason(raw_client, db, mock_resume):
    _login(raw_client)
    sid = _make_approval(db)
    resp = raw_client.post(f"/admin/ui/db-approvals/{sid}/reject", data={"reason": ""}, follow_redirects=False)
    assert resp.status_code == 303
    db.expire_all()
    row = db.query(DbWriteApproval).filter_by(submission_id=sid).one()
    assert row.status == DbWriteApprovalStatus.PENDING
    assert len(mock_resume) == 0


def test_reject_happy_path(raw_client, db, mock_resume):
    _login(raw_client)
    sid = _make_approval(db)
    resp = raw_client.post(
        f"/admin/ui/db-approvals/{sid}/reject",
        data={"reason": "args incorrects, montant trop élevé"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db.expire_all()
    row = db.query(DbWriteApproval).filter_by(submission_id=sid).one()
    assert row.status == DbWriteApprovalStatus.REJECTED
    assert row.decision_reason == "args incorrects, montant trop élevé"
    assert mock_resume[0]["json"] == {
        "decision": "reject",
        "operator": "tester",
        "reason": "args incorrects, montant trop élevé",
    }


def test_approve_idempotent_on_decided_row(raw_client, db, mock_resume):
    """HSP3 — approve sur une row déjà décidée → 409 already_decided, pas de double-fire.

    Adapté depuis SP6 : l'ancien test vérifiait un 303 redirect idempotent.
    HSP3 retourne 409 JSON (already_decided) car le gate HMAC/challenge est
    passé mais la row est déjà traitée — comportement plus explicite.
    L'intent est préservé : la décision existante n'est pas modifiée.
    Justification modification (R01) : HSP3 approve retourne 409 JSON au lieu
    de 303 redirect pour les rows already_decided — plus clair pour le caller JS.
    """
    _login(raw_client)
    secret = "test-secret-sp6"
    _seed_human_secret_hash(db, secret)
    raw_client.post(
        "/admin/ui/db-approvals/unlock",
        data={"secret": secret},
        follow_redirects=False,
    )
    sid = uuid.uuid4()
    # Crée une row avec manifest mais status=REJECTED (déjà décidée).
    row_obj = _seed_pending_approval_with_manifest(db, sid)
    row_obj.status = DbWriteApprovalStatus.REJECTED
    db.commit()

    resp = _approve_with_challenge(raw_client, db, sid, secret)
    assert resp.status_code == 409
    assert "already_decided" in resp.text
    db.expire_all()
    row = db.query(DbWriteApproval).filter_by(submission_id=sid).one()
    assert row.status == DbWriteApprovalStatus.REJECTED  # unchanged
    assert len(mock_resume) == 0


def test_approve_n8n_unreachable_row_still_committed(raw_client, db, monkeypatch):
    """HSP3 — n8n injoignable : la décision est durable même si le resume POST échoue.

    Adapté depuis SP6 : l'ancien test POSTait un form vide. HSP3 requiert
    challenge + HMAC. L'intent est identique : vérifier que ``db.commit()``
    précède le resume POST et que la row est approuvée même en cas d'erreur
    n8n.
    Justification modification (R01) : flow approve SP6 → HSP3 challenge.
    """
    from admin_ui import db_approvals as mod

    async def _failing_stub(url, body):
        return False

    monkeypatch.setattr(mod, "_post_resume", _failing_stub)

    _login(raw_client)
    secret = "test-secret-sp6"
    _seed_human_secret_hash(db, secret)
    raw_client.post(
        "/admin/ui/db-approvals/unlock",
        data={"secret": secret},
        follow_redirects=False,
    )
    sid = uuid.uuid4()
    _seed_pending_approval_with_manifest(db, sid)
    resp = _approve_with_challenge(raw_client, db, sid, secret)
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "injoignable" in location

    db.expire_all()
    row = db.query(DbWriteApproval).filter_by(submission_id=sid).one()
    assert row.status == DbWriteApprovalStatus.APPROVED


# ---------------------------------------------------------------------------
# HSP3 — M2 unlock flow
# ---------------------------------------------------------------------------


def _seed_human_secret_hash(db, secret: str) -> None:
    """Helper : seed app_settings.human_approval avec un hash argon2id."""
    from admin_ui.human_secret import hash_secret
    from ratis_core.models.settings import AppSettings

    h = hash_secret(secret)
    existing = db.get(AppSettings, "human_approval")
    if existing:
        existing.data = {"secret_set": True, "argon2_hash": h}
    else:
        db.add(
            AppSettings(
                section="human_approval",
                data={"secret_set": True, "argon2_hash": h},
            )
        )
    db.commit()


def test_unlock_get_renders_form(raw_client, db):
    """GET /admin/ui/db-approvals/unlock → 200 + textarea name=secret."""
    _login(raw_client)
    r = raw_client.get(
        "/admin/ui/db-approvals/unlock",
        follow_redirects=False,
    )
    assert r.status_code == 200
    body = r.text
    assert "<textarea" in body
    assert 'name="secret"' in body


def test_unlock_get_requires_admin_session(raw_client, db):
    """Sans admin_session, /unlock redirige vers /admin/ui/login."""
    r = raw_client.get("/admin/ui/db-approvals/unlock", follow_redirects=False)
    assert r.status_code == 302
    assert "/admin/ui/login" in r.headers["location"]


def test_unlock_post_correct_secret_sets_cookie(raw_client, db):
    """POST /unlock avec le bon secret → 303 vers /db-approvals + Set-Cookie
    human_approval_session."""
    _seed_human_secret_hash(db, "correct-secret")
    _login(raw_client)
    r = raw_client.post(
        "/admin/ui/db-approvals/unlock",
        data={"secret": "correct-secret"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    set_cookie = r.headers.get("set-cookie", "")
    assert "human_approval_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=strict" in set_cookie.lower()


def test_unlock_post_wrong_secret_401_no_cookie(raw_client, db):
    """POST /unlock avec mauvais secret → 401 sans cookie posé."""
    _seed_human_secret_hash(db, "right")
    _login(raw_client)
    r = raw_client.post(
        "/admin/ui/db-approvals/unlock",
        data={"secret": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 401
    assert "human_approval_session=" not in r.headers.get("set-cookie", "")


def test_unlock_post_when_secret_not_seeded_503(raw_client, db):
    """Si app_settings.human_approval.secret_set=false → 503
    ``human_secret_not_initialised``."""
    from ratis_core.models.settings import AppSettings

    existing = db.get(AppSettings, "human_approval")
    if existing:
        existing.data = {"secret_set": False, "argon2_hash": None}
    else:
        db.add(
            AppSettings(
                section="human_approval",
                data={"secret_set": False, "argon2_hash": None},
            )
        )
    db.commit()
    _login(raw_client)
    r = raw_client.post(
        "/admin/ui/db-approvals/unlock",
        data={"secret": "whatever"},
        follow_redirects=False,
    )
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# HSP3 — M1+M2 challenge + HMAC + quota integration tests
# ---------------------------------------------------------------------------


def _hmac_body(secret: str, body: bytes) -> str:
    import hashlib
    import hmac as _h

    return _h.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _seed_pending_approval_with_manifest(db, submission_id, procedure="support_credit_cab", args=None):
    """Helper : seed une row pending avec manifest snapshot dans payload."""
    args = args or {
        "amount_cents": 10000,
        "user_id": "00000000-0000-0000-0000-000000004728",
    }
    row = DbWriteApproval(
        submission_id=submission_id,
        payload={
            "procedure": procedure,
            "mode": "execute",
            "args": args,
            "rationale": "test",
            "manifest": {
                "name": procedure,
                "purpose": "test",
                "facing": True,
                "direction": "credit",
                "money_tier": "cab",
                "args": [],
                "affects": [{"table": "user_cab_balance", "op": "update", "rows": 1}],
                "trust_level_initial": "manual",
                "allowed_callers": ["claude-code-main"],
            },
        },
        touches_money_tables=True,
        llm_unavailable=False,
        resume_url="http://n8n.test.invalid/resume",
    )
    db.add(row)
    db.commit()
    return row


def _setup_human_session(raw_client, db, secret="test-secret-xx"):
    """Helper : seed hash + POST /unlock → cookie human_approval_session
    posé sur le TestClient (cookies automatiquement persistés)."""
    _seed_human_secret_hash(db, secret)
    r = raw_client.post(
        "/admin/ui/db-approvals/unlock",
        data={"secret": secret},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # TestClient persiste les cookies automatiquement — rien à retourner.
    return secret


def test_approve_requires_valid_hmac(raw_client, db, monkeypatch):
    """HMAC invalide (deadbeef) → 401 bad_mac."""
    import json
    import time as _t

    _login(raw_client)
    _setup_human_session(raw_client, db, "test-secret-xx")
    sid = uuid.uuid4()
    _seed_pending_approval_with_manifest(db, sid)
    body = json.dumps(
        {
            "submission_id": str(sid),
            "decision": "approve",
            "challenge": "it_cab 10000",
            "ts": int(_t.time()),
        }
    ).encode()
    r = raw_client.post(
        f"/admin/ui/db-approvals/{sid}/approve",
        content=body,
        headers={"content-type": "application/json", "x-human-mac": "deadbeef"},
    )
    assert r.status_code == 401
    assert "bad_mac" in r.text


def test_approve_rejects_stale_ts(raw_client, db):
    """ts = 1 (très ancien) → 401 stale_ts."""
    import json

    _login(raw_client)
    secret = _setup_human_session(raw_client, db, "test-secret-xx")
    sid = uuid.uuid4()
    _seed_pending_approval_with_manifest(db, sid)
    body = json.dumps(
        {
            "submission_id": str(sid),
            "decision": "approve",
            "challenge": "it_cab 10000",
            "ts": 1,
        }
    ).encode()
    mac = _hmac_body(secret, body)
    r = raw_client.post(
        f"/admin/ui/db-approvals/{sid}/approve",
        content=body,
        headers={"content-type": "application/json", "x-human-mac": mac},
    )
    assert r.status_code == 401
    assert "stale_ts" in r.text


def test_approve_challenge_mismatch_increments_failed_confirms(raw_client, db):
    """Challenge incorrect → 422, failed_confirms incrémenté dans payload."""
    import json
    import time as _t

    _login(raw_client)
    secret = _setup_human_session(raw_client, db, "test-secret-xx")
    sid = uuid.uuid4()
    row = _seed_pending_approval_with_manifest(db, sid)
    body = json.dumps(
        {
            "submission_id": str(sid),
            "decision": "approve",
            "challenge": "it_cab 9999",  # mauvais montant
            "ts": int(_t.time()),
        }
    ).encode()
    mac = _hmac_body(secret, body)
    r = raw_client.post(
        f"/admin/ui/db-approvals/{sid}/approve",
        content=body,
        headers={"content-type": "application/json", "x-human-mac": mac},
    )
    assert r.status_code == 422
    db.refresh(row)
    assert row.payload.get("failed_confirms") == 1
    assert row.status.value == "pending"


def test_approve_three_failures_lock_429(raw_client, db):
    """3 fails cumulés → la 4e tentative reçoit 429."""
    import json
    import time as _t

    _login(raw_client)
    secret = _setup_human_session(raw_client, db, "test-secret-xx")
    # Reset le store de quota avant le test pour isolation.
    from admin_ui import db_approvals as _mod

    _mod._CHALLENGE_ATTEMPTS.clear()

    sid = uuid.uuid4()
    _seed_pending_approval_with_manifest(db, sid)
    body = json.dumps(
        {
            "submission_id": str(sid),
            "decision": "approve",
            "challenge": "wrong",
            "ts": int(_t.time()),
        }
    ).encode()
    mac = _hmac_body(secret, body)
    for _ in range(3):
        raw_client.post(
            f"/admin/ui/db-approvals/{sid}/approve",
            content=body,
            headers={"content-type": "application/json", "x-human-mac": mac},
        )
    # 4e tentative → 429.
    r = raw_client.post(
        f"/admin/ui/db-approvals/{sid}/approve",
        content=body,
        headers={"content-type": "application/json", "x-human-mac": mac},
    )
    assert r.status_code == 429
    assert "retry_after" in r.text.lower() or r.headers.get("retry-after")


def test_approve_correct_challenge_marks_approved(raw_client, db, monkeypatch):
    """Challenge correct + HMAC correct → 303 + row.status == approved."""
    import json
    import time as _t

    from admin_ui import db_approvals as _mod

    async def _stub(url, body):
        return True

    monkeypatch.setattr(_mod, "_post_resume", _stub)

    _login(raw_client)
    secret = _setup_human_session(raw_client, db, "test-secret-xx")
    sid = uuid.uuid4()
    row = _seed_pending_approval_with_manifest(db, sid)
    body = json.dumps(
        {
            "submission_id": str(sid),
            "decision": "approve",
            "challenge": "it_cab 10000",
            "ts": int(_t.time()),
        }
    ).encode()
    mac = _hmac_body(secret, body)
    r = raw_client.post(
        f"/admin/ui/db-approvals/{sid}/approve",
        content=body,
        headers={"content-type": "application/json", "x-human-mac": mac},
        follow_redirects=False,
    )
    # 303 = succès (redirect liste).
    assert r.status_code == 303
    db.refresh(row)
    assert row.status.value == "approved"


def test_detail_renders_summary_and_flags_and_challenge(raw_client, db):
    """HSP3 — GET /db-approvals/<sid> rend en H1 le summary, les flags actifs,
    et le challenge hint.

    Couvre M1+M3+M4 du template : (a) résumé FR figé en H1, (b) banner
    anomaly flags avec libellés FR, (c) challenge hint en monospace avec
    user-select:none pour l'anti-paste.
    """
    _login(raw_client)
    sid = uuid.uuid4()
    # Crée une row pending avec summary_fr + anomaly_flags figés (Register
    # approval n8n aurait fait ça en HSP3). Le manifest snapshot permet à la
    # route detail de recalculer challenge_hint.
    row = DbWriteApproval(
        submission_id=sid,
        payload={
            "procedure": "support_credit_cab",
            "mode": "execute",
            "args": {
                "amount_cents": 10000,
                "user_id": "00000000-0000-0000-0000-000000004728",
            },
            "rationale": "test summary+flags+challenge",
            "summary_fr": ("TU VAS CRÉDITER 100 CAB à l'utilisateur #00004728.\nProcédure : support_credit_cab."),
            "anomaly_flags": {
                "first_use_of_procedure": True,
                "amount_above_p95": False,
                "user_repeat_in_24h": False,
                "approaching_daily_cap": False,
                "proposed_outside_business_hours": False,
            },
            "manifest": {
                "name": "support_credit_cab",
                "purpose": "P",
                "facing": True,
                "direction": "credit",
                "money_tier": "cab",
                "args": [],
                "affects": [{"table": "user_cab_balance", "op": "update", "rows": 1}],
                "trust_level_initial": "manual",
                "allowed_callers": ["claude-code-main"],
            },
        },
        touches_money_tables=True,
        llm_unavailable=False,
        resume_url="http://n8n.test.invalid/resume",
    )
    db.add(row)
    db.commit()

    r = raw_client.get(
        f"/admin/ui/db-approvals/{sid}",
        follow_redirects=False,
    )
    assert r.status_code == 200
    body = r.text
    # M3 résumé visible en H1 (texte FR + ligne « 100 CAB »).
    assert "CRÉDITER" in body
    assert "100 CAB" in body
    # M4 flag orange affiché (first_use_of_procedure → libellé FR).
    assert "Première utilisation" in body
    # M1 challenge hint — primary key = amount_cents, suffix = "it_cab" (6
    # derniers chars de "support_credit_cab").
    assert "it_cab 10000" in body
    # Anti-paste : user-select:none dans le span challenge.
    assert "user-select:none" in body


def test_detail_renders_caps_already_warning_as_red(raw_client, db):
    """HSP3.1 — le 6e flag ``caps_already_warning`` rend le banner rouge
    (haute sévérité) avec le libellé FR dédié."""
    _login(raw_client)
    sid = uuid.uuid4()
    row = DbWriteApproval(
        submission_id=sid,
        payload={
            "procedure": "support_credit_cab",
            "mode": "execute",
            "args": {
                "amount_cents": 10000,
                "user_id": "00000000-0000-0000-0000-000000004728",
            },
            "rationale": "test caps_already_warning red banner",
            "summary_fr": "TU VAS CRÉDITER 100 CAB.",
            "anomaly_flags": {
                "first_use_of_procedure": False,
                "amount_above_p95": False,
                "user_repeat_in_24h": False,
                "approaching_daily_cap": False,
                "proposed_outside_business_hours": False,
                "caps_already_warning": True,
            },
            "manifest": {
                "name": "support_credit_cab",
                "purpose": "P",
                "facing": True,
                "direction": "credit",
                "money_tier": "cab",
                "args": [],
                "affects": [{"table": "user_cab_balance", "op": "update", "rows": 1}],
                "trust_level_initial": "manual",
                "allowed_callers": ["claude-code-main"],
            },
        },
        touches_money_tables=True,
        llm_unavailable=False,
        resume_url="http://n8n.test.invalid/resume",
    )
    db.add(row)
    db.commit()

    r = raw_client.get(f"/admin/ui/db-approvals/{sid}", follow_redirects=False)
    assert r.status_code == 200
    body = r.text
    # Banner rouge (haute sévérité) déclenché par caps_already_warning.
    assert "anomaly-red" in body
    # Libellé FR dédié.
    assert "caps journaliers CAB sont déjà en zone warn" in body


def test_approve_no_human_session_401(raw_client, db):
    """Sans cookie human_approval_session → 401 ``secret_session_expired``."""
    import json
    import time as _t

    _login(raw_client)
    # Pas de _setup_human_session → pas de cookie human_approval_session.
    sid = uuid.uuid4()
    _seed_pending_approval_with_manifest(db, sid)
    secret = "x"
    body = json.dumps(
        {
            "submission_id": str(sid),
            "decision": "approve",
            "challenge": "it_cab 10000",
            "ts": int(_t.time()),
        }
    ).encode()
    mac = _hmac_body(secret, body)
    r = raw_client.post(
        f"/admin/ui/db-approvals/{sid}/approve",
        content=body,
        headers={"content-type": "application/json", "x-human-mac": mac},
    )
    assert r.status_code == 401
    assert "secret_session_expired" in r.text
