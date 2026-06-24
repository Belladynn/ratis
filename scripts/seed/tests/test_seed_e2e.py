"""End-to-end seed run against a fresh migrated PG database.

Spins up a disposable Postgres DB, runs ``alembic upgrade heads`` (production
source of truth), then invokes ``scripts.seed.main.main()`` and asserts the
expected row counts + persona invariants.

Skipped automatically when Postgres is unreachable (local PG not running) —
CI Linux Docker has it, so this test gates the merge per R15.

Idempotency is exercised explicitly : the second ``main()`` call must NOT
raise and must NOT change the row counts (re-running the seed is safe).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

REPO_ROOT = Path(__file__).resolve().parents[3]
ADMIN_URL = "postgresql+psycopg://ratis:ratis@localhost:5432/postgres"  # pragma: allowlist secret


@pytest.fixture(scope="module")
def seed_migrated_db_url() -> str:
    """Spin up a disposable ``_seed_e2e_<hex>`` DB, run alembic, yield URL.

    The name embeds ``_seed`` so the DA-5 safety guard
    (``DATABASE_URL contains '_seed' or '_dev'``) does not abort.
    """
    db_name = f"ratis_seed_e2e_{uuid.uuid4().hex[:8]}"
    fresh_url = f"postgresql+psycopg://ratis:ratis@localhost:5432/{db_name}"  # pragma: allowlist secret

    try:
        admin_engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
            conn.execute(text(f"CREATE DATABASE {db_name}"))
        admin_engine.dispose()
    except OperationalError as exc:
        pytest.skip(f"Postgres not reachable for seed e2e test: {exc}")

    env = os.environ.copy()
    env["DATABASE_URL"] = fresh_url
    try:
        result = subprocess.run(
            ["uv", "run", "alembic", "upgrade", "heads"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        _drop_db(db_name)
        pytest.skip(f"alembic CLI unavailable for seed e2e test: {exc}")

    if result.returncode != 0:
        _drop_db(db_name)
        pytest.fail(
            f"alembic upgrade head failed during seed e2e test:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    yield fresh_url
    _drop_db(db_name)


def _drop_db(db_name: str) -> None:
    """Best-effort cleanup of a disposable test database."""
    try:
        admin_engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": db_name},
            )
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
        admin_engine.dispose()
    except OperationalError:
        pass


@pytest.fixture
def run_seed(seed_migrated_db_url: str, monkeypatch: pytest.MonkeyPatch):
    """Run ``scripts.seed.main.main()`` against the fresh DB. Returns a fn
    that can be called again for the idempotency assertion."""
    monkeypatch.setenv("DATABASE_URL", seed_migrated_db_url)
    monkeypatch.setenv("ENVIRONMENT", "seed")

    # Force a fresh engine — the module-level cache in ``_engine`` would
    # otherwise reuse a stale connection from a previous test.
    from scripts.seed import _engine

    _engine._engine = None
    _engine._SessionLocal = None

    from scripts.seed.main import main as seed_main

    def _run() -> None:
        # Each call recreates the engine if it was reset (and reuses it
        # otherwise — within the same test scope).
        seed_main()

    return _run


@pytest.fixture
def db_connection(seed_migrated_db_url: str):
    """Direct SQLAlchemy connection for assertions."""
    engine = create_engine(seed_migrated_db_url)
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


# ============================================================
# Tests
# ============================================================
class TestSeedE2EHappyPath:
    def test_main_inserts_expected_foundation_rows(self, run_seed, db_connection) -> None:
        """One ``main()`` call → 6 users + 14 stores + 25 products + 3 audit logs."""
        run_seed()

        # Users — 6 personas, dev_admin's email exact, dev_diane anonymised.
        n_dev_users = db_connection.execute(text("SELECT COUNT(*) FROM users WHERE account_type = 'dev'")).scalar()
        assert n_dev_users == 6, f"expected 6 dev_* users, got {n_dev_users}"

        # Email pattern : 5 dev_*@ratis.app + 1 deleted_<uuid>@ratis.app for diane.
        n_dev_emails = db_connection.execute(
            text("SELECT COUNT(*) FROM users WHERE email LIKE 'dev\\_%@ratis.app' ESCAPE '\\'")
        ).scalar()
        assert n_dev_emails == 5, f"expected 5 dev_*@ratis.app emails, got {n_dev_emails}"

        n_deleted_emails = db_connection.execute(
            text("SELECT COUNT(*) FROM users WHERE email LIKE 'deleted\\_%@ratis.app' ESCAPE '\\'")
        ).scalar()
        assert n_deleted_emails == 1, f"expected 1 deleted_<uuid>@ratis.app email (dev_diane), got {n_deleted_emails}"

        # Stores — 14 total (12 OSM + 1 user_suggested + 1 disabled).
        n_stores = db_connection.execute(text("SELECT COUNT(*) FROM stores")).scalar()
        assert n_stores == 14, f"expected 14 stores, got {n_stores}"

        n_osm = db_connection.execute(text("SELECT COUNT(*) FROM stores WHERE source = 'osm'")).scalar()
        # 12 ring-2km/2-5km/10-15km + 1 soft-deleted (still source='osm', is_disabled=true) = 13
        assert n_osm == 13, f"expected 13 source='osm' stores, got {n_osm}"

        n_pending = db_connection.execute(
            text("SELECT COUNT(*) FROM stores WHERE source = 'user_suggested' AND validation_status = 'pending'")
        ).scalar()
        assert n_pending == 1, f"expected 1 user_suggested+pending store, got {n_pending}"

        n_disabled = db_connection.execute(text("SELECT COUNT(*) FROM stores WHERE is_disabled = true")).scalar()
        assert n_disabled == 1, f"expected 1 soft-deleted store, got {n_disabled}"

        # Products — 25 valid (the 26th invalid EAN is NEVER DB-inserted).
        n_products = db_connection.execute(text("SELECT COUNT(*) FROM products")).scalar()
        assert n_products == 25, f"expected 25 products, got {n_products}"

        # Invalid EAN must NOT be in the DB.
        n_invalid = db_connection.execute(text("SELECT COUNT(*) FROM products WHERE ean = '9999999999999'")).scalar()
        assert n_invalid == 0, "synthetic invalid EAN must NOT be DB-inserted"

        # admin_settings_audit — 3 sample rows operated by dev_admin@ratis.app.
        n_audit = db_connection.execute(
            text("SELECT COUNT(*) FROM admin_settings_audit WHERE operator = 'dev_admin@ratis.app'")
        ).scalar()
        assert n_audit >= 3, f"expected ≥3 admin audit samples, got {n_audit}"

    def test_persona_specific_state(self, run_seed, db_connection) -> None:
        """Spot-check : eve shadow-banned, diane is_deleted, alice trust_score=50."""
        run_seed()

        # dev_eve : trust_score=32 + is_shadow_banned=true + total_resolved_scans=140
        eve = db_connection.execute(
            text(
                "SELECT trust_score, is_shadow_banned, total_resolved_scans "
                "FROM users WHERE email = 'dev_eve@ratis.app'"
            )
        ).first()
        assert eve is not None, "dev_eve must exist"
        assert eve.trust_score == 32, f"dev_eve trust_score should be 32, got {eve.trust_score}"
        assert eve.is_shadow_banned is True, "dev_eve must be shadow-banned"
        assert eve.total_resolved_scans == 140, (
            f"dev_eve total_resolved_scans should be 140, got {eve.total_resolved_scans}"
        )

        # dev_alice : neutral defaults trust_score=50, total=0, not shadow-banned
        alice = db_connection.execute(
            text(
                "SELECT trust_score, total_resolved_scans, is_shadow_banned "
                "FROM users WHERE email = 'dev_alice@ratis.app'"
            )
        ).first()
        assert alice is not None, "dev_alice must exist"
        assert alice.trust_score == 50
        assert alice.total_resolved_scans == 0
        assert alice.is_shadow_banned is False

        # dev_diane : is_deleted=true + email anonymised + account_type='dev'
        diane = db_connection.execute(
            text("SELECT email, is_deleted, account_type FROM users WHERE id = '00000000-0000-0000-0000-00000000000d'")
        ).first()
        assert diane is not None, "dev_diane must exist (deterministic UUID)"
        assert diane.is_deleted is True, "dev_diane must be soft-deleted"
        assert diane.email.startswith("deleted_"), f"dev_diane email must be anonymised, got {diane.email}"
        assert diane.account_type == "dev", (
            f"dev_diane keeps account_type='dev' (seed marker), got {diane.account_type}"
        )

        # dev_charlie : CAB balance 47_500, cashback 820 (8.20€)
        charlie_cab = db_connection.execute(
            text("SELECT balance FROM user_cab_balance WHERE user_id = '00000000-0000-0000-0000-00000000000c'")
        ).scalar()
        assert charlie_cab == 47_500, f"charlie CAB balance should be 47500, got {charlie_cab}"

        charlie_cashback = db_connection.execute(
            text("SELECT balance FROM user_cashback_balance WHERE user_id = '00000000-0000-0000-0000-00000000000c'")
        ).scalar()
        assert charlie_cashback == 820, f"charlie cashback (cents) should be 820, got {charlie_cashback}"

    def test_idempotent_double_run(self, run_seed, db_connection) -> None:
        """Calling main() twice must NOT raise and must NOT change row counts."""
        run_seed()
        first_counts = {
            "users": db_connection.execute(text("SELECT COUNT(*) FROM users")).scalar(),
            "stores": db_connection.execute(text("SELECT COUNT(*) FROM stores")).scalar(),
            "products": db_connection.execute(text("SELECT COUNT(*) FROM products")).scalar(),
            "cab_bal": db_connection.execute(text("SELECT COUNT(*) FROM user_cab_balance")).scalar(),
            "cashback_bal": db_connection.execute(text("SELECT COUNT(*) FROM user_cashback_balance")).scalar(),
            "audit": db_connection.execute(
                text("SELECT COUNT(*) FROM admin_settings_audit WHERE operator = 'dev_admin@ratis.app'")
            ).scalar(),
        }

        # Second call — must be a no-op.
        run_seed()
        second_counts = {
            "users": db_connection.execute(text("SELECT COUNT(*) FROM users")).scalar(),
            "stores": db_connection.execute(text("SELECT COUNT(*) FROM stores")).scalar(),
            "products": db_connection.execute(text("SELECT COUNT(*) FROM products")).scalar(),
            "cab_bal": db_connection.execute(text("SELECT COUNT(*) FROM user_cab_balance")).scalar(),
            "cashback_bal": db_connection.execute(text("SELECT COUNT(*) FROM user_cashback_balance")).scalar(),
            "audit": db_connection.execute(
                text("SELECT COUNT(*) FROM admin_settings_audit WHERE operator = 'dev_admin@ratis.app'")
            ).scalar(),
        }
        assert first_counts == second_counts, f"second seed() run mutated counts : {first_counts} → {second_counts}"


class TestAccountTypeDevCheck:
    """Migration 20260518_1300_acct_type — account_type='dev' allowed.

    Since H2 Phase 2 the OAuth identity lives in ``user_identities`` ; the
    ``users`` row only carries an ``account_type`` *state*. The seed
    personas use ``account_type='dev'`` and must be accepted by the
    ``account_type_check`` CHECK.
    """

    def test_dev_account_type_accepted(self, seed_migrated_db_url: str) -> None:
        """Inserting a row with account_type='dev' → no CHECK error."""
        engine = create_engine(seed_migrated_db_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO users (id, email, support_id, account_type, "
                        "password_hash, is_deleted, "
                        "gift_card_redeemed_ytd_cents) "
                        "VALUES (:id, :email, :sid, 'dev', NULL, false, 0)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "email": "dev_smoketest@ratis.app",
                        "sid": f"RTS-TST{uuid.uuid4().hex[:3].upper()}",
                    },
                )
        finally:
            engine.dispose()

    def test_unknown_account_type_rejected(self, seed_migrated_db_url: str) -> None:
        """An account_type outside the whitelist → account_type_check fires."""
        engine = create_engine(seed_migrated_db_url)
        try:
            with engine.connect() as conn, conn.begin():
                with pytest.raises(Exception, match=r"account_type_check|check constraint"):
                    conn.execute(
                        text(
                            "INSERT INTO users (id, email, support_id, account_type, "
                            "password_hash, is_deleted, "
                            "gift_card_redeemed_ytd_cents) "
                            "VALUES (:id, :email, :sid, 'martian', NULL, false, 0)"
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "email": "dev_smoketest2@ratis.app",
                            "sid": f"RTS-TST{uuid.uuid4().hex[:3].upper()}",
                        },
                    )
        finally:
            engine.dispose()


# Persona UUID constants (mirror scripts/seed/users.PERSONA_UUIDS).
_BOB_ID = "00000000-0000-0000-0000-00000000000b"
_CHARLIE_ID = "00000000-0000-0000-0000-00000000000c"
_DIANE_ID = "00000000-0000-0000-0000-00000000000d"
_EVE_ID = "00000000-0000-0000-0000-00000000000e"
_ADMIN_ID = "00000000-0000-0000-0000-0000000000ad"
_ALICE_ID = "00000000-0000-0000-0000-00000000000a"


class TestWave3ScansContent:
    """Wave 3 — scans content per persona + 10 narrative scenarios."""

    def test_per_persona_scan_counts(self, run_seed, db_connection) -> None:
        """Per-persona scan totals match the ARCH spec.

        Volumes (ARCH § Personas) :
          alice   = 0
          bob    47 receipts × 3-8 items + 23 e-labels + 3 manual ≈ 240-400
          charlie ≈ 309 bulk × 5-15 + 32-line + 2 single-line + 1 rejected ≈ 2200-4700
          diane   13 × 3-6 items ≈ 40-80
          eve   = 140 (exact)
          admin = 0
        """
        run_seed()

        def count_scans(user_id: str) -> int:
            return db_connection.execute(
                text("SELECT COUNT(*) FROM scans WHERE user_id = :uid"),
                {"uid": user_id},
            ).scalar()

        assert count_scans(_ALICE_ID) == 0, "alice must have NO scans (empty state)"
        assert count_scans(_ADMIN_ID) == 0, "admin must have NO personal scans"

        bob_n = count_scans(_BOB_ID)
        assert 240 <= bob_n <= 500, f"bob scans out of range : {bob_n}"

        charlie_n = count_scans(_CHARLIE_ID)
        assert 2_200 <= charlie_n <= 5_000, f"charlie scans out of range : {charlie_n}"

        diane_n = count_scans(_DIANE_ID)
        assert 30 <= diane_n <= 100, f"diane scans out of range : {diane_n}"

        eve_n = count_scans(_EVE_ID)
        assert eve_n == 140, f"eve must have EXACTLY 140 scans, got {eve_n}"

    def test_scan_type_invariants(self, run_seed, db_connection) -> None:
        """Bug 6 CHECKs are respected — exercised by INSERT'ing many rows."""
        run_seed()

        # manual scans : product_ean NOT NULL + scanned_name IS NULL
        bad_manual = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM scans WHERE scan_type = 'manual' "
                "AND (product_ean IS NULL OR scanned_name IS NOT NULL)"
            )
        ).scalar()
        assert bad_manual == 0, f"{bad_manual} manual scans violate Bug 6 CHECK"

        # receipt scans : receipt_id NOT NULL ; non-receipt : receipt_id IS NULL
        bad_receipt = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM scans WHERE "
                "(scan_type = 'receipt' AND receipt_id IS NULL) OR "
                "(scan_type <> 'receipt' AND receipt_id IS NOT NULL)"
            )
        ).scalar()
        assert bad_receipt == 0, f"{bad_receipt} scans violate receipt_required CHECK"

    def test_bob_scan_breakdown(self, run_seed, db_connection) -> None:
        """Bob has 47 receipts + 23 e-labels + 3 manual (per ARCH § dev_bob)."""
        run_seed()

        n_receipts = db_connection.execute(
            text("SELECT COUNT(*) FROM receipts WHERE user_id = :u"),
            {"u": _BOB_ID},
        ).scalar()
        assert n_receipts == 47, f"bob should have 47 receipts, got {n_receipts}"

        n_labels = db_connection.execute(
            text("SELECT COUNT(*) FROM scans WHERE user_id = :u AND scan_type = 'electronic_label'"),
            {"u": _BOB_ID},
        ).scalar()
        assert n_labels == 23, f"bob should have 23 e-label scans, got {n_labels}"

        n_manual = db_connection.execute(
            text("SELECT COUNT(*) FROM scans WHERE user_id = :u AND scan_type = 'manual'"),
            {"u": _BOB_ID},
        ).scalar()
        assert n_manual == 3, f"bob should have 3 manual scans, got {n_manual}"

    def test_charlie_312_receipts(self, run_seed, db_connection) -> None:
        """Charlie has exactly 312 receipts (309 bulk + 3 scenarios)."""
        run_seed()
        n_receipts = db_connection.execute(
            text("SELECT COUNT(*) FROM receipts WHERE user_id = :u"),
            {"u": _CHARLIE_ID},
        ).scalar()
        assert n_receipts == 312, f"charlie should have 312 receipts, got {n_receipts}"

    def test_diane_13_receipts(self, run_seed, db_connection) -> None:
        """Diane has 13 receipts (preserved pre-DELETE)."""
        run_seed()
        n_receipts = db_connection.execute(
            text("SELECT COUNT(*) FROM receipts WHERE user_id = :u"),
            {"u": _DIANE_ID},
        ).scalar()
        assert n_receipts == 13, f"diane should have 13 receipts, got {n_receipts}"

    def test_price_consensus_present(self, run_seed, db_connection) -> None:
        """price_consensus has rows — drives the demo dashboard."""
        run_seed()
        n_consensus = db_connection.execute(text("SELECT COUNT(*) FROM price_consensus")).scalar()
        assert n_consensus >= 20, f"expected ≥20 price_consensus rows for demo coverage, got {n_consensus}"

        # Every consensus must have ≥1 link in price_consensus_scans (we drop
        # consensus rows with fewer than 2 agreeing votes upstream).
        orphans = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM price_consensus pc "
                "WHERE NOT EXISTS (SELECT 1 FROM price_consensus_scans pcs "
                "WHERE pcs.consensus_id = pc.id)"
            )
        ).scalar()
        assert orphans == 0, f"{orphans} price_consensus rows have no scan link"

    def test_cab_credits_per_persona(self, run_seed, db_connection) -> None:
        """CAB credits : bob/charlie YES, eve NO (shadow-banned silent skip)."""
        run_seed()

        bob_credits = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM cabecoin_transactions WHERE user_id = :u "
                "AND direction = 'credit' AND reference_type = 'scan'"
            ),
            {"u": _BOB_ID},
        ).scalar()
        assert bob_credits > 0, "bob must have CAB scan-credits"

        charlie_credits = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM cabecoin_transactions WHERE user_id = :u "
                "AND direction = 'credit' AND reference_type = 'scan'"
            ),
            {"u": _CHARLIE_ID},
        ).scalar()
        assert charlie_credits > 100, f"charlie should have many CAB credits, got {charlie_credits}"

        eve_credits = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM cabecoin_transactions WHERE user_id = :u "
                "AND direction = 'credit' AND reference_type = 'scan'"
            ),
            {"u": _EVE_ID},
        ).scalar()
        assert eve_credits == 0, f"eve is shadow-banned — must have 0 CAB scan-credits, got {eve_credits}"


