"""
Part B — receipt-based reconciliation of 'unknown' label scans.

When a receipt is processed for a known store, we match the user's
label scans that were saved as `store_status='unknown'` (store_id NULL)
within a 7-day window and 100m of the store. Each match:

  - Attaches store_id and flips store_status to 'confirmed'
  - Clears user_lat/user_lng (PII — kept only until reconciliation)
  - Triggers the same rewards pipeline as a fresh scan
  - Contributes to the notification_outbox row fired at the end

No DB commit here — the caller owns the transaction boundary.

Phase C-1 (2026-05-11) — the per-scan reward trigger now dispatches a
**dual** ``trigger_action`` when the resolved product is OFF-tagged
organic : (1) a vanilla event with no qualifier — drives the None-
qualifier missions ; (2) a qualified event with ``attribute:organic``
— drives the 3 ``product_identification + attribute:organic`` missions
re-flipped to active in this phase. Distinct idempotency_key suffixes
(``<scan_id>`` vs ``<scan_id>:organic``) keep the
``reward_events UNIQUE(user_id, reference_type, reference_id)`` happy.

Phase C-3 (2026-05-11) — the trigger additionally fans out
``scan_distinct`` events qualified by ``category:<slug>`` (from the
resolved product's ``categories_tags[0]``) and ``store:<uuid>`` (from
the reconciled store). Each carries its own idempotency suffix of shape
``:distinct:<qualifier>`` so the rewards UNIQUE constraint holds. A
single scan can therefore now produce up to **4** ``trigger_action``
calls : vanilla + organic + scan_distinct.category + scan_distinct.store.

Phase C-2 (2026-05-11) — adds a ``qualifier='attribute:french'`` emit
when ``is_french_product(origins_tags)``, mirror of the organic
dual-emit. Brings the per-scan ceiling to **5** ``trigger_action``
calls. The 3 ``product_identification + attribute:french`` mission
templates stay ``is_active=false`` in this PR — the prod backfill
batch (``ratis_batch_origins_backfill``) must populate the new
``products.origins_tags`` column before the operator flips the
missions live (cf PROD_CHECKLIST.md § Missions Phase C-2).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from ratis_core.models.notifications import NotificationOutbox
from ratis_core.models.product import Product
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.store import Store
from ratis_core.rewards_client import trigger_action
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.product_attributes import (
    derive_scan_distinct_qualifiers,
    is_french_product,
    is_organic_product,
)

log = logging.getLogger(__name__)

RECONCILE_RADIUS_KM = 0.1  # 100m
RECONCILE_WINDOW_DAYS = 7
NOTIFICATION_TYPE = "store_validated"


@dataclass(frozen=True)
class ReconciliationResult:
    reconciled_count: int
    scan_ids: tuple[uuid.UUID, ...]
    store_name: str


_MATCH_SQL = text("""
    SELECT s.id
    FROM scans s
    WHERE s.user_id = :user_id
      AND s.store_status = 'unknown'
      AND s.store_id IS NULL
      AND s.user_lat IS NOT NULL
      AND s.user_lng IS NOT NULL
      AND s.scanned_at > now() - (:window_days || ' days')::interval
      AND (6371 * acos(
          LEAST(1.0,
              cos(radians(:store_lat)) * cos(radians(s.user_lat::float))
              * cos(radians(s.user_lng::float) - radians(:store_lng))
              + sin(radians(:store_lat)) * sin(radians(s.user_lat::float))
          )
      )) <= :radius_km
