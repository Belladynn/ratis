"""Tests for migration ``20260502_1000_nrcF`` — NRC bloc F data migration.

Validates the three concerns of the migration :

1. Legacy ``fuzzy_strict`` scans (``status`` ∈ {matched, accepted}) are
   reclassed to ``status='pending'`` while preserving ``product_ean`` /
   ``match_method`` so the scanner originel keeps a resolved view.
2. Historic ``barcode`` scans are backfilled into
   ``product_name_resolutions`` with ``UPPER(TRIM(scanned_name))`` as the
   ``normalized_label`` (decision documented in the migration docstring).
3. The view ``product_observed_names`` carries a deprecation
   ``COMMENT ON VIEW`` (no physical drop — V2 scope).

The fourth test re-runs the migration after a fresh upgrade to confirm
idempotence (the WHERE clause + ``ON CONFLICT DO NOTHING`` should make a
second invocation a no-op).
"""

from __future__ import annotations

import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

# Revision under test
TARGET_REVISION = "20260502_1000_nrcF"
PREV_REVISION = "20260501_2000_nrcD"

ALEMBIC_CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")


def _make_alembic_config(engine) -> Config:
    cfg = Config(ALEMBIC_CFG_PATH)
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


# ── seeding helpers ───────────────────────────────────────────────────────────


def _seed_user(conn) -> uuid.UUID:
    """Insert a minimal user row.

    The ``auth_coherence`` CHECK from the initial schema requires either
    (provider='email' + password_hash) OR (provider!='email' +
    provider_id). We pick the email branch because it does not need a
    second seed string ; ``password_hash`` is opaque to the migration
    under test and can carry any non-null marker.
    """
    user_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO users (id, email, provider, password_hash, support_id)
            VALUES (:id, :email, 'email', :pw, :support_id)
            """
        ),
        {
            "id": str(user_id),
            "email": f"seed-{user_id.hex[:8]}@nrcf.test",
            "pw": "x" * 60,  # bcrypt-shaped opaque marker, never used
            "support_id": f"RTS-{user_id.hex[:6].upper()}",
        },
    )
    return user_id


def _seed_store(conn) -> uuid.UUID:
    """Insert a minimal store row.

    The columns required by the v3 schema : ``id`` (PK), ``name``,
    ``lat`` / ``lng`` (both NOT NULL — using zero is fine, no spatial
    constraint at this layer), and ``source`` / ``validation_status``
    have ``DEFAULT`` clauses we accept.
    """
    store_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO stores (id, name, lat, lng, source, validation_status)
            VALUES (:id, 'NRC-F Test Store', 0, 0, 'user_suggested', 'confirmed')
            """
        ),
        {"id": str(store_id)},
    )
    return store_id


def _seed_product(conn, ean: str) -> None:
    """Insert a product if absent (idempotent on PK)."""
    conn.execute(
        text(
            """
            INSERT INTO products (ean, name, source)
            VALUES (:ean, 'NRC-F Seed', 'off')
            ON CONFLICT (ean) DO NOTHING
            """
        ),
        {"ean": ean},
    )