class TestWave3NarrativeScenarios:
    """The 10 hardcoded narrative scenarios (ARCH § Roadmap line 310)."""

    # Deterministic UUIDs assigned in scripts/seed/scans.py — keep in sync.
    OCR_BORDERLINE_R = "11111111-1111-1111-1111-000000000001"
    UNMATCHED_R = "11111111-1111-1111-1111-000000000002"
    REJECTED_R = "11111111-1111-1111-1111-000000000003"
    PENDING_FRESH_R = "11111111-1111-1111-1111-000000000004"
    BIG_RECEIPT_R = "11111111-1111-1111-1111-000000000005"
    BATTLEPASS_R = "11111111-1111-1111-1111-000000000006"
    REFERRAL_R = "11111111-1111-1111-1111-000000000007"
    DUP_EVE_R1 = "11111111-1111-1111-1111-000000000008"
    DUP_EVE_R2 = "11111111-1111-1111-1111-000000000088"
    GEO_OUTLIER_R = "11111111-1111-1111-1111-000000000009"
    EAN_MISMATCH_R = "11111111-1111-1111-1111-00000000000a"

    def test_scenario_1_ocr_borderline(self, run_seed, db_connection) -> None:
        run_seed()
        row = db_connection.execute(
            text("SELECT user_id, store_status, purchased_at_with_time FROM receipts WHERE id = :id"),
            {"id": self.OCR_BORDERLINE_R},
        ).first()
        assert row is not None, "OCR borderline receipt must exist"
        assert str(row.user_id) == _BOB_ID
        # Borderline OCR : NULL purchased_at_with_time + pending store_status.
        assert row.purchased_at_with_time is None
        assert row.store_status == "pending"

    def test_scenario_2_unmatched(self, run_seed, db_connection) -> None:
        run_seed()
        row = db_connection.execute(
            text("SELECT status FROM scans WHERE receipt_id = :id"),
            {"id": self.UNMATCHED_R},
        ).first()
        assert row is not None, "unmatched scenario scan must exist"
        assert row.status == "unmatched"

    def test_scenario_3_rejected(self, run_seed, db_connection) -> None:
        run_seed()
        row = db_connection.execute(
            text("SELECT status, rejected_reason FROM scans WHERE receipt_id = :id"),
            {"id": self.REJECTED_R},
        ).first()
        assert row is not None
        assert row.status == "rejected"
        assert row.rejected_reason is not None

    def test_scenario_4_pending_fresh(self, run_seed, db_connection) -> None:
        run_seed()
        row = db_connection.execute(
            text("SELECT status, scanned_at FROM scans WHERE receipt_id = :id"),
            {"id": self.PENDING_FRESH_R},
        ).first()
        assert row is not None
        assert row.status == "pending"

    def test_scenario_5_big_receipt(self, run_seed, db_connection) -> None:
        """30+ items receipt — must have ≥30 line scans."""
        run_seed()
        line_count = db_connection.execute(
            text("SELECT COUNT(*) FROM scans WHERE receipt_id = :id"),
            {"id": self.BIG_RECEIPT_R},
        ).scalar()
        assert line_count >= 30, f"big receipt should have ≥30 line scans, got {line_count}"

    def test_scenario_6_battlepass_tier_up(self, run_seed, db_connection) -> None:
        """Battlepass tier-up trigger : cabecoin_transactions.context tagged."""
        run_seed()
        row = db_connection.execute(
            text(
                "SELECT context FROM cabecoin_transactions ct "
                "JOIN scans s ON s.id = ct.reference_id "
                "WHERE s.receipt_id = :id AND ct.reference_type = 'scan'"
            ),
            {"id": self.BATTLEPASS_R},
        ).first()
        assert row is not None, "battlepass scenario CAB credit must exist"
        assert row.context is not None
        assert row.context.get("scenario") == "battlepass_tier_up"

    def test_scenario_7_referral_first_scan(self, run_seed, db_connection) -> None:
        run_seed()
        row = db_connection.execute(
            text(
                "SELECT context FROM cabecoin_transactions ct "
                "JOIN scans s ON s.id = ct.reference_id "
                "WHERE s.receipt_id = :id AND ct.reference_type = 'scan'"
            ),
            {"id": self.REFERRAL_R},
        ).first()
        assert row is not None
        assert row.context is not None
        assert row.context.get("scenario") == "referral_first_scan"

    def test_scenario_8_duplicate_flagrant_eve(self, run_seed, db_connection) -> None:
        """2 eve receipts at same store + same total within 30min."""
        run_seed()
        rows = db_connection.execute(
            text(
                "SELECT id, store_id, total_amount, purchased_at_with_time "
                "FROM receipts WHERE id IN (:r1, :r2) ORDER BY purchased_at_with_time"
            ),
            {"r1": self.DUP_EVE_R1, "r2": self.DUP_EVE_R2},
        ).fetchall()
        assert len(rows) == 2, "must have BOTH duplicate receipts"
        assert rows[0].store_id == rows[1].store_id, "same store"
        assert rows[0].total_amount == rows[1].total_amount, "same total"
        delta = rows[1].purchased_at_with_time - rows[0].purchased_at_with_time
        assert delta.total_seconds() <= 30 * 60, f"duplicates must be within 30min, got {delta}"

    def test_scenario_9_geo_outlier_eve(self, run_seed, db_connection) -> None:
        """Eve has at least one receipt at store #12 (10km away)."""
        run_seed()
        store12_id = "00000000-0000-0000-0002-00000000000c"
        n_eve_at_12 = db_connection.execute(
            text("SELECT COUNT(*) FROM receipts WHERE user_id = :u AND store_id = :s"),
            {"u": _EVE_ID, "s": store12_id},
        ).scalar()
        assert n_eve_at_12 >= 1, f"eve must have ≥1 receipt at store #12, got {n_eve_at_12}"

    def test_scenario_10_ean_mismatch_eve(self, run_seed, db_connection) -> None:
        """EAN consensus mismatch scenario receipt + scan exists."""
        run_seed()
        row = db_connection.execute(
            text("SELECT user_id, product_ean FROM scans WHERE receipt_id = :id"),
            {"id": self.EAN_MISMATCH_R},
        ).first()
        assert row is not None
        assert str(row.user_id) == _EVE_ID
        # Eve voted EAN #1 (mismatch) vs the canonical #0.
        assert row.product_ean == "3228857000852"  # _FOOD_EANS[1] from products.py


