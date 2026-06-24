from __future__ import annotations

import logging
import uuid

from ratis_core.exceptions import Conflict, NotFound
from ratis_core.schemas import ProductDetailResponse
from ratis_core.settings import load_settings
from ratis_core.utils import assert_owner
from repositories.barcode_repository import (
    get_local_price,
    get_product,
    get_scan,
    resolve_scan,
)
from repositories.name_resolution_writes import record_resolution
from repositories.scan_repository import upsert_price_consensus
from sqlalchemy.orm import Session
from worker.ocr.normalize import normalize_text

logger = logging.getLogger(__name__)

try:
    _GLOBALLY_VERIFIED_THRESHOLD: float = load_settings()["consensus"]["globally_verified_threshold"]
except KeyError as exc:
    raise KeyError(
        f"Missing key {exc} in settings — expected: consensus.globally_verified_threshold "
        "(check app_settings table or ratis_settings.json)"
    ) from exc


def scan_barcode(
    db: Session,
    *,
    ean: str,
    user_id: uuid.UUID,
    scan_id: uuid.UUID,
) -> dict:
    """Cas 2 — user resolves an unmatched receipt scan via barcode."""
    scan = get_scan(db, scan_id)
    if scan is None:
        raise NotFound("scan_not_found")

    assert_owner(scan, user_id)

    # Only receipt scans are eligible — electronic labels have their own pipeline.
    # Accept :
    # - v2 'unmatched' / v3 'unresolved' : user resolves an unmatched scan
    # - v2 'accepted' / v3 'matched' : user OVERRIDES the auto-match (e.g. fuzzy_strict
    #   matched the wrong product because OFF data is crappy — barcode physical scan
    #   has higher priority than fuzzy similarity). The new ean replaces the old one.
    # Reject :
    # - 'pending' : pipeline still running, race condition risk → wait
    # - 'rejected' : terminal state, ticket data was unusable → no override
    _RESOLVABLE_STATUSES = ("unmatched", "unresolved", "accepted", "matched")
    if scan.scan_type != "receipt" or scan.status not in _RESOLVABLE_STATUSES:
        raise Conflict("scan_already_resolved")

    product = get_product(db, ean)
    if product is None:
        raise NotFound("product_not_found")

    # User physically scanned the barcode → trust them ; ``match_method``
    # is always ``'barcode'`` (the strongest signal in the cascade,
    # weight 1 in NRC consensus). The legacy coherence check via
    # ``match_product`` (consensus-only refonte 2026-05-02 made the
    # legacy matcher inert) was dropped : there is nothing to verify
    # against — the user's physical scan IS the verification.
    resolve_scan(db, scan, ean, match_method="barcode")

    # NRC bloc C — record the barcode-validation in the name-resolution
    # ledger. The user physically scanning the barcode is the strongest
    # contributing signal (method='barcode', weight 1) and feeds the
    # crowdsourced consensus computation per
    # ARCH_name_resolution_consensus.md.
    #
    # Skip when there is no store_id (rare for receipts ; happens when
    # the OCR/store cascade left the receipt unresolved). The ledger is
    # keyed on (store_id, normalized_label) — without a store there is
    # no meaningful per-store consensus to feed.
    if scan.store_id and scan.scanned_name:
        normalized_label = normalize_text(db, scan.scanned_name)
        record_resolution(
            db,
            scan_id=scan.id,
            store_id=scan.store_id,
            normalized_label=normalized_label,
            product_ean=ean,
            user_id=user_id,
            match_method="barcode",
            source_type="receipt",
        )
    elif not scan.store_id:
        logger.info(
            "Skipping NRC ledger write for scan %s — no store_id (receipt store unresolved)",
            scan.id,
        )

    globally_verified = False
    if scan.store_id:
        upsert_price_consensus(db, scan)
        consensus = get_local_price(db, scan.store_id, ean)
        if consensus:
            globally_verified = float(consensus.trust_score) >= _GLOBALLY_VERIFIED_THRESHOLD

    db.commit()

    return {
        "product": _product_payload(product),
        "resolved_scan": {
            "scan_id": str(scan.id),
            "scanned_name": scan.scanned_name,
            "product_ean": ean,
            "match_method": "barcode",
            "user_verified": True,
            "globally_verified": globally_verified,
        },
    }


def _product_payload(product) -> "dict | None":
    if product is None:
        return None
    return ProductDetailResponse.model_validate(product).model_dump()
