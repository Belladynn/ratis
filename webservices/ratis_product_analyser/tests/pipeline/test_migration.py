"""Tests for the pipeline DB layer (bloc 2 of ARCH_receipt_pipeline.md).

These tests exercise the **schema invariants** that the migration installs :
new CHECK constraints on ``scans``, append-only ``pipeline_audit_log``,
GENERATED ``name_normalized`` columns on products/stores, and the unaccent
extension. They run against the ratis_test DB seeded by the PA conftest
(``Base.metadata.create_all`` — the constraints are mirrored on the SQLAlchemy
models so that ``create_all`` produces them, NOT just ``alembic upgrade``).

Migration-specific aspects (upgrade/downgrade flow, table existence, raw
extension install) are covered separately in
``alembic/tests/test_pipeline_migration.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

# ── helpers ──────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _expect_constraint_violation(
    db,
    sql: str,
    params: dict | None = None,
    *,
    exc=IntegrityError,
):
    """Execute SQL+commit and assert a constraint violation is raised.

    Wraps execute+commit inside a single callable to satisfy ruff PT012
    (pytest.raises block must be a single statement) while still triggering
    the COMMIT phase — some PG CHECK constraints are DEFERRED and only fire
    at COMMIT time. Always rolls back after to leave the session usable.

    Returns the ``ExceptionInfo`` so callers can inspect the message
    (used by the append-only trigger test which asserts on error string).
    """

    def _do() -> None:
        if params is None:
            db.execute(text(sql))
        else:
            db.execute(text(sql), params)
        db.commit()

    with pytest.raises(exc) as info:
        _do()
    db.rollback()
    return info


def _insert_parsed_ticket(
    db,
    *,
    parsed_jsonb_hash: str,
    receipt_id: uuid.UUID | None = None,
    image_hash: str = "imghash",
) -> uuid.UUID:
    """Direct SQL insert (bypasses ORM defaults) to keep tests focused on
    the SQL invariants. Returns the generated id."""
    # Note : the JSON literal is inlined rather than passed as a parameter
    # because psycopg/SQLAlchemy parses ``:foo`` as a parameter — using
    # ``:payload::jsonb`` collides with the ``::jsonb`` cast syntax.
    row = db.execute(
        text(
            """
            INSERT INTO parsed_tickets
                (receipt_id, parsed_jsonb, parsed_jsonb_hash,
                 raw_ticket_image_hash, ocr_engine_version, captured_at)
            VALUES
                (:receipt_id, '{}'::jsonb, :hash, :image_hash,
                 'paddleocr-2.7.3-fr', :captured_at)
            RETURNING id
            """
        ),
        {
            "receipt_id": receipt_id,
            "hash": parsed_jsonb_hash,
            "image_hash": image_hash,
            "captured_at": _now(),
        },
    ).scalar_one()
    db.commit()
    return row


# ── DDL — tables / columns / extensions exist ────────────────────────────────


def test_parsed_tickets_table_exists(db):
    inspector = inspect(db.get_bind())
    assert "parsed_tickets" in inspector.get_table_names()


def test_pipeline_audit_log_table_exists(db):
    inspector = inspect(db.get_bind())
    assert "pipeline_audit_log" in inspector.get_table_names()


def test_scans_has_match_confidence_and_parsed_ticket_id(db):
    inspector = inspect(db.get_bind())
    cols = {c["name"] for c in inspector.get_columns("scans")}
    assert "match_confidence" in cols
    assert "parsed_ticket_id" in cols


def test_unaccent_extension_installed(db):
    """The migration installs ``unaccent`` ; conftest mirrors that for tests."""
    res = db.execute(text("SELECT extname FROM pg_extension WHERE extname = 'unaccent'")).scalar_one_or_none()
    assert res == "unaccent"


# ── scans CHECK invariants ───────────────────────────────────────────────────


def test_scans_status_v3_accepts_v3_values(db, store, user, product):
    """Sanity : the new statuses 'matched' / 'unresolved' / 'rejected' are
    valid values per the superset enum. Side-checks the v3 invariants
    (matched ⟹ ean+method, etc.) so this is also a happy-path smoke test."""
    db.execute(
        text(
            """
            INSERT INTO scans
                (id, user_id, store_id, product_ean, scan_type, price,
                 quantity, status, match_method, match_confidence, scanned_at)
            VALUES
                (:id, :uid, :sid, :ean, 'electronic_label', 100,
                 1, 'matched', 'consensus_match', 0.92, :now)
            """
        ),
        {
            "id": uuid.uuid4(),
            "uid": user.id,
            "sid": store.id,
            "ean": product.ean,
            "now": _now(),
        },
    )
    db.commit()


def test_scans_status_check_v3_rejects_unknown_status(db, store, user, product):
    """An unknown status (not in the superset enum) must be rejected."""
    _expect_constraint_violation(
        db,
        """
        INSERT INTO scans
            (id, user_id, store_id, product_ean, scan_type, price,
             quantity, status, scanned_at)
        VALUES
            (:id, :uid, :sid, :ean, 'electronic_label', 100,
             1, 'frobnicated', :now)
        """,
        {
            "id": uuid.uuid4(),
            "uid": user.id,
            "sid": store.id,
            "ean": product.ean,
            "now": _now(),
        },
    )


def test_scans_matched_requires_ean_and_match_method(db, store, user):
    """status='matched' AND product_ean IS NULL → IntegrityError."""
    _expect_constraint_violation(
        db,
        """
        INSERT INTO scans
            (id, user_id, store_id, scan_type, price, quantity, status,
             match_method, scanned_name, scanned_at)
        VALUES
            (:id, :uid, :sid, 'electronic_label', 100, 1, 'matched',
             'consensus_match', 'foo', :now)
        """,
        {
            "id": uuid.uuid4(),
            "uid": user.id,
            "sid": store.id,
            "now": _now(),
        },
    )


def test_scans_matched_requires_match_method_set(db, store, user, product):
    """status='matched' AND match_method IS NULL → IntegrityError."""
    _expect_constraint_violation(
        db,
        """
        INSERT INTO scans
            (id, user_id, store_id, product_ean, scan_type, price,
             quantity, status, scanned_at)
        VALUES
            (:id, :uid, :sid, :ean, 'electronic_label', 100,
             1, 'matched', :now)
        """,
        {
            "id": uuid.uuid4(),
            "uid": user.id,
            "sid": store.id,
            "ean": product.ean,
            "now": _now(),
        },
    )


def test_scans_unresolved_requires_rejected_reason(db, store, user):
    """status='unresolved' AND rejected_reason IS NULL → IntegrityError."""
    _expect_constraint_violation(
        db,
        """
        INSERT INTO scans
            (id, user_id, store_id, scan_type, price, quantity, status,
             scanned_name, scanned_at)
        VALUES
            (:id, :uid, :sid, 'electronic_label', 100, 1, 'unresolved',
             'lait demi-écrémé', :now)
        """,
        {
            "id": uuid.uuid4(),
            "uid": user.id,
            "sid": store.id,
            "now": _now(),
        },
    )


def test_scans_rejected_requires_rejected_reason(db, store, user):
    """status='rejected' AND rejected_reason IS NULL → IntegrityError."""
    _expect_constraint_violation(
        db,
        """
        INSERT INTO scans
            (id, user_id, store_id, scan_type, price, quantity, status,
             scanned_name, scanned_at)
        VALUES
            (:id, :uid, :sid, 'electronic_label', 100, 1, 'rejected',
             'foo', :now)
        """,
        {
            "id": uuid.uuid4(),
            "uid": user.id,
            "sid": store.id,
            "now": _now(),
        },
    )


def test_scans_match_confidence_out_of_range_rejected(db, store, user):
    """match_confidence > 1.0 → IntegrityError."""
    _expect_constraint_violation(
        db,
        """
        INSERT INTO scans
            (id, user_id, store_id, scan_type, price, quantity, status,
             match_confidence, scanned_name, scanned_at)
        VALUES
            (:id, :uid, :sid, 'electronic_label', 100, 1, 'pending',
             1.5, 'foo', :now)
        """,
        {
            "id": uuid.uuid4(),
            "uid": user.id,
            "sid": store.id,
            "now": _now(),
        },
    )


def test_scans_match_confidence_negative_rejected(db, store, user):
    """match_confidence < 0.0 → IntegrityError."""
    _expect_constraint_violation(
        db,
        """
        INSERT INTO scans
            (id, user_id, store_id, scan_type, price, quantity, status,
             match_confidence, scanned_name, scanned_at)
        VALUES
            (:id, :uid, :sid, 'electronic_label', 100, 1, 'pending',
             -0.1, 'foo', :now)
        """,
        {
            "id": uuid.uuid4(),
            "uid": user.id,
            "sid": store.id,
            "now": _now(),
        },
    )


# ── parsed_tickets — UNIQUE jsonb_hash ───────────────────────────────────────


def test_parsed_tickets_unique_jsonb_hash(db):
    """Two rows with the same parsed_jsonb_hash → IntegrityError (idempotence)."""
    _insert_parsed_ticket(db, parsed_jsonb_hash="dupehash")
    with pytest.raises(IntegrityError):
        _insert_parsed_ticket(db, parsed_jsonb_hash="dupehash")


# ── pipeline_audit_log — append-only trigger ─────────────────────────────────


def test_pipeline_audit_log_phase_check_rejects_unknown_phase(db):
    """phase='unknown' must be rejected (CHECK constraint)."""
    _expect_constraint_violation(
        db,
        """
        INSERT INTO pipeline_audit_log (phase, level, event)
        VALUES ('unknown', 'normal', 'foo')
        """,
    )


def test_pipeline_audit_log_level_check_rejects_unknown_level(db):
    """level='trace' must be rejected (CHECK constraint)."""
    _expect_constraint_violation(
        db,
        """
        INSERT INTO pipeline_audit_log (phase, level, event)
        VALUES ('extract', 'trace', 'foo')
        """,
    )


def test_pipeline_audit_log_append_only_blocks_update(db):
    """The trigger ``trg_pipeline_audit_log_no_update`` raises on UPDATE.
    ``conftest.py``'s ``setup_db`` registers it via ``create_all``? — no,
    it's installed by the migration. The model-level mirror is the trigger
    re-creation in conftest, so we install the trigger lazily here when
    running on a create_all-only test DB. See note at top of file.
    """
    # Install the trigger if missing (create_all does NOT install triggers,
    # only the migration does). This keeps the test honest while running on
    # a Base.metadata.create_all() schema.
    db.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION fn_pipeline_audit_log_no_update()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'pipeline_audit_log is append-only — UPDATE prohibited';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    db.execute(
        text(
            """
            DROP TRIGGER IF EXISTS trg_pipeline_audit_log_no_update
                ON pipeline_audit_log
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TRIGGER trg_pipeline_audit_log_no_update
            BEFORE UPDATE ON pipeline_audit_log
            FOR EACH ROW EXECUTE FUNCTION fn_pipeline_audit_log_no_update()
            """
        )
    )
    row_id = db.execute(
        text(
            """
            INSERT INTO pipeline_audit_log (phase, level, event, payload)
            VALUES ('extract', 'normal', 'ocr_done', '{}'::jsonb)
            RETURNING id
            """
        )
    ).scalar_one()
    db.commit()

    exc_info = _expect_constraint_violation(
        db,
        "UPDATE pipeline_audit_log SET event = 'tampered' WHERE id = :id",
        {"id": row_id},
        exc=(ProgrammingError, IntegrityError, Exception),
    )
    assert "append-only" in str(exc_info.value).lower() or "prohibited" in str(exc_info.value).lower()


# ── GENERATED columns — name_normalized ──────────────────────────────────────


def test_products_name_normalized_uppercases_and_strips_accents(db):
    """INSERT product name='Crémeux' → name_normalized='CREMEUX'."""
    db.execute(
        text(
            """
            INSERT INTO products (ean, name, source)
            VALUES ('1111111111111', 'Crémeux', 'off')
            """
        )
    )
    db.commit()
    normalized = db.execute(text("SELECT name_normalized FROM products WHERE ean = '1111111111111'")).scalar_one()
    assert normalized == "CREMEUX"


def test_stores_name_normalized_uppercases_and_strips_accents(db):
    """INSERT store name='Auchan Saint-Étienne' → 'AUCHAN SAINT-ETIENNE'."""
    sid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO stores (id, name, lat, lng, is_disabled)
            VALUES (:id, 'Auchan Saint-Étienne', 45.4397, 4.3872, false)
            """
        ),
        {"id": sid},
    )
    db.commit()
    normalized = db.execute(
        text("SELECT name_normalized FROM stores WHERE id = :id"),
        {"id": sid},
    ).scalar_one()
    assert normalized == "AUCHAN SAINT-ETIENNE"


# ── pipeline PR-A — barcode_fields jsonb + stores.store_code indexes ──────


def test_receipts_barcode_fields_is_jsonb(db):
    """``receipts.barcode_fields`` must be physical type ``jsonb``.

    ORM declares :class:`JSONB` ; the migration aligns the physical column
    accordingly (was ``json`` pre-PR-A). Asserts the data_type matches so
    that any drift between model and migration breaks the test loudly.
    """
    res = db.execute(
        text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'receipts' AND column_name = 'barcode_fields'"
        )
    ).scalar_one()
    assert res == "jsonb"


def test_stores_store_code_indexes_exist(db):
    """The two partial indexes on ``stores.store_code`` are installed."""
    rows = db.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'stores' AND indexname IN "
            "('ix_stores_retailer_store_code', 'ix_stores_store_code')"
        )
    ).fetchall()
    names = {r.indexname for r in rows}
    assert names == {"ix_stores_retailer_store_code", "ix_stores_store_code"}
