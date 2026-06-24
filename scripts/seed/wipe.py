"""Wipe-and-reseed support — ``make seed-wipe`` target.

Provides :func:`wipe_all` which ``TRUNCATE ... RESTART IDENTITY CASCADE`` the
tables that the seed pipeline populates, so a subsequent ``seed-rebuild``
starts from a known-empty state without dropping the database.

Use case : *« I changed a persona definition, give me a fresh state. »* —
faster than ``DROP DATABASE`` + ``alembic upgrade head`` (no schema replay,
no migration runtime cost).

Safety :
- DA-5 guards (``ENVIRONMENT == 'production'`` OR ``DATABASE_URL`` doesn't
  contain ``_seed``/``_dev``) abort the wipe BEFORE any SQL runs. Same
  contract as :mod:`scripts.seed.main`.
- The TRUNCATE statement uses ``CASCADE`` to follow FK chains automatically
  — any table referencing one of the seeded tables (e.g. ``scans`` →
  ``cabecoin_transactions.reference_id``) is also cleared, ensuring no
  orphan rows survive. Listed in safe dependency order anyway for
  observability + defensive belt-and-suspenders.

Idempotency : running ``wipe_all`` twice in a row is a no-op (TRUNCATE on
empty tables is harmless). The companion test (``test_wipe_then_rebuild
_produces_identical_state``) asserts that a wipe followed by a rebuild
produces the same row counts as a fresh ``seed-db-init`` + ``seed-rebuild``.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import text
from sqlalchemy.orm import Session

# Tables touched by the seed pipeline, listed in dependency order
# (most-dependent first → least-dependent last). TRUNCATE ... CASCADE
# would tolerate any order, but listing them explicitly is :
# 1. self-documenting (you can read this file and know exactly what
#    the seed touches), and
# 2. defensive in case CASCADE is ever toggled off for compliance reasons.
#
# Ordering rationale (top → bottom = leaf → root) :
#   - cashback_transactions FK → cashback_withdrawals (RESTRICT — must come first)
#   - cashback_withdrawals    FK → users
#   - gift_card_orders        FK → users
#   - subscriptions           FK → users
#   - cabecoin_transactions   FK → users / scans (SET NULL but truncate anyway)
#   - price_consensus_scans   FK → price_consensus (CASCADE)
#   - price_consensus_history FK → price_consensus
#   - price_consensus         FK → stores / products
#   - scans                   FK → receipts / users / products / stores
#   - receipts                FK → users / stores
#   - user_cashback_balance   FK → users
#   - user_cab_balance        FK → users
#   - admin_settings_audit    (no FK — orphan-safe)
#   - users                   (root)
#   - products                (root)
#   - stores                  (root)
#   - ocr_knowledge           (no FK to anything we seed)
SEEDED_TABLES: tuple[str, ...] = (
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
    "users",
    "products",
    "stores",
)


def _check_safety_guards() -> None:
    """Mirror of :func:`scripts.seed.main._check_safety_guards`.

    Re-implemented (not imported) to keep ``wipe_all`` self-contained — a
    wipe is *more* destructive than a seed, so failing safe is even more
    important. Diverging copies are caught by the
    ``test_wipe_safety_guards`` regression suite.
    """
    env = os.environ.get("ENVIRONMENT", "").strip().lower()
    if env == "production":
        raise RuntimeError("Seed wipe NEVER runs in production (ENVIRONMENT=production).")

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set — refusing to wipe seed.")
    if "_seed" not in url and "_dev" not in url:
        raise RuntimeError(f"DATABASE_URL must contain '_seed' or '_dev' substring — refusing to wipe {url!r}.")


def wipe_all(session: Session) -> None:
    """Clear every seeded table while preserving migration-injected sentinels.

    Safety-guarded — aborts BEFORE any SQL runs if the runtime smells like
    production. Caller owns the ``session.commit()``.

    ``users`` carries two sentinels created by alembic migrations
    (``anon@deleted.invalid`` from ``20260511_1000_rgpd_anon_completeness``
    and ``admin@ratis.internal`` from ``20260501_2000_nrc_d_admin_user``).
    Those rows must survive a wipe because the seed re-run does NOT recreate
    them — they belong to the schema bootstrap layer. We therefore TRUNCATE
    the data tables (CASCADE through FKs) then DELETE only the seeded
    ``account_type='dev'`` rows from ``users``, leaving the sentinels intact.
    """
    _check_safety_guards()

    # All tables except `users` get the TRUNCATE CASCADE treatment — this also
    # cascades to any FK referencing them (e.g. anti-fraud signals on receipts).
    data_tables = tuple(t for t in SEEDED_TABLES if t != "users")
    print(f"[wipe] truncating {len(data_tables)} data tables (CASCADE)…")
    table_list = ", ".join(data_tables)
    session.execute(text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE"))

    # Delete only the persona users — migration sentinels
    # (account_type='internal') are preserved per the bootstrap-vs-seed
    # boundary.
    print("[wipe] deleting dev-account-type users (sentinels preserved)…")
    deleted = session.execute(text("DELETE FROM users WHERE account_type = 'dev' RETURNING id")).rowcount
    session.flush()
    print(f"[wipe] done — {len(data_tables)} tables cleared + {deleted} persona users deleted")


def main() -> None:
    """CLI entry — ``python -m scripts.seed.wipe``.

    Used by ``make seed-wipe`` (which then chains ``seed-rebuild``).
    """
    _check_safety_guards()

    from scripts.seed import _engine

    print("[wipe] start")
    session = _engine.get_session()
    try:
        wipe_all(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    print("[wipe] done")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"[wipe] aborted : {exc}", file=sys.stderr)
        sys.exit(1)
