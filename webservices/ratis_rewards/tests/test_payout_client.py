"""Tests for ratis_core.payout_client — sandbox mode only (no real Stripe)."""

from __future__ import annotations

import uuid

from ratis_core.payout_client import initiate_payout


def test_sandbox_returns_prefixed_string():
    """Sans PAYMENT_PROVIDER_KEY → retourne sandbox-<withdrawal_id>."""
    wid = uuid.uuid4()
    ref = initiate_payout(wid, 1000)
    assert ref == f"sandbox-{wid}"


def test_sandbox_different_ids_different_refs():
    """Deux withdrawals différents → deux refs différentes."""
    wid1, wid2 = uuid.uuid4(), uuid.uuid4()
    assert initiate_payout(wid1, 500) != initiate_payout(wid2, 500)


def test_sandbox_any_amount():
    """Le montant n'impacte pas le ref sandbox."""
    wid = uuid.uuid4()
    assert initiate_payout(wid, 100) == f"sandbox-{wid}"
    assert initiate_payout(wid, 99999) == f"sandbox-{wid}"
