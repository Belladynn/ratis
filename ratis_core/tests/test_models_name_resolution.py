"""Tests for ``ProductNameResolution`` SQLAlchemy model — bloc A
(cross-retailer consensus + ESL elevated).

Pure metadata tests : no DB required (mirrors ``test_models_retailer.py``).
Asserts the new ``source_type`` / ``retailer_id`` columns plus the
extended CHECK constraints land on the model definition.

The DB-level trigger (``trg_pnr_sync_retailer_id``) is exercised by the
service-side integration tests in
``webservices/ratis_product_analyser/tests/test_ledger_writes.py`` — the
SQLAlchemy event hooks in ``ratis_core/models/name_resolution.py`` install
it during ``Base.metadata.create_all()``.
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


class TestProductNameResolutionModel:
    def test_model_importable_from_core(self):
        from ratis_core.models import ProductNameResolution  # noqa: F401

    def test_tablename(self):
        from ratis_core.models import ProductNameResolution

        assert ProductNameResolution.__tablename__ == "product_name_resolutions"

    # ── new columns ───────────────────────────────────────────────────────────

    def test_source_type_column_exists_text_not_null(self):
        from ratis_core.models import ProductNameResolution

        col = _col(ProductNameResolution, "source_type")
        assert col.nullable is False
        # Text == sa.Text for SQLAlchemy 2.0 ; isinstance is robust.
        assert isinstance(col.type, sa.Text)

    def test_source_type_default_receipt(self):
        from ratis_core.models import ProductNameResolution

        col = _col(ProductNameResolution, "source_type")
        default = col.server_default.arg if col.server_default else None
        assert default is not None
        assert "receipt" in str(default)

    def test_retailer_id_column_exists_uuid_nullable(self):
        from ratis_core.models import ProductNameResolution

        col = _col(ProductNameResolution, "retailer_id")
        assert col.nullable is True
        assert isinstance(col.type, postgresql.UUID)

    def test_retailer_id_fk_on_delete_restrict(self):
        from ratis_core.models import ProductNameResolution

        col = _col(ProductNameResolution, "retailer_id")
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "retailers"
        assert fks[0].ondelete == "RESTRICT"

    # ── CHECK constraints ─────────────────────────────────────────────────────

    def test_source_type_check_constraint(self):
        from ratis_core.models import ProductNameResolution

        names = _check_names(ProductNameResolution)
        assert "pnr_source_type_check" in names

    def test_match_method_check_includes_esl_and_cross_source(self):
        """The CHECK clause must list the two new methods (V1 + V2 stub)."""
        from ratis_core.models import ProductNameResolution

        check = next(
            c
            for c in ProductNameResolution.__table__.constraints
            if isinstance(c, sa.CheckConstraint) and c.name == "pnr_match_method_check"
        )
        sql = str(check.sqltext).lower()
        assert "'esl'" in sql
        assert "'cross_source_esl_exact'" in sql

    # ── indexes ──────────────────────────────────────────────────────────────

    def test_unique_index_on_scan_source_label(self):
        from ratis_core.models import ProductNameResolution

        names = _index_names(ProductNameResolution)
        assert "idx_pnr_scan_source_label" in names
        idx = next(i for i in ProductNameResolution.__table__.indexes if i.name == "idx_pnr_scan_source_label")
        assert idx.unique is True
        cols = [c.name for c in idx.columns]
        assert cols == ["scan_id", "source_type", "normalized_label"]

    def test_legacy_unique_index_dropped_from_model(self):
        """Old idx_pnr_scan_label is gone from the SA metadata too."""
        from ratis_core.models import ProductNameResolution

        names = _index_names(ProductNameResolution)
        assert "idx_pnr_scan_label" not in names

    def test_partial_index_retailer_source_label(self):
        from ratis_core.models import ProductNameResolution

        idx = next(
            (i for i in ProductNameResolution.__table__.indexes if i.name == "idx_pnr_retailer_source_label"),
            None,
        )
        assert idx is not None
        cols = [c.name for c in idx.columns]
        assert cols == ["retailer_id", "source_type", "normalized_label"]
        # Partial WHERE clause
        where = idx.dialect_options.get("postgresql", {}).get("where")
        assert where is not None
        assert "retailer_id" in str(where).lower()

    # ── relationships ─────────────────────────────────────────────────────────

    def test_retailer_relationship_exists(self):
        from ratis_core.models import ProductNameResolution

        mapper = sa_inspect(ProductNameResolution)
        assert "retailer" in mapper.relationships
