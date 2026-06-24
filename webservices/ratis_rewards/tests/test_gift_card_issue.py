"""
TDD — issue_gift_card service (Runa provider)

Tests the service function directly (not via HTTP) — passes a real DB session.
Runa HTTP calls are mocked with unittest.mock.patch.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from services.gift_card_service import issue_gift_card
from sqlalchemy import text

from tests.conftest import make_gift_card_brand, make_gift_card_order, make_user


def _runa_response(status: str, code: str | None = None, order_id: str = "runa_order_xyz") -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "id": order_id,
        "status": status,
        "redemption_code": code,
    }
    return resp


# ---------------------------------------------------------------------------
# Happy path — COMPLETE
# ---------------------------------------------------------------------------


def test_issue_gift_card_complete(db):
    """Runa returns COMPLETE → order status issued, code + provider_order_id set."""
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db, name="Amazon")
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, denomination=2000, status="pending")

    with patch("services.gift_card_service.httpx.post") as mock_post:
        mock_post.return_value = _runa_response("COMPLETE", code="AMZN-1234-ABCD")
        issue_gift_card(order_id, db)
        db.commit()

    row = db.execute(
        text("SELECT status, code, provider_order_id, issued_at FROM gift_card_orders WHERE id = :id"),
        {"id": order_id},
    ).first()
    assert row.status == "issued"
    assert row.code == "AMZN-1234-ABCD"
    assert row.provider_order_id == "runa_order_xyz"
    assert row.issued_at is not None


def test_issue_gift_card_passes_correct_payload(db):
    """Runa POST receives correct product_id, face_value (euros), currency, idempotency_key."""
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db, name="Amazon", provider_brand_id="runa_amzn_fr_001")
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, denomination=2000, status="pending")

    with patch("services.gift_card_service.httpx.post") as mock_post:
        mock_post.return_value = _runa_response("COMPLETE", code="XXX")
        issue_gift_card(order_id, db)
        db.commit()

    call_kwargs = mock_post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs.kwargs["json"]
    assert payload["product_id"] == "runa_amzn_fr_001"
    assert payload["face_value"] == 20.0  # 2000 centimes → 20.00€
    assert payload["currency"] == "EUR"
    assert payload["idempotency_key"] == str(order_id)


# ---------------------------------------------------------------------------
# FAILED status
# ---------------------------------------------------------------------------


def test_issue_gift_card_runa_failed(db):
    """Runa returns FAILED → order status failed, failed_at set."""
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, status="pending")

    with patch("services.gift_card_service.httpx.post") as mock_post:
        mock_post.return_value = _runa_response("FAILED")
        issue_gift_card(order_id, db)
        db.commit()

    row = db.execute(
        text("SELECT status, failed_at FROM gift_card_orders WHERE id = :id"),
        {"id": order_id},
    ).first()
    assert row.status == "failed"
    assert row.failed_at is not None


# ---------------------------------------------------------------------------
# PROCESSING — keep pending
# ---------------------------------------------------------------------------


def test_issue_gift_card_runa_processing(db):
    """Runa returns PROCESSING → order stays pending (will be re-polled by batch)."""
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, status="pending")

    with patch("services.gift_card_service.httpx.post") as mock_post:
        mock_post.return_value = _runa_response("PROCESSING")
        issue_gift_card(order_id, db)
        db.commit()

    status = db.execute(text("SELECT status FROM gift_card_orders WHERE id = :id"), {"id": order_id}).scalar()
    assert status == "pending"


# ---------------------------------------------------------------------------
# HTTP errors — mark failed, never raise
# ---------------------------------------------------------------------------


def test_issue_gift_card_runa_402_marks_failed(db):
    """Runa 402 (insufficient balance) → order failed, never raises."""
    import httpx

    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, status="pending")

    mock_resp = MagicMock()
    mock_resp.status_code = 402
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("402", request=MagicMock(), response=mock_resp)

    with patch("services.gift_card_service.httpx.post", return_value=mock_resp):
        issue_gift_card(order_id, db)
        db.commit()

    status = db.execute(text("SELECT status FROM gift_card_orders WHERE id = :id"), {"id": order_id}).scalar()
    assert status == "failed"


def test_issue_gift_card_network_error_marks_failed(db):
    """Network error → order failed, never raises."""
    import httpx

    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, status="pending")

    with patch("services.gift_card_service.httpx.post", side_effect=httpx.ConnectError("timeout")):
        issue_gift_card(order_id, db)
        db.commit()

    status = db.execute(text("SELECT status FROM gift_card_orders WHERE id = :id"), {"id": order_id}).scalar()
    assert status == "failed"


# ---------------------------------------------------------------------------
# No API key — skip gracefully
# ---------------------------------------------------------------------------


def test_issue_gift_card_no_api_key_sandbox(db, monkeypatch):
    """No GIFT_CARD_PROVIDER_KEY → sandbox mode: order issued with fake code, httpx.post never called."""
    monkeypatch.delenv("GIFT_CARD_PROVIDER_KEY", raising=False)

    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    order_id = make_gift_card_order(db, user_id=user_id, brand_id=brand_id, status="pending")

    with patch("services.gift_card_service.httpx.post") as mock_post:
        issue_gift_card(order_id, db)
        db.commit()
        mock_post.assert_not_called()

    row = db.execute(
        text("SELECT status, code, provider_order_id FROM gift_card_orders WHERE id = :id"),
        {"id": order_id},
    ).first()
    assert row.status == "issued"
    assert row.code.startswith("SANDBOX-")
    assert row.provider_order_id == "sandbox"
