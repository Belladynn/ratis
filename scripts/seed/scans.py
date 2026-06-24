"""Scans seed — Wave 3 : ~512 scans across 4 personas.

Approach **C** (validated 2026-05-08, ARCH § Roadmap line 310) :

    10 hardcoded narrative scenarios + remainder PRNG-seeded reproducible.

Volumes (per ARCH § Personas) :

    dev_alice    0 scans (empty state)
    dev_bob     47 receipt scans + 23 e-labels +  3 manual
    dev_charlie 312 receipt scans
    dev_diane    13 receipt scans (pre-DELETE, preserved)
    dev_admin    0 scans (service account)
    dev_eve    140 mixed scans + anti-fraud patterns (~25 mismatch +
                ~10 duplicate + ~8 geo outlier + ~5 implausible total
                + ~12 manual + ~80 honest)

The 10 narrative scenarios :

    1. OCR borderline       (bob)     — receipt with NULL purchased_at_with_time + sparse fields
    2. Unmatched            (bob)     — receipt-item scan where consensus didn't resolve
    3. Rejected             (charlie) — scan rejected (age policy)
    4. Pending fresh        (charlie) — receipt just submitted (now - 2min), no resolution yet
    5. 30+ items receipt    (charlie) — one receipt with 32 line items
    6. Battlepass tier-up   (bob)     — scan tagged tier-up trigger in cabecoin context
    7. Referral first-scan  (bob)     — scan tagged ``referral_first_scan=true`` in context
    8. Duplicate flagrant   (eve)     — 2 receipts same store + same total + within 30min
    9. Geo outlier          (eve)     — receipt at store #12 (10km away)
    10. EAN consensus mismatch (eve)  — scan votes EAN_A vs canonical EAN_B in price_consensus

Determinism
===========
A module-level ``random.Random(seed=42)`` drives every random choice ;
every UUID is generated through ``_random_uuid(rng)``. Two runs of
``make seed-rebuild`` produce identical row sets (assert by row count +
hash).

Side tables in scope
====================
- ``receipts``                 — 1 row per receipt scan (Bug 6 CHECK obeyed)
- ``scans``                    — N rows per receipt + 1 per electronic_label + 1 per manual
- ``price_consensus``          — 1 row per (store, product) with ≥2 scans
- ``price_consensus_scans``    — link table populated for the scans in the consensus
- ``cabecoin_transactions``    — 1 credit per accepted scan for bob/charlie (NOT eve / NOT diane)

Deferred to later waves (explicit cut, R33) :
- ``product_knowledge`` (OCR auto-learn samples) — Wave 5
- ``price_consensus_history``  — only relevant when a price changes ; not load-bearing for demo
- ``anti_fraud`` fingerprint columns on receipts — auto-populated by the pipeline,
  NOT pre-seeded (set NULL for seed rows)

Idempotency
===========
Re-runs detect existing scans via ``EXISTS (SELECT 1 FROM scans WHERE user_id IN
<personas> LIMIT 1)`` and short-circuit. Granular ``ON CONFLICT`` would
require a stable natural key (UniqueConstraint on
``user_id+store_id+product_ean+scanned_at``) — feasible but heavy ; the
short-circuit is simpler and matches Wave 2 idempotency style.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from ratis_core.models.gamification import CabecoinsTransaction
from ratis_core.models.price import PriceConsensus, PriceConsensusScans
from ratis_core.models.scan import Receipt, Scan
from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts.seed.products import SEED_PRODUCTS
from scripts.seed.stores import STORE_UUIDS
from scripts.seed.users import PERSONA_UUIDS

# ============================================================
# PRNG helpers — deterministic random choices
# ============================================================
PRNG_BASE_SEED = 42

# Per-persona seeds derived from the base. Distinct seeds across personas
# are MANDATORY — two persona generators seeded at the same value emit
# identical UUID sequences, which collides on every UNIQUE index that
# touches scan_id (e.g. ``uq_cabtx_scan_credit`` on
# ``cabecoin_transactions.reference_id WHERE direction='credit' AND
# reference_type='scan'``). The offsets are arbitrary but stable.
PRNG_SEEDS: dict[str, int] = {
    "bob": PRNG_BASE_SEED + 0,
    "charlie": PRNG_BASE_SEED + 1,
    "diane": PRNG_BASE_SEED + 2,
    "eve": PRNG_BASE_SEED + 3,
}


def _now() -> datetime:
    """Helper — timezone-aware UTC now."""
    return datetime.now(UTC)


def _make_rng(persona: str) -> random.Random:
    """Return a fresh PRNG seeded for ``persona``.

    Each persona has its OWN stable seed (see :data:`PRNG_SEEDS`) so re-runs
    are reproducible AND personas never collide on generated UUIDs.
    """
    return random.Random(PRNG_SEEDS[persona])


def _random_uuid(rng: random.Random) -> uuid.UUID:
    """Generate a deterministic UUID4 from ``rng`` (128 random bits)."""
    return uuid.UUID(int=rng.getrandbits(128), version=4)


def _choose(rng: random.Random, sequence):
    """Drop-in for ``random.choice`` that goes through the seeded RNG."""
    return sequence[rng.randrange(len(sequence))]


# ============================================================
# Catalogue — product subsets per persona shopping profile
# ============================================================
# Use real EANs from products.py (Wave 2).
_FOOD_EANS = [p["ean"] for p in SEED_PRODUCTS]  # 25 EANs
_VRAC_EANS = [p["ean"] for p in SEED_PRODUCTS if p["source"] == "internal"]

# Price floor / ceiling (cents). Realistic French supermarket distribution
# of food items in 2026 — 80c → 12€.
_PRICE_MIN = 80
_PRICE_MAX = 1_200

# Ring-2km stores most personas hit daily.
_LOCAL_STORE_IDS = [STORE_UUIDS[i] for i in range(1, 9)]
# Larger ring (charlie drives further).
_DRIVE_STORE_IDS = [STORE_UUIDS[i] for i in range(1, 12)]


# ============================================================
# Consensus tracker — accumulates votes for post-pass aggregation
# ============================================================
@dataclass
class _ConsensusVote:
    """One scan's contribution to the (store, product) consensus."""

    scan_id: uuid.UUID
    price: int  # cents
    seen_at: datetime
    agrees: bool = True  # eve mismatch scans flip this to False


@dataclass
class _Counters:
    """Per-run tally surfaced in the final log line + tests."""

    receipts: int = 0
    scans: int = 0
    consensus_rows: int = 0
    consensus_links: int = 0
    cab_credits: int = 0
    scenarios: dict[str, int] = field(default_factory=dict)


