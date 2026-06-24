"""re-flip 3 product_identification.organic missions to active (Phase C-1).

Revision ID: 20260511_2300_c1org
Revises: 20260511_2200_cbabnd
Create Date: 2026-05-11 23:00:00

Phase C-1 of the missions sprint enriches the PA worker
``trigger_action`` emit with ``qualifier='attribute:organic'`` when the
resolved product is OFF-tagged organic (see
``services/reconciliation_service.py`` + ``services/product_attributes.py``).
With the runtime now able to drive these missions, we re-enable the 3
templates that ``20260509_0100_disqual`` deactivated.

Breakdown of the 3 rows re-flipped here :

  * product_identification + attribute:organic, frequency=daily,
    difficulty=easy
  * product_identification + attribute:organic, frequency=weekly,
    difficulty=easy
  * product_identification + attribute:organic, frequency=weekly,
    difficulty=medium

**Out of scope for this migration :**

  * 3× ``product_identification + attribute:french`` — deferred to
    Phase C-2 (needs ``products.origins_tags`` + OFF re-sync).
  * 3× ``fill_product_field + attribute:organic`` — deferred to
    Phase C-5 (the contribute endpoint emitting ``fill_product_field``
    has not yet shipped). Re-flipping these without the emit site
    would re-introduce the broken-missions UX that the 20260509_0100
    disqual migration eliminated.

Idempotent : the ``AND is_active = FALSE`` guard collapses re-runs to a
no-op once the rows have been flipped. The fail-fast assertion enforces
exactly 3 rows touched on the first apply — anything else means the
catalogue diverged from the canonical seed and a human must triage
before continuing.

Down-migration : reverse to ``is_active = FALSE`` (idempotent guard on
``is_active = TRUE``). Symmetric assertion enforces 3 rows on the
first reverse run.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic. ID kept ≤ 32 chars (R08).
revision = "20260511_2300_c1org"
down_revision = "20260511_2200_cbabnd"
branch_labels = None
depends_on = None


_EXPECTED_ROW_COUNT = 3


def upgrade() -> None:
    """Flip the 3 product_identification.organic templates to
    ``is_active=true``.

    Restricting the predicate to ``action_type='product_identification'``
    is intentional — see module docstring for why fill_product_field
    and attribute:french rows stay disabled in C-1.
    """
    bind = op.get_bind()
    result = bind.execute(
        text(
            "UPDATE missions SET is_active = TRUE "
            "WHERE qualifier = 'attribute:organic' "
            "AND action_type = 'product_identification' "
            "AND is_active = FALSE "
            "RETURNING id"
        )
    )
    rows = result.fetchall()
    n = len(rows)
    # First-apply assertion : exactly 3 rows must have been flipped.
    # On re-run (already-applied prod), 0 rows match the
    # ``is_active=FALSE`` guard which is also acceptable (idempotent
    # no-op). Any value strictly between 0 and 3 — or above 3 — means
    # the catalogue diverged from the canonical seed.
    if n not in (0, _EXPECTED_ROW_COUNT):
        raise AssertionError(
            f"Expected {_EXPECTED_ROW_COUNT} product_identification.organic "
            f"templates to flip from is_active=FALSE to TRUE (or 0 on "
            f"re-run), got {n}. Catalogue may have diverged from the "
            "canonical seed — triage before continuing."
        )


def downgrade() -> None:
    """Reverse : flip the 3 templates back to ``is_active=false``."""
    bind = op.get_bind()
    result = bind.execute(
        text(
            "UPDATE missions SET is_active = FALSE "
            "WHERE qualifier = 'attribute:organic' "
            "AND action_type = 'product_identification' "
            "AND is_active = TRUE "
            "RETURNING id"
        )
    )
    rows = result.fetchall()
    n = len(rows)
    if n not in (0, _EXPECTED_ROW_COUNT):
        raise AssertionError(
            f"Expected {_EXPECTED_ROW_COUNT} product_identification.organic "
            f"templates to flip from is_active=TRUE to FALSE (or 0 on "
            f"re-run), got {n}."
        )
