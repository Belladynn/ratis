"""NEVER PURGE FKs : flip user_id FK to SET NULL on the 3 legally-retained
tables + add a PG WARN trigger on ``users`` hard-DELETE.

Revision ID: 20260511_1000_au9npfk
Revises: 20260510_2200_sirene_schema
Create Date: 2026-05-11 10:00:00.000000

Audit reference
---------------
``docs/audits/2026-05-10-deep-audit-auth.md`` § F-AU-9 :

    ``subscriptions.fk_user = ON DELETE CASCADE`` (idem
    ``cashback_transactions.fk_user``) contradicts the CLAUDE.md NEVER
    PURGE invariant. Today no caller hard-deletes ``users`` (the canonical
    ``DELETE /account`` flow anonymises in place), so CASCADE never fires.
    But a single future migration / batch / manual SQL doing
    ``DELETE FROM users WHERE ...`` would silently wipe legally-retained
    cashback + subscription rows. That data must be kept 5-10 years for tax
    and financial-services compliance.

User decision (2026-05-11)
--------------------------
Belt-and-braces strategy on the 3 NEVER PURGE tables :

* ``subscriptions``           : FK CASCADE → SET NULL
* ``cashback_transactions``   : FK CASCADE → SET NULL
* ``cashback_withdrawals``    : FK RESTRICT → SET NULL

Why SET NULL rather than RESTRICT
---------------------------------
Two angles point to the same answer :

1. RESTRICT blocks legitimate admin anonymise actions if any of these rows
   exist for the user. With SET NULL, an explicit hard-DELETE (rare admin
   operation, never automated) succeeds and the legally-retained financial
   row keeps its data (amount, dates, payment_ref) — only the link back to
   the user is severed, which is what RGPD actually requires once
   anonymisation is invoked. The standard flow (``DELETE /account`` =
   in-place anonymise) still keeps user_id intact because the ``users``
   row is not deleted.
2. The current ``cashback_withdrawals.fk_user`` is RESTRICT. Audit
   recommended bringing the other two up to RESTRICT to match. We go one
   step further (SET NULL) so admin anonymise — when implemented per
   F-AU-10 — has a clean path even for users with legacy rows. The
   trigger below is the actual defence-in-depth.

Belt-and-braces : PG trigger BEFORE DELETE on users
---------------------------------------------------
A ``RAISE WARNING`` trigger fires on any hard-DELETE of a ``users`` row.
The trigger does NOT block — admin anonymise may legitimately want to
hard-delete in the far future, and a hard block would create a footgun
for emergency response. Instead we RAISE WARNING so the action is loud
in Postgres logs / Sentry and an operator paging gets the signal.

Nullability change
------------------
The ``user_id`` columns are currently ``NOT NULL`` on all 3 tables.
SET NULL requires a nullable column, so we ALTER COLUMN nullable as part
of the upgrade. Existing rows are unaffected (all have a valid user_id).

Downgrade
---------
Restores the original ON DELETE actions and the NOT NULL constraint.
This will fail if downgrade is attempted after a hard-DELETE on users
that legitimately set rows to NULL — that's intentional. In that
scenario the operator must consciously decide what to do with the
orphaned rows (preserve as-is for legal record vs. assign to a tombstone
user). The trigger is also dropped on downgrade.

KP-42 (backfill safety) audit
-----------------------------
No UPDATE on existing rows. The DDL is purely structural :
* ALTER COLUMN nullable (additive — strictly weaker constraint)
* DROP CONSTRAINT + ADD CONSTRAINT (same shape, different ON DELETE)
* CREATE FUNCTION + CREATE TRIGGER (purely additive)

No risk to data.
"""
from __future__ import annotations

from alembic import op


# revision identifiers (≤32 chars per R-DB-08).
revision = "20260511_1000_au9npfk"
# Merge-revision : main currently has two heads (``20260511_0900_obp_opf``
# and ``20260511_0900_pg_earthdistance``) both branching off
# ``20260510_2200_sirene_schema``. This audit-fix migration touches a
# disjoint scope (FK actions on 3 tables + a users-DELETE trigger) so it
# is safe to merge the two heads here rather than introduce a separate
# no-op merge revision.
down_revision = ("20260511_0900_obp_opf", "20260511_0900_pg_earthdistance")
branch_labels = None
depends_on = None


