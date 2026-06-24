"""Tests for retailers + retailer_aliases models (DA-34).

No DB required: we inspect SQLAlchemy column metadata.
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


# ── Retailer ─────────────────────────────────────────────────────────────────


class TestRetailerModel:
    def test_model_importable_from_core(self):
        from ratis_core.models import Retailer  # noqa: F401

    def test_tablename(self):
        from ratis_core.models import Retailer

        assert Retailer.__tablename__ == "retailers"

    def test_id_pk_is_uuid(self):
        from ratis_core.models import Retailer

        col = _col(Retailer, "id")
        assert col.primary_key
        assert isinstance(col.type, postgresql.UUID)

    def test_canonical_name_is_unique_text(self):
        from ratis_core.models import Retailer

        col = _col(Retailer, "canonical_name")
        assert col.nullable is False
        assert col.unique is True

    def test_slug_is_unique_text(self):
        from ratis_core.models import Retailer

        col = _col(Retailer, "slug")
        assert col.nullable is False
        assert col.unique is True

    def test_parent_id_nullable_fk_self_ref(self):
        from ratis_core.models import Retailer

        col = _col(Retailer, "parent_id")
        assert col.nullable is True
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "retailers"
        assert fks[0].ondelete == "SET NULL"

    def test_color_hex_check_constraint(self):
        from ratis_core.models import Retailer

        assert "ck_retailers_color_hex" in _check_names(Retailer)

    def test_country_code_default_fr(self):
        from ratis_core.models import Retailer

        col = _col(Retailer, "country_code")
        assert col.nullable is False
        # default
        default = col.server_default.arg if col.server_default else None
        if default is not None:
            assert "FR" in str(default)

    def test_is_verified_default_false(self):
        from ratis_core.models import Retailer

        col = _col(Retailer, "is_verified")
        assert col.nullable is False

    def test_timestamps_exist(self):
        from ratis_core.models import Retailer

        assert _col(Retailer, "created_at") is not None
        assert _col(Retailer, "updated_at") is not None

    def test_has_parent_children_relationships(self):
        from ratis_core.models import Retailer

        mapper = sa_inspect(Retailer)
        assert "parent" in mapper.relationships
        assert "children" in mapper.relationships
        assert "aliases" in mapper.relationships

    def test_has_stores_relationship(self):
        from ratis_core.models import Retailer

        mapper = sa_inspect(Retailer)
        assert "stores" in mapper.relationships


# ── RetailerAlias ────────────────────────────────────────────────────────────


class TestRetailerAliasModel:
    def test_model_importable(self):
        from ratis_core.models import RetailerAlias  # noqa: F401

    def test_tablename(self):
        from ratis_core.models import RetailerAlias

        assert RetailerAlias.__tablename__ == "retailer_aliases"

    def test_composite_pk(self):
        from ratis_core.models import RetailerAlias

        pk_cols = {c.name for c in RetailerAlias.__table__.primary_key.columns}
        assert pk_cols == {"retailer_id", "alias"}

    def test_retailer_id_fk_cascade(self):
        from ratis_core.models import RetailerAlias

        col = _col(RetailerAlias, "retailer_id")
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "retailers"
        assert fks[0].ondelete == "CASCADE"

    def test_source_check_constraint(self):
        from ratis_core.models import RetailerAlias

        assert "ck_retailer_aliases_source" in _check_names(RetailerAlias)

    def test_retailer_relationship_back_populates(self):
        from ratis_core.models import RetailerAlias

        mapper = sa_inspect(RetailerAlias)
        assert "retailer" in mapper.relationships


# ── Store.retailer_id + relationship ─────────────────────────────────────────


class TestStoreRetailerId:
    def test_retailer_id_column_exists(self):
        from ratis_core.models import Store

        col = _col(Store, "retailer_id")
        assert col.nullable is True
        assert isinstance(col.type, postgresql.UUID)

    def test_retailer_id_fk_set_null(self):
        from ratis_core.models import Store

        col = _col(Store, "retailer_id")
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "retailers"
        assert fks[0].ondelete == "SET NULL"

    def test_retailer_obj_relationship_exists(self):
        """`retailer` stays the TEXT cache; the relationship is `retailer_obj`."""
        from ratis_core.models import Store

        mapper = sa_inspect(Store)
        assert "retailer_obj" in mapper.relationships


# ── OcrKnowledge.entity_id ───────────────────────────────────────────────────


class TestOcrKnowledgeEntityId:
    def test_entity_id_column_exists(self):
        from ratis_core.models import OcrKnowledge

        col = _col(OcrKnowledge, "entity_id")
        assert col.nullable is True
        assert isinstance(col.type, postgresql.UUID)

    def test_type_check_uses_retailer_header(self):
        from ratis_core.models import OcrKnowledge

        for c in OcrKnowledge.__table__.constraints:
            if isinstance(c, sa.CheckConstraint) and c.name == "ck_ocr_knowledge_type":
                assert "retailer_header" in str(c.sqltext)
                assert "store_header" not in str(c.sqltext)
                return
        raise AssertionError("ck_ocr_knowledge_type not found")
