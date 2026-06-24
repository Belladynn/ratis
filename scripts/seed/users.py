"""Personas seed — 6 deterministic dev_* users.

Each persona has a fixed UUID so demo screenshots / E2E tests stay
reproducible across re-seeds. See ARCH_seed_test_data.md § Personas for
the full spec (trust_score / created_at / is_deleted / is_shadow_banned).

Idempotent : every INSERT is guarded by ``SELECT 1 WHERE id = …`` so re-runs
are no-ops (Wave 5 will replace this with proper ``DELETE … WHERE
email LIKE 'dev_%'`` + re-insert when the rebuild semantics land).

Account-type semantics
======================
All 6 personas use ``account_type='dev'`` — the seed-only marker
(DB-level, greppable, never collides with prod ``oauth`` users).
``password_hash`` is NULL. Since H2 Phase 2 the OAuth identity lives in
``user_identities`` ; seed personas carry no identity row, so the
``account_type='dev'`` marker stands alone.

dev_diane is in **post-DELETE tombstone state** : email anonymised,
``is_deleted=true``, ``deleted_at`` set ~2 months ago. Per the ARCH
spec the *active* state had ``account_type='dev'`` ; we keep that here
(rather than ``'deleted'``) to preserve the "this row was seeded, not
real" property — the email pattern + account_type check together flag it
unambiguously.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from ratis_core.models.admin_audit import AdminSettingsAudit, AdminSettingsAuditStatus
from ratis_core.models.gamification import UserCabBalance, UserCashbackBalance
from ratis_core.models.user import User
from sqlalchemy import select
from sqlalchemy.orm import Session

# Deterministic UUIDs — chosen so the last hex char matches the persona
# initial (a/b/c/d/e + admin uses 'a' too but distinguished by the higher bits).
PERSONA_UUIDS = {
    "alice": uuid.UUID("00000000-0000-0000-0000-00000000000a"),
    "bob": uuid.UUID("00000000-0000-0000-0000-00000000000b"),
    "charlie": uuid.UUID("00000000-0000-0000-0000-00000000000c"),
    "diane": uuid.UUID("00000000-0000-0000-0000-00000000000d"),
    "eve": uuid.UUID("00000000-0000-0000-0000-00000000000e"),
    # admin uses a distinct prefix (no overlap with the 0x0a alice id)
    "admin": uuid.UUID("00000000-0000-0000-0000-0000000000ad"),
}


def _now() -> datetime:
    """Helper — timezone-aware UTC now."""
    return datetime.now(UTC)


def _upsert_user(session: Session, user: User) -> bool:
    """Insert ``user`` if it doesn't already exist by id. Returns True if inserted."""
    existing = session.execute(select(User).where(User.id == user.id)).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(user)
    session.flush()
    return True


def _ensure_balances(session: Session, user_id: uuid.UUID, *, cab: int, cashback: int) -> None:
    """Idempotent UPSERT of user_cab_balance + user_cashback_balance rows."""
    cab_row = session.get(UserCabBalance, user_id)
    if cab_row is None:
        session.add(UserCabBalance(user_id=user_id, balance=cab))
    else:
        cab_row.balance = cab
    cashback_row = session.get(UserCashbackBalance, user_id)
    if cashback_row is None:
        session.add(UserCashbackBalance(user_id=user_id, balance=cashback))
    else:
        cashback_row.balance = cashback


def _seed_alice(session: Session) -> User:
    """🟢 dev_alice — fresh signup, empty state (trust_score 50, neutral default)."""
    now = _now()
    user = User(
        id=PERSONA_UUIDS["alice"],
        email="dev_alice@ratis.app",
        support_id="RTS-DEVAA2",
        account_type="dev",
        password_hash=None,
        created_at=now - timedelta(minutes=2),
        updated_at=now - timedelta(minutes=2),
        display_name="Alice (dev)",
        is_deleted=False,
        timezone="Europe/Paris",
        trust_score=50,
        total_resolved_scans=0,
        is_shadow_banned=False,
    )
    _upsert_user(session, user)
    _ensure_balances(session, user.id, cab=0, cashback=0)
    return user


def _seed_bob(session: Session) -> User:
    """🔵 dev_bob — active daily user (4 months, trust_score 88, grace period)."""
    now = _now()
    user = User(
        id=PERSONA_UUIDS["bob"],
        email="dev_bob@ratis.app",
        support_id="RTS-DEVBB3",
        account_type="dev",
        password_hash=None,
        created_at=now - timedelta(days=120),
        updated_at=now - timedelta(days=1),
        display_name="Bob (dev)",
        is_deleted=False,
        timezone="Europe/Paris",
        trust_score=88,
        total_resolved_scans=55,
        is_shadow_banned=False,
    )
    _upsert_user(session, user)
    _ensure_balances(session, user.id, cab=3_250, cashback=1_840)  # 18.40€ as cents
    return user


def _seed_charlie(session: Session) -> User:
    """🟣 dev_charlie — power user premium (1 year, trust_score 95, 312 scans)."""
    now = _now()
    user = User(
        id=PERSONA_UUIDS["charlie"],
        email="dev_charlie@ratis.app",
        support_id="RTS-DEVCC4",
        account_type="dev",
        password_hash=None,
        created_at=now - timedelta(days=365),
        updated_at=now - timedelta(hours=2),
        display_name="Charlie (dev)",
        is_deleted=False,
        timezone="Europe/Paris",
        trust_score=95,
        total_resolved_scans=285,
        is_shadow_banned=False,
    )
    _upsert_user(session, user)
    _ensure_balances(session, user.id, cab=47_500, cashback=820)  # 8.20€ as cents
    return user