# ============================================================
# Low-level builders
# ============================================================
def _build_receipt(
    *,
    rid: uuid.UUID,
    user_id: uuid.UUID,
    store_id: uuid.UUID | None,
    purchased_at: date,
    total_amount: int,
    line_count: int,
    purchased_at_with_time: datetime | None = None,
    store_status: str = "confirmed",
) -> Receipt:
    """Construct a Receipt row with seed-safe defaults.

    Anti-fraud fingerprint columns are left NULL — the pipeline populates
    them on the hot-path only ; pre-seeding would defeat the dedup index.
    """
    return Receipt(
        id=rid,
        user_id=user_id,
        store_id=store_id,
        purchased_at=purchased_at,
        purchased_at_with_time=purchased_at_with_time,
        total_amount=total_amount,
        total_lines_detected=line_count,
        store_status=store_status,
        # All anti-fraud + barcode_v1 fields → NULL (legacy / pre-pipeline).
    )


def _build_receipt_scan(
    *,
    sid: uuid.UUID,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    receipt_id: uuid.UUID,
    product_ean: str,
    price: int,
    scanned_at: datetime,
    status: str = "accepted",
    match_method: str | None = "consensus_match",
    rejected_reason: str | None = None,
    quantity: float = 1.0,
) -> Scan:
    """Receipt-typed scan (Bug 6 — ``receipt_id`` MUST be set)."""
    return Scan(
        id=sid,
        user_id=user_id,
        store_id=store_id,
        product_ean=product_ean,
        scanned_name=None,
        price=price,
        quantity=Decimal(str(quantity)),
        tva_amount=None,
        scan_type="receipt",
        receipt_id=receipt_id,
        status=status,
        match_method=match_method if status in ("accepted", "matched") else None,
        rejected_reason=rejected_reason,
        scanned_at=scanned_at,
        status_updated_at=scanned_at,
        store_status="confirmed",
    )


def _build_label_scan(
    *,
    sid: uuid.UUID,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    product_ean: str,
    price: int,
    scanned_at: datetime,
) -> Scan:
    """Electronic-label scan (no receipt). Bug 6 — ``receipt_id IS NULL``."""
    return Scan(
        id=sid,
        user_id=user_id,
        store_id=store_id,
        product_ean=product_ean,
        scanned_name=None,
        price=price,
        quantity=Decimal("1"),
        tva_amount=None,
        scan_type="electronic_label",
        receipt_id=None,
        status="accepted",
        match_method="consensus_match",
        scanned_at=scanned_at,
        status_updated_at=scanned_at,
        store_status="confirmed",
    )


def _build_manual_scan(
    *,
    sid: uuid.UUID,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    product_ean: str,
    price: int,
    scanned_at: datetime,
) -> Scan:
    """Manual scan (Bug 6 — product_ean NOT NULL, scanned_name MUST be NULL)."""
    return Scan(
        id=sid,
        user_id=user_id,
        store_id=store_id,
        product_ean=product_ean,
        scanned_name=None,
        price=price,
        quantity=Decimal("1"),
        tva_amount=None,
        scan_type="manual",
        receipt_id=None,
        status="accepted",
        match_method="manual",
        scanned_at=scanned_at,
        status_updated_at=scanned_at,
        store_status="confirmed",
    )


def _credit_cab(
    *,
    user_id: uuid.UUID,
    scan_id: uuid.UUID,
    amount: int,
    created_at: datetime,
    context: dict | None = None,
) -> CabecoinsTransaction:
    """1 cabecoin credit per accepted scan. Reason ``receipt_scan``."""
    return CabecoinsTransaction(
        id=uuid.uuid4(),  # cab_tx id doesn't need to be deterministic
        user_id=user_id,
        direction="credit",
        amount=amount,
        reason="receipt_scan",
        created_at=created_at,
        reference_id=scan_id,
        reference_type="scan",
        context=context,
    )


# ============================================================
# Bulk persona generators — PRNG-driven
# ============================================================
def _gen_random_receipt(
    rng: random.Random,
    *,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    scanned_at: datetime,
    line_count: int,
    status_distribution: list[tuple[str, float]],
    counters: _Counters,
    votes: list[_ConsensusVote],
    grant_cab: bool,
) -> tuple[Receipt, list[Scan], list[CabecoinsTransaction]]:
    """Generate a random receipt + N scans + optional CAB credit.

    ``status_distribution`` is a list of ``(status, weight)`` tuples : the
    receipt's overall classification is drawn from it, then propagated to
    each line scan (so a 'rejected' receipt produces 'rejected' scans, etc.).
    """
    receipt_id = _random_uuid(rng)
    statuses, weights = zip(*status_distribution, strict=True)
    receipt_status = rng.choices(statuses, weights=weights, k=1)[0]

    # Pick distinct EANs for line items.
    eans = rng.sample(_FOOD_EANS, k=min(line_count, len(_FOOD_EANS)))
    prices = [rng.randint(_PRICE_MIN, _PRICE_MAX) for _ in eans]
    total_amount = sum(prices)
    purchased_at = scanned_at.date()
    # 70% of receipts have a parsed time-to-the-second (OCR usually finds it).
    purchased_at_with_time = scanned_at.replace(tzinfo=None) if rng.random() < 0.70 else None

    receipt = _build_receipt(
        rid=receipt_id,
        user_id=user_id,
        store_id=store_id,
        purchased_at=purchased_at,
        total_amount=total_amount,
        line_count=len(eans),
        purchased_at_with_time=purchased_at_with_time,
    )

    line_scans: list[Scan] = []
    cab_txs: list[CabecoinsTransaction] = []
    rejected_reason = None
    match_method: str | None = "consensus_match"
    if receipt_status == "rejected":
        rejected_reason = "scan_too_old"
        match_method = None
    elif receipt_status == "pending" or receipt_status == "unmatched":
        match_method = None

    for idx, (ean, px) in enumerate(zip(eans, prices, strict=True)):
        sid = _random_uuid(rng)
        # Jitter scanned_at per line by ``idx`` microseconds so the per-row
        # UNIQUE (user_id, store_id, product_ean, scanned_at) constraint
        # cannot fire across receipts that happen to share a minute. In
        # prod each Scan from a receipt OCR also gets a distinct timestamp
        # because the worker timestamps lines one by one.
        line_scanned_at = scanned_at + timedelta(microseconds=idx)
        s = _build_receipt_scan(
            sid=sid,
            user_id=user_id,
            store_id=store_id,
            receipt_id=receipt_id,
            product_ean=ean,
            price=px,
            scanned_at=line_scanned_at,
            status=receipt_status,
            match_method=match_method,
            rejected_reason=rejected_reason,
        )
        line_scans.append(s)
        # Only accepted scans contribute to consensus + earn CAB.
        if receipt_status == "accepted":
            votes.append(_ConsensusVote(scan_id=sid, price=px, seen_at=line_scanned_at))
            if grant_cab:
                cab_txs.append(
                    _credit_cab(
                        user_id=user_id,
                        scan_id=sid,
                        amount=2,
                        created_at=line_scanned_at,
                    )
                )

    counters.receipts += 1
    counters.scans += len(line_scans)
    counters.cab_credits += len(cab_txs)
    return receipt, line_scans, cab_txs