class TestWave3Idempotency:
    """Re-running main() with scans already seeded is a strict no-op."""

    def test_scans_idempotent(self, run_seed, db_connection) -> None:
        """Second main() call must NOT mutate scan / receipt / consensus row counts."""
        run_seed()
        first = {
            "scans": db_connection.execute(text("SELECT COUNT(*) FROM scans")).scalar(),
            "receipts": db_connection.execute(text("SELECT COUNT(*) FROM receipts")).scalar(),
            "consensus": db_connection.execute(text("SELECT COUNT(*) FROM price_consensus")).scalar(),
            "consensus_links": db_connection.execute(text("SELECT COUNT(*) FROM price_consensus_scans")).scalar(),
            "cab_tx": db_connection.execute(text("SELECT COUNT(*) FROM cabecoin_transactions")).scalar(),
        }
        # Second run — short-circuits via _already_seeded().
        run_seed()
        second = {
            "scans": db_connection.execute(text("SELECT COUNT(*) FROM scans")).scalar(),
            "receipts": db_connection.execute(text("SELECT COUNT(*) FROM receipts")).scalar(),
            "consensus": db_connection.execute(text("SELECT COUNT(*) FROM price_consensus")).scalar(),
            "consensus_links": db_connection.execute(text("SELECT COUNT(*) FROM price_consensus_scans")).scalar(),
            "cab_tx": db_connection.execute(text("SELECT COUNT(*) FROM cabecoin_transactions")).scalar(),
        }
        assert first == second, f"scans seed not idempotent : {first} → {second}"