# Tables whose user_id FK must point at users(id) with ON DELETE SET NULL,
# matching the CLAUDE.md "NEVER PURGE" invariant.
_NEVER_PURGE_TABLES = ("subscriptions", "cashback_transactions", "cashback_withdrawals")


def upgrade() -> None:
    # 1. Drop the existing fk_user constraint on each NEVER PURGE table.
    # Use IF EXISTS per R-DB-07 — these constraints come from
    # ``20250401_0000_0001_initial_schema``.
    for tbl in _NEVER_PURGE_TABLES:
        op.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS fk_user")

    # 2. Make user_id nullable on each table — SET NULL requires it.
    # Existing rows are unaffected; NOT NULL is a strict-subset of nullable.
    for tbl in _NEVER_PURGE_TABLES:
        op.execute(f"ALTER TABLE {tbl} ALTER COLUMN user_id DROP NOT NULL")

    # 3. Re-create each fk_user with ON DELETE SET NULL.
    for tbl in _NEVER_PURGE_TABLES:
        op.execute(
            f"ALTER TABLE {tbl} "
            f"ADD CONSTRAINT fk_user FOREIGN KEY (user_id) "
            f"REFERENCES users(id) ON DELETE SET NULL"
        )

    # 4. Belt-and-braces : WARN trigger on hard-DELETE of users rows.
    # Does NOT block (admin anonymise might legitimately hard-delete one
    # day) but RAISEs a WARNING so the signal is loud in PG logs / Sentry.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION warn_user_hard_delete() RETURNS trigger AS $$
        BEGIN
            RAISE WARNING
                'Hard DELETE on users.id=% detected. Use DELETE /account '
                '(anonymize in place) instead — NEVER PURGE invariant '
                '(subscriptions, cashback_transactions, cashback_withdrawals '
                'must be retained 5-10y for tax/financial-services compliance).',
                OLD.id;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_users_warn_hard_delete "
        "BEFORE DELETE ON users "
        "FOR EACH ROW EXECUTE FUNCTION warn_user_hard_delete()"
    )


def downgrade() -> None:
    # 1. Drop the WARN trigger + function first.
    op.execute("DROP TRIGGER IF EXISTS trg_users_warn_hard_delete ON users")
    op.execute("DROP FUNCTION IF EXISTS warn_user_hard_delete()")

    # 2. Drop the SET NULL fk_user constraints.
    for tbl in _NEVER_PURGE_TABLES:
        op.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS fk_user")

    # 3. Restore NOT NULL on user_id. NOTE : this will fail if any row has
    # NULL user_id (i.e. the trigger fired between upgrade and downgrade
    # and a hard-DELETE actually nullified some rows). That is intentional
    # — restoring NOT NULL would lose forensic data. Operator must
    # consciously fix the rows first (e.g. assign to a tombstone user)
    # before downgrade succeeds.
    for tbl in _NEVER_PURGE_TABLES:
        op.execute(f"ALTER TABLE {tbl} ALTER COLUMN user_id SET NOT NULL")

    # 4. Re-create the original fk_user constraints :
    # * subscriptions / cashback_transactions had ON DELETE CASCADE
    # * cashback_withdrawals had ON DELETE RESTRICT
    op.execute(
        "ALTER TABLE subscriptions "
        "ADD CONSTRAINT fk_user FOREIGN KEY (user_id) "
        "REFERENCES users(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE cashback_transactions "
        "ADD CONSTRAINT fk_user FOREIGN KEY (user_id) "
        "REFERENCES users(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE cashback_withdrawals "
        "ADD CONSTRAINT fk_user FOREIGN KEY (user_id) "
        "REFERENCES users(id) ON DELETE RESTRICT"
    )
