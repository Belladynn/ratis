"""Price consensus computation — shared between ratis_product_analyser and ratis_batch_consensus.

Callers are responsible for converting SQLAlchemy row objects to plain tuples before
calling these functions so that ratis_core stays agnostic of SQLAlchemy.

Usage:
    window: Sequence[tuple[int, datetime]] = [
        (row.price, row.scanned_at) for row in db_rows
    ]
    trust_score = compute_trust_score(window, consensus.price, now, cfg)
    dominant_price, dominant_score = find_dominant_price(window, now, cfg)

Prices are INTEGER centimes throughout (post-migration a8b9c0d1e2f3).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal


def compute_trust_score(
    window: Sequence[tuple[int, datetime]],
    consensus_price: int,
    now: datetime,
    cfg: dict,
) -> Decimal:
    """Return the weighted trust score (0–100) for *consensus_price* over *window*.

    weight = max(scan_weight_floor, 1.0 - age_days × scan_weight_decay_per_day)
    trust_score = concordant_weight / total_weight × 100
    """
    decay_per_day: float = cfg["scan_weight_decay_per_day"]
    floor: float = cfg["scan_weight_floor"]

    score_total = Decimal("0")
    score_concordants = Decimal("0")

    for price, scanned_at in window:
        age_days = (now - scanned_at).total_seconds() / 86400
        weight = Decimal(str(round(max(floor, 1.0 - age_days * decay_per_day), 10)))
        score_total += weight
        if price == consensus_price:
            score_concordants += weight

    if score_total == 0:
        return Decimal("0.00")
    return (score_concordants / score_total * 100).quantize(Decimal("0.01"))


def find_dominant_price(
    window: Sequence[tuple[int, datetime]],
    now: datetime,
    cfg: dict,
) -> tuple[int | None, Decimal]:
    """Return the price with the highest weighted score and its score (0–100).

    Useful for detecting price basculement: if the returned price differs from the
    current consensus price AND its score is strictly greater, a switch is warranted.

    Returns (None, Decimal("0.00")) when the window is empty.
    """
    decay_per_day: float = cfg["scan_weight_decay_per_day"]
    floor: float = cfg["scan_weight_floor"]

    price_scores: dict[int, Decimal] = {}
    score_total = Decimal("0")

    for price, scanned_at in window:
        age_days = (now - scanned_at).total_seconds() / 86400
        weight = Decimal(str(round(max(floor, 1.0 - age_days * decay_per_day), 10)))
        price_scores[price] = price_scores.get(price, Decimal("0")) + weight
        score_total += weight

    if not price_scores or score_total == 0:
        return None, Decimal("0.00")

    dominant_price = max(price_scores, key=lambda p: price_scores[p])
    dominant_score = (price_scores[dominant_price] / score_total * 100).quantize(Decimal("0.01"))
    return dominant_price, dominant_score