class TestWave4Monetization:
    """Wave 4 — subscriptions + gift cards + cashback withdrawals."""

    def test_subscriptions_seeded(self, run_seed, db_connection) -> None:
        """5 subscriptions : 4 charlie (1 active + 1 cancelled + 2 expired) + 1 alice pending."""
        run_seed()

        n_total = db_connection.execute(text("SELECT COUNT(*) FROM subscriptions")).scalar()
        assert n_total == 5, f"expected 5 subscriptions, got {n_total}"

        # Per-persona breakdown
        n_charlie = db_connection.execute(
            text("SELECT COUNT(*) FROM subscriptions WHERE user_id = :u"),
            {"u": _CHARLIE_ID},
        ).scalar()
        assert n_charlie == 4, f"charlie should have 4 subscriptions, got {n_charlie}"

        n_alice = db_connection.execute(
            text("SELECT COUNT(*) FROM subscriptions WHERE user_id = :u"),
            {"u": _ALICE_ID},
        ).scalar()
        assert n_alice == 1, f"alice should have 1 (trial) subscription, got {n_alice}"

        # Status distribution
        statuses = dict(db_connection.execute(text("SELECT status, COUNT(*) FROM subscriptions GROUP BY status")).all())
        # 1 active (charlie current monthly), 1 cancelled (charlie),
        # 2 expired (charlie annual + charlie monthly), 1 pending (alice trial)
        assert statuses.get("active") == 1, f"expected 1 active, got {statuses}"
        assert statuses.get("cancelled") == 1, f"expected 1 cancelled, got {statuses}"
        assert statuses.get("expired") == 2, f"expected 2 expired, got {statuses}"
        assert statuses.get("pending") == 1, f"expected 1 pending (alice trial), got {statuses}"

    def test_subscription_payment_ref_coherence(self, run_seed, db_connection) -> None:
        """Every seeded subscription respects ``payment_ref_coherence`` CHECK.

        Predicate (mirrored from PG) :
            paid_with = 'cashback'
            OR payment_ref IS NOT NULL
            OR status NOT IN ('active', 'expired')

        Active + expired must carry a payment_ref ; pending (trial) is allowed
        to have NULL.
        """
        run_seed()
        bad = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM subscriptions "
                "WHERE NOT ("
                "  paid_with = 'cashback' "
                "  OR payment_ref IS NOT NULL "
                "  OR status NOT IN ('active', 'expired')"
                ")"
            )
        ).scalar()
        assert bad == 0, f"{bad} seeded subscriptions violate payment_ref_coherence"

    def test_gift_card_orders_charlie(self, run_seed, db_connection) -> None:
        """Charlie has 8 gift_card_orders : 5 referral_reward (3 issued + 2 pending) + 3 shop_purchase."""
        run_seed()

        n_charlie = db_connection.execute(
            text("SELECT COUNT(*) FROM gift_card_orders WHERE user_id = :u"),
            {"u": _CHARLIE_ID},
        ).scalar()
        assert n_charlie == 8, f"charlie should have 8 gift_card_orders, got {n_charlie}"

        # source_type breakdown
        by_source = dict(
            db_connection.execute(
                text("SELECT source_type, COUNT(*) FROM gift_card_orders WHERE user_id = :u GROUP BY source_type"),
                {"u": _CHARLIE_ID},
            ).all()
        )
        assert by_source.get("referral_reward") == 5, f"5 referral_reward gift cards expected, got {by_source}"
        assert by_source.get("shop_purchase") == 3, (
            f"3 shop_purchase (cashback redemption) gift cards expected, got {by_source}"
        )

        # 2 referral cards must still be in 30-day cooldown (pending +
        # eligible_at > now) — KP-07-bis.
        n_cooldown = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM gift_card_orders "
                "WHERE user_id = :u AND source_type = 'referral_reward' "
                "AND status = 'pending' AND eligible_at > now()"
            ),
            {"u": _CHARLIE_ID},
        ).scalar()
        assert n_cooldown == 2, f"expected 2 referral gift cards in cooldown, got {n_cooldown}"

        # 3 referral cards must be eligible (issued + eligible_at in past).
        n_eligible = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM gift_card_orders "
                "WHERE user_id = :u AND source_type = 'referral_reward' "
                "AND status = 'issued' AND eligible_at <= now()"
            ),
            {"u": _CHARLIE_ID},
        ).scalar()
        assert n_eligible == 3, f"expected 3 eligible (issued) referral gift cards, got {n_eligible}"

        # Charlie's gift_card_redeemed_ytd_cents denorm bumped from cashback
        # redemptions (20€ + 15€ + 50€ = 8500c).
        ytd = db_connection.execute(
            text("SELECT gift_card_redeemed_ytd_cents FROM users WHERE id = :u"),
            {"u": _CHARLIE_ID},
        ).scalar()
        assert ytd == 8500, f"charlie YTD gift card redeem should be 8500c, got {ytd}"

    def test_gift_card_idempotency_unique_constraint(self, run_seed, db_connection) -> None:
        """UNIQUE(source_type, source_ref_id) — every seeded source_ref_id is distinct."""
        run_seed()
        dupes = db_connection.execute(
            text(
                "SELECT source_type, source_ref_id, COUNT(*) "
                "FROM gift_card_orders "
                "GROUP BY source_type, source_ref_id HAVING COUNT(*) > 1"
            )
        ).all()
        assert not dupes, f"gift_card_orders has duplicate (source_type,source_ref_id) : {dupes}"

    def test_cashback_withdrawals_distribution(self, run_seed, db_connection) -> None:
        """5 withdrawals : 3 charlie (processed/pending/failed) + 2 diane (processed/abandoned)."""
        run_seed()

        n_total = db_connection.execute(text("SELECT COUNT(*) FROM cashback_withdrawals")).scalar()
        assert n_total == 5, f"expected 5 withdrawals, got {n_total}"

        # Charlie : 1 processed + 1 pending + 1 failed
        charlie_by_status = dict(
            db_connection.execute(
                text("SELECT status, COUNT(*) FROM cashback_withdrawals WHERE user_id = :u GROUP BY status"),
                {"u": _CHARLIE_ID},
            ).all()
        )
        assert charlie_by_status.get("processed") == 1, f"charlie processed=1 expected, got {charlie_by_status}"
        assert charlie_by_status.get("pending") == 1, f"charlie pending=1 expected, got {charlie_by_status}"
        assert charlie_by_status.get("failed") == 1, f"charlie failed=1 expected, got {charlie_by_status}"

        # Diane : 1 processed (preserved pre-DELETE) + 1 abandoned (post-DELETE)
        diane_by_status = dict(
            db_connection.execute(
                text("SELECT status, COUNT(*) FROM cashback_withdrawals WHERE user_id = :u GROUP BY status"),
                {"u": _DIANE_ID},
            ).all()
        )
        assert diane_by_status.get("processed") == 1, (
            f"diane processed=1 expected (pre-DELETE preserved), got {diane_by_status}"
        )
        assert diane_by_status.get("abandoned") == 1, f"diane abandoned=1 expected (post-DELETE), got {diane_by_status}"

    def test_withdrawal_check_constraints(self, run_seed, db_connection) -> None:
        """Seeded withdrawals respect every PG CHECK (failure / processed / provider coherence)."""
        run_seed()

        # failure_check : status='failed' ↔ failure_reason IS NOT NULL
        bad_failure = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM cashback_withdrawals WHERE "
                "(status = 'failed' AND failure_reason IS NULL) OR "
                "(status <> 'failed' AND failure_reason IS NOT NULL)"
            )
        ).scalar()
        assert bad_failure == 0, f"{bad_failure} withdrawals violate failure_check"

        # processed_check : status='processed' ↔ processed_at IS NOT NULL
        bad_processed = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM cashback_withdrawals WHERE "
                "(status = 'processed' AND processed_at IS NULL) OR "
                "(status <> 'processed' AND processed_at IS NOT NULL)"
            )
        ).scalar()
        assert bad_processed == 0, f"{bad_processed} withdrawals violate processed_check"

        # provider_coherence : ref + initiated_at both NULL or both NOT NULL
        bad_provider = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM cashback_withdrawals WHERE "
                "(payment_provider_ref IS NOT NULL AND provider_initiated_at IS NULL) OR "
                "(payment_provider_ref IS NULL AND provider_initiated_at IS NOT NULL)"
            )
        ).scalar()
        assert bad_provider == 0, f"{bad_provider} withdrawals violate provider_coherence"

        # transaction_required : status='processed' → cashback_transaction_id NOT NULL
        bad_tx_req = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM cashback_withdrawals WHERE "
                "status = 'processed' AND cashback_transaction_id IS NULL"
            )
        ).scalar()
        assert bad_tx_req == 0, f"{bad_tx_req} processed withdrawals miss cashback_transaction_id"

    def test_charlie_balances_preserved(self, run_seed, db_connection) -> None:
        """Wave 4 must NOT mutate charlie's CAB/cashback balances (set by Wave 2/3).

        ARCH spec : charlie post-Wave 4 still shows 8.20€ cashback + 47 500 CAB.
        """
        run_seed()
        cab = db_connection.execute(
            text("SELECT balance FROM user_cab_balance WHERE user_id = :u"),
            {"u": _CHARLIE_ID},
        ).scalar()
        assert cab == 47_500, f"charlie CAB should be 47500, got {cab}"

        cashback = db_connection.execute(
            text("SELECT balance FROM user_cashback_balance WHERE user_id = :u"),
            {"u": _CHARLIE_ID},
        ).scalar()
        assert cashback == 820, f"charlie cashback should be 820c (8.20€), got {cashback}"

    def test_alice_trial_visible(self, run_seed, db_connection) -> None:
        """Alice has exactly 1 subscription in trial state (status='pending', payment_ref NULL)."""
        run_seed()
        row = db_connection.execute(
            text("SELECT status, plan, payment_ref, expires_at FROM subscriptions WHERE user_id = :u"),
            {"u": _ALICE_ID},
        ).first()
        assert row is not None
        assert row.status == "pending", f"alice subscription status should be pending (trial), got {row.status}"
        assert row.plan == "monthly", f"alice trial plan should be monthly, got {row.plan}"
        assert row.payment_ref is None, f"alice trial must have NULL payment_ref, got {row.payment_ref}"

    def test_paired_withdrawal_transactions(self, run_seed, db_connection) -> None:
        """Each non-abandoned withdrawal has a paired WITHDRAWAL cashback_transactions row."""
        run_seed()
        # Charlie has 3 withdrawal rows backed by 3 WITHDRAWAL tx rows.
        n_charlie_tx = db_connection.execute(
            text("SELECT COUNT(*) FROM cashback_transactions WHERE user_id = :u AND type = 'WITHDRAWAL'"),
            {"u": _CHARLIE_ID},
        ).scalar()
        assert n_charlie_tx == 3, f"charlie should have 3 WITHDRAWAL tx rows, got {n_charlie_tx}"

        # Diane : 1 processed → 1 WITHDRAWAL tx ; abandoned has NO tx row
        # (the absorption tx requires a follow-up CHECK widening — out of scope here).
        n_diane_tx = db_connection.execute(
            text("SELECT COUNT(*) FROM cashback_transactions WHERE user_id = :u AND type = 'WITHDRAWAL'"),
            {"u": _DIANE_ID},
        ).scalar()
        assert n_diane_tx == 1, f"diane should have 1 WITHDRAWAL tx row (processed only), got {n_diane_tx}"