# ============================================================
# 10 narrative scenarios — hardcoded
# ============================================================
def _scenario_ocr_borderline_bob(
    bob_id: uuid.UUID,
    counters: _Counters,
    votes: list[_ConsensusVote],
) -> tuple[Receipt, list[Scan]]:
    """Scenario 1 — OCR borderline : receipt with NULL purchased_at_with_time,
    sparse fields (no time, no tva). Status='pending' awaiting human review."""
    rid = uuid.UUID("11111111-1111-1111-1111-000000000001")
    sid = uuid.UUID("22222222-2222-2222-2222-000000000001")
    scanned_at = _now() - timedelta(days=3, hours=4)
    receipt = _build_receipt(
        rid=rid,
        user_id=bob_id,
        store_id=STORE_UUIDS[1],
        purchased_at=scanned_at.date(),
        total_amount=487,
        line_count=1,
        purchased_at_with_time=None,  # borderline OCR — no time parsed
        store_status="pending",  # store_id present but resolution low confidence
    )
    scan = _build_receipt_scan(
        sid=sid,
        user_id=bob_id,
        store_id=STORE_UUIDS[1],
        receipt_id=rid,
        product_ean=_FOOD_EANS[0],
        price=487,
        scanned_at=scanned_at,
        status="pending",
        match_method=None,
    )
    # store_status mirrors receipt
    scan.store_status = "pending"
    counters.receipts += 1
    counters.scans += 1
    counters.scenarios["ocr_borderline_bob"] = 1
    return receipt, [scan]


def _scenario_unmatched_bob(
    bob_id: uuid.UUID,
    counters: _Counters,
) -> tuple[Receipt, list[Scan]]:
    """Scenario 2 — Unmatched : consensus engine couldn't resolve this line."""
    rid = uuid.UUID("11111111-1111-1111-1111-000000000002")
    sid = uuid.UUID("22222222-2222-2222-2222-000000000002")
    scanned_at = _now() - timedelta(days=14)
    receipt = _build_receipt(
        rid=rid,
        user_id=bob_id,
        store_id=STORE_UUIDS[2],
        purchased_at=scanned_at.date(),
        total_amount=159,
        line_count=1,
        purchased_at_with_time=scanned_at.replace(tzinfo=None),
    )
    # 'unmatched' is a legacy status (kept in CHECK enum) — line couldn't be
    # tied to a known EAN. product_ean still set (it's the OCR hint), but
    # match_method=NULL signals the engine bailed.
    scan = _build_receipt_scan(
        sid=sid,
        user_id=bob_id,
        store_id=STORE_UUIDS[2],
        receipt_id=rid,
        product_ean=_FOOD_EANS[1],
        price=159,
        scanned_at=scanned_at,
        status="unmatched",
        match_method=None,
    )
    counters.receipts += 1
    counters.scans += 1
    counters.scenarios["unmatched_bob"] = 1
    return receipt, [scan]


def _scenario_rejected_charlie(
    charlie_id: uuid.UUID,
    counters: _Counters,
) -> tuple[Receipt, list[Scan]]:
    """Scenario 3 — Rejected : age policy (receipt >7 days when scanned)."""
    rid = uuid.UUID("11111111-1111-1111-1111-000000000003")
    sid = uuid.UUID("22222222-2222-2222-2222-000000000003")
    scanned_at = _now() - timedelta(days=90)
    receipt = _build_receipt(
        rid=rid,
        user_id=charlie_id,
        store_id=STORE_UUIDS[3],
        purchased_at=scanned_at.date() - timedelta(days=10),  # 10d old
        total_amount=349,
        line_count=1,
        purchased_at_with_time=scanned_at.replace(tzinfo=None),
    )
    scan = _build_receipt_scan(
        sid=sid,
        user_id=charlie_id,
        store_id=STORE_UUIDS[3],
        receipt_id=rid,
        product_ean=_FOOD_EANS[2],
        price=349,
        scanned_at=scanned_at,
        status="rejected",
        match_method=None,
        rejected_reason="receipt_too_old",
    )
    counters.receipts += 1
    counters.scans += 1
    counters.scenarios["rejected_charlie"] = 1
    return receipt, [scan]


def _scenario_pending_fresh_charlie(
    charlie_id: uuid.UUID,
    counters: _Counters,
) -> tuple[Receipt, list[Scan]]:
    """Scenario 4 — Pending fresh : receipt just uploaded (-2min), no resolution."""
    rid = uuid.UUID("11111111-1111-1111-1111-000000000004")
    sid = uuid.UUID("22222222-2222-2222-2222-000000000004")
    scanned_at = _now() - timedelta(minutes=2)
    receipt = _build_receipt(
        rid=rid,
        user_id=charlie_id,
        store_id=STORE_UUIDS[4],
        purchased_at=scanned_at.date(),
        total_amount=1_249,
        line_count=1,
        purchased_at_with_time=scanned_at.replace(tzinfo=None),
    )
    scan = _build_receipt_scan(
        sid=sid,
        user_id=charlie_id,
        store_id=STORE_UUIDS[4],
        receipt_id=rid,
        product_ean=_FOOD_EANS[3],
        price=1_249,
        scanned_at=scanned_at,
        status="pending",
        match_method=None,
    )
    counters.receipts += 1
    counters.scans += 1
    counters.scenarios["pending_fresh_charlie"] = 1
    return receipt, [scan]


