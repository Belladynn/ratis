"""Tests for ``user_identity_repository`` — the ``user_identities`` data layer.

H2 Phase 2 moved the OAuth identity off the ``users`` row into a dedicated
``user_identities`` table keyed by the unique ``(provider, provider_id)``
pair. This module is pure data-layer (R03 : no SQL outside repositories) —
no login behaviour is exercised here.

The DB-session fixture is ``db`` (SQLAlchemy 2.0 SAVEPOINT rollback, see
``conftest.py``). ``user_repository.create_user`` seeds the parent
``users`` row the identity FK requires.
"""

from __future__ import annotations

import pytest
import repositories.user_identity_repository as identity_repo
import repositories.user_repository as user_repo
from sqlalchemy.exc import IntegrityError


def test_create_and_get_by_provider(db, client):
    user = user_repo.create_user(
        db,
        email="identity-create@example.com",
        account_type="oauth",
    )
    identity_repo.create(
        db,
        user_id=user.id,
        provider="google",
        provider_id="g-1",
        email="x@y.z",
    )
    db.commit()

    found = identity_repo.get_by_provider(db, "google", "g-1")
    assert found is not None
    assert found.user_id == user.id


def test_get_by_provider_miss_returns_none(db, client):
    assert identity_repo.get_by_provider(db, "google", "nope") is None


def test_list_for_user_returns_all(db, client):
    user = user_repo.create_user(
        db,
        email="identity-list@example.com",
        account_type="oauth",
    )
    identity_repo.create(db, user_id=user.id, provider="google", provider_id="g-2")
    identity_repo.create(db, user_id=user.id, provider="apple", provider_id="a-2")
    db.commit()

    assert len(identity_repo.list_for_user(db, user.id)) == 2


def test_count_for_user(db, client):
    user = user_repo.create_user(
        db,
        email="identity-count@example.com",
        account_type="oauth",
    )
    identity_repo.create(db, user_id=user.id, provider="google", provider_id="g-3")
    identity_repo.create(db, user_id=user.id, provider="apple", provider_id="a-3")
    db.commit()

    assert identity_repo.count_for_user(db, user.id) == 2


def test_delete_for_user_removes_one_provider(db, client):
    user = user_repo.create_user(
        db,
        email="identity-delete@example.com",
        account_type="oauth",
    )
    identity_repo.create(db, user_id=user.id, provider="google", provider_id="g-4")
    identity_repo.create(db, user_id=user.id, provider="apple", provider_id="a-4")
    db.commit()

    deleted = identity_repo.delete_for_user(db, user.id, "apple")
    db.commit()

    assert deleted == 1
    assert identity_repo.count_for_user(db, user.id) == 1
    remaining = identity_repo.list_for_user(db, user.id)
    assert remaining[0].provider == "google"


def test_delete_for_user_missing_provider_returns_zero(db, client):
    user = user_repo.create_user(
        db,
        email="identity-delete-miss@example.com",
        account_type="oauth",
    )
    identity_repo.create(db, user_id=user.id, provider="google", provider_id="g-5")
    db.commit()

    deleted = identity_repo.delete_for_user(db, user.id, "apple")
    # The DELETE statement is still emitted (matching 0 rows) — commit to
    # release the SAVEPOINT, mirroring real route behaviour.
    db.commit()

    assert deleted == 0


def test_get_by_provider_for_user(db, client):
    user = user_repo.create_user(
        db,
        email="identity-by-provider@example.com",
        account_type="oauth",
    )
    identity_repo.create(db, user_id=user.id, provider="google", provider_id="g-bp")

    other = user_repo.create_user(
        db,
        email="identity-by-provider-other@example.com",
        account_type="oauth",
    )
    identity_repo.create(db, user_id=other.id, provider="apple", provider_id="a-bp")
    db.commit()

    found = identity_repo.get_by_provider_for_user(db, user.id, "google")
    assert found is not None
    assert found.user_id == user.id

    # Provider not linked to this user → None.
    assert identity_repo.get_by_provider_for_user(db, user.id, "apple") is None


def test_unique_provider_provider_id_enforced(db, client):
    first = user_repo.create_user(
        db,
        email="identity-uniq-1@example.com",
        account_type="oauth",
    )
    second = user_repo.create_user(
        db,
        email="identity-uniq-2@example.com",
        account_type="oauth",
    )
    identity_repo.create(db, user_id=first.id, provider="google", provider_id="g-dup")
    db.commit()

    # Same (provider, provider_id) for a different user → UNIQUE violation.
    # IntegrityError fires at the flush inside ``create``; the session is
    # poisoned afterwards — the test ends here.
    with pytest.raises(IntegrityError):
        identity_repo.create(db, user_id=second.id, provider="google", provider_id="g-dup")
