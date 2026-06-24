"""install cube + earthdistance PG extensions for fuzzy radius lookups

Revision ID: 20260511_0900_pg_earthdistance
Revises: 20260510_2200_sirene_schema
Create Date: 2026-05-11 09:00:00.000000

SIRENE PR2 prerequisite — the PR2 shared helper ``batch/_shared/store_consolidation.py``
``find_match()`` fuzzy radius branch executes ``earth_distance(ll_to_earth(...), ll_to_earth(...))``
which is provided by PostgreSQL contrib modules ``cube`` and ``earthdistance``.

Audit reference : ``docs/audits/2026-05-10-deep-audit-sirene-foundation.md`` § F-10 (PR2 BLOCKER).
The plan PR2 § Pitfalls flagged this as "vérifier sinon ajouter dans PR1", but PR1 was merged
without the fix → bundled here as the first commit of PR2 instead.

Risk : nil. Both extensions are stock contrib modules shipped with every supported
PostgreSQL version (≥12). ``CREATE EXTENSION IF NOT EXISTS`` is idempotent so the
migration is safe on environments where they happen to be pre-installed.

Downgrade : ``DROP EXTENSION`` only if no other DB object depends on them. The
downgrade keeps the ``IF EXISTS`` guard for symmetry but should not normally be
exercised — extensions are environment-level resources, not application schema.
"""

from __future__ import annotations

from alembic import op


# revision identifiers (≤32 chars per R-DB-08).
revision = "20260511_0900_pg_earthdistance"
down_revision = "20260510_2200_sirene_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Order matters : earthdistance depends on cube.
    op.execute("CREATE EXTENSION IF NOT EXISTS cube")
    op.execute("CREATE EXTENSION IF NOT EXISTS earthdistance")


def downgrade() -> None:
    # Reverse order : earthdistance first (it depends on cube).
    # CASCADE intentionally NOT used — if other objects depend on these
    # extensions, downgrade should fail loudly rather than silently drop.
    op.execute("DROP EXTENSION IF EXISTS earthdistance")
    op.execute("DROP EXTENSION IF EXISTS cube")
