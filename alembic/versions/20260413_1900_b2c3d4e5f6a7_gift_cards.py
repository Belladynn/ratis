"""gift_cards: gift_card_brands + gift_card_orders

Revision ID: b2c3d4e5f6a7
Revises: a8b9c0d1e2f3
Create Date: 2026-04-13 19:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "b2c3d4e5f6a7"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gift_card_brands",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("provider_brand_id", sa.Text, nullable=False),
        sa.Column("logo_url", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "gift_card_orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("brand_id", UUID(as_uuid=True), sa.ForeignKey("gift_card_brands.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("denomination", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("source_ref_id", sa.Text, nullable=False),
        sa.Column("provider_order_id", sa.Text, nullable=True),
        sa.Column("code", sa.Text, nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('pending', 'issued', 'failed')",
            name="ck_gift_card_orders_status",
        ),
        sa.CheckConstraint(
            "source_type IN ('annual_subscription', 'battlepass_milestone', 'shop_purchase')",
            name="ck_gift_card_orders_source_type",
        ),
        sa.UniqueConstraint("source_type", "source_ref_id", name="uq_gift_card_orders_idempotency"),
    )


def downgrade() -> None:
    op.drop_table("gift_card_orders")
    op.drop_table("gift_card_brands")