def _scenario_big_receipt_charlie(
    charlie_id: uuid.UUID,
    rng: random.Random,
    counters: _Counters,
    votes: list[_ConsensusVote],
) -> tuple[Receipt, list[Scan], list[CabecoinsTransaction]]:
    """Scenario 5 — 30+ items receipt : one big weekly haul (32 lines)."""
    rid = uuid.UUID("11111111-1111-1111-1111-000000000005")
    scanned_at = _now() - timedelta(days=30)
    line_count = 32  # ≥30 per spec
    # Cycle through the 25 EANs + add duplicates for the remainder.
    eans = (_FOOD_EANS * 2)[:line_count]
    prices = [rng.randint(_PRICE_MIN, _PRICE_MAX) for _ in eans]
    total_amount = sum(prices)

    receipt = _build_receipt(
        rid=rid,
        user_id=charlie_id,
        store_id=STORE_UUIDS[9],  # Auchan, drive
        purchased_at=scanned_at.date(),
        total_amount=total_amount,
        line_count=line_count,
        purchased_at_with_time=scanned_at.replace(tzinfo=None),
    )
    scans: list[Scan] = []
    cab_txs: list[CabecoinsTransaction] = []
    for i, (ean, px) in enumerate(zip(eans, prices, strict=True)):
        sid = uuid.UUID(f"55555555-5555-5555-5555-{i:012d}")
        # Jitter per-line scanned_at by i microseconds — `eans` cycles
        # through `_FOOD_EANS * 2` for the 32-line receipt, so the same
        # EAN appears twice ; without jitter the per-row UNIQUE (user,
        # store, ean, scanned_at) would fire.
        line_scanned_at = scanned_at + timedelta(microseconds=i)
        s = _build_receipt_scan(
            sid=sid,
            user_id=charlie_id,
            store_id=STORE_UUIDS[9],
            receipt_id=rid,
            product_ean=ean,
            price=px,
            scanned_at=line_scanned_at,
            status="accepted",
        )
        scans.append(s)
        votes.append(_ConsensusVote(scan_id=sid, price=px, seen_at=line_scanned_at))
        cab_txs.append(
            _credit_cab(
                user_id=charlie_id,
                scan_id=sid,
                amount=2,
                created_at=line_scanned_at,
            )
        )
    counters.receipts += 1
    counters.scans += len(scans)
    counters.cab_credits += len(cab_txs)
    counters.scenarios["big_receipt_charlie"] = 1
    return receipt, scans, cab_txs


def _scenario_battlepass_tier_up_bob(
    bob_id: uuid.UUID,
    counters: _Counters,
    votes: list[_ConsensusVote],
) -> tuple[Receipt, list[Scan], list[CabecoinsTransaction]]:
    """Scenario 6 — Battlepass tier-up trigger : a scan that bumped bob from
    tier 7→8. Wave 4 wires the real tier-up effect ; for now we tag the
    cabecoin_transactions.context so a UI/audit query can find it.
    """
    rid = uuid.UUID("11111111-1111-1111-1111-000000000006")
    sid = uuid.UUID("22222222-2222-2222-2222-000000000006")
    scanned_at = _now() - timedelta(days=7)
    receipt = _build_receipt(
        rid=rid,
        user_id=bob_id,
        store_id=STORE_UUIDS[1],
        purchased_at=scanned_at.date(),
        total_amount=799,
        line_count=1,
        purchased_at_with_time=scanned_at.replace(tzinfo=None),
    )
    scan = _build_receipt_scan(
        sid=sid,
        user_id=bob_id,
        store_id=STORE_UUIDS[1],
        receipt_id=rid,
        product_ean=_FOOD_EANS[4],
        price=799,
        scanned_at=scanned_at,
    )
    votes.append(_ConsensusVote(scan_id=sid, price=799, seen_at=scanned_at))
    cab = _credit_cab(
        user_id=bob_id,
        scan_id=sid,
        amount=2,
        created_at=scanned_at,
        context={"scenario": "battlepass_tier_up", "tier_before": 7, "tier_after": 8},
    )
    counters.receipts += 1
    counters.scans += 1
    counters.cab_credits += 1
    counters.scenarios["battlepass_tier_up_bob"] = 1
    return receipt, [scan], [cab]


def _scenario_referral_first_scan_bob(
    bob_id: uuid.UUID,
    counters: _Counters,
    votes: list[_ConsensusVote],
) -> tuple[Receipt, list[Scan], list[CabecoinsTransaction]]:
    """Scenario 7 — Referral first scan : bob's first-ever scan post-referral.
    Tagged in cabecoin context for the referral_payout batch / audit UI.
    """
    rid = uuid.UUID("11111111-1111-1111-1111-000000000007")
    sid = uuid.UUID("22222222-2222-2222-2222-000000000007")
    # First scan = at the very start of bob's tenure (-120 days).
    scanned_at = _now() - timedelta(days=119, hours=22)
    receipt = _build_receipt(
        rid=rid,
        user_id=bob_id,
        store_id=STORE_UUIDS[2],
        purchased_at=scanned_at.date(),
        total_amount=345,
        line_count=1,
        purchased_at_with_time=scanned_at.replace(tzinfo=None),
    )
    scan = _build_receipt_scan(
        sid=sid,
        user_id=bob_id,
        store_id=STORE_UUIDS[2],
        receipt_id=rid,
        product_ean=_FOOD_EANS[5],
        price=345,
        scanned_at=scanned_at,
    )
    votes.append(_ConsensusVote(scan_id=sid, price=345, seen_at=scanned_at))
    cab = _credit_cab(
        user_id=bob_id,
        scan_id=sid,
        amount=2,
        created_at=scanned_at,
        context={"scenario": "referral_first_scan", "referrer_role": "filleul"},
    )
    counters.receipts += 1
    counters.scans += 1
    counters.cab_credits += 1
    counters.scenarios["referral_first_scan_bob"] = 1
    return receipt, [scan], [cab]


