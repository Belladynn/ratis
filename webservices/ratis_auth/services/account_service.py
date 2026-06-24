from typing import Literal

import repositories.preferences_repository as prefs_repo
import repositories.refresh_token_repository as refresh_token_repo
from ratis_core.anonymize import (
    ANON_SENTINEL_USER_ID,
    anonymize_user_id,
    get_anonymize_salt,
)
from ratis_core.jwt import decode_refresh_token
from ratis_core.models import (
    LeaderboardSnapshot,
    NotificationLog,
    PriceAlert,
    ProductTracking,
    RefreshToken,
    ShoppingList,
    User,
    UserBadge,
    UserCabBalance,
    UserIdentity,
    UserPreferences,
    UserPushToken,
    UserSession,
    UserSessionStat,
    UserStorePreference,
    UserStreak,
)
from ratis_core.models.referral import ReferralCode
from ratis_core.schemas import UserUpdate
from sqlalchemy import text
from sqlalchemy.orm import Session

# ============================================================
# Profile
# ============================================================


def get_profile(user: User) -> User:
    return user


def update_profile(db: Session, user: User, data: UserUpdate) -> User:
    changed = False

    # display_name: non-nullable once set — ignore null (can't clear)
    if "display_name" in data.model_fields_set and data.display_name is not None:
        user.display_name = data.display_name
        changed = True

    # avatar_url: nullable — explicit null clears the field
    if "avatar_url" in data.model_fields_set:
        user.avatar_url = data.avatar_url
        changed = True

    if "timezone" in data.model_fields_set and data.timezone is not None:
        user.timezone = data.timezone
        changed = True

    if changed:
        db.commit()
        db.refresh(user)

    return user


# ============================================================
# Preferences
# ============================================================


def get_preferences(db: Session, user: User) -> UserPreferences:
    # Pure read — user_preferences is created at register/oauth time, so the
    # nominal path hits an existing row. No commit in a GET handler.
    return prefs_repo.get_or_create(db, user.id)


def update_preferences(
    db: Session,
    user: User,
    search_radius_km: int | None,
    transport_mode: Literal["driving", "walking", "cycling"] | None,
) -> UserPreferences:
    changed = search_radius_km is not None or transport_mode is not None
    prefs = prefs_repo.upsert(db, user.id, search_radius_km=search_radius_km, transport_mode=transport_mode)
    if changed:
        db.commit()
        db.refresh(prefs)
    return prefs


# ============================================================
# Session management
# ============================================================


def logout(db: Session, user: User, refresh_token_str: str) -> None:
    """Revoke one refresh token. Idempotent: silently succeeds if token is already invalid."""
    try:
        jti = decode_refresh_token(refresh_token_str)
    except ValueError:
        return

    db_token = refresh_token_repo.get_by_jti(db, jti)
    if not db_token or db_token.revoked_at is not None or db_token.user_id != user.id:
        return

    refresh_token_repo.revoke(db, db_token)
    db.commit()


def logout_all(db: Session, user: User) -> None:
    """Revoke all active refresh tokens for the current user."""
    refresh_token_repo.revoke_all_for_user(db, user.id)
    db.commit()


# ============================================================
# Account deletion (RGPD)
# ============================================================