class TestWave4Idempotency:
    """Re-running main() with Wave 4 already seeded is a strict no-op."""

    def test_monetization_idempotent(self, run_seed, db_connection) -> None:
        """Second main() call must NOT mutate sub / gift_card / withdrawal counts."""
        run_seed()
        first = {
            "subs": db_connection.execute(text("SELECT COUNT(*) FROM subscriptions")).scalar(),
            "gift_cards": db_connection.execute(text("SELECT COUNT(*) FROM gift_card_orders")).scalar(),
            "withdrawals": db_connection.execute(text("SELECT COUNT(*) FROM cashback_withdrawals")).scalar(),
            "cashback_tx": db_connection.execute(text("SELECT COUNT(*) FROM cashback_transactions")).scalar(),
        }
        run_seed()
        second = {
            "subs": db_connection.execute(text("SELECT COUNT(*) FROM subscriptions")).scalar(),
            "gift_cards": db_connection.execute(text("SELECT COUNT(*) FROM gift_card_orders")).scalar(),
            "withdrawals": db_connection.execute(text("SELECT COUNT(*) FROM cashback_withdrawals")).scalar(),
            "cashback_tx": db_connection.execute(text("SELECT COUNT(*) FROM cashback_transactions")).scalar(),
        }
        assert first == second, f"monetization seed not idempotent : {first} → {second}"


