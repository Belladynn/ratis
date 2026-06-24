"""migrate all DateTime columns to TIMESTAMPTZ

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-04-08 12:00:00.000000

Converts every TIMESTAMP WITHOUT TIME ZONE column that was previously
declared as DateTime (tz-naive) to TIMESTAMP WITH TIME ZONE (TIMESTAMPTZ).
Existing stored values are interpreted as UTC (AT TIME ZONE 'UTC').

Already-TIMESTAMPTZ columns are excluded (they are unchanged):
  label_sessions.created_at, receipts.image_uploaded_at/image_deleted_at,
  scans.user_verified_at, batch_sync_log.last_run_at,
  product_knowledge.created_at
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


# (table, column) pairs that need migrating
_COLUMNS: list[tuple[str, str]] = [
    # users
    ("users", "created_at"),
    ("users", "updated_at"),
    # refresh_tokens
    ("refresh_tokens", "expires_at"),
    ("refresh_tokens", "revoked_at"),
    ("refresh_tokens", "created_at"),
    # receipts
    ("receipts", "created_at"),
    ("receipts", "updated_at"),
    # scans
    ("scans", "scanned_at"),
    ("scans", "status_updated_at"),
    # price_consensus
    ("price_consensus", "first_seen_at"),
    ("price_consensus", "last_seen_at"),
    ("price_consensus", "frozen_until"),
    ("price_consensus", "computed_at"),
    # price_consensus_history
    ("price_consensus_history", "first_seen_at"),
    ("price_consensus_history", "last_seen_at"),
    ("price_consensus_history", "frozen_until"),
    ("price_consensus_history", "recorded_at"),
    # stores
    ("stores", "disabled_at"),
    ("stores", "created_at"),
    ("stores", "updated_at"),
    # categories
    ("categories", "created_at"),
    ("categories", "updated_at"),
    # products
    ("products", "created_at"),
    ("products", "updated_at"),
    # level_tiers
    ("level_tiers", "created_at"),
    ("level_tiers", "updated_at"),
    # reward_config
    ("reward_config", "created_at"),
    ("reward_config", "updated_at"),
    # streak_tiers
    ("streak_tiers", "created_at"),
    ("streak_tiers", "updated_at"),
    # badges
    ("badges", "created_at"),
    # user_cab_balance
    ("user_cab_balance", "updated_at"),
    # user_cashback_balance
    ("user_cashback_balance", "updated_at"),
    # cabecoin_transactions
    ("cabecoin_transactions", "created_at"),
    # user_streaks
    ("user_streaks", "updated_at"),
    # user_badges
    ("user_badges", "unlocked_at"),
    # leaderboard_snapshots
    ("leaderboard_snapshots", "created_at"),
    # shopping_lists
    ("shopping_lists", "created_at"),
    ("shopping_lists", "updated_at"),
    # shopping_list_items
    ("shopping_list_items", "checked_at"),
    ("shopping_list_items", "created_at"),
    ("shopping_list_items", "updated_at"),
    # product_tracking
    ("product_tracking", "created_at"),
    ("product_tracking", "updated_at"),
    ("product_tracking", "deactivated_at"),
    # optimized_routes
    ("optimized_routes", "computed_at"),
    ("optimized_routes", "expires_at"),
    # price_alerts
    ("price_alerts", "triggered_at"),
    ("price_alerts", "created_at"),
    ("price_alerts", "updated_at"),
    # user_store_preferences
    ("user_store_preferences", "created_at"),
    # user_push_tokens
    ("user_push_tokens", "created_at"),
    # user_preferences
    ("user_preferences", "created_at"),
    ("user_preferences", "updated_at"),
    # user_sessions
    ("user_sessions", "started_at"),
    # notification_logs
    ("notification_logs", "sent_at"),
    ("notification_logs", "read_at"),
    # price_challenges
    ("price_challenges", "created_at"),
    ("price_challenges", "updated_at"),
    # price_challenge_responses
    ("price_challenge_responses", "created_at"),
    # affiliate_offers
    ("affiliate_offers", "valid_from"),
    ("affiliate_offers", "valid_until"),
    ("affiliate_offers", "created_at"),
    ("affiliate_offers", "updated_at"),
    # cashback_transactions
    ("cashback_transactions", "created_at"),
    # discount_campaigns
    ("discount_campaigns", "valid_from"),
    ("discount_campaigns", "valid_until"),
    ("discount_campaigns", "created_at"),
    ("discount_campaigns", "updated_at"),
    # subscriptions
    ("subscriptions", "started_at"),
    ("subscriptions", "expires_at"),
    ("subscriptions", "cancelled_at"),
    # cashback_withdrawals
    ("cashback_withdrawals", "provider_initiated_at"),
    ("cashback_withdrawals", "last_reconciled_at"),
    ("cashback_withdrawals", "requested_at"),
    ("cashback_withdrawals", "processed_at"),
    ("cashback_withdrawals", "updated_at"),
]


# Views that depend on columns being altered — must be dropped/recreated.
_VIEWS: dict[str, str] = {
    "leaderboard_weekly": """\
        CREATE VIEW leaderboard_weekly AS
        SELECT user_id,
            sum(base_amount::numeric * COALESCE(rate, 1::numeric)) AS cab_earned_week,
            rank() OVER (
                ORDER BY sum(base_amount::numeric * COALESCE(rate, 1::numeric)) DESC
            ) AS rank
        FROM cabecoin_transactions
        WHERE direction = 'credit'
          AND created_at >= (now() - '7 days'::interval)
        GROUP BY user_id
    """,
    "price_history": """\
        CREATE VIEW price_history AS
        SELECT
            id          AS observation_id,
            store_id,
            product_ean,
            price,
            quantity,
            scan_type,
            scanned_name,
            scanned_at  AS recorded_at
        FROM scans
        WHERE status = 'accepted'
    """,
}


def upgrade() -> None:
    for view in _VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {view}")
    for table, column in _COLUMNS:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE TIMESTAMPTZ "
            f"USING {column} AT TIME ZONE 'UTC'"
        )
    for ddl in _VIEWS.values():
        op.execute(ddl)


def downgrade() -> None:
    for view in _VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {view}")
    for table, column in _COLUMNS:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE TIMESTAMP WITHOUT TIME ZONE "
            f"USING {column} AT TIME ZONE 'UTC'"
        )
    for ddl in _VIEWS.values():
        op.execute(ddl)
