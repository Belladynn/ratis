"""Migration tests for ``20260511_1500_afpr1`` — anti-fraud PR1 schema.

Validates :

* Upgrade installs the 7 new ``receipts`` columns (all nullable) +
  the 4 partial indexes + the ``time_precision`` CHECK.
* Upgrade creates the ``fraud_suspicions`` table with the canonical
  shape (9 columns, 3 CHECKs including the resolution coherence
  invariant, FK to receipts with ON DELETE CASCADE, 4 indexes).
* The intra-user UNIQUE partial index ``idx_receipts_fp_user`` REJECTS
  a second row with the same ``parse_fingerprint_user`` when
  ``receipt_barcode IS NULL`` …
* … but ACCEPTS the same fingerprint when one of the rows carries a
  ``receipt_barcode`` (the existing ``uq_receipts_receipt_barcode``
  takes over the dedup contract in that case).
* The cross-user lookup index ``idx_receipts_fp_global_lookup`` is
  NOT unique — collisions are allowed at the DB layer (the policy is
  applied by the application code in PR4).
* The ``time_precision`` CHECK rejects values other than ``'second'``
  and ``'minute'`` and accepts NULL.
* The ``fraud_suspicions`` resolution-coherence CHECK rejects an
  inconsistent state (e.g. ``'cleared'`` with NULL ``resolved_at``).
* The FK ``fraud_suspicions.receipt_id → receipts.id`` is ON DELETE
  CASCADE — deleting a receipt removes its suspicions.
* Downgrade fully reverses the schema (columns, indexes, table all
  gone, no leftover constraints).
* Full roundtrip (upgrade → downgrade → upgrade) is idempotent.
"""
from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


TARGET_REVISION = "20260511_1500_afpr1"
PREV_REVISION = "20260511_1100_widen"

ALEMBIC_CFG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "alembic.ini"
)


def _unmasked_url(engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _make_alembic_config(engine) -> Config:
    cfg = Config(ALEMBIC_CFG_PATH)
    cfg.set_main_option("sqlalchemy.url", _unmasked_url(engine))
    return cfg


@pytest.fixture(scope="module")
def upgraded_engine(migration_engine):
    """Wipe + run alembic upgrade up to the migration under test."""
    with migration_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    os.environ["DATABASE_URL"] = _unmasked_url(migration_engine)
    cfg = _make_alembic_config(migration_engine)
    command.upgrade(cfg, TARGET_REVISION)
    return migration_engine


def _column_names(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t"
        ),
        {"t": table},
    ).all()
    return {r[0] for r in rows}


def _index_names(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = :t"
        ),
        {"t": table},
    ).all()
    return {r[0] for r in rows}