# ============================================================
# Wave 5 — wipe-and-reseed + product_knowledge + safety
# ============================================================
class TestWave5ProductKnowledge:
    """Wave 5 — 10 OCR auto-learn samples (deferred from Wave 3)."""

    def test_product_knowledge_seeded(self, run_seed, db_connection) -> None:
        """10 rows total : 5 confirmed (corrected NOT NULL) + 5 unconfirmed."""
        run_seed()

        n_total = db_connection.execute(text("SELECT COUNT(*) FROM ocr_knowledge")).scalar()
        assert n_total == 10, f"expected 10 ocr_knowledge rows, got {n_total}"

        n_confirmed = db_connection.execute(
            text("SELECT COUNT(*) FROM ocr_knowledge WHERE corrected IS NOT NULL")
        ).scalar()
        assert n_confirmed == 5, f"expected 5 confirmed corrections, got {n_confirmed}"

        n_unconfirmed = db_connection.execute(
            text("SELECT COUNT(*) FROM ocr_knowledge WHERE corrected IS NULL")
        ).scalar()
        assert n_unconfirmed == 5, f"expected 5 unconfirmed (manual queue) entries, got {n_unconfirmed}"

    def test_product_knowledge_sources_respect_check(self, run_seed, db_connection) -> None:
        """Every seeded row passes the ``ck_ocr_knowledge_source`` CHECK.

        Valid values per the model (see ratis_core.models.product.OcrKnowledge) :
        ``ocr_arbitrage`` / ``user_correction`` / ``manual`` / ``llm``.
        """
        run_seed()
        bad = db_connection.execute(
            text(
                "SELECT COUNT(*) FROM ocr_knowledge "
                "WHERE source NOT IN ('ocr_arbitrage', 'user_correction', 'manual', 'llm')"
            )
        ).scalar()
        assert bad == 0, f"{bad} seeded rows violate ck_ocr_knowledge_source"

    def test_product_knowledge_idempotent(self, run_seed, db_connection) -> None:
        """Second main() call must NOT duplicate the 10 rows."""
        run_seed()
        first = db_connection.execute(text("SELECT COUNT(*) FROM ocr_knowledge")).scalar()
        run_seed()
        second = db_connection.execute(text("SELECT COUNT(*) FROM ocr_knowledge")).scalar()
        assert first == second == 10, f"product_knowledge seed not idempotent : {first} → {second}"


