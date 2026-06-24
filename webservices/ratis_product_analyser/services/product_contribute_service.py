"""Business logic for user-driven product field contributions (Phase C-5).

Endpoint :       ``POST /api/v1/product/{ean}/contribute``
Mission impact : drives the 6 None-qualifier ``fill_product_field``
                 missions seeded in ``miss_pb`` (Phase B). Each accepted
                 contribution fires a single ``trigger_action(
                 "fill_product_field", qualifier=None, ...)``.

Apply-or-defer logic :

* The target field on ``products`` is **NULL or empty** → the row is
  patched in place (``status='applied'``) AND a mission credit fires.
* The target field already has a non-empty value → the contribution is
  parked for admin review (``status='pending_review'``) AND **no
  mission credit fires** (admins must vet the override first to avoid
  rewarding bad-faith edits).

Idempotency window :

* Same user + same product + same field within the last 24h → return
  the existing contribution row, no new INSERT, no new mission credit.
* The window is 24h to absorb spammy double-taps on the mobile UI
  while still letting a user submit a follow-up correction a day later
  if needed.

Audit trail :

* Every accepted contribution writes one ``pipeline_audit_log`` row
  with ``phase='manual'``, ``event='product_contribution'`` carrying
  the field name, value, status, and contribution id. The audit log
  insert is best-effort — a failure logs a warning but does not abort
  the contribution (the row in ``product_contributions`` is itself the
  forensics anchor).

Validation contract :

* ``brands`` / ``name``           : non-empty ``str``, ≤ 200 chars,
  stripped, no control characters.
* ``categories_tags`` /
  ``labels_tags``                 : ``list[str]`` with 1..30 entries
  each ≤ 100 chars matching ``^[a-z]{2}:[a-z0-9-]+$`` (OFF tag shape).

Any violation raises ``UnprocessableEntity('contribution_invalid_<x>')``
which the route maps to HTTP 422.

The service never commits — the route owns the transaction boundary
(R03). The route is responsible for ``db.commit()`` after the trigger
side-effect.
"""

from __future__ import annotations

import json
import logging
import re
import uuid