def _scenario_duplicate_flagrant_eve(
    eve_id: uuid.UUID,
    counters: _Counters,
) -> tuple[list[Receipt], list[Scan]]:
    """Scenario 8 — Duplicate flagrant : 2 eve receipts at same store, same total,
    within 30min. Triggers anti-fraud cross-receipt dedup nightly batch.
    """
    rid1 = uuid.UUID("11111111-1111-1111-1111-000000000008")
    rid2 = uuid.UUID("11111111-1111-1111-1111-000000000088")
    sid1 = uuid.UUID("22222222-2222-2222-2222-000000000008")
    sid2 = uuid.UUID("22222222-2222-2222-2222-000000000088")
    t1 = _now() - timedelta(days=40, hours=2)
    t2 = t1 + timedelta(minutes=18)  # within 30min
    store = STORE_UUIDS[3]
    total = 1_499

    # Use distinct ``purchased_at_with_time`` so the unique partial index
    # ``receipts_semantic_dedup_key`` (store_id, purchased_at_with_time,
    # total_amount) doesn't fire — the duplicate is *detected by the batch*,
    # not blocked at INSERT. The batch keys on a wider window (30min) than
    # the strict equality of the index.
    pa1 = t1.replace(tzinfo=None)
    pa2 = t2.replace(tzinfo=None)

    r1 = _build_receipt(
        rid=rid1,
        user_id=eve_id,
        store_id=store,
        purchased_at=t1.date(),
        total_amount=total,
        line_count=1,
        purchased_at_with_time=pa1,
    )
    r2 = _build_receipt(
        rid=rid2,
        user_id=eve_id,
        store_id=store,
        purchased_at=t2.date(),
        total_amount=total,
        line_count=1,
        purchased_at_with_time=pa2,
    )
    s1 = _build_receipt_scan(
        sid=sid1,
        user_id=eve_id,
        store_id=store,
        receipt_id=rid1,
        product_ean=_FOOD_EANS[6],
        price=total,
        scanned_at=t1,
    )
    s2 = _build_receipt_scan(
        sid=sid2,
        user_id=eve_id,
        store_id=store,
        receipt_id=rid2,
        product_ean=_FOOD_EANS[6],
        price=total,
        scanned_at=t2,
    )
    counters.receipts += 2
    counters.scans += 2
    counters.scenarios["duplicate_flagrant_eve"] = 1
    return [r1, r2], [s1, s2]


def _scenario_geo_outlier_eve(
    eve_id: uuid.UUID,
    counters: _Counters,
) -> tuple[Receipt, list[Scan]]:
    """Scenario 9 — Geo outlier : eve receipt at store #12 (10km away) when
    most scans are local. Drives geo anomaly detection in nightly batch.
    """
    rid = uuid.UUID("11111111-1111-1111-1111-000000000009")
    sid = uuid.UUID("22222222-2222-2222-2222-000000000009")
    scanned_at = _now() - timedelta(days=55, hours=5)
    receipt = _build_receipt(
        rid=rid,
        user_id=eve_id,
        store_id=STORE_UUIDS[12],
        purchased_at=scanned_at.date(),
        total_amount=1_899,
        line_count=1,
        purchased_at_with_time=scanned_at.replace(tzinfo=None),
    )
    scan = _build_receipt_scan(
        sid=sid,
        user_id=eve_id,
        store_id=STORE_UUIDS[12],
        receipt_id=rid,
        product_ean=_FOOD_EANS[7],
        price=1_899,
        scanned_at=scanned_at,
    )
    counters.receipts += 1
    counters.scans += 1
    counters.scenarios["geo_outlier_eve"] = 1
    return receipt, [scan]


def _scenario_ean_mismatch_eve(
    eve_id: uuid.UUID,
    counters: _Counters,
    votes: list[_ConsensusVote],
) -> tuple[Receipt, list[Scan]]:
    """Scenario 10 — EAN consensus mismatch : eve scan votes a wrong EAN
    against the canonical top-1 EAN in price_consensus. The mismatch is
    flagged at the consensus level (``_ConsensusVote.agrees=False``) so
    the batch can recompute trust_score correctly.
    """
    rid = uuid.UUID("11111111-1111-1111-1111-00000000000a")
    sid = uuid.UUID("22222222-2222-2222-2222-00000000000a")
    scanned_at = _now() - timedelta(days=20)
    # The consensus's canonical top-1 EAN is ``_FOOD_EANS[0]`` ; eve votes
    # for ``_FOOD_EANS[1]`` instead. The mismatch is recorded as a vote with
    # ``agrees=False`` so the post-pass aggregation classifies it correctly.
    mismatch_ean = _FOOD_EANS[1]
    receipt = _build_receipt(
        rid=rid,
        user_id=eve_id,
        store_id=STORE_UUIDS[1],
        purchased_at=scanned_at.date(),
        total_amount=429,
        line_count=1,
        purchased_at_with_time=scanned_at.replace(tzinfo=None),
    )
    scan = _build_receipt_scan(
        sid=sid,
        user_id=eve_id,
        store_id=STORE_UUIDS[1],
        receipt_id=rid,
        product_ean=mismatch_ean,
        price=429,
        scanned_at=scanned_at,
    )
    # Vote with agrees=False so the post-pass consensus rollup counts this
    # as a contribution to the disagreement ratio (drives ratio ↘).
    votes.append(
        _ConsensusVote(
            scan_id=sid,
            price=429,
            seen_at=scanned_at,
            agrees=False,
        )
    )
    counters.receipts += 1
    counters.scans += 1
    counters.scenarios["ean_mismatch_eve"] = 1
    return receipt, [scan]