""")


_SCAN_TYPE_TO_ACTION_TYPE = {
    "receipt": "receipt_scan",
    "electronic_label": "label_scan",
    "manual": "product_identification",
}

# Phase C-1 — organic qualifier emitted as a separate ``trigger_action``
# call alongside the vanilla event. The suffix is appended to the
# vanilla ``idempotency_key`` so the two events are independent rows in
# ``reward_events`` (UNIQUE(user_id, reference_type, reference_id) holds).
_ORGANIC_QUALIFIER = "attribute:organic"
_ORGANIC_IDEMPOTENCY_SUFFIX = ":organic"

# Phase C-2 — french qualifier emitted alongside vanilla + organic.
# Same pattern as organic : suffix-keyed idempotency to keep
# ``reward_events UNIQUE(user_id, reference_type, reference_id)`` happy.
_FRENCH_QUALIFIER = "attribute:french"
_FRENCH_IDEMPOTENCY_SUFFIX = ":french"

# Phase C-3 — scan_distinct emits (category + store). Suffix shape
# ``:distinct:<qualifier>`` keeps each fan-out call idempotent and
# distinct from the vanilla / organic events on the same scan.
_SCAN_DISTINCT_ACTION_TYPE = "scan_distinct"
_SCAN_DISTINCT_IDEMPOTENCY_PREFIX = ":distinct:"


def _default_reward_trigger(
    user_id: uuid.UUID,
    scan_id: uuid.UUID,
    scan_type: str,
    *,
    labels_tags: list[str] | None = None,
    product_origins_tags: list[str] | None = None,
    product_categories_tags: list[str] | None = None,
    scan_store_id: uuid.UUID | None = None,
) -> None:
    """Default reward_trigger — dispatch up to **five** ``trigger_action``
    events for a reconciled scan.

    Emit matrix (one ``trigger_action`` per non-empty row) :

    +---+-----------------------+--------------------------+----------------------------------+
    | # | action_type           | qualifier                | idempotency_key                  |
    +===+=======================+==========================+==================================+
    | 1 | <scan_type-mapped>    | None                     | ``<scan_id>``                    |
    | 2 | <scan_type-mapped>    | ``attribute:organic``    | ``<scan_id>:organic``            |
    | 3 | <scan_type-mapped>    | ``attribute:french``     | ``<scan_id>:french``             |
    | 4 | ``scan_distinct``     | ``category:<off-tag>``   | ``<scan_id>:distinct:category:…``|
    | 5 | ``scan_distinct``     | ``store:<uuid>``         | ``<scan_id>:distinct:store:<…>`` |
    +---+-----------------------+--------------------------+----------------------------------+

      * Row 1 (vanilla)  — always fires. Drives every None-qualifier
        mission template.
      * Row 2 (organic, Phase C-1) — fires iff ``is_organic_product(
        labels_tags)``. Drives the 3 ``product_identification +
        attribute:organic`` templates re-flipped to active in C-1.
      * Row 3 (french, Phase C-2) — fires iff ``is_french_product(
        product_origins_tags)``. The 3 ``product_identification +
        attribute:french`` templates stay ``is_active=false`` in this
        PR and get re-flipped manually after the prod backfill batch
        completes (cf PROD_CHECKLIST.md § Missions Phase C-2). The emit
        is wired now so progress accrues silently — once the operator
        flips the missions on, users get instant credit for in-flight
        events the runtime would otherwise have ignored.
      * Row 4 (category, Phase C-3) — fires iff
        ``product_categories_tags`` is non-empty. Drives the 6
        ``scan_distinct + category`` templates.
      * Row 5 (store, Phase C-3) — fires iff ``scan_store_id is not None``
        (i.e. the scan has been reconciled to a store). Drives the 2
        ``scan_distinct + store`` templates.

    ``scan_id`` doubles as the forensics anchor : a Celery retry
    reaching reconciliation a second time will hit the same
    idempotency_keys → server-side dedup → no double-credit on any of
    the five events.

    Args :
        user_id : owner of the scan.
        scan_id : forensics anchor + base idempotency key.
        scan_type : raw ``scans.scan_type`` (mapped via
            ``_SCAN_TYPE_TO_ACTION_TYPE`` for rows 1 / 2 / 3).
        labels_tags : ``products.labels_tags`` for the resolved EAN.
            ``None`` / empty disables the organic emit.
        product_origins_tags : ``products.origins_tags`` for the
            resolved EAN (Phase C-2 column ; ``None`` for pre-backfill
            rows). ``None`` / empty disables the french emit.
        product_categories_tags : ``products.categories_tags`` for the
            resolved EAN. ``None`` / empty disables the
            scan_distinct.category emit.
        scan_store_id : ``scans.store_id`` *after* reconciliation has
            attached it. ``None`` disables the scan_distinct.store emit.
    """
    action_type = _SCAN_TYPE_TO_ACTION_TYPE.get(scan_type, "product_identification")
    base_context = {"scan_id": str(scan_id), "source": "reconciliation"}

    # (1) Vanilla emit — always.
    trigger_action(
        user_id,
        action_type,
        quantity=1,
        idempotency_key=str(scan_id),
        context=base_context,
    )

    # (2) Organic emit — only when the product carries an organic
    # signal in its OFF ``labels_tags``. Suffix-keyed so the two events
    # never collide on the reward_events UNIQUE constraint.
    if is_organic_product(labels_tags):
        trigger_action(
            user_id,
            action_type,
            quantity=1,
            qualifier=_ORGANIC_QUALIFIER,
            idempotency_key=f"{scan_id}{_ORGANIC_IDEMPOTENCY_SUFFIX}",
            context={**base_context, "attribute": "organic"},
        )

    # (3) French emit — only when the product carries a French-origin
    # signal in its OFF ``origins_tags`` (Phase C-2). The corresponding
    # mission templates stay disabled in this PR — events queue
    # silently via the rewards runtime and start counting the moment
    # the operator flips ``missions.is_active = TRUE`` after the
    # backfill batch run (cf PROD_CHECKLIST.md § Missions Phase C-2).
    if is_french_product(product_origins_tags):
        trigger_action(
            user_id,
            action_type,
            quantity=1,
            qualifier=_FRENCH_QUALIFIER,
            idempotency_key=f"{scan_id}{_FRENCH_IDEMPOTENCY_SUFFIX}",
            context={**base_context, "attribute": "french"},
        )

    # (4 + 5) scan_distinct emits — one per qualifier shape derived
    # from the product's categories_tags + the reconciled store_id.
    # Empty list (no product, no store) → no emit.
    for q in derive_scan_distinct_qualifiers(
        categories_tags=product_categories_tags,
        store_id=scan_store_id,
    ):
        # tracked_value = qualifier minus the type-tag prefix
        # (everything after the first colon). Persisted in
        # ``reward_events.payload`` for admin audit + forensics.
        tracked_value = q.split(":", 1)[1]
        trigger_action(
            user_id,
            _SCAN_DISTINCT_ACTION_TYPE,
            quantity=1,
            qualifier=q,
            idempotency_key=(f"{scan_id}{_SCAN_DISTINCT_IDEMPOTENCY_PREFIX}{q}"),
            context={**base_context, "tracked_value": tracked_value},
        )


def reconcile_unknown_scans_for_receipt(
    db: Session,
    receipt: Receipt,
    *,
    reward_trigger: Callable[..., None] | None = None,
) -> ReconciliationResult | None:
    """
    Attach unknown scans matching (user_id, store.coords, 7d, 100m) to the
    receipt's store. Awards rewards for each and enqueues a single push
    notification summarizing the outcome.

    Returns None if nothing to reconcile (or preconditions unmet).
    Never raises — caller can rely on best-effort semantics.

    ``reward_trigger`` is injectable so tests can avoid the HTTP side-effect.
    The default wraps ``trigger_action`` (phase B PR #325) and is itself
    fire-and-forget, so swallowing exceptions remains the caller's job.

    Phase C-1 contract — ``reward_trigger`` is called with
    ``(user_id, scan_id, scan_type, *, labels_tags=None)``. Test stubs
    using ``**kwargs`` keep working ; strict 3-arg stubs need to be
    updated to accept the keyword-only ``labels_tags``.

    Phase C-3 contract — ``reward_trigger`` additionally receives
    ``product_categories_tags`` (the resolved product's OFF
    ``categories_tags`` array) and ``scan_store_id`` (the freshly
    reconciled ``scans.store_id``, NOT the pre-reconciliation NULL).
    ``**kwargs`` test stubs keep working without change.

    Phase C-2 contract — ``reward_trigger`` additionally receives
    ``product_origins_tags`` (the resolved product's OFF
    ``origins_tags`` array, ``None`` for pre-backfill rows). Used to
    decide whether to fire the ``attribute:french`` qualifier emit.
    ``**kwargs`` test stubs keep working without change.
    """
    if receipt.user_id is None or receipt.store_id is None:
        return None
    if receipt.store_status != "confirmed":
        return None

    # Late-bound default so tests can monkeypatch trigger_action on the
    # module after import (otherwise the name is captured at def-time).
    if reward_trigger is None:
        reward_trigger = _default_reward_trigger

    store = db.get(Store, receipt.store_id)
    if store is None:
        log.warning("reconciliation: store %s not found", receipt.store_id)
        return None

    try:
        rows = db.execute(
            _MATCH_SQL,
            {
                "user_id": str(receipt.user_id),
                "store_lat": float(store.lat),
                "store_lng": float(store.lng),
                "radius_km": RECONCILE_RADIUS_KM,
                "window_days": str(RECONCILE_WINDOW_DAYS),
            },
        ).all()
    except Exception:
        log.exception("reconciliation: match query failed")
        return None

    scan_ids = [row[0] for row in rows]
    if not scan_ids:
        return None

    reconciled: list[uuid.UUID] = []
    for scan_id in scan_ids:
        scan = db.get(Scan, scan_id)
        if scan is None:
            continue
        scan.store_id = store.id
        scan.store_status = "confirmed"
        # Clear PII now that geo-match served its purpose.
        scan.user_lat = None
        scan.user_lng = None
        db.flush()
        reconciled.append(scan.id)

        # Phase C-1 + C-2 + C-3 — fetch the resolved product's OFF tags
        # so the default reward trigger can fan-out :
        #   * organic-qualified event       (C-1, labels_tags)
        #   * french-qualified event        (C-2, origins_tags)
        #   * scan_distinct + category emit (C-3, categories_tags)
        #   * scan_distinct + store emit    (C-3, scan_store_id below)
        # Scans without a resolved product_ean (rare here, since
        # reconciliation works on a label scan that ought to carry one)
        # get every tag-based input set to ``None``; only the vanilla
        # emit (+ the store-distinct emit, if any) fire.
        labels_tags: list[str] | None = None
        product_origins_tags: list[str] | None = None
        product_categories_tags: list[str] | None = None
        if scan.product_ean is not None:
            product = db.get(Product, scan.product_ean)
            if product is not None:
                labels_tags = product.labels_tags
                product_origins_tags = product.origins_tags
                product_categories_tags = product.categories_tags

        # Fire-and-forget reward trigger (never raises). ``scan.store_id``
        # has been set to ``store.id`` above, so the trigger sees the
        # post-reconciliation value (drives the scan_distinct.store
        # emit). For unmatched scans (no product_ean), the trigger still
        # fires the vanilla emit ; the scan_distinct.store emit is
        # gated on a resolved product (see ARCH_missions Phase C-3
        # rationale).
        scan_store_id = scan.store_id if scan.product_ean is not None else None
        try:
            reward_trigger(
                scan.user_id,
                scan.id,
                scan.scan_type,
                labels_tags=labels_tags,
                product_origins_tags=product_origins_tags,
                product_categories_tags=product_categories_tags,
                scan_store_id=scan_store_id,
            )
        except Exception:
            log.warning(
                "reconciliation: reward_trigger raised unexpectedly (scan=%s)",
                scan.id,
                exc_info=True,
            )

    if not reconciled:
        return None

    # Enqueue a push via the outbox (same transaction as the mutations —
    # DA-15). The notifier worker picks it up asynchronously.
    outbox_row = NotificationOutbox(
        user_id=receipt.user_id,
        type=NOTIFICATION_TYPE,
        data={
            "store_name": store.name,
            "reconciled_count": len(reconciled),
            "receipt_id": str(receipt.id),
        },
    )
    db.add(outbox_row)
    db.flush()

    return ReconciliationResult(
        reconciled_count=len(reconciled),
        scan_ids=tuple(reconciled),
        store_name=store.name,
    )