from ratis_core.exceptions import NotFound, UnprocessableEntity
from ratis_core.models.product import Product
from ratis_core.models.product_contributions import ProductContribution
from ratis_core.rewards_client import trigger_action
from ratis_core.settings import load_settings
from repositories.barcode_repository import get_product
from repositories.product_contribute_repository import (
    count_user_contributions_last_24h,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


class ContributionDailyCapExceeded(Exception):
    """The user has reached the per-day product-contribution cap.

    Maps to HTTP 429 ``contribution_daily_cap_reached`` at the route
    layer. Mirrors ``rescan_service.RescanCapExceeded`` — we do not
    piggy-back on slowapi's ``RateLimitExceeded`` (fixed
    ``rate_limit_exceeded`` detail) because this is a domain-level
    anti-spam cap, not a transport rate-limit, and ops wants to
    distinguish the two in logs.
    """

    def __init__(self, *, count: int, cap: int) -> None:
        self.detail = "contribution_daily_cap_reached"
        self.count = count
        self.cap = cap
        super().__init__(self.detail)


# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

_SCALAR_FIELDS = {"brands", "name"}
_ARRAY_FIELDS = {"categories_tags", "labels_tags"}
_ALL_FIELDS = _SCALAR_FIELDS | _ARRAY_FIELDS

_SCALAR_MAX_LEN = 200
_TAG_MAX_LEN = 100
_TAG_LIST_MAX_LEN = 30
_OFF_TAG_RE = re.compile(r"^[a-z]{2}:[a-z0-9-]+$")
# Control chars (0x00..0x1F + 0x7F) are forbidden in scalar input — they
# break logs, screen readers, and downstream pipelines. ``\t``/``\n`` are
# accepted by re.sub on text but we explicitly reject everything below 0x20
# except plain ASCII space.
_CTRL_RE = re.compile(r"[\x00-\x1F\x7F]")

_IDEMPOTENCY_WINDOW_HOURS = 24


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_scalar(value: object) -> str:
    if not isinstance(value, str):
        raise UnprocessableEntity("contribution_value_type")
    stripped = value.strip()
    if not stripped:
        raise UnprocessableEntity("contribution_value_empty")
    if len(stripped) > _SCALAR_MAX_LEN:
        raise UnprocessableEntity("contribution_value_too_long")
    if _CTRL_RE.search(stripped):
        raise UnprocessableEntity("contribution_value_invalid_chars")
    return stripped


def _validate_array(value: object) -> list[str]:
    if not isinstance(value, list):
        raise UnprocessableEntity("contribution_value_type")
    if len(value) == 0:
        raise UnprocessableEntity("contribution_value_empty")
    if len(value) > _TAG_LIST_MAX_LEN:
        raise UnprocessableEntity("contribution_value_too_many_entries")
    cleaned: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise UnprocessableEntity("contribution_value_type")
        stripped = entry.strip()
        if not stripped:
            raise UnprocessableEntity("contribution_value_empty")
        if len(stripped) > _TAG_MAX_LEN:
            raise UnprocessableEntity("contribution_value_too_long")
        if not _OFF_TAG_RE.match(stripped):
            raise UnprocessableEntity("contribution_value_invalid_tag")
        cleaned.append(stripped)
    return cleaned


def _normalize_value(field: str, value: object) -> tuple[str | None, list[str] | None]:
    """Return ``(value_text, value_array)`` to persist for the field.

    Exactly one of the two is non-None. Raises ``UnprocessableEntity``
    on validation failure.
    """
    if field not in _ALL_FIELDS:
        raise UnprocessableEntity("contribution_field_invalid")
    if field in _SCALAR_FIELDS:
        return _validate_scalar(value), None
    return None, _validate_array(value)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def _find_recent_contribution(
    db: Session,
    *,
    user_id: uuid.UUID,
    product_ean: str,
    field: str,
) -> ProductContribution | None:
    """Return the most recent contribution row by this user for this
    (product, field) tuple within the idempotency window, if any.
    """
    row = db.execute(
        text(
            "SELECT id FROM product_contributions "
            "WHERE user_id = :uid "
            "  AND product_ean = :ean "
            "  AND field = :field "
            "  AND created_at > now() - (:hours || ' hours')::interval "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {
            "uid": user_id,
            "ean": product_ean,
            "field": field,
            "hours": _IDEMPOTENCY_WINDOW_HOURS,
        },
    ).first()
    if row is None:
        return None
    return db.get(ProductContribution, row[0])


# ---------------------------------------------------------------------------
# Anti-spam daily cap
# ---------------------------------------------------------------------------


def _load_daily_cap() -> int:
    """Return the per-user daily contribution cap from settings.

    Fail-fast (R19) — the key is mandatory in ``ratis_settings.json``
    under ``product_contributions.max_per_day_per_user``. A missing
    section / key raises ``KeyError`` which surfaces as a 500 rather
    than silently disabling the anti-spam protection.
    """
    s = load_settings()
    return int(s["product_contributions"]["max_per_day_per_user"])


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------


def _audit_log(
    db: Session,
    *,
    event: str,
    payload: dict,
) -> None:
    """Best-effort audit row in ``pipeline_audit_log``.

    Failures are logged at WARN and swallowed — the contribution row is
    itself the durable trace, the audit log is a convenience for ops.
    """
    try:
        db.execute(
            text(
                "INSERT INTO pipeline_audit_log "
                "    (phase, level, event, scan_id, payload, created_at) "
                "VALUES "
                "    ('manual', 'normal', :event, NULL, "
                "     CAST(:payload AS jsonb), clock_timestamp())"
            ),
            {"event": event, "payload": json.dumps(payload, default=str)},
        )
    except Exception:
        log.warning(
            "product_contribute audit_log insert failed (event=%s)",
            event,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Mutating products row
# ---------------------------------------------------------------------------


def _field_is_empty(product: Product, field: str) -> bool:
    """Return True if the target field on the products row is
    NULL/empty — i.e. the contribution can be applied directly.

    For scalar fields, "empty" means NULL or empty/whitespace string.
    For array fields, "empty" means NULL or zero-length list.
    """
    current = getattr(product, field)
    if current is None:
        return True
    if field in _SCALAR_FIELDS:
        return not str(current).strip()
    # array field
    return len(current) == 0


def _apply_to_product(
    product: Product,
    *,
    field: str,
    value_text: str | None,
    value_array: list[str] | None,
) -> None:
    if field in _SCALAR_FIELDS:
        setattr(product, field, value_text)
    else:
        setattr(product, field, value_array)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def contribute_product_field(
    db: Session,
    *,
    user_id: uuid.UUID,
    ean: str,
    field: str,
    value: object,
    _trigger_action=None,
) -> dict:
    """Apply (or queue) a user contribution on a product's editable field.

    ``_trigger_action`` is injectable for tests — it defaults (when
    ``None``) to the module-level ``trigger_action`` symbol, which lets
    pytest ``monkeypatch.setattr(
    "services.product_contribute_service.trigger_action", fake)`` win.

    Returns a serialisable dict of the resulting ``ProductContribution``
    row : ``{id, status, field, applied}`` (applied=True for the
    direct UPDATE path, False for pending_review or idempotent replays).

    Raises :
        NotFound('product_not_found')          : EAN unknown.
        UnprocessableEntity('contribution_*')  : validation failure.
        ContributionDailyCapExceeded           : per-user daily cap
                                                 reached (HTTP 429).
    """
    # 1. Validate value + field shape.
    value_text, value_array = _normalize_value(field, value)

    # 2. Locate the product (404 if unknown).
    product = get_product(db, ean)
    if product is None:
        raise NotFound("product_not_found")

    # 3. Idempotency window — return the existing row without new credit.
    existing = _find_recent_contribution(db, user_id=user_id, product_ean=ean, field=field)
    if existing is not None:
        return {
            "id": str(existing.id),
            "status": existing.status,
            "field": existing.field,
            "applied": existing.status == "applied",
            "idempotent": True,
        }

    # 4. Anti-spam daily cap — a fresh contribution (idempotency window
    #    missed above) counts against the per-user daily limit. An
    #    idempotent replay never reaches here, so re-tapping the same
    #    field is free ; only distinct contributions consume the budget.
    cap = _load_daily_cap()
    count = count_user_contributions_last_24h(db, user_id=user_id)
    if count >= cap:
        raise ContributionDailyCapExceeded(count=count, cap=cap)

    # 5. Apply-or-queue decision.
    can_apply = _field_is_empty(product, field)
    status = "applied" if can_apply else "pending_review"

    contribution = ProductContribution(
        user_id=user_id,
        product_ean=ean,
        field=field,
        value_text=value_text,
        value_array=value_array,
        status=status,
    )
    db.add(contribution)
    db.flush()  # populate id

    if can_apply:
        _apply_to_product(
            product,
            field=field,
            value_text=value_text,
            value_array=value_array,
        )

    # 6. Audit + (conditional) reward emit.
    _audit_log(
        db,
        event="product_contribution",
        payload={
            "contribution_id": str(contribution.id),
            "user_id": str(user_id),
            "product_ean": ean,
            "field": field,
            "status": status,
        },
    )

    if can_apply:
        # Mission credit only fires for direct-apply contributions —
        # pending_review rows are vetted by an admin later. The
        # idempotency key is anchored on the contribution id : per-row
        # unique by construction, so the server-side reward_events
        # UNIQUE absorbs any retry.
        emit = _trigger_action if _trigger_action is not None else trigger_action
        emit(
            user_id,
            "fill_product_field",
            quantity=1,
            qualifier=None,
            idempotency_key=f"contribution:{contribution.id}",
            context={
                "contribution_id": str(contribution.id),
                "product_ean": ean,
                "field": field,
                "source": "user_contribution",
            },
        )

    return {
        "id": str(contribution.id),
        "status": status,
        "field": field,
        "applied": can_apply,
        "idempotent": False,
    }
