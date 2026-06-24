"""brands table + affiliate_offers refactor + cashback_transactions extensions

Revision ID: c4d5e6f7a8b9
Revises: f93171d19694
Create Date: 2026-04-12 15:00:00.000000

Changes:
- CREATE TABLE brands (id, name, slug UNIQUE, created_at)
- products: ADD COLUMN brand_id UUID FK brands (nullable — filled progressively)
- affiliate_offers: DROP COLUMN store_brand, product_ean → NOT NULL,
  ADD COLUMN brand_id UUID NOT NULL FK brands
- cashback_transactions: ADD COLUMN status (pending|confirmed|refused),
  distributed_at, scan_id FK scans, parent_transaction_id self-ref FK
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4d5e6f7a8b9"
down_revision = "f93171d19694"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. brands table
    # ------------------------------------------------------------------
    op.create_table(
        "brands",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint("uq_brands_slug", "brands", ["slug"])
    op.create_index("idx_brands_slug", "brands", ["slug"])

    # ------------------------------------------------------------------
    # 2. products — add brand_id (nullable, filled progressively)
    # ------------------------------------------------------------------
    op.add_column(
        "products",
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_products_brand",
        "products",
        "brands",
        ["brand_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_products_brand_id", "products", ["brand_id"])

    # ------------------------------------------------------------------
    # 3. affiliate_offers — drop store_brand, product_ean NOT NULL,
    #    add brand_id NOT NULL
    # ------------------------------------------------------------------
    op.drop_column("affiliate_offers", "store_brand")
    op.alter_column("affiliate_offers", "product_ean", nullable=False)
    op.add_column(
        "affiliate_offers",
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.create_foreign_key(
        "fk_affiliate_offers_brand",
        "affiliate_offers",
        "brands",
        ["brand_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("idx_affiliate_offers_brand", "affiliate_offers", ["brand_id"])

    # ------------------------------------------------------------------
    # 4. cashback_transactions — status, distributed_at, scan_id,
    #    parent_transaction_id
    # ------------------------------------------------------------------
    op.add_column(
        "cashback_transactions",
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending",
        ),
    )
    op.create_check_constraint(
        "ck_cashback_transactions_status",
        "cashback_transactions",
        "status IN ('pending', 'confirmed', 'refused')",
    )
    op.add_column(
        "cashback_transactions",
        sa.Column("distributed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "cashback_transactions",
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_cashback_tx_scan",
        "cashback_transactions",
        "scans",
        ["scan_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "cashback_transactions",
        sa.Column("parent_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_cashback_tx_parent",
        "cashback_transactions",
        "cashback_transactions",
        ["parent_transaction_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_cashback_tx_scan_ean",
        "cashback_transactions",
        ["scan_id", "product_ean"],
    )


def downgrade() -> None:
    # cashback_transactions
    op.drop_index("idx_cashback_tx_scan_ean", "cashback_transactions")
    op.drop_constraint("fk_cashback_tx_parent", "cashback_transactions", type_="foreignkey")
    op.drop_column("cashback_transactions", "parent_transaction_id")
    op.drop_constraint("fk_cashback_tx_scan", "cashback_transactions", type_="foreignkey")
    op.drop_column("cashback_transactions", "scan_id")
    op.drop_column("cashback_transactions", "distributed_at")
    op.drop_constraint("ck_cashback_transactions_status", "cashback_transactions", type_="check")
    op.drop_column("cashback_transactions", "status")

    # affiliate_offers
    op.drop_index("idx_affiliate_offers_brand", "affiliate_offers")
    op.drop_constraint("fk_affiliate_offers_brand", "affiliate_offers", type_="foreignkey")
    op.drop_column("affiliate_offers", "brand_id")
    op.alter_column("affiliate_offers", "product_ean", nullable=True)
    op.add_column(
        "affiliate_offers",
        sa.Column("store_brand", sa.Text(), nullable=True),
    )

    # products
    op.drop_index("idx_products_brand_id", "products")
    op.drop_constraint("fk_products_brand", "products", type_="foreignkey")
    op.drop_column("products", "brand_id")

    # brands
    op.drop_index("idx_brands_slug", "brands")
    op.drop_constraint("uq_brands_slug", "brands", type_="unique")
    op.drop_table("brands")