def _seed_diane(session: Session) -> User:
    """🟡 dev_diane — RGPD-deleted (-8mo created, -2mo DELETE; email anonymised)."""
    now = _now()
    diane_id = PERSONA_UUIDS["diane"]
    # Email anonymised to match real ``delete_account`` flow shape.
    anon_email = f"deleted_{diane_id.hex}@ratis.app"
    user = User(
        id=diane_id,
        email=anon_email,
        support_id="RTS-DEVDD5",
        account_type="dev",  # see module docstring — kept as 'dev' to retain seed-only marker
        password_hash=None,
        created_at=now - timedelta(days=240),  # ~8 months
        updated_at=now - timedelta(days=60),
        display_name=None,  # blanked at deletion
        is_deleted=True,
        timezone="Europe/Paris",
        trust_score=80,  # preserved snapshot from before deletion
        total_resolved_scans=13,
        is_shadow_banned=False,
    )
    _upsert_user(session, user)
    # Cashback transactions stay (NEVER PURGE) — Wave 4 will seed them.
    # CAB balance forced to 0 per spec.
    _ensure_balances(session, user.id, cab=0, cashback=0)
    return user


def _seed_admin(session: Session) -> User:
    """🔴 dev_admin — service ops account (2 years, no scans, no gamif)."""
    now = _now()
    user = User(
        id=PERSONA_UUIDS["admin"],
        email="dev_admin@ratis.app",
        support_id="RTS-DEVMN6",
        account_type="dev",
        password_hash=None,
        created_at=now - timedelta(days=730),  # 2 years
        updated_at=now - timedelta(days=1),
        display_name="Admin (dev)",
        is_deleted=False,
        timezone="Europe/Paris",
        trust_score=100,
        total_resolved_scans=0,
        is_shadow_banned=False,
    )
    _upsert_user(session, user)
    _ensure_balances(session, user.id, cab=0, cashback=0)
    return user


def _seed_eve(session: Session) -> User:
    """🟠 dev_eve — shadow-banned anti-fraud cohort (-6mo, trust_score 32)."""
    now = _now()
    user = User(
        id=PERSONA_UUIDS["eve"],
        email="dev_eve@ratis.app",
        support_id="RTS-DEVEE7",
        account_type="dev",
        password_hash=None,
        created_at=now - timedelta(days=180),  # 6 months
        updated_at=now - timedelta(days=2),
        display_name="Eve (dev)",
        is_deleted=False,
        timezone="Europe/Paris",
        trust_score=32,
        total_resolved_scans=140,
        is_shadow_banned=True,
        # Stamp the trust_score_updated_at so the batch can see this was set.
        trust_score_updated_at=now - timedelta(days=90),
    )
    _upsert_user(session, user)
    _ensure_balances(session, user.id, cab=800, cashback=0)
    return user


def _seed_admin_audit_samples(session: Session) -> int:
    """Insert ~3 admin_settings_audit entries for the dev_admin operator.

    Lightweight — Wave 4/5 can expand. Provides enough state to render the
    audit log viewer screen on the admin UI.
    """
    now = _now()
    samples = [
        {
            "timestamp": now - timedelta(days=18),
            "operator": "dev_admin@ratis.app",
            "section": "cab_economy",
            "reason": "Seeded dev sample — recalibrate scan reward floor for premium retailers.",
            "old_data": {"scan_reward_floor": 5},
            "new_data": {"scan_reward_floor": 8},
            "diff": {"scan_reward_floor": [5, 8]},
            "status": AdminSettingsAuditStatus.APPLIED,
            "applied_at": now - timedelta(days=18),
        },
        {
            "timestamp": now - timedelta(days=7),
            "operator": "dev_admin@ratis.app",
            "section": "anti_fraud",
            "reason": "Seeded dev sample — raise duplicate-detection sensitivity for hard-discount segment.",
            "old_data": {"duplicate_window_minutes": 60},
            "new_data": {"duplicate_window_minutes": 90},
            "diff": {"duplicate_window_minutes": [60, 90]},
            "status": AdminSettingsAuditStatus.APPLIED,
            "applied_at": now - timedelta(days=7),
        },
        {
            "timestamp": now - timedelta(days=2),
            "operator": "dev_admin@ratis.app",
            "section": "rewards_notification",
            "reason": "Seeded dev sample — pre-launch quiet hours adjustment for Europe/Paris.",
            "old_data": {"quiet_hours_start": "22:00"},
            "new_data": {"quiet_hours_start": "21:30"},
            "diff": {"quiet_hours_start": ["22:00", "21:30"]},
            "status": AdminSettingsAuditStatus.APPLIED,
            "applied_at": now - timedelta(days=2),
        },
    ]
    # Idempotent : skip if dev_admin operator already has 3+ rows.
    existing = session.execute(
        select(AdminSettingsAudit).where(AdminSettingsAudit.operator == "dev_admin@ratis.app")
    ).all()
    if len(existing) >= 3:
        return 0
    inserted = 0
    for row in samples:
        audit = AdminSettingsAudit(**row)
        session.add(audit)
        inserted += 1
    session.flush()
    return inserted


def seed_users(session: Session) -> None:
    """Insert 6 personas (alice/bob/charlie/diane/admin/eve) + admin audit samples.

    See ARCH § Personas. Idempotent — re-runs skip existing rows.
    """
    print("[users] seeding 6 personas (alice/bob/charlie/diane/admin/eve)…")
    _seed_alice(session)
    _seed_bob(session)
    _seed_charlie(session)
    _seed_diane(session)
    _seed_admin(session)
    _seed_eve(session)
    audit_count = _seed_admin_audit_samples(session)
    session.flush()
    print(f"[users] done — 6 users + {audit_count} admin_settings_audit samples")
