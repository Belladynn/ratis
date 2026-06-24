"""Admin-side subscription manage helpers.

Domain semantics
----------------

- ``Subscription`` rows are **NEVER PURGE** (legal — every paid month is a
  taxable event). All admin mutations therefore UPDATE the row in place and
  never DELETE.

- The DB CHECK constraint ``payment_ref_coherence`` enforces that any row
  with ``status IN ('active', 'expired')`` and ``paid_with != 'cashback'``
  must carry a non-NULL ``payment_ref``. When an admin force-activates a
  subscription manually (``source='manual_admin'``), we synthesize a
  ``payment_ref = 'manual_admin:<operator>:<short-uuid>'`` so the row stays
  legal-compliant. The synthesized ref is purely a traceability handle —
  it is never sent to Stripe.

- ``cancelled_check`` enforces ``cancelled_at`` is set iff status='cancelled'.
  We therefore set ``cancelled_at`` only for the immediate cancel path.

- The ``end_of_period`` deactivation path is intentionally **non-mutating**
  on the row : the admin operator records intent in the audit log only ;
  the Stripe ``checkout.session.expired`` / cancellation webhook remains
  source of truth for flipping the row to ``cancelled`` at expiry. This
  avoids introducing a fifth ``cancelling`` status to the CHECK constraint
  and the migration churn that would imply. See ``ARCH_admin_endpoints.md``.

Audit
-----

Each mutation emits a structured log line including the operator handle,
the human reason, and the before/after relevant field(s). The repo's
``pipeline_audit_log`` table lives in PA — for AU we currently rely on
the structured-log trail (Sentry + log aggregator). A dedicated
``admin_audit_log`` table is proposed in ``DECISIONS_PENDING.md`` and
covers all admin-bearing services uniformly.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ratis_core.models.rewards import Subscription
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class SubscriptionNotFoundError(LookupError):
    """Raised when no subscription row exists for the given user."""


class SubscriptionAlreadyActiveError(Exception):
    """Raised when activate is called on a subscription already active with a
    future expiry — idempotent guard."""


class SubscriptionExtendInvalidError(ValueError):
    """Raised when ``new_expires_at`` is not strictly after the current
    ``expires_at`` (and after now())."""


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def get_subscription(db: Session, user_id: uuid.UUID) -> Subscription:
    """Return the subscription row for ``user_id`` (any status), or raise.

    There can be at most one row per user with status='active' (UNIQUE
    partial index ``idx_one_active_subscription``) and one with
    status='pending'. We surface the most-recently-started row to give
    admins the active-or-latest view they expect.
    """
    sub = (
        db.query(Subscription).filter(Subscription.user_id == user_id).order_by(Subscription.started_at.desc()).first()
    )
    if sub is None:
        raise SubscriptionNotFoundError("subscription_not_found")
    return sub


# ---------------------------------------------------------------------------
# Activate
# ---------------------------------------------------------------------------


def activate_subscription(
    db: Session,
    user_id: uuid.UUID,
    *,
    reason: str,
    until_date: datetime | None,
    operator: str,
) -> Subscription:
    """Force-activate a subscription manually.

    Creates a new row when none exists ; updates the existing row otherwise.

    Idempotency : if a subscription is already ``status='active'`` with
    ``expires_at`` strictly in the future, raises
    ``SubscriptionAlreadyActiveError`` so the caller maps to 409.

    Side effects (in-Python, not committed by this helper) :
        - INSERT or UPDATE subscriptions
            status='active', paid_with='manual_admin',
            payment_ref='manual_admin:<operator>:<short-uuid>',
            expires_at = until_date or now()+1y
            started_at = now() (only on INSERT or when previously not active)
        - logger.info structured trail with operator + reason

    The caller is responsible for ``db.commit()``.
    """
    now = datetime.now(UTC)
    expires_at = until_date or (now + timedelta(days=365))
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    existing = (
        db.query(Subscription).filter(Subscription.user_id == user_id).order_by(Subscription.started_at.desc()).first()
    )

    if existing is not None and existing.status == "active" and existing.expires_at > now:
        raise SubscriptionAlreadyActiveError("already_active")

    payment_ref = f"manual_admin:{operator}:{uuid.uuid4().hex[:8]}"

    if existing is None:
        sub = Subscription(
            user_id=user_id,
            status="active",
            plan=None,  # plan is meaningful only for Stripe-backed subs
            # Catalog default — manual grants don't reflect a real charge but
            # the column is NOT NULL with a default of 11.99 ; mirror that.
            price=Decimal("11.99"),
            paid_with="manual_admin",
            payment_ref=payment_ref,
            started_at=now,
            expires_at=expires_at,
        )
        db.add(sub)
    else:
        sub = existing
        sub.status = "active"
        sub.paid_with = "manual_admin"
        sub.payment_ref = payment_ref
        sub.started_at = now
        sub.expires_at = expires_at
        # cancelled_check : cancelled_at must be NULL when status != 'cancelled'.
        sub.cancelled_at = None

    db.flush()

    logger.info(
        "admin_subscription_activate operator=%s user_id=%s reason=%s until=%s subscription_id=%s",
        operator,
        user_id,
        reason,
        expires_at.isoformat(),
        sub.id,
    )
    return sub


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


def deactivate_subscription(
    db: Session,
    user_id: uuid.UUID,
    *,
    reason: str,
    effective: str,  # 'immediate' | 'end_of_period'
    operator: str,
) -> Subscription:
    """Cancel an active subscription.

    - ``effective='immediate'`` → ``status='cancelled'`` + ``cancelled_at=now()``
      + ``expires_at=now()``. Caller commits.
    - ``effective='end_of_period'`` → no row mutation. The intent is recorded
      in the audit log ; the Stripe cancellation webhook remains source of
      truth for the eventual flip to ``cancelled`` at the natural expiry.
      We expose this as ``status='active'`` in the response with the
      ``effective`` confirmation field.

    Raises ``SubscriptionNotFoundError`` if no row exists for the user.
    """
    sub = get_subscription(db, user_id)
    now = datetime.now(UTC)

    if effective == "immediate":
        sub.status = "cancelled"
        sub.cancelled_at = now
        sub.expires_at = now
        db.flush()
        logger.info(
            "admin_subscription_deactivate effective=immediate operator=%s user_id=%s reason=%s subscription_id=%s",
            operator,
            user_id,
            reason,
            sub.id,
        )
    else:  # 'end_of_period'
        # No DB mutation — Stripe webhook is source of truth at expiry.
        logger.info(
            "admin_subscription_deactivate effective=end_of_period operator=%s "
            "user_id=%s reason=%s subscription_id=%s expires_at=%s",
            operator,
            user_id,
            reason,
            sub.id,
            sub.expires_at.isoformat(),
        )
    return sub


# ---------------------------------------------------------------------------
# Extend
# ---------------------------------------------------------------------------


def extend_subscription(
    db: Session,
    user_id: uuid.UUID,
    *,
    new_expires_at: datetime,
    reason: str,
    operator: str,
) -> Subscription:
    """Push ``expires_at`` forward to ``new_expires_at`` (trial grace path).

    Validation : ``new_expires_at`` must be strictly after the current
    ``expires_at`` AND after now(). Otherwise raises
    ``SubscriptionExtendInvalidError`` so the caller maps to 422.

    Raises ``SubscriptionNotFoundError`` if no row exists for the user.
    """
    sub = get_subscription(db, user_id)
    if new_expires_at.tzinfo is None:
        new_expires_at = new_expires_at.replace(tzinfo=UTC)
    now = datetime.now(UTC)

    if new_expires_at <= sub.expires_at or new_expires_at <= now:
        raise SubscriptionExtendInvalidError("new_expires_at must be strictly after current expires_at and now()")

    sub.expires_at = new_expires_at
    db.flush()

    logger.info(
        "admin_subscription_extend operator=%s user_id=%s reason=%s subscription_id=%s new_expires_at=%s",
        operator,
        user_id,
        reason,
        sub.id,
        new_expires_at.isoformat(),
    )
    return sub
