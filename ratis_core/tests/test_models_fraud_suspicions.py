"""Tests for anti-fraud PR1 ORM additions.

Covers :

* ``Receipt`` model — the 7 new anti-fraud columns + their types +
  Pattern A mirror of ``ck_receipts_time_precision`` + the 4 partial
  indexes.
* ``FraudSuspicion`` model — full shape contract (columns, types,
  nullability, defaults, FK ON DELETE behavior, CHECKs, indexes).
* Module-level constants ``DETECTION_SIGNALS`` + ``RESOLUTION_STATUSES``
  match the PG CHECK enums (single source of truth for application
  code building admin filters / dropdowns in PR2-5).

No DB required — these tests only inspect SQLAlchemy metadata. The
DB-level invariants (UNIQUE partial index behavior, CHECK rejections)
live in ``alembic/tests/test_anti_fraud_pr1_migration.py``.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql


def _col(model, name: str) -> sa.Column:
    mapper = sa_inspect(model)
    return mapper.columns[name]


def _check_names(model) -> set[str]:
    return {c.name for c in model.__table__.constraints if isinstance(c, sa.CheckConstraint)}


def _index_names(model) -> set[str]:
    return {idx.name for idx in model.__table__.indexes}


# ────────────────────────────────────────────────────────────────────
# Receipt — 7 new anti-fraud columns
# ────────────────────────────────────────────────────────────────────


class TestReceiptAntiFraudColumns:
    def test_parse_fingerprint_user_string64_nullable(self):
        from ratis_core.models import Receipt

        col = _col(Receipt, "parse_fingerprint_user")
        assert col.nullable is True
        assert isinstance(col.type, sa.String)
        assert col.type.length == 64

    def test_parse_fingerprint_global_string64_nullable(self):
        from ratis_core.models import Receipt

        col = _col(Receipt, "parse_fingerprint_global")
        assert col.nullable is True
        assert isinstance(col.type, sa.String)
        assert col.type.length == 64

    def test_fingerprint_components_jsonb_nullable(self):
        from ratis_core.models import Receipt

        col = _col(Receipt, "fingerprint_components_jsonb")
        assert col.nullable is True
        assert isinstance(col.type, postgresql.JSONB)

    def test_image_phash_string16_nullable(self):
        from ratis_core.models import Receipt

        col = _col(Receipt, "image_phash")
        assert col.nullable is True
        assert isinstance(col.type, sa.String)
        assert col.type.length == 16

    def test_device_fingerprint_string16_nullable(self):
        from ratis_core.models import Receipt

        col = _col(Receipt, "device_fingerprint")
        assert col.nullable is True
        assert isinstance(col.type, sa.String)
        assert col.type.length == 16

    def test_time_precision_text_nullable(self):
        from ratis_core.models import Receipt

        col = _col(Receipt, "time_precision")
        assert col.nullable is True
        # PG type is TEXT — SQLAlchemy maps to ``Text``.
        assert isinstance(col.type, sa.Text)

    def test_consolidated_from_ids_uuid_array_nullable(self):
        from ratis_core.models import Receipt

        col = _col(Receipt, "consolidated_from_ids")
        assert col.nullable is True
        assert isinstance(col.type, postgresql.ARRAY)
        # Inner item type is UUID.
        assert isinstance(col.type.item_type, postgresql.UUID)


class TestReceiptAntiFraudConstraints:
    """Pattern A — CHECK mirror + 4 partial indexes in ``__table_args__``."""

    def test_time_precision_check_present(self):
        from ratis_core.models import Receipt

        assert "ck_receipts_time_precision" in _check_names(Receipt)

    def test_fp_user_unique_partial_index(self):
        from ratis_core.models import Receipt

        idx_by_name = {idx.name: idx for idx in Receipt.__table__.indexes}
        idx = idx_by_name.get("idx_receipts_fp_user")
        assert idx is not None, "idx_receipts_fp_user missing on Receipt"
        assert idx.unique is True
        # Partial — postgresql_where present.
        where = idx.dialect_options.get("postgresql", {}).get("where")
        assert where is not None
        sql = str(where)
        assert "receipt_barcode IS NULL" in sql
        assert "parse_fingerprint_user IS NOT NULL" in sql

    def test_fp_global_lookup_non_unique_partial_index(self):
        from ratis_core.models import Receipt

        idx_by_name = {idx.name: idx for idx in Receipt.__table__.indexes}
        idx = idx_by_name.get("idx_receipts_fp_global_lookup")
        assert idx is not None
        # NON-unique — collisions are the fraud signal.
        assert idx.unique is False or idx.unique is None
        where = idx.dialect_options.get("postgresql", {}).get("where")
        assert where is not None
        assert "parse_fingerprint_global IS NOT NULL" in str(where)

    def test_image_phash_partial_index(self):
        from ratis_core.models import Receipt

        idx_by_name = {idx.name: idx for idx in Receipt.__table__.indexes}
        idx = idx_by_name.get("idx_receipts_image_phash")
        assert idx is not None
        where = idx.dialect_options.get("postgresql", {}).get("where")
        assert where is not None
        assert "image_phash IS NOT NULL" in str(where)

    def test_device_fp_partial_index(self):
        from ratis_core.models import Receipt

        idx_by_name = {idx.name: idx for idx in Receipt.__table__.indexes}
        idx = idx_by_name.get("idx_receipts_device_fp")
        assert idx is not None
        where = idx.dialect_options.get("postgresql", {}).get("where")
        assert where is not None
        assert "device_fingerprint IS NOT NULL" in str(where)


# ────────────────────────────────────────────────────────────────────
# FraudSuspicion — full shape contract
# ────────────────────────────────────────────────────────────────────


class TestFraudSuspicionModel:
    def test_model_importable_from_core(self):
        from ratis_core.models import FraudSuspicion  # noqa: F401

    def test_tablename(self):
        from ratis_core.models import FraudSuspicion

        assert FraudSuspicion.__tablename__ == "fraud_suspicions"

    def test_id_pk_uuid_with_server_default(self):
        from ratis_core.models import FraudSuspicion

        col = _col(FraudSuspicion, "id")
        assert col.primary_key
        assert isinstance(col.type, postgresql.UUID)
        # server_default = ``gen_random_uuid()``.
        assert col.server_default is not None
        assert "gen_random_uuid" in str(col.server_default.arg)

    def test_receipt_id_fk_cascade(self):
        from ratis_core.models import FraudSuspicion

        col = _col(FraudSuspicion, "receipt_id")
        assert col.nullable is False
        assert isinstance(col.type, postgresql.UUID)
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "receipts"
        assert fks[0].ondelete == "CASCADE"

    def test_evidence_receipt_ids_uuid_array_not_null(self):
        from ratis_core.models import FraudSuspicion

        col = _col(FraudSuspicion, "evidence_receipt_ids")
        assert col.nullable is False
        assert isinstance(col.type, postgresql.ARRAY)
        assert isinstance(col.type.item_type, postgresql.UUID)

    def test_detection_signal_text_not_null(self):
        from ratis_core.models import FraudSuspicion

        col = _col(FraudSuspicion, "detection_signal")
        assert col.nullable is False
        assert isinstance(col.type, sa.Text)

    def test_detected_at_timestamptz_default_now(self):
        from ratis_core.models import FraudSuspicion

        col = _col(FraudSuspicion, "detected_at")
        assert col.nullable is False
        assert isinstance(col.type, sa.DateTime)
        assert col.type.timezone is True
        assert col.server_default is not None
        assert "now()" in str(col.server_default.arg)

    def test_resolution_status_default_pending(self):
        from ratis_core.models import FraudSuspicion

        col = _col(FraudSuspicion, "resolution_status")
        assert col.nullable is False
        default_sql = str(col.server_default.arg)
        assert "'pending'" in default_sql

    def test_admin_operator_nullable_text(self):
        from ratis_core.models import FraudSuspicion

        col = _col(FraudSuspicion, "admin_operator")
        assert col.nullable is True
        assert isinstance(col.type, sa.Text)

    def test_resolved_at_nullable_timestamptz(self):
        from ratis_core.models import FraudSuspicion

        col = _col(FraudSuspicion, "resolved_at")
        assert col.nullable is True
        assert isinstance(col.type, sa.DateTime)
        assert col.type.timezone is True

    def test_resolution_note_nullable_text(self):
        from ratis_core.models import FraudSuspicion

        col = _col(FraudSuspicion, "resolution_note")
        assert col.nullable is True
        assert isinstance(col.type, sa.Text)

    def test_three_check_constraints_present(self):
        from ratis_core.models import FraudSuspicion

        checks = _check_names(FraudSuspicion)
        assert "ck_fraud_suspicions_signal" in checks
        assert "ck_fraud_suspicions_status" in checks
        assert "ck_fraud_suspicions_resolution_coherence" in checks

    def test_signal_check_enumerates_four_values(self):
        from ratis_core.models import FraudSuspicion

        for c in FraudSuspicion.__table__.constraints:
            if isinstance(c, sa.CheckConstraint) and c.name == "ck_fraud_suspicions_signal":
                sql = str(c.sqltext)
                for v in (
                    "phash",
                    "fp_global_strict",
                    "fp_global_minute",
                    "device_shared",
                ):
                    assert v in sql, f"{v} missing from CHECK SQL"
                return
        raise AssertionError("ck_fraud_suspicions_signal not found")

    def test_status_check_enumerates_four_values(self):
        from ratis_core.models import FraudSuspicion

        for c in FraudSuspicion.__table__.constraints:
            if isinstance(c, sa.CheckConstraint) and c.name == "ck_fraud_suspicions_status":
                sql = str(c.sqltext)
                for v in (
                    "pending",
                    "confirmed_fraud",
                    "cleared",
                    "escalated_support",
                ):
                    assert v in sql, f"{v} missing from CHECK SQL"
                return
        raise AssertionError("ck_fraud_suspicions_status not found")

    def test_four_indexes_present(self):
        from ratis_core.models import FraudSuspicion

        idx = _index_names(FraudSuspicion)
        assert "idx_fraud_suspicions_status" in idx
        assert "idx_fraud_suspicions_receipt" in idx
        assert "idx_fraud_suspicions_signal" in idx
        assert "idx_fraud_suspicions_detected_at" in idx

    def test_status_index_partial_on_pending(self):
        from ratis_core.models import FraudSuspicion

        idx_by_name = {idx.name: idx for idx in FraudSuspicion.__table__.indexes}
        idx = idx_by_name["idx_fraud_suspicions_status"]
        where = idx.dialect_options.get("postgresql", {}).get("where")
        assert where is not None
        assert "pending" in str(where)

    def test_receipt_relationship_back_to_receipt(self):
        from ratis_core.models import FraudSuspicion

        mapper = sa_inspect(FraudSuspicion)
        assert "receipt" in mapper.relationships


# ────────────────────────────────────────────────────────────────────
# Module-level constants — single source of truth for app code
# ────────────────────────────────────────────────────────────────────


class TestSignalAndStatusConstants:
    def test_detection_signals_match_check_constraint(self):
        from ratis_core.models import DETECTION_SIGNALS

        assert (
            frozenset(
                {
                    "phash",
                    "fp_global_strict",
                    "fp_global_minute",
                    "device_shared",
                    # Added by anti-fraud PR4 (migration 20260511_1700_afpr4)
                    # for the daily-soft-burst flag (≥ soft_warn, < hard cap).
                    "daily_soft_burst",
                }
            )
            == DETECTION_SIGNALS
        )

    def test_resolution_statuses_match_check_constraint(self):
        from ratis_core.models import RESOLUTION_STATUSES

        assert (
            frozenset(
                {
                    "pending",
                    "confirmed_fraud",
                    "cleared",
                    "escalated_support",
                }
            )
            == RESOLUTION_STATUSES
        )

    def test_constants_are_frozenset(self):
        """Frozenset — immutable across the lifetime of the process."""
        from ratis_core.models import DETECTION_SIGNALS, RESOLUTION_STATUSES

        assert isinstance(DETECTION_SIGNALS, frozenset)
        assert isinstance(RESOLUTION_STATUSES, frozenset)