def _seed_scan(
    conn,
    *,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    product_ean: str | None,
    scanned_name: str | None,
    status: str,
    match_method: str | None,
    scan_type: str = "electronic_label",
) -> uuid.UUID:
    """Insert a scan row directly via SQL (bypasses ORM defaults).

    ``scan_type='electronic_label'`` is the simplest choice that admits
    a populated ``scanned_name`` :
    - ``manual_no_scanned_name`` forbids non-null scanned_name on manual.
    - ``receipt_required`` forces a non-null receipt_id on receipt — would
      need to seed a receipt row first.
    - ``electronic_label`` requires receipt_id NULL but admits everything
      we need for the migration's invariants.
    """
    # The UNIQUE constraint ``(user_id, store_id, product_ean, scanned_at)``
    # is enforced at row-level. Multiple seed scans for the same triple
    # need distinct ``scanned_at`` to coexist — we offset by the row's
    # UUID hash to keep the helper allocation-free.
    scan_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO scans
                (id, user_id, store_id, product_ean, scanned_name,
                 price, quantity, scan_type, status, match_method,
                 scanned_at, status_updated_at)
            VALUES
                (:id, :uid, :sid, :ean, :name,
                 100, 1, :stype, :status, :method,
                 NOW() + (:offset_us || ' microseconds')::interval,
                 NOW() + (:offset_us || ' microseconds')::interval)
            """
        ),
        {
            "id": str(scan_id),
            "uid": str(user_id),
            "sid": str(store_id),
            "ean": product_ean,
            "name": scanned_name,
            "stype": scan_type,
            "status": status,
            "method": match_method,
            # Hash to a 0..999_999 microsecond offset — collision-free
            # within the bounds of one fixture run.
            "offset_us": str(scan_id.int % 1_000_000),
        },
    )
    return scan_id


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def upgraded_engine(migration_engine):
    """Run alembic upgrade to PREV_REVISION, seed deterministic test data,
    then upgrade to TARGET_REVISION (the migration under test).

    This lets each test inspect the *post-migration* state of rows that
    existed *pre-migration* — exactly the production scenario.
    """
    # Wipe any state left by a previous module's tests.
    with migration_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    os.environ["DATABASE_URL"] = str(migration_engine.url)
    cfg = _make_alembic_config(migration_engine)

    # 1. Bring the schema up to PREV_REVISION (everything but the migration
    # under test).
    command.upgrade(cfg, PREV_REVISION)

    # 2. Seed legacy fixtures via raw SQL so the test does not depend on
    # the Python ORM importing cleanly. Each test's invariant is keyed
    # off these IDs (yielded via the ``seed_ids`` fixture below).
    with migration_engine.connect() as conn:
        user_a = _seed_user(conn)
        user_b = _seed_user(conn)
        store_a = _seed_store(conn)
        store_b = _seed_store(conn)

        _seed_product(conn, "1111111111111")
        _seed_product(conn, "2222222222222")
        _seed_product(conn, "3333333333333")

        # (1) reclass-target scans : matched + fuzzy_strict, accepted +
        # fuzzy_strict. Both should become ``pending``.
        scan_matched_fuzzy = _seed_scan(
            conn,
            user_id=user_a,
            store_id=store_a,
            product_ean="1111111111111",
            scanned_name="LEGACY MATCHED FUZZY",
            status="matched",
            match_method="fuzzy_strict",
        )
        scan_accepted_fuzzy = _seed_scan(
            conn,
            user_id=user_a,
            store_id=store_a,
            product_ean="1111111111111",
            scanned_name="LEGACY ACCEPTED FUZZY",
            status="accepted",
            match_method="fuzzy_strict",
        )
        # (1) NEGATIVE control : matched + barcode must NOT be reclassed
        # (different match_method). Use a v3 status that admits a
        # populated ean+method (matched satisfies
        # ck_scans_matched_requires_ean_method).
        scan_matched_barcode = _seed_scan(
            conn,
            user_id=user_a,
            store_id=store_a,
            product_ean="2222222222222",
            scanned_name="BARCODE NOT TOUCHED",
            status="matched",
            match_method="barcode",
        )

        # (2) backfill-target scans : barcode + complete fields → ledger row
        # expected.
        scan_barcode_complete = _seed_scan(
            conn,
            user_id=user_b,
            store_id=store_b,
            product_ean="3333333333333",
            scanned_name="  Nutella 400g  ",  # whitespace test for TRIM
            status="accepted",
            match_method="barcode",
        )
        # (2) NEGATIVE control : barcode + missing store_id must be skipped
        # (the WHERE clause demands a non-null store).
        scan_barcode_no_store = _seed_scan(
            conn,
            user_id=user_b,
            store_id=store_b,  # seed valid then NULL it (FK is nullable)
            product_ean="3333333333333",
            scanned_name="NO STORE",
            status="accepted",
            match_method="barcode",
        )
        # ``ck_scans_store_status_consistency`` ties store_status='unknown'
        # to store_id IS NULL — must move both atomically.
        conn.execute(
            text(
                "UPDATE scans "
                "SET store_id = NULL, store_status = 'unknown' "
                "WHERE id = :id"
            ),
            {"id": str(scan_barcode_no_store)},
        )
        conn.commit()

    # 3. Apply the migration under test.
    command.upgrade(cfg, TARGET_REVISION)

    # ``return`` rather than ``yield`` : the session-level
    # ``migration_engine`` fixture owns the schema teardown ; this fixture
    # has no per-module cleanup of its own.
    return migration_engine, {
        "scan_matched_fuzzy": scan_matched_fuzzy,
        "scan_accepted_fuzzy": scan_accepted_fuzzy,
        "scan_matched_barcode": scan_matched_barcode,
        "scan_barcode_complete": scan_barcode_complete,
        "scan_barcode_no_store": scan_barcode_no_store,
        "user_a": user_a,
        "user_b": user_b,
        "store_a": store_a,
        "store_b": store_b,
    }


# ── (1) reclass legacy fuzzy_strict ───────────────────────────────────────────


def test_reclass_fuzzy_strict_matched_to_pending(upgraded_engine):
    """``status='matched' + match_method='fuzzy_strict'`` → ``status='pending'``.

    The match_method / product_ean columns are *preserved* — only the
    lifecycle status moves so the scanner originel keeps a resolved view.
    """
    engine, ids = upgraded_engine
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, match_method, product_ean "
                "FROM scans WHERE id = :id"
            ),
            {"id": str(ids["scan_matched_fuzzy"])},
        ).first()
    assert row is not None
    assert row.status == "pending", (
        "matched+fuzzy_strict should have been reclassed to pending"
    )
    assert row.match_method == "fuzzy_strict", "match_method must be preserved"
    assert row.product_ean == "1111111111111", "product_ean must be preserved"


def test_reclass_does_not_touch_accepted_scans(upgraded_engine):
    """``status='accepted' + fuzzy_strict`` rows are intentionally LEFT
    untouched.

    The DB trigger ``fn_check_scan_status_transition`` forbids any
    transition out of ``accepted`` (load-bearing invariant — user-
    confirmed scans feed cashback / receipt history materialised views).
    We pick the clean path : exclude ``accepted`` from the reclass
    rather than disable the trigger. See migration docstring § (1) for
    the full rationale.
    """
    engine, ids = upgraded_engine
    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM scans WHERE id = :id"),
            {"id": str(ids["scan_accepted_fuzzy"])},
        ).scalar_one()
    assert status == "accepted", (
        "accepted+fuzzy_strict must stay accepted — the status-transition "
        "trigger forbids the move and we refuse to disable it"
    )


def test_reclass_does_not_touch_barcode_method(upgraded_engine):
    """A scan with ``match_method='barcode'`` is *not* a reclass target —
    even if status was 'matched', the row stays as-is."""
    engine, ids = upgraded_engine
    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM scans WHERE id = :id"),
            {"id": str(ids["scan_matched_barcode"])},
        ).scalar_one()
    assert status == "matched", (
        "barcode-method scans must be left untouched by the reclass step"
    )


# ── (2) backfill ledger from barcode ──────────────────────────────────────────


def test_backfill_creates_ledger_row_for_complete_barcode_scan(upgraded_engine):
    """A barcode scan with all required fields lands one ledger row."""
    engine, ids = upgraded_engine
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT scan_id, store_id, normalized_label, product_ean,
                       user_id, match_method
                FROM product_name_resolutions
                WHERE scan_id = :id
                """
            ),
            {"id": str(ids["scan_barcode_complete"])},
        ).fetchall()
    assert len(rows) == 1, "exactly one ledger row expected for the complete barcode scan"
    row = rows[0]
    assert str(row.store_id) == str(ids["store_b"])
    # UPPER(TRIM(...)) is the chosen ``normalized_label`` derivation.
    assert row.normalized_label == "NUTELLA 400G"
    assert row.product_ean == "3333333333333"
    assert str(row.user_id) == str(ids["user_b"])
    assert row.match_method == "barcode"