def delete_account(db: Session, user: User) -> None:
    """RGPD anonymize — tombstone-style, NEVER deletes the users row.

    Rationale: ``cashback_withdrawals.user_id`` is FK RESTRICT (legal retention
    5-10 years). Anonymizing in place is consistent regardless of financial
    history.

    Per audit F-AU-3 (2026-05-11), the routine applies a four-tier policy
    that breaks cross-table per-user correlation while preserving the
    analytics value of aggregated history :

    1. **Hard DELETE** (no analytics / pure PII or per-user state) :
       ``refresh_tokens, user_identities`` (PII: OAuth subject + email),
       ``user_push_tokens, shopping_lists`` (cascades items +
       routes), ``product_tracking, price_alerts, user_sessions,
       user_session_stats, notification_logs, user_store_preferences,
       user_streaks, user_badges, leaderboard_snapshots, user_cab_balance,
       user_xp_balance, user_savings_snapshot, notification_outbox,
       product_favorites`` (PII-adjacent).

    2. **SET NULL** (data preserved for downstream consensus, FK already
       SET NULL in schema) : ``scans, receipts, price_challenge_responses,
       referral_codes, stores.suggested_by_user_id``.

    3. **Per-user anon UUID** (preserves per-user analytics grouping while
       breaking re-identification — see ``ratis_core.anonymize``) :
       ``user_achievements, reward_events, user_missions,
       user_battlepass_progress, user_battlepass_claims,
       community_challenge_claims, community_multipliers,
       mystery_challenge_finds, label_sessions, mission_xp_records,
       xp_transactions, referral_uses.referred_user_id,
       product_name_resolutions``.

    4. **Static anon sentinel** (NEVER-PURGE financial / audit — row kept
       intact but FK rewritten to ``ANON_SENTINEL_USER_ID``) :
       ``cabecoin_transactions, cashback_transactions, cashback_withdrawals,
       gift_card_orders``.

    NOT touched: ``subscriptions, user_cashback_balance, user_preferences,
    store_validation_history.triggered_by``. ``subscriptions`` carries the
    Stripe customer_id which is a separate identifier with active business
    coupling — out of scope per F-AU-3 stop-conditions. ``user_preferences``
    has no PII content. ``user_cashback_balance`` is the materialized
    balance for residual cashback the user forfeited at deletion.

    Idempotency
    -----------
    The routine is safe to re-run : the second call DELETEs already-empty
    sets, UPDATEs anonymized rows to the same (deterministic) anon UUID,
    and re-asserts the same tombstone state on ``users``. No row state
    flips back and forth between calls.
    """
    user_id = user.id

    # Resolve the per-user anon UUID + sentinel ONCE — fails fast if the
    # salt is missing (production lifespan should also require_env it).
    salt = get_anonymize_salt()
    anon_uid = anonymize_user_id(user_id, salt)

    # --- Tier 1 : Hard DELETE — PII or per-user materialized state ---
    db.query(RefreshToken).filter(RefreshToken.user_id == user_id).delete(synchronize_session=False)
    # user_identities carries raw OAuth provider_id + email — anonymizing the
    # users row never fires the FK CASCADE, so purge identities explicitly.
    db.query(UserIdentity).filter(UserIdentity.user_id == user_id).delete(synchronize_session=False)
    db.query(UserPushToken).filter(UserPushToken.user_id == user_id).delete(synchronize_session=False)
    # cascades items + routes
    db.query(ShoppingList).filter(ShoppingList.user_id == user_id).delete(synchronize_session=False)
    db.query(ProductTracking).filter(ProductTracking.user_id == user_id).delete(synchronize_session=False)
    db.query(PriceAlert).filter(PriceAlert.user_id == user_id).delete(synchronize_session=False)
    db.query(UserSession).filter(UserSession.user_id == user_id).delete(synchronize_session=False)
    db.query(UserSessionStat).filter(UserSessionStat.user_id == user_id).delete(synchronize_session=False)
    db.query(NotificationLog).filter(NotificationLog.user_id == user_id).delete(synchronize_session=False)
    db.query(UserStorePreference).filter(UserStorePreference.user_id == user_id).delete(synchronize_session=False)
    db.query(UserStreak).filter(UserStreak.user_id == user_id).delete(synchronize_session=False)
    db.query(UserBadge).filter(UserBadge.user_id == user_id).delete(synchronize_session=False)
    db.query(LeaderboardSnapshot).filter(LeaderboardSnapshot.user_id == user_id).delete(synchronize_session=False)
    db.query(UserCabBalance).filter(UserCabBalance.user_id == user_id).delete(synchronize_session=False)

    # PII-adjacent / new tables — raw SQL to avoid importing cross-service
    # models. Each table is owned by the team listed inline.
    uid_str = str(user_id)
    # PA-owned : reveals consumption habits.
    db.execute(text("DELETE FROM product_favorites WHERE user_id = :uid"), {"uid": uid_str})
    # NT-owned : raw notification body retained, no value post-delete.
    db.execute(text("DELETE FROM notification_outbox WHERE user_id = :uid"), {"uid": uid_str})
    # RW-owned : materialized per-user xp balance + savings snapshot.
    db.execute(text("DELETE FROM user_xp_balance WHERE user_id = :uid"), {"uid": uid_str})
    db.execute(text("DELETE FROM user_savings_snapshot WHERE user_id = :uid"), {"uid": uid_str})

    # --- Tier 2 : SET NULL — preserved data, no per-user attribution ---
    # Referral code becomes orphan (unusable as sponsor).
    db.query(ReferralCode).filter(ReferralCode.user_id == user_id).update({"user_id": None}, synchronize_session=False)
    db.execute(text("UPDATE scans SET user_id = NULL WHERE user_id = :uid"), {"uid": uid_str})
    db.execute(text("UPDATE receipts SET user_id = NULL WHERE user_id = :uid"), {"uid": uid_str})
    db.execute(text("UPDATE price_challenge_responses SET user_id = NULL WHERE user_id = :uid"), {"uid": uid_str})
    db.execute(
        text("UPDATE stores SET suggested_by_user_id = NULL WHERE suggested_by_user_id = :uid"),
        {"uid": uid_str},
    )

    # --- Tier 3 : per-user anon UUID — analytics preserved, correlation broken ---
    anon_str = str(anon_uid)
    _PER_USER_ANON_TABLES = (
        "user_achievements",
        "reward_events",
        "user_missions",
        "user_battlepass_progress",
        "user_battlepass_claims",
        "community_challenge_claims",
        "community_multipliers",
        "mystery_challenge_finds",
        "label_sessions",
        "mission_xp_records",
        "xp_transactions",
        "product_name_resolutions",
    )
    for table in _PER_USER_ANON_TABLES:
        # Table names are from a hardcoded module-local tuple, not user input
        # — no SQL injection surface (false positive on B608/S608).
        db.execute(
            text(f"UPDATE {table} SET user_id = :anon WHERE user_id = :uid"),  # noqa: S608  # nosec B608
            {"anon": anon_str, "uid": uid_str},
        )
    # referral_uses uses a non-standard column name.
    db.execute(
        text("UPDATE referral_uses SET referred_user_id = :anon WHERE referred_user_id = :uid"),
        {"anon": anon_str, "uid": uid_str},
    )

    # --- Tier 4 : static sentinel — NEVER-PURGE financial / audit tables ---
    sentinel_str = str(ANON_SENTINEL_USER_ID)
    _SENTINEL_TABLES = (
        "cabecoin_transactions",
        "cashback_transactions",
        "cashback_withdrawals",
        "gift_card_orders",
    )
    for table in _SENTINEL_TABLES:
        # Table names are from a hardcoded module-local tuple, not user input.
        db.execute(
            text(f"UPDATE {table} SET user_id = :sentinel WHERE user_id = :uid"),  # noqa: S608  # nosec B608
            {"sentinel": sentinel_str, "uid": uid_str},
        )

    # --- Tombstone the users row (blocks all future token-based access) ---
    user.email = f"deleted_{user_id}@deleted.invalid"
    user.display_name = None
    user.avatar_url = None
    user.account_type = "deleted"
    user.is_deleted = True

    db.commit()