class TestWave5WipeAndReseed:
    """Wave 5 — `wipe_all` produces a clean slate ; reseed reproduces state."""

    def test_wipe_then_rebuild_produces_identical_state(
        self, run_seed, db_connection, seed_migrated_db_url, monkeypatch
    ) -> None:
        """Capture counts → wipe_all → reseed → counts must match (modulo
        non-deterministic timestamps which we don't compare here).

        This is the integrity check the brief asked for : ``seed-wipe`` +
        ``seed-rebuild`` should be observably equivalent to a fresh ``seed-
        db-init`` + ``seed-rebuild``.
        """
        # First seed pass — establishes the reference state.
        run_seed()
        ref_counts = _full_seed_counts(db_connection)
        # Release the SELECT-implicit AccessShareLock so TRUNCATE doesn't hang
        # — db_connection is reused after wipe for assertions.
        db_connection.rollback()

        # Wipe via the public API (mirrors `make seed-wipe` semantics).
        monkeypatch.setenv("DATABASE_URL", seed_migrated_db_url)
        monkeypatch.setenv("ENVIRONMENT", "seed")
        from scripts.seed import _engine
        from scripts.seed.wipe import wipe_all

        # Re-init engine pointed at the test DB (already fresh from the
        # fixture, but we keep this consistent with the run_seed fixture).
        _engine._engine = None
        _engine._SessionLocal = None
        session = _engine.get_session()
        try:
            wipe_all(session)
            session.commit()
        finally:
            session.close()

        # After wipe — every seeded table must be empty. `users` is scoped
        # to ``provider='dev'`` in _full_seed_counts (migration sentinels +
        # cross-test smoke users are out of the wipe contract).
        post_wipe = _full_seed_counts(db_connection)
        for table, count in post_wipe.items():
            assert count == 0, f"post-wipe {table} should be 0, got {count}"
        # Release the snapshot again before the re-seed inserts.
        db_connection.rollback()

        # Re-run main() — must reproduce the reference state.
        run_seed()
        rebuilt_counts = _full_seed_counts(db_connection)
        assert rebuilt_counts == ref_counts, f"wipe+reseed diverged from reference : {ref_counts} → {rebuilt_counts}"

    def test_wipe_safety_guards_production_aborts(self, monkeypatch) -> None:
        """``wipe_all`` aborts under DA-5 production conditions BEFORE any SQL."""
        from scripts.seed.wipe import _check_safety_guards

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://ratis:ratis@localhost:5432/ratis_seed",  # pragma: allowlist secret
        )
        with pytest.raises(RuntimeError, match="production"):
            _check_safety_guards()

    def test_wipe_safety_guards_unsafe_url_aborts(self, monkeypatch) -> None:
        """``wipe_all`` aborts when ``DATABASE_URL`` lacks ``_seed``/``_dev``."""
        from scripts.seed.wipe import _check_safety_guards

        monkeypatch.setenv("ENVIRONMENT", "seed")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://ratis:ratis@prod:5432/ratis_prod",  # pragma: allowlist secret
        )
        with pytest.raises(RuntimeError, match="_seed.*_dev|_dev.*_seed|_seed|_dev"):
            _check_safety_guards()

    def test_wipe_safety_guards_database_url_missing_aborts(self, monkeypatch) -> None:
        """``wipe_all`` aborts when ``DATABASE_URL`` is unset."""
        from scripts.seed.wipe import _check_safety_guards

        monkeypatch.setenv("ENVIRONMENT", "seed")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(RuntimeError, match="DATABASE_URL not set"):
            _check_safety_guards()