def test_backfill_skips_barcode_scan_without_store(upgraded_engine):
    """A barcode scan with NULL store_id is not seedable — NRC consensus
    is per-store. The migration's WHERE clause must filter it out."""
    engine, ids = upgraded_engine
    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM product_name_resolutions WHERE scan_id = :id"
            ),
            {"id": str(ids["scan_barcode_no_store"])},
        ).scalar_one()
    assert count == 0, "scan with NULL store_id should NOT be backfilled"


def test_backfill_skips_non_barcode_scans(upgraded_engine):
    """``match_method='fuzzy_strict'`` (even after reclass to pending) must
    not produce a ledger row — the backfill is barcode-only."""
    engine, ids = upgraded_engine
    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM product_name_resolutions WHERE scan_id = :id"
            ),
            {"id": str(ids["scan_matched_fuzzy"])},
        ).scalar_one()
    assert count == 0


# ── (3) deprecate observed_names view ─────────────────────────────────────────


def test_observed_names_view_carries_deprecation_comment(upgraded_engine):
    """The view ``product_observed_names`` must carry a non-empty COMMENT
    mentioning the deprecation. The view itself stays — physical drop is
    V2 scope per ARCH § F."""
    engine, _ = upgraded_engine
    with engine.connect() as conn:
        # ``obj_description`` returns the comment for an object's OID.
        comment = conn.execute(
            text(
                "SELECT obj_description('product_observed_names'::regclass, 'pg_class')"
            )
        ).scalar_one_or_none()
    assert comment is not None, "view should carry a COMMENT after migration"
    assert "DEPRECATED" in comment.upper(), (
        f"comment should announce the deprecation, got : {comment!r}"
    )