# ============================================================
# Persona bulk generators
# ============================================================
def _seed_bob(
    session: Session,
    counters: _Counters,
    votes: list[_ConsensusVote],
) -> None:
    """🔵 dev_bob — 47 receipts + 23 e-labels + 3 manual scans over 4 months.

    Spec :
      - status mix (receipts) : ~35 accepted / ~6 unmatched / ~4 pending / ~2 rejected
      - stores 1-8 (ring 2km)
      - 3-8 items per receipt
    """
    bob_id = PERSONA_UUIDS["bob"]
    rng = _make_rng("bob")
    now = _now()
    base = now - timedelta(days=120)

    # 4 of the 47 receipts are scenario-anchored (ocr_borderline + unmatched +
    # battlepass_tier_up + referral_first_scan) → 43 bulk receipts driven by
    # PRNG. Scenarios are added separately below.
    BULK_RECEIPTS = 47 - 4

    # Status distribution for the bulk : ~33 accepted / 4 unmatched / 4 pending / 2 rejected
    # (the scenario receipts already cover 2 accepted + 1 unmatched + 1 pending).
    status_distrib: list[tuple[str, float]] = [
        ("accepted", 33),
        ("unmatched", 4),
        ("pending", 4),
        ("rejected", 2),
    ]

    for i in range(BULK_RECEIPTS):
        # ``microseconds=i * 1000`` makes every receipt's base timestamp
        # unique even if two receipts land on the same minute — this is
        # required for the per-row UNIQUE (user, store, ean, scanned_at)
        # to hold over high-volume seeds (charlie = 312 receipts/year).
        scanned_at = base + timedelta(
            days=int(i * 120 / BULK_RECEIPTS),
            hours=rng.randint(0, 23),
            minutes=rng.randint(0, 59),
            seconds=rng.randint(0, 59),
            microseconds=i * 1000,
        )
        store = _choose(rng, _LOCAL_STORE_IDS)
        line_count = rng.randint(3, 8)
        receipt, line_scans, cab_txs = _gen_random_receipt(
            rng,
            user_id=bob_id,
            store_id=store,
            scanned_at=scanned_at,
            line_count=line_count,
            status_distribution=status_distrib,
            counters=counters,
            votes=votes,
            grant_cab=True,
        )
        session.add(receipt)
        session.add_all(line_scans)
        session.add_all(cab_txs)

    # Narrative scenarios anchored to bob.
    r1, s1 = _scenario_ocr_borderline_bob(bob_id, counters, votes)
    session.add(r1)
    session.add_all(s1)
    r2, s2 = _scenario_unmatched_bob(bob_id, counters)
    session.add(r2)
    session.add_all(s2)
    r6, s6, cab6 = _scenario_battlepass_tier_up_bob(bob_id, counters, votes)
    session.add(r6)
    session.add_all(s6)
    session.add_all(cab6)
    r7, s7, cab7 = _scenario_referral_first_scan_bob(bob_id, counters, votes)
    session.add(r7)
    session.add_all(s7)
    session.add_all(cab7)

    # 23 electronic_label scans (no receipt). Spread evenly.
    for i in range(23):
        scanned_at = base + timedelta(
            days=int(i * 120 / 23),
            hours=rng.randint(8, 20),
            minutes=rng.randint(0, 59),
            seconds=rng.randint(0, 59),
            microseconds=(600 + i) * 1000,
        )
        store = _choose(rng, _LOCAL_STORE_IDS)
        ean = _choose(rng, _FOOD_EANS)
        price = rng.randint(_PRICE_MIN, _PRICE_MAX)
        sid = _random_uuid(rng)
        scan = _build_label_scan(
            sid=sid,
            user_id=bob_id,
            store_id=store,
            product_ean=ean,
            price=price,
            scanned_at=scanned_at,
        )
        session.add(scan)
        votes.append(_ConsensusVote(scan_id=sid, price=price, seen_at=scanned_at))
        # Label scans earn CAB too (reason=label_scan).
        session.add(
            CabecoinsTransaction(
                id=uuid.uuid4(),
                user_id=bob_id,
                direction="credit",
                amount=1,
                reason="label_scan",
                created_at=scanned_at,
                reference_id=sid,
                reference_type="scan",
            )
        )
        counters.scans += 1
        counters.cab_credits += 1

    # 3 manual scans (Bug 6 — product_ean set, scanned_name MUST be NULL).
    for i in range(3):
        scanned_at = base + timedelta(
            days=int(i * 120 / 3),
            hours=10,
            minutes=rng.randint(0, 59),
            seconds=rng.randint(0, 59),
            microseconds=(700 + i) * 1000,
        )
        store = _choose(rng, _LOCAL_STORE_IDS)
        # Manual = bulk produce sample.
        ean = _choose(rng, _VRAC_EANS) if _VRAC_EANS else _choose(rng, _FOOD_EANS)
        price = rng.randint(_PRICE_MIN, _PRICE_MAX)
        sid = _random_uuid(rng)
        scan = _build_manual_scan(
            sid=sid,
            user_id=bob_id,
            store_id=store,
            product_ean=ean,
            price=price,
            scanned_at=scanned_at,
        )
        session.add(scan)
        counters.scans += 1

    session.flush()


def _seed_charlie(
    session: Session,
    counters: _Counters,
    votes: list[_ConsensusVote],
) -> None:
    """🟣 dev_charlie — 312 receipts over 1 year, premium power user.

    Spec :
      - ~280 accepted / ~15 unmatched / ~12 pending / ~5 rejected
      - stores 1-11 (ring 2km + drive 2-5km)
      - mostly 5-15 items
      - 3 narrative scenarios anchored : rejected, pending_fresh, big_receipt
    """
    charlie_id = PERSONA_UUIDS["charlie"]
    rng = _make_rng("charlie")
    now = _now()
    base = now - timedelta(days=365)

    # 3 scenarios anchored → 309 bulk receipts.
    BULK_RECEIPTS = 312 - 3

    status_distrib: list[tuple[str, float]] = [
        ("accepted", 280),
        ("unmatched", 14),
        ("pending", 11),
        ("rejected", 4),
    ]

    for i in range(BULK_RECEIPTS):
        scanned_at = base + timedelta(
            days=int(i * 365 / BULK_RECEIPTS),
            hours=rng.randint(0, 23),
            minutes=rng.randint(0, 59),
            seconds=rng.randint(0, 59),
            microseconds=i * 1000,
        )
        store = _choose(rng, _DRIVE_STORE_IDS)
        line_count = rng.randint(5, 15)
        receipt, line_scans, cab_txs = _gen_random_receipt(
            rng,
            user_id=charlie_id,
            store_id=store,
            scanned_at=scanned_at,
            line_count=line_count,
            status_distribution=status_distrib,
            counters=counters,
            votes=votes,
            grant_cab=True,
        )
        session.add(receipt)
        session.add_all(line_scans)
        session.add_all(cab_txs)

    # Narrative scenarios.
    r3, s3 = _scenario_rejected_charlie(charlie_id, counters)
    session.add(r3)
    session.add_all(s3)
    r4, s4 = _scenario_pending_fresh_charlie(charlie_id, counters)
    session.add(r4)
    session.add_all(s4)
    r5, s5, cab5 = _scenario_big_receipt_charlie(charlie_id, rng, counters, votes)
    session.add(r5)
    session.add_all(s5)
    session.add_all(cab5)

    session.flush()


def _seed_diane(
    session: Session,
    counters: _Counters,
    votes: list[_ConsensusVote],
) -> None:
    """🟡 dev_diane — 13 receipts pre-DELETE. All accepted (clean user).

    Receipts persist post-DELETE (legal preservation). user_id points at
    the anonymised tombstone row already seeded in Wave 2. CAB credits
    were earned pre-DELETE → preserved (legal NEVER PURGE invariant for
    cashback_transactions, mirrored here for cabecoin_transactions).
    """
    diane_id = PERSONA_UUIDS["diane"]
    rng = _make_rng("diane")
    now = _now()
    # Created between -6 months and -2 months (DELETE was -2 months).
    base = now - timedelta(days=180)

    status_distrib: list[tuple[str, float]] = [("accepted", 13)]
    for i in range(13):
        scanned_at = base + timedelta(
            days=int(i * 120 / 13),
            hours=rng.randint(0, 23),
            minutes=rng.randint(0, 59),
            seconds=rng.randint(0, 59),
            microseconds=i * 1000,
        )
        store = _choose(rng, _LOCAL_STORE_IDS)
        line_count = rng.randint(3, 6)
        receipt, line_scans, cab_txs = _gen_random_receipt(
            rng,
            user_id=diane_id,
            store_id=store,
            scanned_at=scanned_at,
            line_count=line_count,
            status_distribution=status_distrib,
            counters=counters,
            votes=votes,
            grant_cab=True,
        )
        session.add(receipt)
        session.add_all(line_scans)
        session.add_all(cab_txs)

    session.flush()