def _full_seed_counts(db_connection) -> dict[str, int]:
    """Capture row counts of every table the seed pipeline touches.

    Used by ``TestWave5WipeAndReseed`` to assert wipe+reseed reproduces the
    exact reference state. Listed in the same order as
    :data:`scripts.seed.wipe.SEEDED_TABLES` for visual diffing.

    Note : ``users`` is scoped to ``provider='dev'`` only — the persona
    population the seed pipeline owns. Migration-injected sentinels
    (``provider='internal'``) plus any cross-test smoke users
    (``dev_smoketest@…``) are out of the wipe contract and would otherwise
    pollute the comparison.
    """
    tables = (
        "cashback_transactions",
        "cashback_withdrawals",
        "gift_card_orders",
        "subscriptions",
        "cabecoin_transactions",
        "price_consensus_scans",
        "price_consensus_history",
        "price_consensus",
        "scans",
        "receipts",
        "user_cashback_balance",
        "user_cab_balance",
        "admin_settings_audit",
        "ocr_knowledge",
        "products",
        "stores",
    )
    counts = {t: db_connection.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables}
    # Scope to the 6 canonical persona UUIDs (deterministic by design — see
    # `scripts/seed/users.py`). Avoids picking up cross-test smoke users like
    # `dev_smoketest@ratis.app` that some earlier tests in the same module
    # create against the shared module-scoped DB.
    counts["users"] = db_connection.execute(
        text("""
            SELECT COUNT(*) FROM users
             WHERE id IN (
                '00000000-0000-0000-0000-00000000000a',
                '00000000-0000-0000-0000-00000000000b',
                '00000000-0000-0000-0000-00000000000c',
                '00000000-0000-0000-0000-00000000000d',
                '00000000-0000-0000-0000-00000000000e',
                '00000000-0000-0000-0000-0000000000ad'
             )
        """)
    ).scalar()
    return counts