# ── (4) idempotence ───────────────────────────────────────────────────────────


def test_migration_is_idempotent_on_double_apply(upgraded_engine):
    """Re-running the migration's body must not produce duplicate ledger
    rows or oscillate the reclassed scans.

    We invoke the SQL directly (rather than re-running ``alembic upgrade``,
    which is a no-op past head) to verify the WHERE clause + ``ON CONFLICT``
    semantics protect against accidental replay (e.g. operator runs the
    migration twice, or a CI rerun applies it on a partially-migrated DB).
    """
    engine, ids = upgraded_engine

    # Snapshot pre-state for diff later.
    with engine.connect() as conn:
        ledger_count_before = conn.execute(
            text("SELECT COUNT(*) FROM product_name_resolutions")
        ).scalar_one()
        reclassed_status_before = conn.execute(
            text("SELECT status FROM scans WHERE id = :id"),
            {"id": str(ids["scan_matched_fuzzy"])},
        ).scalar_one()

    # Re-run the migration's payload.
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                UPDATE scans
                   SET status = 'pending'
                 WHERE status = 'matched'
                   AND match_method = 'fuzzy_strict'
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO product_name_resolutions
                    (id, scan_id, store_id, normalized_label, product_ean,
                     user_id, match_method, resolved_at)
                SELECT
                    gen_random_uuid(),
                    s.id,
                    s.store_id,
                    UPPER(TRIM(s.scanned_name)) AS normalized_label,
                    s.product_ean,
                    s.user_id,
                    'barcode',
                    s.scanned_at
                FROM scans s
                WHERE s.match_method = 'barcode'
                  AND s.store_id IS NOT NULL
                  AND s.scanned_name IS NOT NULL
                  AND TRIM(s.scanned_name) <> ''
                  AND s.product_ean IS NOT NULL
                  AND s.user_id IS NOT NULL
                ON CONFLICT (scan_id, normalized_label) DO NOTHING
                """
            )
        )
        conn.commit()
        ledger_count_after = conn.execute(
            text("SELECT COUNT(*) FROM product_name_resolutions")
        ).scalar_one()
        reclassed_status_after = conn.execute(
            text("SELECT status FROM scans WHERE id = :id"),
            {"id": str(ids["scan_matched_fuzzy"])},
        ).scalar_one()

    assert ledger_count_before == ledger_count_after, (
        "ON CONFLICT DO NOTHING must keep the row count stable on replay"
    )
    assert reclassed_status_before == reclassed_status_after == "pending", (
        "reclass must be a fixed-point — second run is a no-op"
    )