def _seed_eve(
    session: Session,
    counters: _Counters,
    votes: list[_ConsensusVote],
) -> None:
    """🟠 dev_eve — 140 scans (80 honest + 25 mismatch + 10 duplicate
    + 8 geo outlier + 5 implausible + 12 manual). NO CAB credits
    (shadow-banned silent skip).

    The 4 narrative scenarios anchored here count against the per-bucket
    totals so the persona-level total stays at exactly 140.
    """
    eve_id = PERSONA_UUIDS["eve"]
    rng = _make_rng("eve")
    now = _now()
    base = now - timedelta(days=180)
    LOCAL_EVE = _LOCAL_STORE_IDS[:5]  # eve mostly shops at stores #1-#5

    # 1) 80 honest scan rows (≈80 receipts × 1 line each). status=accepted.
    for i in range(80):
        scanned_at = base + timedelta(
            days=int(i * 180 / 80),
            hours=rng.randint(0, 23),
            minutes=rng.randint(0, 59),
            seconds=rng.randint(0, 59),
            microseconds=i * 1000,
        )
        store = _choose(rng, LOCAL_EVE)
        ean = _choose(rng, _FOOD_EANS)
        price = rng.randint(_PRICE_MIN, _PRICE_MAX)
        rid = _random_uuid(rng)
        sid = _random_uuid(rng)
        receipt = _build_receipt(
            rid=rid,
            user_id=eve_id,
            store_id=store,
            purchased_at=scanned_at.date(),
            total_amount=price,
            line_count=1,
            purchased_at_with_time=scanned_at.replace(tzinfo=None),
        )
        scan = _build_receipt_scan(
            sid=sid,
            user_id=eve_id,
            store_id=store,
            receipt_id=rid,
            product_ean=ean,
            price=price,
            scanned_at=scanned_at,
        )
        session.add(receipt)
        session.add(scan)
        votes.append(_ConsensusVote(scan_id=sid, price=price, seen_at=scanned_at))
        # NO CAB credit — shadow-banned silent skip (ARCH § dev_eve).
        counters.receipts += 1
        counters.scans += 1

    # 2) 24 product_ean mismatch (+1 narrative scenario = 25 total).
    canonical = _FOOD_EANS[0]
    mismatch_choices = [e for e in _FOOD_EANS if e != canonical]
    for i in range(24):
        # Offset base hour by 4h so the mismatch bucket doesn't overlap with
        # the honest bucket's (i * 180/80) day schedule.
        scanned_at = base + timedelta(
            days=int(i * 180 / 24),
            hours=4 + rng.randint(0, 19),
            minutes=rng.randint(0, 59),
            seconds=rng.randint(0, 59),
            microseconds=(80 + i) * 1000,
        )
        store = _choose(rng, LOCAL_EVE)
        wrong = _choose(rng, mismatch_choices)
        price = rng.randint(_PRICE_MIN, _PRICE_MAX)
        rid = _random_uuid(rng)
        sid = _random_uuid(rng)
        receipt = _build_receipt(
            rid=rid,
            user_id=eve_id,
            store_id=store,
            purchased_at=scanned_at.date(),
            total_amount=price,
            line_count=1,
            purchased_at_with_time=scanned_at.replace(tzinfo=None),
        )
        scan = _build_receipt_scan(
            sid=sid,
            user_id=eve_id,
            store_id=store,
            receipt_id=rid,
            product_ean=wrong,
            price=price,
            scanned_at=scanned_at,
        )
        session.add(receipt)
        session.add(scan)
        votes.append(
            _ConsensusVote(
                scan_id=sid,
                price=price,
                seen_at=scanned_at,
                agrees=False,
            )
        )
        counters.receipts += 1
        counters.scans += 1
    # Plus narrative scenario for the 25th mismatch.
    r_mm, s_mm = _scenario_ean_mismatch_eve(eve_id, counters, votes)
    session.add(r_mm)
    session.add_all(s_mm)

    # 3) Duplicate flagrant — narrative scenario yields 2 scans.
    #    Plus 8 random duplicate pairs of the SAME (store,total) on the same
    #    day but with slightly different purchased_at_with_time so the index
    #    doesn't fire — to reach 10 duplicates total. Each pair = 2 scans →
    #    but only 8 duplicate rows count toward the 140 target (the scenario
    #    already contributed 2). So generate 8 *extra* duplicate scans.
    r_dups, s_dups = _scenario_duplicate_flagrant_eve(eve_id, counters)
    session.add_all(r_dups)
    session.add_all(s_dups)
    for i in range(8):
        scanned_at = base + timedelta(
            days=int(i * 180 / 8) + 1,
            hours=12,
            minutes=rng.randint(0, 59),
            seconds=rng.randint(0, 59),
            microseconds=(200 + i) * 1000,
        )
        store = _choose(rng, LOCAL_EVE)
        # Both receipts identical store + total + same date — different
        # micro-times so the partial unique idx (drops same-microsecond
        # collisions) doesn't trip.
        total = 999 + i  # vary slightly across pairs to avoid collisions
        rid = _random_uuid(rng)
        sid = _random_uuid(rng)
        receipt = _build_receipt(
            rid=rid,
            user_id=eve_id,
            store_id=store,
            purchased_at=scanned_at.date(),
            total_amount=total,
            line_count=1,
            purchased_at_with_time=scanned_at.replace(tzinfo=None),
        )
        scan = _build_receipt_scan(
            sid=sid,
            user_id=eve_id,
            store_id=store,
            receipt_id=rid,
            product_ean=_FOOD_EANS[8],
            price=total,
            scanned_at=scanned_at,
        )
        session.add(receipt)
        session.add(scan)
        counters.receipts += 1
        counters.scans += 1

    # 4) Geo outlier — narrative scenario yields 1 scan. Add 7 more at
    #    store #12 to reach 8 total.
    r_geo, s_geo = _scenario_geo_outlier_eve(eve_id, counters)
    session.add(r_geo)
    session.add_all(s_geo)
    for i in range(7):
        scanned_at = base + timedelta(
            days=30 + i * 12,
            hours=14,
            minutes=rng.randint(0, 59),
            seconds=rng.randint(0, 59),
            microseconds=(300 + i) * 1000,
        )
        ean = _choose(rng, _FOOD_EANS)
        price = rng.randint(_PRICE_MIN, _PRICE_MAX)
        rid = _random_uuid(rng)
        sid = _random_uuid(rng)
        receipt = _build_receipt(
            rid=rid,
            user_id=eve_id,
            store_id=STORE_UUIDS[12],
            purchased_at=scanned_at.date(),
            total_amount=price,
            line_count=1,
            purchased_at_with_time=scanned_at.replace(tzinfo=None),
        )
        scan = _build_receipt_scan(
            sid=sid,
            user_id=eve_id,
            store_id=STORE_UUIDS[12],
            receipt_id=rid,
            product_ean=ean,
            price=price,
            scanned_at=scanned_at,
        )
        session.add(receipt)
        session.add(scan)
        counters.receipts += 1
        counters.scans += 1

    # 5) 5 implausible total receipts (total > €400). 1 scan each.
    for i in range(5):
        scanned_at = base + timedelta(
            days=60 + i * 20,
            hours=11,
            minutes=rng.randint(0, 59),
            seconds=rng.randint(0, 59),
            microseconds=(400 + i) * 1000,
        )
        store = _choose(rng, LOCAL_EVE)
        price = rng.randint(40_000, 80_000)  # 400-800€ in cents
        rid = _random_uuid(rng)
        sid = _random_uuid(rng)
        receipt = _build_receipt(
            rid=rid,
            user_id=eve_id,
            store_id=store,
            purchased_at=scanned_at.date(),
            total_amount=price,
            line_count=1,
            purchased_at_with_time=scanned_at.replace(tzinfo=None),
        )
        scan = _build_receipt_scan(
            sid=sid,
            user_id=eve_id,
            store_id=store,
            receipt_id=rid,
            product_ean=_FOOD_EANS[9],
            price=price,
            scanned_at=scanned_at,
        )
        session.add(receipt)
        session.add(scan)
        counters.receipts += 1
        counters.scans += 1

    # 6) 12 manual entries with suspicious patterns (Bug 6 — scanned_name=NULL).
    for i in range(12):
        scanned_at = base + timedelta(
            days=15 + i * 12,
            hours=16,
            minutes=rng.randint(0, 59),
            seconds=rng.randint(0, 59),
            microseconds=(500 + i) * 1000,
        )
        store = _choose(rng, LOCAL_EVE)
        # Cycle products to look automated.
        ean = _FOOD_EANS[i % len(_FOOD_EANS)]
        price = rng.randint(_PRICE_MIN, _PRICE_MAX)
        sid = _random_uuid(rng)
        scan = _build_manual_scan(
            sid=sid,
            user_id=eve_id,
            store_id=store,
            product_ean=ean,
            price=price,
            scanned_at=scanned_at,
        )
        session.add(scan)
        counters.scans += 1

    session.flush()


