"""Tests for :mod:`ratis_core.anonymize`.

Coverage matrix (per F-AU-3) :
- Determinism : same (user_id, salt) → same output.
- Different user_ids with same salt → different outputs (no trivial collision).
- Same user_id with different salts → different outputs (salt actually mixed).
- Sentinel constant has the expected fixed value (must match migration seed).
- Empty / whitespace salt raises ValueError (defensive guard).
- get_anonymize_salt fails fast when env var is missing or blank.
- Output is a proper UUID (version 4 nibble + RFC 4122 variant) — harmless
  cosmetic check that catches regressions on the bit-tweaking lines.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from ratis_core.anonymize import (
    ANON_SENTINEL_USER_ID,
    anonymize_user_id,
    get_anonymize_salt,
)


def test_sentinel_value_is_stable() -> None:
    """The sentinel is a public contract — must never change without a
    migration that re-seeds the users row referenced by every NEVER-PURGE
    financial table after anonymization."""
    assert UUID("00000000-0000-0000-0000-000000000001") == ANON_SENTINEL_USER_ID


def test_anonymize_is_deterministic() -> None:
    uid = uuid4()
    a = anonymize_user_id(uid, "my-secret-salt")
    b = anonymize_user_id(uid, "my-secret-salt")
    assert a == b


def test_anonymize_differs_per_user() -> None:
    """Different real users must hash to different anon UUIDs (otherwise
    achievement grouping is broken — every "% unlocked" stat collapses)."""
    salt = "shared-salt"
    a = anonymize_user_id(uuid4(), salt)
    b = anonymize_user_id(uuid4(), salt)
    assert a != b


def test_anonymize_differs_per_salt() -> None:
    """Same user, different salts → different outputs. Validates the salt is
    actually mixed into the hash (regression on a future refactor)."""
    uid = uuid4()
    a = anonymize_user_id(uid, "salt-one")
    b = anonymize_user_id(uid, "salt-two")
    assert a != b


def test_anonymize_rejects_empty_salt() -> None:
    with pytest.raises(ValueError, match="anonymize_salt_empty"):
        anonymize_user_id(uuid4(), "")


def test_anonymize_rejects_whitespace_salt() -> None:
    with pytest.raises(ValueError, match="anonymize_salt_empty"):
        anonymize_user_id(uuid4(), "   \n\t")


def test_anonymize_output_has_uuid4_shape() -> None:
    """The function sets version=4 + RFC 4122 variant explicitly. Asserting
    this catches a refactor that removes the bit-tweaking lines."""
    result = anonymize_user_id(uuid4(), "salt")
    assert result.version == 4
    # RFC 4122 variant : top 2 bits of byte 8 must be 10 (binary).
    assert (result.bytes[8] >> 6) == 0b10


def test_anonymize_output_never_collides_with_sentinel() -> None:
    """The sentinel UUID is all zeros + a 1 at the very end. The hash
    output for any non-trivial input has probability ~2^-128 of matching ;
    this test just spot-checks a few inputs to catch the trivial case
    where the function returns ANON_SENTINEL_USER_ID by accident."""
    salt = "salt"
    for _ in range(100):
        result = anonymize_user_id(uuid4(), salt)
        assert result != ANON_SENTINEL_USER_ID


def test_get_anonymize_salt_returns_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RGPD_ANONYMIZE_SALT", "from-env")
    assert get_anonymize_salt() == "from-env"


def test_get_anonymize_salt_fails_if_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RGPD_ANONYMIZE_SALT", raising=False)
    with pytest.raises(RuntimeError, match="RGPD_ANONYMIZE_SALT is not set"):
        get_anonymize_salt()


def test_get_anonymize_salt_fails_if_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RGPD_ANONYMIZE_SALT", "   ")
    with pytest.raises(RuntimeError, match="RGPD_ANONYMIZE_SALT is not set"):
        get_anonymize_salt()
