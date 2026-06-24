"""Tests for store-resolution cold-start columns.

TDD — these tests must FAIL before the model changes, then pass after.
No DB required: we inspect the SQLAlchemy column definitions directly.
"""

from __future__ import annotations

import sqlalchemy as sa
from ratis_core.models.scan import Receipt
from ratis_core.models.store import Store
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql

# ── helpers ──────────────────────────────────────────────────────────────────


def _col(model, name: str) -> sa.Column:
    """Return the mapped column object for *name* from *model*."""
    mapper = sa_inspect(model)
    return mapper.columns[name]


def _check_names(model) -> set[str]:
    """Return the set of CHECK constraint names on *model*'s table."""
    return {c.name for c in model.__table__.constraints if isinstance(c, sa.CheckConstraint)}


# ── Receipt.pending_items ─────────────────────────────────────────────────────


class TestReceiptPendingItems:
    def test_column_exists(self):
        col = _col(Receipt, "pending_items")
        assert col is not None

    def test_column_is_nullable(self):
        col = _col(Receipt, "pending_items")
        assert col.nullable is True

    def test_column_type_is_jsonb(self):
        """Column must use dialect-specific JSONB (not generic JSON)."""
        col = _col(Receipt, "pending_items")
        assert isinstance(col.type, postgresql.JSONB), f"Expected postgresql.JSONB type, got {type(col.type)}"


# ── Receipt.user_store_hint ───────────────────────────────────────────────────


class TestReceiptUserStoreHint:
    def test_column_exists(self):
        col = _col(Receipt, "user_store_hint")
        assert col is not None

    def test_column_is_nullable(self):
        col = _col(Receipt, "user_store_hint")
        assert col.nullable is True

    def test_column_type_is_text(self):
        col = _col(Receipt, "user_store_hint")
        assert isinstance(col.type, sa.Text)


# ── Store.source ──────────────────────────────────────────────────────────────


class TestStoreSource:
    def test_column_exists(self):
        col = _col(Store, "source")
        assert col is not None

    def test_column_is_not_nullable(self):
        col = _col(Store, "source")
        assert col.nullable is False

    def test_column_type_is_text(self):
        col = _col(Store, "source")
        assert isinstance(col.type, sa.Text)

    def test_column_has_default_osm(self):
        col = _col(Store, "source")
        # server_default contains the SQL expression
        assert col.server_default is not None
        assert "osm" in str(col.server_default.arg)

    def test_check_constraint_exists(self):
        names = _check_names(Store)
        assert "ck_stores_source" in names, f"Expected 'ck_stores_source' check constraint, found: {names}"

    def test_check_constraint_includes_sirene_and_overture(self):
        """SIRENE PR1 — extend the source enum to support multi-source ingestion.

        ``sirene``  : INSEE batch (FR primary, ratis_batch_sirene_sync).
        ``overture``: anticipation V3 (international second source).
        """
        for c in Store.__table__.constraints:
            if isinstance(c, sa.CheckConstraint) and c.name == "ck_stores_source":
                sql = str(c.sqltext)
                for value in ("osm", "sirene", "overture", "admin", "user_suggested"):
                    assert f"'{value}'" in sql, f"ck_stores_source must allow '{value}', got: {sql}"
                return
        raise AssertionError("ck_stores_source check constraint not found on Store")


# ── Store.siret partial index (SIRENE PR1) ────────────────────────────────────


class TestStoreSiretLookupIndex:
    """SIRENE PR1 — partial index on stores.siret for INSEE upsert lookups.

    Indexes are excluded from Alembic autogenerate (see ``alembic/env.py``),
    so the migration creates it explicitly. The model still declares it so the
    contract is visible at the ORM layer.
    """

    def test_ix_stores_siret_lookup_declared(self):
        index_names = {idx.name for idx in Store.__table__.indexes}
        assert "ix_stores_siret_lookup" in index_names, f"Expected 'ix_stores_siret_lookup' index, found: {index_names}"

    def test_ix_stores_siret_lookup_is_partial(self):
        """Partial WHERE ``siret IS NOT NULL`` to keep the index small.

        Most stores (OSM/admin/user_suggested) have NULL siret today; only
        SIRENE-sourced rows populate it.
        """
        for idx in Store.__table__.indexes:
            if idx.name == "ix_stores_siret_lookup":
                where = str(idx.dialect_options["postgresql"].get("where", ""))
                assert "siret" in where, f"Index WHERE must reference siret, got: {where}"
                assert "NOT NULL" in where.upper(), f"Index must be partial WHERE siret IS NOT NULL, got: {where}"
                cols = [c.name for c in idx.columns]
                assert cols == ["siret"], f"Index must be on (siret,) only, got columns: {cols}"
                return
        raise AssertionError("ix_stores_siret_lookup not found on Store")
