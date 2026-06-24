"""disable qualifier-attribute mission templates pending phase C.

Revision ID: 20260509_0100_disqual
Revises: 20260509_0000_bp_s1_w
Create Date: 2026-05-09 01:00:00

Phase B (PR #325) flipped every mission template to ``is_active=true``
on the assumption that the runtime would honour every qualifier shape
end-to-end. Reality check : the PA worker (phase C, not yet shipped)
does not emit qualifier-enriched events for ``attribute:organic`` and
``attribute:french``. Consequence : the 9 templates whose qualifier
matches one of those two attributes are visible to users via lazy-gen
but their ``current_count`` never increments — broken missions.

This data-only migration deactivates those 9 rows so they stop being
surfaced. They will be flipped back to ``is_active=true`` by the
phase C migration once the PA worker emits qualifier-enriched events.

Breakdown of the 9 rows :

  * product_identification + attribute:organic : daily/easy +
    weekly/easy + weekly/medium → 3 rows
  * product_identification + attribute:french  : same 3 frequencies → 3 rows
  * fill_product_field    + attribute:organic : same 3 frequencies → 3 rows

Idempotent : the ``AND is_active = TRUE`` guard collapses re-runs to a
no-op once the rows have been flipped. The fail-fast assertion enforces
exactly 9 rows touched on the first apply — anything else means the
catalogue diverged from the canonical seed and a human must triage
before continuing.

Down-migration : reverse to ``is_active = TRUE`` (idempotent guard on
``is_active = FALSE``). Symmetric assertion enforces 9 rows on the
first reverse run.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic. ID kept ≤ 32 chars (R08).
revision = "20260509_0100_disqual"
down_revision = "20260509_0000_bp_s1_w"
branch_labels = None
depends_on = None


_QUALIFIERS = ("attribute:organic", "attribute:french")
_EXPECTED_ROW_COUNT = 9


def upgrade() -> None:
    """Flip the 9 attribute-qualifier templates to ``is_active=false``.

    The ``RETURNING id`` clause feeds the assertion so the migration
    fails fast if the catalogue holds an unexpected number of matching
    rows — that would mean the seed diverged from the migration chain
    and we'd rather refuse to apply than silently flip more or fewer
    rows than intended.
    """
    bind = op.get_bind()
    result = bind.execute(
        text(
            "UPDATE missions SET is_active = FALSE "
            "WHERE qualifier IN ('attribute:organic', 'attribute:french') "
            "AND is_active = TRUE "
            "RETURNING id"
        )
    )
    rows = result.fetchall()
    # First-apply assertion : exactly 9 rows must have been flipped.
    # On re-run (already-applied prod), 0 rows match the ``is_active=TRUE``
    # guard which is also acceptable (idempotent no-op). Any value strictly
    # between 0 and 9 — or above 9 — means the catalogue diverged.
    n = len(rows)
    if n not in (0, _EXPECTED_ROW_COUNT):
        raise AssertionError(
            f"Expected {_EXPECTED_ROW_COUNT} attribute-qualifier templates to "
            f"flip from is_active=TRUE to FALSE (or 0 on re-run), got {n}. "
            "Catalogue may have diverged from the canonical seed — triage "
            "before continuing."
        )


def downgrade() -> None:
    """Reverse : flip the 9 attribute-qualifier templates back to active."""
    bind = op.get_bind()
    result = bind.execute(
        text(
            "UPDATE missions SET is_active = TRUE "
            "WHERE qualifier IN ('attribute:organic', 'attribute:french') "
            "AND is_active = FALSE "
            "RETURNING id"
        )
    )
    rows = result.fetchall()
    n = len(rows)
    if n not in (0, _EXPECTED_ROW_COUNT):
        raise AssertionError(
            f"Expected {_EXPECTED_ROW_COUNT} attribute-qualifier templates to "
            f"flip from is_active=FALSE to TRUE (or 0 on re-run), got {n}."
        )
