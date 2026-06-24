"""Add OFF multi-field columns to products (display_name source-of-truth fields)

Revision ID: 20260501_1000_offmf
Revises: 20260501_0700_dropck
Create Date: 2026-05-01 10:00:00

OFF (OpenFoodFacts) exposes several name-related fields beyond ``product_name``
that are often richer or better localised. Today we only persist a single
``name`` column on ``products`` (best-of ``product_name_fr`` → ``product_name``).
For some EANs (e.g. 7610113013175 — "Hipro +") the picked name is poor while
``generic_name_fr`` would be excellent ("Yaourt à boire saveur fraise"), and
``brands`` + ``quantity`` together would form a usable display label.

This migration adds the four new optional text columns so the OFF sync can
populate them and ``ratis_core.products.pick_display_name`` can compose a
better-quality label downstream. ``brands_text`` is the raw OFF multi-comma
string ("Hipro,Danone") — distinct from the existing ``brand_id`` FK.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260501_1000_offmf"
down_revision = "20260501_0700_dropck"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("product_name_fr", sa.Text(), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("generic_name_fr", sa.Text(), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("brands_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("quantity_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    # Use IF EXISTS guards (R-mig-drop) so a partially-applied upgrade can
    # still be rolled back without raising on missing columns.
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS quantity_text")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS brands_text")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS generic_name_fr")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS product_name_fr")
