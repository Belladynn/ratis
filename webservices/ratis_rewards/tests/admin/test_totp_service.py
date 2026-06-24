"""TOTP dependency unit tests — verify_totp_dep behaviour."""

from __future__ import annotations

import os
import time

import pyotp
import pytest
from fastapi import HTTPException
from services.totp_service import verify_totp_dep


class TestVerifyTotpDep:
    def test_missing_header_raises_401_totp_required(self, totp_secret):
        with pytest.raises(HTTPException) as exc_info:
            verify_totp_dep(x_admin_totp=None)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "totp_required"

    def test_wrong_code_raises_401_totp_invalid(self, totp_secret):
        with pytest.raises(HTTPException) as exc_info:
            verify_totp_dep(x_admin_totp="000000")
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "totp_invalid"

    def test_correct_code_passes(self, totp_secret):
        code = pyotp.TOTP(totp_secret).now()
        # No exception means OK.
        result = verify_totp_dep(x_admin_totp=code)
        assert result is None

    def test_no_secret_in_env_raises_500(self, totp_secret, monkeypatch):
        monkeypatch.delenv("ADMIN_TOTP_SECRET", raising=False)
        with pytest.raises(HTTPException) as exc_info:
            verify_totp_dep(x_admin_totp="123456")
        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "admin_totp_not_configured"

    def test_accepts_previous_window_clock_skew(self, totp_secret):
        """valid_window=1 → previous 30s code must still be accepted."""
        # Code computed at t-30 must still verify at t.
        totp = pyotp.TOTP(totp_secret)
        previous_code = totp.at(time.time() - 30)
        # Should pass without raising — within valid_window=1.
        verify_totp_dep(x_admin_totp=previous_code)

    def test_rejects_far_future_window(self, totp_secret):
        """valid_window=1 → code from t+90 must NOT verify."""
        totp = pyotp.TOTP(totp_secret)
        future_code = totp.at(time.time() + 90)
        # Force-reset env in case it was wiped.
        os.environ["ADMIN_TOTP_SECRET"] = totp_secret
        with pytest.raises(HTTPException) as exc_info:
            verify_totp_dep(x_admin_totp=future_code)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "totp_invalid"
