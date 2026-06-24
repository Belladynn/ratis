"""Seed orchestrator entry point — populates `ratis_seed` DB with personas.

CLI : `python -m scripts.seed.main`

Wave roadmap :

    Wave 1  → safety guards + skeleton orchestration ✅
    Wave 2  → users / stores / products ✅
    Wave 3  → scans ✅
    Wave 4  → monetization ✅
    Wave 5  → `make seed-wipe` + product_knowledge + operator docs ✅

See `ARCH_seed_test_data.md` § Roadmap for full sequencing.

Safety guards (DA-5) — abort BEFORE touching the DB if :
- `ENVIRONMENT == "production"` (primary signal)
- `DATABASE_URL` does not contain `_seed` or `_dev` substring (defense-in-depth)
"""

from __future__ import annotations

import os
import sys

from scripts.seed import (
    _engine,
    monetization,
    product_knowledge,
    products,
    scans,
    stores,
    users,
)


def _check_safety_guards() -> None:
    """Raise RuntimeError if the runtime smells like production. DA-5."""
    env = os.environ.get("ENVIRONMENT", "").strip().lower()
    if env == "production":
        raise RuntimeError("Seed scripts NEVER run in production (ENVIRONMENT=production).")

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set — refusing to run seed.")
    if "_seed" not in url and "_dev" not in url:
        raise RuntimeError(
            f"DATABASE_URL must contain '_seed' or '_dev' substring — refusing to run seed against {url!r}."
        )


def main() -> None:
    """Orchestrate the seed pipeline.

    Wave 2 wires a real SQLAlchemy ``Session`` ; domain modules now mutate
    the DB. The session commits once at the end so re-running the script
    is observably atomic (all-or-nothing).
    """
    _check_safety_guards()

    print("[seed] start")
    session = _engine.get_session()
    try:
        # Foundation : independent ordering (users / stores / products can
        # be inserted in any order). Wave 3+ scans / monetization will
        # depend on these.
        stores.seed_stores(session)
        products.seed_products(session)
        users.seed_users(session)
        # Wave 3 / 4 / 5 — scans → monetization → product_knowledge.
        # product_knowledge depends on no other seeded table (the curation
        # dictionary is independent of users/products), but we run it last
        # so its log line surfaces after the rest of the pipeline.
        scans.seed_scans(session)
        monetization.seed_monetization(session)
        product_knowledge.seed_product_knowledge(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    print("[seed] done")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"[seed] aborted : {exc}", file=sys.stderr)
        sys.exit(1)
