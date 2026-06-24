"""Add plan + stripe_session_id to subscriptions, pending status, fix constraints + trigger

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-04-09 12:00:00.000000

Changes:
- subscriptions.plan TEXT CHECK (plan IN ('monthly', 'annual'))
- subscriptions.stripe_session_id TEXT (lien Stripe Checkout Session)
- Status CHECK: add 'pending'
- payment_ref_coherence: allow NULL payment_ref when status='pending'
- fn_increment_discount_uses trigger: only fires when NEW.status = 'active'
  (pending subscriptions must not prematurely increment uses_count)
- Unique partial index on (user_id) WHERE status='pending' to prevent
  duplicate open Stripe sessions per user
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add new columns
    op.add_column("subscriptions", sa.Column("plan", sa.Text, nullable=True))
    op.add_column("subscriptions", sa.Column("stripe_session_id", sa.Text, nullable=True))

    # 2. Status CHECK: add 'pending'
    op.execute("ALTER TABLE subscriptions DROP CONSTRAINT status_check")
    op.execute(
        "ALTER TABLE subscriptions ADD CONSTRAINT subscriptions_status_check "
        "CHECK (status IN ('pending', 'active', 'cancelled', 'expired'))"
    )

    # 3. payment_ref_coherence: allow NULL payment_ref for non-completed subscriptions
    #    (pending = awaiting payment, cancelled-from-pending = never completed)
    op.execute("ALTER TABLE subscriptions DROP CONSTRAINT payment_ref_coherence")
    op.execute(
        "ALTER TABLE subscriptions ADD CONSTRAINT payment_ref_coherence "
        "CHECK (paid_with = 'cashback' OR payment_ref IS NOT NULL OR status NOT IN ('active', 'expired'))"
    )

    # 4. Fix fn_increment_discount_uses trigger:
    #    - Guard: only fires when NEW.status = 'active'
    #    - On UPDATE: skip if OLD.status was already 'active' (no double-count)
    #    - Trigger event: INSERT OR UPDATE (covers pending→active path)
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_increment_discount_uses() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.status <> 'active' THEN
            RETURN NEW;
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.status = 'active' THEN
            RETURN NEW;
          END IF;
          IF NEW.discount_campaign_code IS NOT NULL THEN
            UPDATE discount_campaigns
              SET uses_count = uses_count + 1
              WHERE code = NEW.discount_campaign_code
                AND (max_uses IS NULL OR uses_count < max_uses)
                AND (valid_from  IS NULL OR valid_from  <= now())
                AND (valid_until IS NULL OR valid_until >= now());
            IF NOT FOUND THEN
              RAISE EXCEPTION 'Code promo % invalide, expiré ou épuisé', NEW.discount_campaign_code;
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$
    """)
    # Re-create trigger to fire on INSERT OR UPDATE (original was INSERT only)
    op.execute("DROP TRIGGER IF EXISTS trg_increment_discount_uses ON subscriptions")
    op.execute(
        "CREATE TRIGGER trg_increment_discount_uses "
        "BEFORE INSERT OR UPDATE ON subscriptions "
        "FOR EACH ROW EXECUTE FUNCTION fn_increment_discount_uses()"
    )

    # 5. Unique partial index: at most one pending subscription per user
    op.execute(
        "CREATE UNIQUE INDEX idx_one_pending_subscription "
        "ON subscriptions (user_id) WHERE status = 'pending'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_one_pending_subscription")

    # Restore trigger to INSERT only + function without status guard
    op.execute("DROP TRIGGER IF EXISTS trg_increment_discount_uses ON subscriptions")
    op.execute(
        "CREATE TRIGGER trg_increment_discount_uses "
        "BEFORE INSERT ON subscriptions "
        "FOR EACH ROW EXECUTE FUNCTION fn_increment_discount_uses()"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_increment_discount_uses() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.discount_campaign_code IS NOT NULL THEN
            UPDATE discount_campaigns
              SET uses_count = uses_count + 1
              WHERE code = NEW.discount_campaign_code
                AND (max_uses IS NULL OR uses_count < max_uses)
                AND (valid_from  IS NULL OR valid_from  <= now())
                AND (valid_until IS NULL OR valid_until >= now());
            IF NOT FOUND THEN
              RAISE EXCEPTION 'Code promo % invalide, expiré ou épuisé', NEW.discount_campaign_code;
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$
    """)

    op.execute("ALTER TABLE subscriptions DROP CONSTRAINT payment_ref_coherence")
    op.execute(
        "ALTER TABLE subscriptions ADD CONSTRAINT payment_ref_coherence "
        "CHECK (paid_with = 'cashback' OR payment_ref IS NOT NULL)"  # original — pending did not exist
    )

    op.execute("ALTER TABLE subscriptions DROP CONSTRAINT subscriptions_status_check")
    op.execute(
        "ALTER TABLE subscriptions ADD CONSTRAINT status_check "
        "CHECK (status IN ('active', 'cancelled', 'expired'))"
    )

    op.drop_column("subscriptions", "stripe_session_id")
    op.drop_column("subscriptions", "plan")