# ============================================================
# Consensus aggregation (post-pass)
# ============================================================
def _aggregate_consensus(
    session: Session,
    votes: list[_ConsensusVote],
    counters: _Counters,
) -> None:
    """Group votes by (store_id, product_ean), insert one PriceConsensus row
    per group with ≥2 votes, link via PriceConsensusScans.

    The store_id + product_ean come from the originating scan rows ; we
    re-query them (single bulk SELECT) to map scan_id → (store, ean).
    """
    if not votes:
        return

    scan_ids = [v.scan_id for v in votes]
    rows = session.execute(select(Scan.id, Scan.store_id, Scan.product_ean).where(Scan.id.in_(scan_ids))).all()
    scan_meta = {sid: (st, ean) for sid, st, ean in rows}

    # Group : (store_id, ean) → list[_ConsensusVote]
    groups: dict[tuple[uuid.UUID, str], list[_ConsensusVote]] = {}
    for v in votes:
        meta = scan_meta.get(v.scan_id)
        if meta is None:
            continue
        store_id, ean = meta
        if store_id is None or ean is None:
            continue
        groups.setdefault((store_id, ean), []).append(v)

    for (store_id, ean), gvotes in groups.items():
        if len(gvotes) < 2:
            continue
        # Median price = consensus value.
        prices = sorted(v.price for v in gvotes if v.agrees)
        if not prices:
            continue
        median = prices[len(prices) // 2]
        first_seen = min(v.seen_at for v in gvotes)
        last_seen = max(v.seen_at for v in gvotes)
        agreed = sum(1 for v in gvotes if v.agrees)
        trust = Decimal(agreed * 100) / Decimal(len(gvotes))

        consensus = PriceConsensus(
            id=uuid.uuid4(),
            store_id=store_id,
            product_ean=ean,
            price=median,
            trust_score=trust.quantize(Decimal("0.01")),
            first_seen_at=first_seen,
            last_seen_at=last_seen,
        )
        session.add(consensus)
        counters.consensus_rows += 1
        # Link only the agreeing scans (consensus links are "this scan
        # supports this consensus row").
        for v in gvotes:
            if not v.agrees:
                continue
            session.add(
                PriceConsensusScans(
                    id=uuid.uuid4(),
                    consensus_id=consensus.id,
                    scan_id=v.scan_id,
                )
            )
            counters.consensus_links += 1


# ============================================================
# Public entrypoint
# ============================================================
def _already_seeded(session: Session) -> bool:
    """Idempotency probe : if any persona already has a scan row, skip."""
    persona_ids = [
        PERSONA_UUIDS["bob"],
        PERSONA_UUIDS["charlie"],
        PERSONA_UUIDS["diane"],
        PERSONA_UUIDS["eve"],
    ]
    existing = session.execute(select(Scan.id).where(Scan.user_id.in_(persona_ids)).limit(1)).first()
    return existing is not None


def seed_scans(session: Session) -> None:
    """Insert scans + receipts + consensus + CAB credits for personas.

    See ARCH_seed_test_data.md § Step 3 + module docstring. Idempotent —
    re-runs short-circuit if any persona already has scans.
    """
    if _already_seeded(session):
        print("[scans] already seeded — skipping (idempotent)")
        return

    print("[scans] seeding personas — bob/charlie/diane/eve…")
    counters = _Counters()
    votes: list[_ConsensusVote] = []

    _seed_bob(session, counters, votes)
    _seed_charlie(session, counters, votes)
    _seed_diane(session, counters, votes)
    _seed_eve(session, counters, votes)

    _aggregate_consensus(session, votes, counters)
    session.flush()

    print(
        f"[scans] done — {counters.receipts} receipts, {counters.scans} scans, "
        f"{counters.consensus_rows} price_consensus rows ({counters.consensus_links} links), "
        f"{counters.cab_credits} CAB credits, "
        f"{len(counters.scenarios)} narrative scenarios materialised"
    )
