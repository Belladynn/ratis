"""Tests for ratis_batch_trust_score.

Anti-fraud V1 — see ARCH_anti_fraud.md for the contract.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from sqlalchemy import text

# Add the batch dir to sys.path so we can import the entry module by
# name without packaging it.
_BATCH_DIR = Path(__file__).resolve().parents[1]
if str(_BATCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BATCH_DIR))

import trust_score as tsm

# ──────────────────────────────────────────────────────────────────────
# Pure logic — UserStats.trust_score & decision helpers
# ──────────────────────────────────────────────────────────────────────


class TestTrustScoreFormula:
    def test_total_zero_returns_neutral_50(self):
        s = tsm.UserStats(uuid.uuid4(), total=0, agreed=0, previous_score=50, previous_shadow_banned=False)
        assert s.trust_score == 50

    def test_all_agreed_returns_100(self):
        s = tsm.UserStats(uuid.uuid4(), total=100, agreed=100, previous_score=50, previous_shadow_banned=False)
        assert s.trust_score == 100

    def test_zero_agreed_returns_0(self):
        s = tsm.UserStats(uuid.uuid4(), total=100, agreed=0, previous_score=50, previous_shadow_banned=False)
        assert s.trust_score == 0

    def test_half_agreed_returns_50(self):
        s = tsm.UserStats(uuid.uuid4(), total=100, agreed=50, previous_score=50, previous_shadow_banned=False)
        assert s.trust_score == 50

    def test_thirty_percent(self):
        s = tsm.UserStats(uuid.uuid4(), total=100, agreed=30, previous_score=50, previous_shadow_banned=False)
        assert s.trust_score == 30

    def test_rounding_half_up(self):
        # 1/3 → 33.33% → 33
        s = tsm.UserStats(uuid.uuid4(), total=3, agreed=1, previous_score=50, previous_shadow_banned=False)
        assert s.trust_score == 33
        # 2/3 → 66.67% → 67 (round half up via integer math)
        s = tsm.UserStats(uuid.uuid4(), total=3, agreed=2, previous_score=50, previous_shadow_banned=False)
        assert s.trust_score == 67


class TestShadowBanDecision:
    def test_under_grace_period_does_not_flip(self):
        s = tsm.UserStats(uuid.uuid4(), total=99, agreed=0, previous_score=50, previous_shadow_banned=False)
        # score is 0 but total < 100 → grace period protects
        assert tsm._decide_shadow_ban(s) is False

    def test_at_grace_with_low_score_flips(self):
        s = tsm.UserStats(uuid.uuid4(), total=100, agreed=30, previous_score=50, previous_shadow_banned=False)
        # 30 < 65
        assert tsm._decide_shadow_ban(s) is True

    def test_at_grace_with_score_65_does_not_flip(self):
        s = tsm.UserStats(uuid.uuid4(), total=100, agreed=65, previous_score=50, previous_shadow_banned=False)
        # 65 not < 65
        assert tsm._decide_shadow_ban(s) is False

    def test_recovery_does_not_auto_unban(self):
        # User was banned ; their score is now 90 ; we keep the flag.
        s = tsm.UserStats(uuid.uuid4(), total=200, agreed=180, previous_score=50, previous_shadow_banned=True)
        assert tsm._decide_shadow_ban(s) is True


class TestWarningTransition:
    def test_no_warning_under_grace(self):
        s = tsm.UserStats(uuid.uuid4(), total=50, agreed=35, previous_score=80, previous_shadow_banned=False)
        # 70% but total < 100 → no warning
        assert tsm._decide_warning(s, 70) is False

    def test_warning_when_crossing_into_band(self):
        s = tsm.UserStats(uuid.uuid4(), total=100, agreed=70, previous_score=80, previous_shadow_banned=False)
        # was OK (80) → now 70, in [65,75) → warn
        assert tsm._decide_warning(s, 70) is True

    def test_no_warning_when_already_in_band(self):
        s = tsm.UserStats(uuid.uuid4(), total=100, agreed=70, previous_score=70, previous_shadow_banned=False)
        # already 70, still 70 → no re-warn
        assert tsm._decide_warning(s, 70) is False

    def test_no_warning_when_above_band(self):
        s = tsm.UserStats(uuid.uuid4(), total=100, agreed=80, previous_score=80, previous_shadow_banned=False)
        assert tsm._decide_warning(s, 80) is False


# ──────────────────────────────────────────────────────────────────────
# Integration — _compute_user_stats reads the ledger
# ──────────────────────────────────────────────────────────────────────


class TestComputeUserStats:
    def test_no_consensus_pairs_returns_empty(self, db, make_user):
        make_user()
        rows = tsm._compute_user_stats(db)
        assert rows == []

    def test_only_pending_state_excluded(self, db, make_user, make_scan, add_resolution, add_state_event):
        uid = make_user()
        scan = make_scan(uid)
        add_resolution(
            scan_id=scan,
            user_id=uid,
            normalized_label="X",
            product_ean="1234567890123",
        )
        # State is PENDING — should not count.
        add_state_event(
            normalized_label="X",
            top1_ean="1234567890123",
            state="pending",
        )
        rows = tsm._compute_user_stats(db)
        assert rows == []

    def test_verified_pair_with_match_counts_as_agreed(self, db, make_user, make_scan, add_resolution, add_state_event):
        uid = make_user()
        scan = make_scan(uid)
        add_resolution(
            scan_id=scan,
            user_id=uid,
            normalized_label="X",
            product_ean="1234567890123",
        )
        add_state_event(
            normalized_label="X",
            top1_ean="1234567890123",
            state="verified",
        )
        rows = tsm._compute_user_stats(db)
        assert len(rows) == 1
        assert rows[0].user_id == uid
        assert rows[0].total == 1
        assert rows[0].agreed == 1

    def test_verified_pair_with_wrong_ean_counts_as_disagreed(
        self, db, make_user, make_scan, add_resolution, add_state_event
    ):
        uid = make_user()
        scan = make_scan(uid)
        add_resolution(
            scan_id=scan,
            user_id=uid,
            normalized_label="X",
            product_ean="0000000000000",
        )
        add_state_event(
            normalized_label="X",
            top1_ean="1234567890123",
            state="verified",
        )
        rows = tsm._compute_user_stats(db)
        assert len(rows) == 1
        assert rows[0].total == 1
        assert rows[0].agreed == 0

    def test_soft_deleted_users_excluded(self, db, make_user, make_scan, add_resolution, add_state_event):
        uid = make_user(is_deleted=True)
        scan = make_scan(uid)
        add_resolution(
            scan_id=scan,
            user_id=uid,
            normalized_label="X",
            product_ean="1234567890123",
        )
        add_state_event(
            normalized_label="X",
            top1_ean="1234567890123",
            state="verified",
        )
        rows = tsm._compute_user_stats(db)
        assert rows == []

    def test_latest_state_event_is_used(self, db, make_user, make_scan, add_resolution, add_state_event):
        # First state was VERIFIED with EAN A ; later flipped to UNVERIFIED
        # with EAN B. Latest snapshot wins.
        uid = make_user()
        scan = make_scan(uid)
        add_resolution(
            scan_id=scan,
            user_id=uid,
            normalized_label="X",
            product_ean="EAN_A",
        )
        add_state_event(
            normalized_label="X",
            top1_ean="EAN_A",
            state="verified",
        )
        add_state_event(
            normalized_label="X",
            top1_ean="EAN_B",
            state="unverified",
        )
        rows = tsm._compute_user_stats(db)
        assert len(rows) == 1
        # User voted EAN_A but latest top1 is EAN_B → disagreement.
        assert rows[0].total == 1
        assert rows[0].agreed == 0


# ──────────────────────────────────────────────────────────────────────
# End-to-end — run_batch persists the new state correctly
# ──────────────────────────────────────────────────────────────────────


def _seed_user_with_n_consensual_scans(
    db,
    *,
    make_user,
    make_scan,
    add_resolution,
    add_state_event,
    n_total: int,
    n_agreed: int,
    user_kwargs: dict | None = None,
) -> uuid.UUID:
    """Helper : seed a user with N total consensual contributions of
    which n_agreed match the (verified) top1_ean.
    """
    uid = make_user(**(user_kwargs or {}))
    for i in range(n_total):
        scan = make_scan(uid)
        ean = "GOOD_EAN" if i < n_agreed else "BAD_EAN"
        label = f"label_{i}"
        add_resolution(
            scan_id=scan,
            user_id=uid,
            normalized_label=label,
            product_ean=ean,
        )
        add_state_event(
            normalized_label=label,
            top1_ean="GOOD_EAN",
            state="verified",
        )
    return uid


def _read_user(db, uid):
    return db.execute(
        text("""
            SELECT trust_score, total_resolved_scans, is_shadow_banned,
                   trust_score_updated_at
            FROM users WHERE id = :uid
        """),
        {"uid": str(uid)},
    ).first()


class TestRunBatchPersistence:
    def test_user_under_grace_keeps_default_when_score_low(
        self,
        db,
        session_factory,
        make_user,
        make_scan,
        add_resolution,
        add_state_event,
    ):
        uid = _seed_user_with_n_consensual_scans(
            db,
            make_user=make_user,
            make_scan=make_scan,
            add_resolution=add_resolution,
            add_state_event=add_state_event,
            n_total=50,
            n_agreed=10,  # 20% but only 50 scans
        )
        db.commit()

        notifs: list = []
        counters = tsm.run_batch(
            session_factory,
            dry_run=False,
            notifier=lambda **kw: notifs.append(kw),
        )

        row = _read_user(db, uid)
        assert row.trust_score == 20  # 10/50
        assert row.total_resolved_scans == 50
        assert row.is_shadow_banned is False  # grace period
        assert row.trust_score_updated_at is not None
        assert counters["users_processed"] == 1
        assert counters["shadow_banned_now"] == 0
        assert counters["warnings_emitted"] == 0
        assert notifs == []

    def test_user_at_100_with_perfect_score_no_action(
        self,
        db,
        session_factory,
        make_user,
        make_scan,
        add_resolution,
        add_state_event,
    ):
        uid = _seed_user_with_n_consensual_scans(
            db,
            make_user=make_user,
            make_scan=make_scan,
            add_resolution=add_resolution,
            add_state_event=add_state_event,
            n_total=100,
            n_agreed=100,
        )
        db.commit()
        notifs: list = []
        tsm.run_batch(session_factory, dry_run=False, notifier=lambda **kw: notifs.append(kw))
        row = _read_user(db, uid)
        assert row.trust_score == 100
        assert row.total_resolved_scans == 100
        assert row.is_shadow_banned is False
        assert notifs == []

    def test_user_at_100_with_30pct_gets_shadow_banned_silently(
        self,
        db,
        session_factory,
        make_user,
        make_scan,
        add_resolution,
        add_state_event,
    ):
        uid = _seed_user_with_n_consensual_scans(
            db,
            make_user=make_user,
            make_scan=make_scan,
            add_resolution=add_resolution,
            add_state_event=add_state_event,
            n_total=100,
            n_agreed=30,
        )
        db.commit()
        notifs: list = []
        counters = tsm.run_batch(
            session_factory,
            dry_run=False,
            notifier=lambda **kw: notifs.append(kw),
        )
        row = _read_user(db, uid)
        assert row.trust_score == 30
        assert row.is_shadow_banned is True
        assert counters["shadow_banned_now"] == 1
        # Silent — no notification.
        assert notifs == []

    def test_user_at_100_with_70pct_gets_warning_only(
        self,
        db,
        session_factory,
        make_user,
        make_scan,
        add_resolution,
        add_state_event,
    ):
        uid = _seed_user_with_n_consensual_scans(
            db,
            make_user=make_user,
            make_scan=make_scan,
            add_resolution=add_resolution,
            add_state_event=add_state_event,
            n_total=100,
            n_agreed=70,
            user_kwargs={"trust_score": 80},  # was OK before
        )
        db.commit()
        notifs: list = []
        counters = tsm.run_batch(
            session_factory,
            dry_run=False,
            notifier=lambda **kw: notifs.append(kw),
        )
        row = _read_user(db, uid)
        assert row.trust_score == 70
        assert row.is_shadow_banned is False
        assert counters["warnings_emitted"] == 1
        assert len(notifs) == 1
        assert notifs[0]["notif_type"] == "trust_score_warning"
        assert notifs[0]["data"]["trust_score"] == 70

    def test_idempotent_run(
        self,
        db,
        session_factory,
        make_user,
        make_scan,
        add_resolution,
        add_state_event,
    ):
        uid = _seed_user_with_n_consensual_scans(
            db,
            make_user=make_user,
            make_scan=make_scan,
            add_resolution=add_resolution,
            add_state_event=add_state_event,
            n_total=100,
            n_agreed=80,
            user_kwargs={"trust_score": 80},
        )
        db.commit()
        notifs1: list = []
        notifs2: list = []
        tsm.run_batch(session_factory, dry_run=False, notifier=lambda **kw: notifs1.append(kw))
        # Second run — same data, expect no new effect.
        tsm.run_batch(session_factory, dry_run=False, notifier=lambda **kw: notifs2.append(kw))
        row = _read_user(db, uid)
        assert row.trust_score == 80
        assert row.is_shadow_banned is False

    def test_dry_run_does_not_persist_or_notify(
        self,
        db,
        session_factory,
        make_user,
        make_scan,
        add_resolution,
        add_state_event,
    ):
        uid = _seed_user_with_n_consensual_scans(
            db,
            make_user=make_user,
            make_scan=make_scan,
            add_resolution=add_resolution,
            add_state_event=add_state_event,
            n_total=100,
            n_agreed=70,
            user_kwargs={"trust_score": 80},
        )
        db.commit()
        notifs: list = []
        tsm.run_batch(session_factory, dry_run=True, notifier=lambda **kw: notifs.append(kw))
        row = _read_user(db, uid)
        # State unchanged — dry run.
        assert row.trust_score == 80
        assert row.is_shadow_banned is False
        assert notifs == []
