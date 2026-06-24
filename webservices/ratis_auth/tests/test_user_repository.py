"""Tests for ``user_repository.create_user`` — focus on ``support_id``.

Properties asserted :
- A fresh ``support_id`` is assigned on every successful insert.
- A UNIQUE collision is retried (mocked generator returns a colliding
  value first, then a unique one — the second call must succeed).
- After ``_SUPPORT_ID_MAX_RETRIES`` colliding draws the IntegrityError
  is propagated (operator must widen the alphabet).

Note : all fixtures use ``account_type="oauth"`` — the post-Phase-2
default. The OAuth identity itself lives in ``user_identities``, not on
the ``users`` row.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
import repositories.user_repository as user_repo
from ratis_core.identifiers import generate_support_id
from sqlalchemy.exc import IntegrityError

_FORMAT_RE = re.compile(r"^RTS-[A-HJ-NP-Z2-9]{6}$")


def test_create_user_assigns_valid_support_id(db, client):
    user = user_repo.create_user(
        db,
        email="repo-sid@example.com",
        account_type="oauth",
    )
    db.commit()
    assert user.support_id is not None
    assert _FORMAT_RE.match(user.support_id), f"bad shape : {user.support_id!r}"


def test_create_user_retries_on_support_id_collision(db, client):
    """First draw collides with an existing user ⇒ repo must retry."""
    # Seed an existing user with a known support_id.
    existing = user_repo.create_user(
        db,
        email="exists@example.com",
        account_type="oauth",
    )
    db.commit()
    colliding_sid = existing.support_id

    # Make ``generate_support_id`` return the colliding value once,
    # then a fresh one (real implementation).
    real_sid = generate_support_id()
    while real_sid == colliding_sid:  # pragma: no cover — astronomical
        real_sid = generate_support_id()

    side_effects = iter([colliding_sid, real_sid])

    with patch(
        "repositories.user_repository.generate_support_id",
        side_effect=lambda: next(side_effects),
    ):
        user = user_repo.create_user(
            db,
            email="retry-victim@example.com",
            account_type="oauth",
        )
        db.commit()

    assert user.support_id == real_sid


def test_create_user_propagates_after_max_retries(db, client):
    """If every draw collides, the IntegrityError must surface."""
    existing = user_repo.create_user(
        db,
        email="block@example.com",
        account_type="oauth",
    )
    db.commit()

    with (
        patch(
            "repositories.user_repository.generate_support_id",
            return_value=existing.support_id,
        ),
        pytest.raises(IntegrityError),
    ):
        user_repo.create_user(
            db,
            email="never-fits@example.com",
            account_type="oauth",
        )


def test_create_user_allows_duplicate_email(db, client):
    """Two ``create_user`` calls with the same email both succeed.

    Email is NOT unique since H2 Phase 2 (migration
    ``20260518_1300_acct_type``) — the account key moved to
    ``user_identities.(provider, provider_id)``. This test guards that
    ``create_user`` does not spuriously fail (nor mis-retry as a
    ``support_id`` collision) on a duplicate email — both rows must land
    with distinct ids and distinct ``support_id`` values.
    """
    first = user_repo.create_user(
        db,
        email="dup@example.com",
        account_type="oauth",
    )
    db.commit()
    second = user_repo.create_user(
        db,
        email="dup@example.com",
        account_type="oauth",
    )
    db.commit()
    assert first.id != second.id
    assert first.support_id != second.support_id
