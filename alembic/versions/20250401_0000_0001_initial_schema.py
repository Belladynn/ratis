"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2025-04-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # EXTENSIONS
    # ------------------------------------------------------------------
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # ------------------------------------------------------------------
    # CATEGORIES
    # ------------------------------------------------------------------
    op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["parent_id"], ["categories.id"], name="fk_parent", ondelete="SET NULL"),
        sa.CheckConstraint("name != ''", name="name_not_empty"),
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION fn_check_category_cycle()
        RETURNS TRIGGER AS $$
        DECLARE current_id UUID;
        BEGIN
          current_id := NEW.parent_id;
          WHILE current_id IS NOT NULL LOOP
            IF current_id = NEW.id THEN
              RAISE EXCEPTION 'Cycle detected in category hierarchy: id=%', NEW.id;
            END IF;
            SELECT parent_id INTO current_id FROM categories WHERE id = current_id;
          END LOOP;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_categories_no_cycle
        BEFORE INSERT OR UPDATE OF parent_id ON categories
        FOR EACH ROW WHEN (NEW.parent_id IS NOT NULL)
        EXECUTE FUNCTION fn_check_category_cycle()
    """)

    # ------------------------------------------------------------------
    # LEVEL_TIERS  (declared before users — deferred FK in schema)
    # ------------------------------------------------------------------
    op.create_table(
        "level_tiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("level", sa.Integer, nullable=False, unique=True),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("cab_threshold", sa.Integer, nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("level > 0", name="level_pos"),
        sa.CheckConstraint("cab_threshold >= 0", name="cab_threshold_nn"),
        sa.CheckConstraint("label != ''", name="label_not_empty"),
    )

    # ------------------------------------------------------------------
    # USERS
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("provider", sa.Text, nullable=False, server_default="email"),
        sa.Column("provider_id", sa.Text, nullable=True),
        sa.Column("password_hash", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("display_name", sa.Text, nullable=True),
        sa.Column("avatar_url", sa.Text, nullable=True),
        sa.Column("current_level_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("email ~ '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$'", name="email_format"),
        sa.CheckConstraint("provider IN ('google', 'apple', 'email')", name="provider_check"),
        sa.CheckConstraint(
            "(provider = 'email' AND password_hash IS NOT NULL AND provider_id IS NULL) OR "
            "(provider != 'email' AND provider_id IS NOT NULL AND password_hash IS NULL)",
            name="auth_coherence",
        ),
        sa.UniqueConstraint("provider", "provider_id"),
    )
    op.create_foreign_key("fk_current_level", "users", "level_tiers", ["current_level_id"], ["id"], ondelete="SET NULL")

    # ------------------------------------------------------------------
    # STORES
    # ------------------------------------------------------------------
    op.create_table(
        "stores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("brand", sa.Text, nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("city", sa.Text, nullable=True),
        sa.Column("postal_code", sa.Text, nullable=True),
        sa.Column("lat", sa.Numeric(9, 6), nullable=False),
        sa.Column("lng", sa.Numeric(9, 6), nullable=False),
        sa.Column("is_disabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("disabled_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("name != ''", name="name_not_empty"),
        sa.CheckConstraint("brand IS NULL OR brand != ''", name="brand_not_empty"),
        sa.CheckConstraint("city IS NULL OR city != ''", name="city_not_empty"),
        sa.CheckConstraint("address IS NULL OR address != ''", name="address_not_empty"),
        sa.CheckConstraint("postal_code IS NULL OR postal_code != ''", name="postal_not_empty"),
        sa.CheckConstraint("lat BETWEEN -90 AND 90", name="lat_range"),
        sa.CheckConstraint("lng BETWEEN -180 AND 180", name="lng_range"),
        sa.CheckConstraint(
            "(is_disabled = true AND disabled_at IS NOT NULL) OR (is_disabled = false AND disabled_at IS NULL)",
            name="disabled_at_check",
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX unique_store ON stores "
        "(COALESCE(brand, ''), COALESCE(address, ''), COALESCE(postal_code, ''))"
    )

    # ------------------------------------------------------------------
    # PRODUCTS
    # ------------------------------------------------------------------
    op.create_table(
        "products",
        sa.Column("ean", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("photo_url", sa.Text, nullable=True),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.Text, nullable=False, server_default="off"),
        sa.Column("unit", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], name="fk_category", ondelete="SET NULL"),
        sa.CheckConstraint("name != ''", name="name_not_empty"),
        sa.CheckConstraint("ean ~ '^\\d{8,14}$'", name="ean_format"),
        sa.CheckConstraint("source IN ('off', 'internal')", name="source_check"),
        sa.CheckConstraint("unit IN ('kg', 'l', 'unit') OR unit IS NULL", name="unit_check"),
        sa.CheckConstraint("source != 'internal' OR unit IS NOT NULL", name="internal_has_unit"),
        sa.CheckConstraint("source != 'off' OR unit IS NULL", name="off_no_unit"),
        sa.CheckConstraint("source != 'internal' OR ean LIKE '2%'", name="internal_ean_prefix"),
    )

    # ------------------------------------------------------------------
    # RECEIPTS
    # ------------------------------------------------------------------
    op.create_table(
        "receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchased_at", sa.Date, nullable=False),
        sa.Column("tva_total", sa.Numeric(10, 2), nullable=True),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name="fk_store", ondelete="RESTRICT"),
        sa.CheckConstraint("tva_total IS NULL OR tva_total >= 0", name="tva_pos"),
        sa.CheckConstraint("total_amount IS NULL OR total_amount > 0", name="total_amount_pos"),
        sa.CheckConstraint("purchased_at <= CURRENT_DATE", name="purchased_not_future"),
    )

    # ------------------------------------------------------------------
    # SCANS
    # ------------------------------------------------------------------
    op.create_table(
        "scans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_ean", sa.Text, nullable=True),
        sa.Column("scanned_name", sa.Text, nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False, server_default="1"),
        sa.Column("tva_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("scan_type", sa.Text, nullable=False),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("rejected_reason", sa.Text, nullable=True),
        sa.Column("scanned_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("status_updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("image_url", sa.Text, nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name="fk_store", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_ean"], ["products.ean"], name="fk_product", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], name="fk_receipt", ondelete="SET NULL"),
        sa.CheckConstraint("price > 0", name="price_pos"),
        sa.CheckConstraint("quantity > 0", name="quantity_pos"),
        sa.CheckConstraint("tva_amount IS NULL OR tva_amount >= 0", name="tva_pos"),
        sa.CheckConstraint("tva_amount IS NULL OR scan_type = 'receipt'", name="tva_receipt_only"),
        sa.CheckConstraint("scan_type IN ('receipt', 'electronic_label', 'manual')", name="scan_type_check"),
        sa.CheckConstraint("status IN ('pending', 'unmatched', 'accepted', 'rejected')", name="status_check"),
        sa.CheckConstraint(
            "(scan_type = 'receipt' AND receipt_id IS NOT NULL) OR (scan_type != 'receipt' AND receipt_id IS NULL)",
            name="receipt_required",
        ),
        sa.CheckConstraint(
            "(status = 'rejected' AND rejected_reason IS NOT NULL) OR (status != 'rejected' AND rejected_reason IS NULL)",
            name="rejected_reason_check",
        ),
        sa.CheckConstraint(
            "(status = 'unmatched' AND product_ean IS NULL) OR status != 'unmatched'",
            name="unmatched_requires_null_ean",
        ),
        sa.CheckConstraint(
            "NOT (status = 'unmatched' AND scan_type = 'manual')",
            name="unmatched_not_manual",
        ),
        sa.CheckConstraint(
            "status != 'unmatched' OR scanned_name IS NOT NULL",
            name="unmatched_requires_scanned_name",
        ),
        sa.CheckConstraint(
            "status != 'accepted' OR product_ean IS NOT NULL",
            name="accepted_requires_ean",
        ),
        sa.CheckConstraint(
            "scan_type != 'manual' OR (product_ean IS NOT NULL AND scanned_name IS NULL)",
            name="manual_no_scanned_name",
        ),
        sa.UniqueConstraint("user_id", "store_id", "product_ean", "scanned_at"),
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION fn_check_scan_status_transition()
        RETURNS TRIGGER AS $$
        BEGIN
          IF OLD.status = NEW.status THEN RETURN NEW; END IF;
          IF OLD.status = 'accepted' AND NEW.status != 'accepted' THEN
            RAISE EXCEPTION 'Forbidden transition: an accepted scan cannot change status (id=%)', OLD.id;
          END IF;
          IF OLD.status = 'rejected' AND NEW.status != 'rejected' THEN
            RAISE EXCEPTION 'Forbidden transition: a rejected scan cannot change status (id=%)', OLD.id;
          END IF;
          NEW.status_updated_at = now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_scan_status_transition
        BEFORE UPDATE OF status ON scans
        FOR EACH ROW EXECUTE FUNCTION fn_check_scan_status_transition()
    """)

    # ------------------------------------------------------------------
    # PRICE_CONSENSUS
    # ------------------------------------------------------------------
    op.create_table(
        "price_consensus",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_ean", sa.Text, nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("trust_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("first_seen_at", sa.DateTime, nullable=False),
        sa.Column("last_seen_at", sa.DateTime, nullable=False),
        sa.Column("frozen_until", sa.DateTime, nullable=True),
        sa.Column("computed_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name="fk_store", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_ean"], ["products.ean"], name="fk_product", ondelete="RESTRICT"),
        sa.CheckConstraint("price > 0", name="price_pos"),
        sa.CheckConstraint("trust_score >= 0 AND trust_score <= 100", name="trust_range"),
        sa.CheckConstraint("first_seen_at <= last_seen_at", name="seen_order"),
        sa.UniqueConstraint("store_id", "product_ean"),
    )

    # ------------------------------------------------------------------
    # PRICE_CONSENSUS_SCANS
    # ------------------------------------------------------------------
    op.create_table(
        "price_consensus_scans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("consensus_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["consensus_id"], ["price_consensus.id"], name="fk_consensus", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"], name="fk_scan", ondelete="RESTRICT"),
        sa.UniqueConstraint("consensus_id", "scan_id"),
    )

    # ------------------------------------------------------------------
    # PRICE_CONSENSUS_HISTORY
    # ------------------------------------------------------------------
    op.create_table(
        "price_consensus_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("consensus_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_ean", sa.Text, nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("trust_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("first_seen_at", sa.DateTime, nullable=False),
        sa.Column("last_seen_at", sa.DateTime, nullable=False),
        sa.Column("frozen_until", sa.DateTime, nullable=True),
        sa.Column("recorded_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["consensus_id"], ["price_consensus.id"], name="fk_consensus", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name="fk_store", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_ean"], ["products.ean"], name="fk_product", ondelete="RESTRICT"),
        sa.CheckConstraint("price > 0", name="price_pos"),
        sa.CheckConstraint("trust_score >= 0 AND trust_score <= 100", name="trust_range"),
        sa.CheckConstraint("first_seen_at <= last_seen_at", name="seen_order"),
    )

    # ------------------------------------------------------------------
    # PRICE_CONSENSUS_HISTORY_SCANS
    # ------------------------------------------------------------------
    op.create_table(
        "price_consensus_history_scans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("history_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["history_id"], ["price_consensus_history.id"], name="fk_history", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"], name="fk_scan", ondelete="RESTRICT"),
        sa.UniqueConstraint("history_id", "scan_id"),
    )

    # ------------------------------------------------------------------
    # SHOPPING_LISTS
    # ------------------------------------------------------------------
    op.create_table(
        "shopping_lists",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text, nullable=False, server_default=""),
        sa.Column("has_default_name", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION fn_shopping_list_name()
        RETURNS TRIGGER AS $$
        BEGIN
          IF NEW.has_default_name = true AND trim(NEW.name) != '' THEN NEW.name = ''; END IF;
          IF NEW.name IS NULL OR trim(NEW.name) = '' THEN
            NEW.name = ''; NEW.has_default_name = true;
          ELSE
            NEW.has_default_name = false;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_shopping_list_name
        BEFORE INSERT OR UPDATE OF name, has_default_name ON shopping_lists
        FOR EACH ROW EXECUTE FUNCTION fn_shopping_list_name()
    """)

    # ------------------------------------------------------------------
    # SHOPPING_LIST_ITEMS
    # ------------------------------------------------------------------
    op.create_table(
        "shopping_list_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("list_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_ean", sa.Text, nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False, server_default="1"),
        sa.Column("checked", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("checked_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["list_id"], ["shopping_lists.id"], name="fk_list", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_ean"], ["products.ean"], name="fk_product", ondelete="RESTRICT"),
        sa.CheckConstraint("quantity > 0", name="quantity_pos"),
        sa.CheckConstraint(
            "(checked = true AND checked_at IS NOT NULL) OR (checked = false AND checked_at IS NULL)",
            name="checked_at_check",
        ),
        sa.UniqueConstraint("list_id", "product_ean"),
    )

    # ------------------------------------------------------------------
    # PRODUCT_TRACKING
    # ------------------------------------------------------------------
    op.create_table(
        "product_tracking",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_ean", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("deactivated_at", sa.DateTime, nullable=True),
        sa.Column("avg_quantity", sa.Numeric(10, 3), nullable=True),
        sa.Column("avg_frequency_days", sa.Integer, nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_ean"], ["products.ean"], name="fk_product", ondelete="RESTRICT"),
        sa.CheckConstraint(
            "(active = false AND deactivated_at IS NOT NULL) OR (active = true AND deactivated_at IS NULL)",
            name="deactivated_check",
        ),
        sa.CheckConstraint("avg_quantity IS NULL OR avg_quantity > 0", name="avg_quantity_pos"),
        sa.CheckConstraint("avg_frequency_days IS NULL OR avg_frequency_days > 0", name="avg_frequency_pos"),
        sa.UniqueConstraint("user_id", "product_ean"),
    )

    # ------------------------------------------------------------------
    # USER_PUSH_TOKENS
    # ------------------------------------------------------------------
    op.create_table(
        "user_push_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token", sa.Text, nullable=False, unique=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.CheckConstraint("platform IN ('ios', 'android', 'web')", name="platform_check"),
    )
    op.create_index("idx_push_tokens_user", "user_push_tokens", ["user_id"])

    # ------------------------------------------------------------------
    # USER_PREFERENCES
    # ------------------------------------------------------------------
    op.create_table(
        "user_preferences",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("search_radius_km", sa.Integer, nullable=False, server_default="5"),
        sa.Column("transport_mode", sa.Text, nullable=False, server_default="driving"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.CheckConstraint("search_radius_km > 0 AND search_radius_km <= 50", name="radius_range"),
        sa.CheckConstraint("transport_mode IN ('driving', 'walking', 'cycling')", name="transport_check"),
    )

    # ------------------------------------------------------------------
    # OPTIMIZED_ROUTES
    # ------------------------------------------------------------------
    op.create_table(
        "optimized_routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("list_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("total_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("total_savings", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("distance_km", sa.Numeric(8, 2), nullable=True),
        sa.Column("steps", postgresql.JSONB, nullable=False),
        sa.Column("computed_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime, nullable=False, server_default=sa.text("now() + INTERVAL '48 hours'")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["list_id"], ["shopping_lists.id"], name="fk_list", ondelete="CASCADE"),
        sa.CheckConstraint("total_price > 0", name="total_price_pos"),
        sa.CheckConstraint("total_savings >= 0", name="total_savings_pos"),
        sa.CheckConstraint("total_savings <= total_price", name="savings_lte_price"),
        sa.CheckConstraint("distance_km IS NULL OR distance_km >= 0", name="distance_pos"),
        sa.CheckConstraint("expires_at > computed_at", name="expires_after_computed"),
    )
    op.create_index("idx_routes_user", "optimized_routes", ["user_id", sa.text("computed_at DESC")])
    op.create_index("idx_routes_expires", "optimized_routes", ["expires_at"])

    # ------------------------------------------------------------------
    # REWARD_CONFIG
    # ------------------------------------------------------------------
    op.create_table(
        "reward_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("action_type", sa.Text, nullable=False, unique=True),
        sa.Column("base_amount", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "action_type IN ('DAILY_LOGIN', 'SCAN_RECEIPT', 'VIDEO_SCAN', 'PRICE_CHALLENGE')",
            name="action_type_check",
        ),
        sa.CheckConstraint("base_amount > 0", name="base_amount_pos"),
    )

    # ------------------------------------------------------------------
    # STREAK_TIERS
    # ------------------------------------------------------------------
    op.create_table(
        "streak_tiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("days", sa.Integer, nullable=False, unique=True),
        sa.Column("multiplier", sa.Numeric(4, 2), nullable=False),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("days > 0", name="days_pos"),
        sa.CheckConstraint("multiplier > 1", name="multiplier_gt_1"),
        sa.CheckConstraint("label != ''", name="label_not_empty"),
    )

    # ------------------------------------------------------------------
    # BADGES
    # ------------------------------------------------------------------
    op.create_table(
        "badges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("icon_url", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("code != ''", name="code_not_empty"),
        sa.CheckConstraint("code = upper(code)", name="code_uppercase"),
    )

    # ------------------------------------------------------------------
    # USER_CAB_BALANCE
    # ------------------------------------------------------------------
    op.create_table(
        "user_cab_balance",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("balance", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.CheckConstraint("balance >= 0", name="balance_nn"),
    )

    # ------------------------------------------------------------------
    # USER_CASHBACK_BALANCE
    # ------------------------------------------------------------------
    op.create_table(
        "user_cashback_balance",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("balance", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.CheckConstraint("balance >= 0", name="balance_nn"),
    )

    # ------------------------------------------------------------------
    # CABECOIN_TRANSACTIONS
    # ------------------------------------------------------------------
    op.create_table(
        "cabecoin_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.Text, nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("base_amount", sa.Integer, nullable=False),
        sa.Column("rate", sa.Numeric(4, 2), nullable=True),
        sa.Column("rate_reason", sa.Text, nullable=True),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"], name="fk_scan", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], name="fk_receipt", ondelete="SET NULL"),
        sa.CheckConstraint(
            "action_type IN ('DAILY_LOGIN', 'SCAN_RECEIPT', 'VIDEO_SCAN', 'PRICE_CHALLENGE', 'BOOST_CASHBACK')",
            name="action_type_check",
        ),
        sa.CheckConstraint("direction IN ('credit', 'debit')", name="direction_check"),
        sa.CheckConstraint("base_amount > 0", name="base_amount_pos"),
        sa.CheckConstraint(
            "(rate IS NOT NULL AND rate_reason IS NOT NULL) OR (rate IS NULL AND rate_reason IS NULL)",
            name="rate_coherence",
        ),
        sa.CheckConstraint("rate IS NULL OR action_type = 'VIDEO_SCAN'", name="rate_only_video"),
        sa.CheckConstraint("rate IS NULL OR rate > 0", name="rate_pos"),
        sa.CheckConstraint(
            "(action_type = 'BOOST_CASHBACK' AND direction = 'debit') OR "
            "(action_type != 'BOOST_CASHBACK' AND direction = 'credit')",
            name="boost_is_debit",
        ),
    )

    # ------------------------------------------------------------------
    # AFFILIATE_OFFERS
    # ------------------------------------------------------------------
    op.create_table(
        "affiliate_offers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("product_ean", sa.Text, nullable=True),
        sa.Column("store_brand", sa.Text, nullable=True),
        sa.Column("cashback_rate", sa.Numeric(5, 4), nullable=False),
        sa.Column("valid_from", sa.DateTime, nullable=False),
        sa.Column("valid_until", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["product_ean"], ["products.ean"], name="fk_product", ondelete="RESTRICT"),
        sa.CheckConstraint("provider IN ('affilae', 'awin', 'cj')", name="provider_check"),
        sa.CheckConstraint("cashback_rate > 0", name="rate_pos"),
        sa.CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="valid_range"),
        sa.UniqueConstraint("provider", "external_id", name="external_unique"),
    )
    op.create_index(
        "idx_affiliate_offers_ean",
        "affiliate_offers",
        ["product_ean"],
        postgresql_where=sa.text("product_ean IS NOT NULL"),
    )
    op.create_index(
        "idx_affiliate_offers_brand",
        "affiliate_offers",
        ["store_brand"],
        postgresql_where=sa.text("store_brand IS NOT NULL"),
    )
    op.create_index(
        "idx_affiliate_offers_valid",
        "affiliate_offers",
        ["valid_until"],
        postgresql_where=sa.text("valid_until IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # CASHBACK_TRANSACTIONS
    # ------------------------------------------------------------------
    op.create_table(
        "cashback_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("product_ean", sa.Text, nullable=True),
        sa.Column("affiliate_offer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("boost_applied", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_ean"], ["products.ean"], name="fk_product", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["affiliate_offer_id"], ["affiliate_offers.id"], name="fk_affiliate_offer", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "type IN ('CREDIT', 'BOOST', 'WITHDRAWAL', 'SUBSCRIPTION_PAYMENT')",
            name="type_check",
        ),
        sa.CheckConstraint("amount > 0", name="amount_pos"),
        sa.CheckConstraint(
            "type NOT IN ('CREDIT', 'BOOST') OR product_ean IS NOT NULL",
            name="credit_requires_product",
        ),
        sa.CheckConstraint(
            "type NOT IN ('CREDIT', 'BOOST') OR affiliate_offer_id IS NOT NULL",
            name="credit_requires_offer",
        ),
    )

    # ------------------------------------------------------------------
    # DISCOUNT_CAMPAIGNS
    # ------------------------------------------------------------------
    op.create_table(
        "discount_campaigns",
        sa.Column("code", sa.Text, primary_key=True),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("value", sa.Numeric(10, 2), nullable=False),
        sa.Column("valid_from", sa.DateTime, nullable=True),
        sa.Column("valid_until", sa.DateTime, nullable=True),
        sa.Column("max_uses", sa.Integer, nullable=True),
        sa.Column("uses_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_public", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("code != ''", name="code_not_empty"),
        sa.CheckConstraint("label != ''", name="label_not_empty"),
        sa.CheckConstraint("code = upper(code)", name="code_uppercase"),
        sa.CheckConstraint("type IN ('percentage', 'fixed')", name="type_check"),
        sa.CheckConstraint("value > 0", name="value_pos"),
        sa.CheckConstraint("max_uses IS NULL OR max_uses > 0", name="max_uses_pos"),
        sa.CheckConstraint("uses_count >= 0", name="uses_count_nn"),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_until IS NULL OR valid_until > valid_from",
            name="valid_range",
        ),
        sa.CheckConstraint("max_uses IS NULL OR uses_count <= max_uses", name="uses_not_exceed_max"),
        sa.CheckConstraint("type != 'percentage' OR value <= 100", name="percentage_max"),
    )
    op.create_index(
        "idx_discount_campaigns_valid",
        "discount_campaigns",
        ["valid_until"],
        postgresql_where=sa.text("valid_until IS NOT NULL"),
    )
    op.create_index(
        "idx_discount_campaigns_public",
        "discount_campaigns",
        ["is_public"],
        postgresql_where=sa.text("is_public = true"),
    )

    # ------------------------------------------------------------------
    # SUBSCRIPTIONS
    # ------------------------------------------------------------------
    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("price", sa.Numeric(10, 2), nullable=False, server_default="11.99"),
        sa.Column("paid_with", sa.Text, nullable=False, server_default="stripe"),
        sa.Column("discount_campaign_code", sa.Text, nullable=True),
        sa.Column("discount_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("cancelled_at", sa.DateTime, nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["discount_campaign_code"], ["discount_campaigns.code"], name="fk_discount", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("status IN ('active', 'cancelled', 'expired')", name="status_check"),
        sa.CheckConstraint("price > 0", name="price_pos"),
        sa.CheckConstraint(
            "(discount_campaign_code IS NOT NULL AND discount_amount IS NOT NULL) OR "
            "(discount_campaign_code IS NULL AND discount_amount IS NULL)",
            name="discount_coherence",
        ),
        sa.CheckConstraint("discount_amount IS NULL OR discount_amount > 0", name="discount_amount_pos"),
        sa.CheckConstraint("discount_amount IS NULL OR discount_amount < price", name="discount_not_exceed_price"),
        sa.CheckConstraint("expires_at > started_at", name="expires_after_start"),
        sa.CheckConstraint(
            "(status = 'cancelled' AND cancelled_at IS NOT NULL) OR (status != 'cancelled' AND cancelled_at IS NULL)",
            name="cancelled_check",
        ),
    )
    op.create_index("idx_subscriptions_user", "subscriptions", ["user_id", sa.text("started_at DESC")])
    op.execute("CREATE UNIQUE INDEX idx_one_active_subscription ON subscriptions(user_id) WHERE status = 'active'")

    op.execute("""
        CREATE OR REPLACE FUNCTION fn_increment_discount_uses()
        RETURNS TRIGGER AS $$
        BEGIN
          IF NEW.discount_campaign_code IS NOT NULL THEN
            UPDATE discount_campaigns
              SET uses_count = uses_count + 1
              WHERE code = NEW.discount_campaign_code
                AND (max_uses IS NULL OR uses_count < max_uses)
                AND (valid_from  IS NULL OR valid_from  <= now())
                AND (valid_until IS NULL OR valid_until >= now());
            IF NOT FOUND THEN
              RAISE EXCEPTION 'Promo code % is invalid, expired or exhausted', NEW.discount_campaign_code;
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_subscription_discount_uses
        AFTER INSERT ON subscriptions
        FOR EACH ROW EXECUTE FUNCTION fn_increment_discount_uses()
    """)

    # ------------------------------------------------------------------
    # USER_STREAKS
    # ------------------------------------------------------------------
    op.create_table(
        "user_streaks",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("daily_streak", sa.Integer, nullable=False, server_default="0"),
        sa.Column("daily_streak_best", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_login_date", sa.Date, nullable=True),
        sa.Column("weekly_streak", sa.Integer, nullable=False, server_default="0"),
        sa.Column("weekly_streak_best", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_scan_week", sa.Integer, nullable=True),
        sa.Column("last_scan_year", sa.Integer, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.CheckConstraint("daily_streak >= 0", name="daily_streak_nn"),
        sa.CheckConstraint("weekly_streak >= 0", name="weekly_streak_nn"),
        sa.CheckConstraint(
            "daily_streak_best >= daily_streak AND weekly_streak_best >= weekly_streak",
            name="best_gte_current",
        ),
        sa.CheckConstraint(
            "(last_scan_week IS NULL AND last_scan_year IS NULL) OR "
            "(last_scan_week IS NOT NULL AND last_scan_year IS NOT NULL)",
            name="scan_week_coherence",
        ),
        sa.CheckConstraint(
            "last_scan_week IS NULL OR last_scan_week BETWEEN 1 AND 53",
            name="scan_week_range",
        ),
    )

    # ------------------------------------------------------------------
    # USER_BADGES
    # ------------------------------------------------------------------
    op.create_table(
        "user_badges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("badge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("unlocked_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["badge_id"], ["badges.id"], name="fk_badge", ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "badge_id"),
    )
    op.create_index("idx_user_badges_user", "user_badges", ["user_id"])

    # ------------------------------------------------------------------
    # LEADERBOARD_SNAPSHOTS
    # ------------------------------------------------------------------
    op.create_table(
        "leaderboard_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_year", sa.Integer, nullable=False),
        sa.Column("period_month", sa.Integer, nullable=False),
        sa.Column("cab_earned", sa.Integer, nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.CheckConstraint("period_year BETWEEN 2024 AND 2100", name="year_range"),
        sa.CheckConstraint("period_month BETWEEN 1 AND 12", name="month_range"),
        sa.CheckConstraint("cab_earned >= 0", name="cab_earned_nn"),
        sa.CheckConstraint("rank > 0", name="rank_pos"),
        sa.UniqueConstraint("user_id", "period_year", "period_month"),
    )
    op.create_index("idx_leaderboard_period", "leaderboard_snapshots", ["period_year", "period_month", "rank"])
    op.create_index(
        "idx_leaderboard_user",
        "leaderboard_snapshots",
        ["user_id", sa.text("period_year DESC"), sa.text("period_month DESC")],
    )

    # ------------------------------------------------------------------
    # PRICE_CHALLENGES
    # ------------------------------------------------------------------
    op.create_table(
        "price_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_ean", sa.Text, nullable=True),
        sa.Column("image_crop_url", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("validated_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("trust_score", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"], name="fk_scan", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name="fk_store", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_ean"], ["products.ean"], name="fk_product", ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('pending', 'validated', 'rejected')", name="status_check"),
        sa.CheckConstraint("trust_score >= 0 AND trust_score <= 100", name="trust_range"),
        sa.CheckConstraint(
            "(status = 'validated' AND validated_price IS NOT NULL) OR (status != 'validated' AND validated_price IS NULL)",
            name="validated_coherence",
        ),
        sa.CheckConstraint("validated_price IS NULL OR validated_price > 0", name="validated_price_pos"),
    )
    op.create_index(
        "idx_price_challenges_pending",
        "price_challenges",
        ["store_id", sa.text("created_at ASC")],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # ------------------------------------------------------------------
    # PRICE_CHALLENGE_RESPONSES
    # ------------------------------------------------------------------
    op.create_table(
        "price_challenge_responses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("challenge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["challenge_id"], ["price_challenges.id"], name="fk_challenge", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="SET NULL"),
        sa.CheckConstraint("price > 0", name="price_pos"),
        sa.UniqueConstraint("challenge_id", "user_id"),
    )
    op.create_index("idx_challenge_responses", "price_challenge_responses", ["challenge_id"])

    # ------------------------------------------------------------------
    # PRICE_ALERTS
    # ------------------------------------------------------------------
    op.create_table(
        "price_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_ean", sa.Text, nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("triggered_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_ean"], ["products.ean"], name="fk_product", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name="fk_store", ondelete="CASCADE"),
        sa.CheckConstraint("target_price > 0", name="target_price_pos"),
        sa.CheckConstraint("triggered_at IS NULL OR active = false", name="triggered_check"),
        sa.UniqueConstraint("user_id", "product_ean", "store_id", "target_price"),
    )
    op.create_index(
        "idx_price_alerts_active",
        "price_alerts",
        ["product_ean", "store_id"],
        postgresql_where=sa.text("active = true"),
    )
    op.create_index("idx_price_alerts_user", "price_alerts", ["user_id"])

    # ------------------------------------------------------------------
    # CASHBACK_WITHDRAWALS
    # ------------------------------------------------------------------
    op.create_table(
        "cashback_withdrawals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("cashback_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payment_provider_ref", sa.Text, nullable=True),
        sa.Column("provider_initiated_at", sa.DateTime, nullable=True),
        sa.Column("last_reconciled_at", sa.DateTime, nullable=True),
        sa.Column("requested_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime, nullable=True),
        sa.Column("failure_reason", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["cashback_transaction_id"], ["cashback_transactions.id"], name="fk_transaction", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("amount > 0", name="amount_pos"),
        sa.CheckConstraint("status IN ('pending', 'processed', 'failed')", name="status_check"),
        sa.CheckConstraint(
            "(status = 'processed' AND processed_at IS NOT NULL) OR (status != 'processed' AND processed_at IS NULL)",
            name="processed_check",
        ),
        sa.CheckConstraint("status != 'processed' OR cashback_transaction_id IS NOT NULL", name="transaction_required"),
        sa.CheckConstraint(
            "(status = 'failed' AND failure_reason IS NOT NULL) OR (status != 'failed' AND failure_reason IS NULL)",
            name="failure_check",
        ),
        sa.CheckConstraint(
            "(payment_provider_ref IS NOT NULL AND provider_initiated_at IS NOT NULL) OR "
            "(payment_provider_ref IS NULL AND provider_initiated_at IS NULL)",
            name="provider_coherence",
        ),
    )
    op.create_index("idx_withdrawals_user", "cashback_withdrawals", ["user_id", sa.text("requested_at DESC")])
    op.create_index(
        "idx_withdrawals_pending",
        "cashback_withdrawals",
        ["status", sa.text("requested_at ASC")],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "idx_withdrawals_reconcile",
        "cashback_withdrawals",
        ["last_reconciled_at"],
        postgresql_where=sa.text("status = 'pending' AND payment_provider_ref IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # NOTIFICATION_LOGS
    # ------------------------------------------------------------------
    op.create_table(
        "notification_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column("sent_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("read_at", sa.DateTime, nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.CheckConstraint(
            "type IN ('price_drop', 'streak_reminder', 'weekly_recap', 'challenge_available', 'cashback_credited', 'level_up')",
            name="type_check",
        ),
    )
    op.create_index(
        "idx_notif_user_unread",
        "notification_logs",
        ["user_id", sa.text("sent_at DESC")],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index("idx_notif_type", "notification_logs", ["type", sa.text("sent_at DESC")])

    # ------------------------------------------------------------------
    # USER_STORE_PREFERENCES
    # ------------------------------------------------------------------
    op.create_table(
        "user_store_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preference", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name="fk_store", ondelete="CASCADE"),
        sa.CheckConstraint("preference IN ('favourite', 'excluded')", name="preference_check"),
        sa.UniqueConstraint("user_id", "store_id"),
    )
    op.create_index("idx_store_prefs_user", "user_store_preferences", ["user_id", "preference"])

    # ------------------------------------------------------------------
    # USER_SESSIONS
    # ------------------------------------------------------------------
    op.create_table(
        "user_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.CheckConstraint("platform IN ('ios', 'android', 'web')", name="platform_check"),
    )
    op.create_index("idx_sessions_user", "user_sessions", ["user_id", sa.text("started_at DESC")])
    op.create_index("idx_sessions_daily", "user_sessions", [sa.text("started_at DESC")])

    # ------------------------------------------------------------------
    # USER_SESSION_STATS
    # ------------------------------------------------------------------
    op.create_table(
        "user_session_stats",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_year", sa.Integer, nullable=False),
        sa.Column("period_month", sa.Integer, nullable=False),
        sa.Column("ios_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("android_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("web_count", sa.Integer, nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "period_year", "period_month"),
        sa.CheckConstraint("period_year BETWEEN 2024 AND 2100", name="year_range"),
        sa.CheckConstraint("period_month BETWEEN 1 AND 12", name="month_range"),
        sa.CheckConstraint("ios_count >= 0", name="ios_nn"),
        sa.CheckConstraint("android_count >= 0", name="android_nn"),
        sa.CheckConstraint("web_count >= 0", name="web_nn"),
    )
    op.create_index("idx_session_stats_period", "user_session_stats", ["period_year", "period_month"])

    # ------------------------------------------------------------------
    # TRIGGER updated_at (shared by all relevant tables)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    for table in [
        "users",
        "categories",
        "stores",
        "products",
        "receipts",
        "shopping_lists",
        "shopping_list_items",
        "product_tracking",
        "user_preferences",
        "reward_config",
        "discount_campaigns",
        "streak_tiers",
        "level_tiers",
        "price_challenges",
        "price_alerts",
        "cashback_withdrawals",
        "affiliate_offers",
    ]:
        op.execute(f"""
            CREATE TRIGGER trg_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()
        """)


def downgrade() -> None:
    tables = [
        "user_session_stats",
        "user_sessions",
        "user_store_preferences",
        "notification_logs",
        "cashback_withdrawals",
        "price_alerts",
        "price_challenge_responses",
        "price_challenges",
        "leaderboard_snapshots",
        "user_badges",
        "user_streaks",
        "subscriptions",
        "discount_campaigns",
        "cashback_transactions",
        "affiliate_offers",
        "cabecoin_transactions",
        "user_cashback_balance",
        "user_cab_balance",
        "badges",
        "streak_tiers",
        "reward_config",
        "optimized_routes",
        "user_preferences",
        "user_push_tokens",
        "product_tracking",
        "shopping_list_items",
        "shopping_lists",
        "price_consensus_history_scans",
        "price_consensus_history",
        "price_consensus_scans",
        "price_consensus",
        "scans",
        "receipts",
        "products",
        "stores",
        "users",
        "level_tiers",
        "categories",
    ]
    for table in tables:
        op.drop_table(table)

    for fn in [
        "fn_set_updated_at",
        "fn_increment_discount_uses",
        "fn_check_scan_status_transition",
        "fn_shopping_list_name",
        "fn_check_category_cycle",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")