def _check_names(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT con.conname
              FROM pg_constraint con
              JOIN pg_class c ON c.oid = con.conrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE con.contype = 'c'
               AND n.nspname = 'public'
               AND c.relname = :t
            """
        ),
        {"t": table},
    ).all()
    return {r[0] for r in rows}


def _insert_receipt(
    conn,
    *,
    receipt_barcode: str | None = None,
    parse_fingerprint_user: str | None = None,
    parse_fingerprint_global: str | None = None,
    image_phash: str | None = None,
    device_fingerprint: str | None = None,
    time_precision: str | None = None,
) -> uuid.UUID:
    """Insert a minimal valid receipt row, returning its id."""
    rid = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO receipts ("
            "  id, purchased_at, receipt_barcode, "
            "  parse_fingerprint_user, parse_fingerprint_global, "
            "  image_phash, device_fingerprint, time_precision"
            ") VALUES ("
            "  :id, :pdate, :barcode, :fu, :fg, :ph, :df, :tp"
            ")"
        ),
        {
            "id": rid,
            "pdate": date(2026, 5, 11),
            "barcode": receipt_barcode,
            "fu": parse_fingerprint_user,
            "fg": parse_fingerprint_global,
            "ph": image_phash,
            "df": device_fingerprint,
            "tp": time_precision,
        },
    )
    return rid


# ---------------------------------------------------------------------------
# Schema invariants — receipts new columns + indexes + CHECK
# ---------------------------------------------------------------------------


_EXPECTED_RECEIPTS_COLUMNS = {
    "parse_fingerprint_user",
    "parse_fingerprint_global",
    "fingerprint_components_jsonb",
    "image_phash",
    "device_fingerprint",
    "time_precision",
    "consolidated_from_ids",
}


def test_receipts_has_seven_new_columns(upgraded_engine):
    with upgraded_engine.connect() as conn:
        cols = _column_names(conn, "receipts")
    missing = _EXPECTED_RECEIPTS_COLUMNS - cols
    assert not missing, f"Missing on receipts: {missing}"


def test_receipts_new_columns_are_nullable(upgraded_engine):
    """All 7 anti-fraud columns must be nullable (NULL = legacy / not-yet-computed)."""
    with upgraded_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'receipts' AND column_name = ANY(:cols)"
            ),
            {"cols": list(_EXPECTED_RECEIPTS_COLUMNS)},
        ).all()
    for col, nullable in rows:
        assert nullable == "YES", f"{col} must be nullable"


_EXPECTED_RECEIPTS_NEW_INDEXES = {
    "idx_receipts_fp_user",
    "idx_receipts_fp_global_lookup",
    "idx_receipts_image_phash",
    "idx_receipts_device_fp",
}


def test_receipts_has_four_new_indexes(upgraded_engine):
    with upgraded_engine.connect() as conn:
        idx = _index_names(conn, "receipts")
    missing = _EXPECTED_RECEIPTS_NEW_INDEXES - idx
    assert not missing, f"Missing indexes: {missing}"


def test_receipts_fp_user_index_is_unique(upgraded_engine):
    """``idx_receipts_fp_user`` must be UNIQUE (intra-user dedup)."""
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'idx_receipts_fp_user'"
            )
        ).first()
    assert row is not None
    assert "UNIQUE" in row[0].upper()


def test_receipts_fp_global_index_not_unique(upgraded_engine):
    """``idx_receipts_fp_global_lookup`` must NOT be UNIQUE — collisions are the signal."""
    with upgraded_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'idx_receipts_fp_global_lookup'"
            )
        ).first()
    assert row is not None
    # No CREATE UNIQUE INDEX in the definition.
    assert "UNIQUE INDEX" not in row[0].upper()


def test_receipts_time_precision_check_present(upgraded_engine):
    with upgraded_engine.connect() as conn:
        checks = _check_names(conn, "receipts")
    assert "ck_receipts_time_precision" in checks


# ---------------------------------------------------------------------------
# Partial index behavior — UNIQUE only when barcode is NULL
# ---------------------------------------------------------------------------


def test_fp_user_unique_blocks_duplicate_when_barcode_null(upgraded_engine):
    """Two rows with same ``parse_fingerprint_user`` + barcode NULL → IntegrityError."""
    with upgraded_engine.begin() as conn:
        _insert_receipt(
            conn,
            receipt_barcode=None,
            parse_fingerprint_user="a" * 64,
        )
    with pytest.raises(IntegrityError):
        with upgraded_engine.begin() as conn:
            _insert_receipt(
                conn,
                receipt_barcode=None,
                parse_fingerprint_user="a" * 64,
            )
    # Cleanup the surviving row so other tests stay isolated.
    with upgraded_engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM receipts WHERE parse_fingerprint_user = :fp"
            ),
            {"fp": "a" * 64},
        )


def test_fp_user_unique_allows_duplicate_when_barcode_set(upgraded_engine):
    """Two rows with same ``parse_fingerprint_user`` but DIFFERENT barcodes → both INSERT.

    The partial index only fires when ``receipt_barcode IS NULL``. When a
    barcode is present, the existing ``uq_receipts_receipt_barcode`` is
    the authoritative dedup gate (different barcodes ⇒ legitimately
    different receipts).
    """
    fp = "b" * 64
    with upgraded_engine.begin() as conn:
        _insert_receipt(
            conn,
            receipt_barcode="BC-1-" + uuid.uuid4().hex[:8],
            parse_fingerprint_user=fp,
        )
        _insert_receipt(
            conn,
            receipt_barcode="BC-2-" + uuid.uuid4().hex[:8],
            parse_fingerprint_user=fp,
        )
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM receipts WHERE parse_fingerprint_user = :fp"
            ),
            {"fp": fp},
        ).scalar()
    assert count == 2
    with upgraded_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM receipts WHERE parse_fingerprint_user = :fp"),
            {"fp": fp},
        )


def test_fp_global_lookup_allows_cross_user_collisions(upgraded_engine):
    """``idx_receipts_fp_global_lookup`` is non-unique by design."""
    fg = "c" * 64
    with upgraded_engine.begin() as conn:
        _insert_receipt(
            conn,
            receipt_barcode=None,
            parse_fingerprint_user="u1-" + "0" * 60,
            parse_fingerprint_global=fg,
        )
        _insert_receipt(
            conn,
            receipt_barcode=None,
            parse_fingerprint_user="u2-" + "0" * 60,
            parse_fingerprint_global=fg,
        )
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM receipts WHERE parse_fingerprint_global = :fp"
            ),
            {"fp": fg},
        ).scalar()
    assert count == 2
    with upgraded_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM receipts WHERE parse_fingerprint_global = :fp"),
            {"fp": fg},
        )


# ---------------------------------------------------------------------------
# time_precision CHECK
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tp", ["second", "minute", None])
def test_time_precision_accepts_valid_values(upgraded_engine, tp):
    with upgraded_engine.begin() as conn:
        rid = _insert_receipt(conn, time_precision=tp)
        conn.execute(text("DELETE FROM receipts WHERE id = :id"), {"id": rid})


def _insert_invalid_time_precision(engine, value: str) -> None:
    with engine.begin() as conn:
        _insert_receipt(conn, time_precision=value)


@pytest.mark.parametrize("tp", ["hour", "SECOND", "", "millisecond"])
def test_time_precision_rejects_invalid_values(upgraded_engine, tp):
    with pytest.raises(IntegrityError):
        _insert_invalid_time_precision(upgraded_engine, tp)


# ---------------------------------------------------------------------------
# fraud_suspicions table — shape + invariants + FK cascade
# ---------------------------------------------------------------------------


_EXPECTED_FS_COLUMNS = {
    "id",
    "receipt_id",
    "evidence_receipt_ids",
    "detection_signal",
    "detected_at",
    "resolution_status",
    "admin_operator",
    "resolved_at",
    "resolution_note",
}


def test_fraud_suspicions_table_shape(upgraded_engine):
    with upgraded_engine.connect() as conn:
        cols = _column_names(conn, "fraud_suspicions")
    assert cols == _EXPECTED_FS_COLUMNS, f"shape drift: {cols ^ _EXPECTED_FS_COLUMNS}"


_EXPECTED_FS_CHECKS = {
    "ck_fraud_suspicions_signal",
    "ck_fraud_suspicions_status",
    "ck_fraud_suspicions_resolution_coherence",
}


def test_fraud_suspicions_has_three_checks(upgraded_engine):
    with upgraded_engine.connect() as conn:
        checks = _check_names(conn, "fraud_suspicions")
    assert _EXPECTED_FS_CHECKS.issubset(checks), (
        f"missing: {_EXPECTED_FS_CHECKS - checks}"
    )


_EXPECTED_FS_INDEXES = {
    "fraud_suspicions_pkey",
    "idx_fraud_suspicions_status",
    "idx_fraud_suspicions_receipt",
    "idx_fraud_suspicions_signal",
    "idx_fraud_suspicions_detected_at",
}


def test_fraud_suspicions_indexes_present(upgraded_engine):
    with upgraded_engine.connect() as conn:
        idx = _index_names(conn, "fraud_suspicions")
    missing = _EXPECTED_FS_INDEXES - idx
    assert not missing, f"missing indexes: {missing}"


def test_fraud_suspicions_signal_check_rejects_unknown_value(upgraded_engine):
    """Detection signal must be one of the 4 acted-2026-05-11 values."""
    with upgraded_engine.begin() as conn:
        rid = _insert_receipt(conn)
    with pytest.raises(IntegrityError):
        with upgraded_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO fraud_suspicions ("
                    "  receipt_id, evidence_receipt_ids, detection_signal"
                    ") VALUES (:rid, ARRAY[]::uuid[], 'unknown_signal')"
                ),
                {"rid": rid},
            )
    with upgraded_engine.begin() as conn:
        conn.execute(text("DELETE FROM receipts WHERE id = :id"), {"id": rid})


def test_fraud_suspicions_pending_must_have_null_resolution(upgraded_engine):
    """``pending`` with ``resolved_at`` set must violate the coherence CHECK."""
    with upgraded_engine.begin() as conn:
        rid = _insert_receipt(conn)
    with pytest.raises(IntegrityError):
        with upgraded_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO fraud_suspicions ("
                    "  receipt_id, evidence_receipt_ids, detection_signal, "
                    "  resolution_status, resolved_at"
                    ") VALUES ("
                    "  :rid, ARRAY[]::uuid[], 'phash', 'pending', now()"
                    ")"
                ),
                {"rid": rid},
            )
    with upgraded_engine.begin() as conn:
        conn.execute(text("DELETE FROM receipts WHERE id = :id"), {"id": rid})


def test_fraud_suspicions_resolved_must_have_resolved_at(upgraded_engine):
    """Non-``pending`` status with NULL ``resolved_at`` must violate CHECK."""
    with upgraded_engine.begin() as conn:
        rid = _insert_receipt(conn)
    with pytest.raises(IntegrityError):
        with upgraded_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO fraud_suspicions ("
                    "  receipt_id, evidence_receipt_ids, detection_signal, "
                    "  resolution_status"
                    ") VALUES ("
                    "  :rid, ARRAY[]::uuid[], 'phash', 'cleared'"
                    ")"
                ),
                {"rid": rid},
            )
    with upgraded_engine.begin() as conn:
        conn.execute(text("DELETE FROM receipts WHERE id = :id"), {"id": rid})


def test_fraud_suspicions_default_pending(upgraded_engine):
    """``resolution_status`` defaults to ``'pending'`` when omitted."""
    with upgraded_engine.begin() as conn:
        rid = _insert_receipt(conn)
        conn.execute(
            text(
                "INSERT INTO fraud_suspicions ("
                "  receipt_id, evidence_receipt_ids, detection_signal"
                ") VALUES (:rid, ARRAY[]::uuid[], 'phash')"
            ),
            {"rid": rid},
        )
        status = conn.execute(
            text(
                "SELECT resolution_status FROM fraud_suspicions "
                "WHERE receipt_id = :rid"
            ),
            {"rid": rid},
        ).scalar()
        # Cleanup
        conn.execute(text("DELETE FROM receipts WHERE id = :id"), {"id": rid})
    assert status == "pending"


def test_fraud_suspicions_cascades_on_receipt_delete(upgraded_engine):
    """Deleting the receipt cascades to delete its suspicions."""
    with upgraded_engine.begin() as conn:
        rid = _insert_receipt(conn)
        conn.execute(
            text(
                "INSERT INTO fraud_suspicions ("
                "  receipt_id, evidence_receipt_ids, detection_signal"
                ") VALUES (:rid, ARRAY[]::uuid[], 'phash')"
            ),
            {"rid": rid},
        )
        before = conn.execute(
            text(
                "SELECT COUNT(*) FROM fraud_suspicions WHERE receipt_id = :r"
            ),
            {"r": rid},
        ).scalar()
        conn.execute(text("DELETE FROM receipts WHERE id = :id"), {"id": rid})
        after = conn.execute(
            text(
                "SELECT COUNT(*) FROM fraud_suspicions WHERE receipt_id = :r"
            ),
            {"r": rid},
        ).scalar()
    assert before == 1
    assert after == 0


# ---------------------------------------------------------------------------
# Roundtrip — upgrade → downgrade → upgrade is idempotent
# ---------------------------------------------------------------------------


def test_downgrade_reverses_schema_then_upgrade_restores(migration_engine):
    """Downgrade removes columns + table ; re-upgrade restores them all."""
    with migration_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    os.environ["DATABASE_URL"] = _unmasked_url(migration_engine)
    cfg = _make_alembic_config(migration_engine)

    # upgrade
    command.upgrade(cfg, TARGET_REVISION)
    with migration_engine.connect() as conn:
        assert "fraud_suspicions" in {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).all()
        }
        assert _EXPECTED_RECEIPTS_COLUMNS.issubset(
            _column_names(conn, "receipts")
        )

    # downgrade
    command.downgrade(cfg, PREV_REVISION)
    with migration_engine.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).all()
        }
        assert "fraud_suspicions" not in tables
        leftover = _EXPECTED_RECEIPTS_COLUMNS & _column_names(conn, "receipts")
        assert not leftover, f"columns leaked downgrade: {leftover}"
        # No leftover index either.
        idx_leftover = _EXPECTED_RECEIPTS_NEW_INDEXES & _index_names(
            conn, "receipts"
        )
        assert not idx_leftover, f"indexes leaked downgrade: {idx_leftover}"
        assert "ck_receipts_time_precision" not in _check_names(
            conn, "receipts"
        )

    # re-upgrade — idempotent
    command.upgrade(cfg, TARGET_REVISION)
    with migration_engine.connect() as conn:
        assert _EXPECTED_RECEIPTS_COLUMNS.issubset(
            _column_names(conn, "receipts")
        )
        assert _EXPECTED_RECEIPTS_NEW_INDEXES.issubset(
            _index_names(conn, "receipts")
        )
        assert _EXPECTED_FS_COLUMNS.issubset(
            _column_names(conn, "fraud_suspicions")
        )
