"""
Mystery product repository — raw SQL for atomicity.

All write operations work within the caller's session transaction.
The caller is responsible for commit() after all operations succeed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class NoEligibleProduct(Exception):
    """No product eligible for mystery challenge draw."""


class ChallengeOverlap(Exception):
    """A challenge already exists in the requested time window."""


class ChallengeNotModifiable(Exception):
    """Challenge can only be modified when status='scheduled'."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def resolve_cab_tier(reward_tiers: list[dict], rank: int) -> int:
    """
    Return the CAB amount for the given rank from the reward_tiers JSONB config.

    Each tier has: min_rank (int), max_rank (int | None), cab (int).
    If max_rank is None the tier applies to all ranks >= min_rank.
    Returns 0 if no tier matches (safe fallback).
    """
    for tier in reward_tiers:
        min_rank = tier["min_rank"]
        max_rank = tier.get("max_rank")
        if rank >= min_rank and (max_rank is None or rank <= max_rank):
            return tier["cab"]
    return 0


# ---------------------------------------------------------------------------
# Read queries
# ---------------------------------------------------------------------------


def get_active_challenge(db: Session) -> dict | None:
    """Return the most recent active or frozen challenge, or None."""
    row = db.execute(
        text(
            "SELECT id, product_ean, starts_at, ends_at, status, reward_tiers, created_at "
            "FROM mystery_challenges "
            "WHERE status IN ('active', 'frozen') "
            "ORDER BY starts_at DESC "
            "LIMIT 1"
        )
    ).first()
    if not row:
        return None
    return {
        "id": row.id,
        "product_ean": row.product_ean,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "status": row.status,
        "reward_tiers": row.reward_tiers,
        "created_at": row.created_at,
    }


def get_challenge_by_id(db: Session, challenge_id: uuid.UUID) -> dict | None:
    """Return a challenge by id, or None."""
    row = db.execute(
        text(
            "SELECT id, product_ean, starts_at, ends_at, status, reward_tiers, created_at "
            "FROM mystery_challenges WHERE id = :id"
        ),
        {"id": challenge_id},
    ).first()
    if not row:
        return None
    return {
        "id": row.id,
        "product_ean": row.product_ean,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "status": row.status,
        "reward_tiers": row.reward_tiers,
        "created_at": row.created_at,
    }


def get_challenge_clues(
    db: Session,
    challenge_id: uuid.UUID,
    *,
    include_unrevealed: bool = False,
) -> list[dict]:
    """
    Return clues for a challenge.

    include_unrevealed=False (user-facing): only revealed clues, with clue_text.
    include_unrevealed=True (admin): all clues, but clue_text omitted for unrevealed.
    """
    if include_unrevealed:
        rows = db.execute(
            text(
                "SELECT reveal_day, clue_text, revealed_at "
                "FROM mystery_challenge_clues "
                "WHERE challenge_id = :cid "
                "ORDER BY reveal_day"
            ),
            {"cid": challenge_id},
        ).fetchall()
        return [
            {
                "reveal_day": r.reveal_day,
                "clue_text": r.clue_text if r.revealed_at is not None else None,
                "revealed": r.revealed_at is not None,
            }
            for r in rows
        ]
    else:
        rows = db.execute(
            text(
                "SELECT reveal_day, clue_text "
                "FROM mystery_challenge_clues "
                "WHERE challenge_id = :cid AND revealed_at IS NOT NULL "
                "ORDER BY reveal_day"
            ),
            {"cid": challenge_id},
        ).fetchall()
        return [
            {
                "reveal_day": r.reveal_day,
                "clue_text": r.clue_text,
                "revealed": True,
            }
            for r in rows
        ]


def get_user_find(db: Session, challenge_id: uuid.UUID, user_id: uuid.UUID) -> dict | None:
    """Return a user's find for a challenge, or None."""
    row = db.execute(
        text(
            "SELECT rank, cab_awarded, found_at, announced_at "
            "FROM mystery_challenge_finds "
            "WHERE challenge_id = :cid AND user_id = :uid"
        ),
        {"cid": challenge_id, "uid": user_id},
    ).first()
    if not row:
        return None
    return {
        "rank": row.rank,
        "cab_awarded": row.cab_awarded,
        "found_at": row.found_at,
        "announced_at": row.announced_at,
    }


def get_leaderboard(db: Session, challenge_id: uuid.UUID) -> list[dict]:
    """Return announced finds for a challenge, ordered by rank."""
    rows = db.execute(
        text(
            "SELECT f.rank, f.cab_awarded, f.found_at, "
            "       EXTRACT(DAY FROM (f.found_at - mc.starts_at)) + 1 AS found_at_day, "
            "       SPLIT_PART(u.email, '@', 1) AS username "
            "FROM mystery_challenge_finds f "
            "JOIN mystery_challenges mc ON mc.id = f.challenge_id "
            "LEFT JOIN users u ON u.id = f.user_id "
            "WHERE f.challenge_id = :cid AND f.announced_at IS NOT NULL "
            "ORDER BY f.rank"
        ),
        {"cid": challenge_id},
    ).fetchall()
    return [
        {
            "rank": r.rank,
            "cab_awarded": r.cab_awarded,
            "found_at": r.found_at,
            "found_at_day": int(r.found_at_day),
            "username": r.username,
        }
        for r in rows
    ]


def list_challenges(db: Session) -> list[dict]:
    """Return all challenges with find counts, newest first."""
    rows = db.execute(
        text(
            "SELECT mc.id, mc.product_ean, mc.starts_at, mc.ends_at, mc.status, "
            "       mc.reward_tiers, "
            "       COUNT(f.id) AS finds_count "
            "FROM mystery_challenges mc "
            "LEFT JOIN mystery_challenge_finds f ON f.challenge_id = mc.id "
            "GROUP BY mc.id "
            "ORDER BY mc.starts_at DESC"
        )
    ).fetchall()
    return [
        {
            "id": r.id,
            "product_ean": r.product_ean,
            "starts_at": r.starts_at,
            "ends_at": r.ends_at,
            "status": r.status,
            "reward_tiers": r.reward_tiers,
            "finds_count": r.finds_count,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Draw random product
# ---------------------------------------------------------------------------


def draw_random_product(db: Session, category_filter: str | None = None) -> str:
    """
    Draw a random eligible product EAN.

    Eligibility: price_consensus updated within 90 days, not in exclusions.
    Raises NoEligibleProduct if no eligible product found.
    """
    # Use a single parameterised query — avoid string concatenation (S608).
    # (:category IS NULL OR p.category = :category) short-circuits when no filter.
    row = db.execute(
        text(
            "SELECT pc.product_ean AS ean "
            "FROM price_consensus pc "
            "JOIN products p ON p.ean = pc.product_ean "
            "LEFT JOIN categories cat ON cat.id = p.category_id "
            "WHERE pc.last_seen_at > now() - interval '90 days' "
            "  AND pc.product_ean NOT IN ( "
            "    SELECT product_ean FROM mystery_challenge_exclusions "
            "  ) "
            "  AND (CAST(:category AS text) IS NULL OR cat.name = :category) "
            "ORDER BY random() "
            "LIMIT 1"
        ),
        {"category": category_filter},
    ).first()
    if not row:
        raise NoEligibleProduct("No eligible product for mystery challenge draw")
    return row.ean


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def create_mystery_challenge(
    db: Session,
    *,
    starts_at: datetime,
    product_ean: str | None,
    reward_tiers: list[dict],
    clues: list[dict],
    category_filter: str | None = None,
) -> uuid.UUID:
    """
    Create a scheduled mystery challenge.

    Steps:
    1. Compute ends_at = starts_at + 7 days
    2. Check for overlapping scheduled/active/frozen challenges
    3. Draw product EAN if not provided
    4. Verify product exists
    5. INSERT mystery_challenges
    6. INSERT mystery_challenge_clues
    7. Upsert exclusions, keep only 5 most recent
    """
    import json

    ends_at = starts_at + timedelta(days=7)

    # Step 2 — overlap check
    overlap = db.execute(
        text(
            "SELECT 1 FROM mystery_challenges "
            "WHERE status IN ('scheduled', 'active', 'frozen') "
            "  AND starts_at < :ends AND ends_at > :starts "
            "LIMIT 1"
        ),
        {"starts": starts_at, "ends": ends_at},
    ).first()
    if overlap:
        raise ChallengeOverlap("A challenge already exists in this time window")

    # Step 3 — draw if needed
    if product_ean is None:
        product_ean = draw_random_product(db, category_filter)

    # Step 4 — verify product exists
    exists = db.execute(
        text("SELECT 1 FROM products WHERE ean = :ean"),
        {"ean": product_ean},
    ).first()
    if not exists:
        raise ValueError(f"Product not found: {product_ean!r}")

    # Step 5 — INSERT challenge
    challenge_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO mystery_challenges "
            "    (id, product_ean, starts_at, ends_at, status, reward_tiers) "
            "VALUES (:id, :ean, :starts, :ends, 'scheduled', CAST(:tiers AS jsonb))"
        ),
        {
            "id": challenge_id,
            "ean": product_ean,
            "starts": starts_at,
            "ends": ends_at,
            "tiers": json.dumps(reward_tiers),
        },
    )

    # Step 6 — INSERT clues
    for clue in clues:
        db.execute(
            text(
                "INSERT INTO mystery_challenge_clues "
                "    (id, challenge_id, reveal_day, clue_text) "
                "VALUES (:id, :cid, :day, :text)"
            ),
            {
                "id": uuid.uuid4(),
                "cid": challenge_id,
                "day": clue["reveal_day"],
                "text": clue["clue_text"],
            },
        )

    # Step 7 — upsert exclusion + prune to 5
    db.execute(
        text(
            "INSERT INTO mystery_challenge_exclusions (product_ean, excluded_until) "
            "VALUES (:ean, :until) "
            "ON CONFLICT (product_ean) DO UPDATE SET excluded_until = EXCLUDED.excluded_until"
        ),
        {"ean": product_ean, "until": ends_at},
    )
    db.execute(
        text(
            "DELETE FROM mystery_challenge_exclusions "
            "WHERE product_ean NOT IN ( "
            "  SELECT product_ean FROM mystery_challenge_exclusions "
            "  ORDER BY excluded_until DESC LIMIT 5 "
            ")"
        )
    )

    db.flush()
    return challenge_id


def update_mystery_challenge(
    db: Session,
    challenge_id: uuid.UUID,
    *,
    starts_at: datetime | None = None,
    product_ean: str | None = None,
    reward_tiers: list[dict] | None = None,
    clues: list[dict] | None = None,
) -> None:
    """Update a scheduled challenge. Raises ChallengeNotModifiable if not 'scheduled'."""
    import json

    row = db.execute(
        text("SELECT status FROM mystery_challenges WHERE id = :id"),
        {"id": challenge_id},
    ).first()
    if not row or row.status != "scheduled":
        raise ChallengeNotModifiable(f"Challenge {challenge_id} is not in 'scheduled' status")

    if starts_at is not None:
        ends_at = starts_at + timedelta(days=7)
        db.execute(
            text("UPDATE mystery_challenges SET starts_at = :starts, ends_at = :ends WHERE id = :id"),
            {"starts": starts_at, "ends": ends_at, "id": challenge_id},
        )

    if product_ean is not None:
        db.execute(
            text("UPDATE mystery_challenges SET product_ean = :ean WHERE id = :id"),
            {"ean": product_ean, "id": challenge_id},
        )

    if reward_tiers is not None:
        db.execute(
            text("UPDATE mystery_challenges SET reward_tiers = CAST(:tiers AS jsonb) WHERE id = :id"),
            {"tiers": json.dumps(reward_tiers), "id": challenge_id},
        )

    if clues is not None:
        db.execute(
            text("DELETE FROM mystery_challenge_clues WHERE challenge_id = :id"),
            {"id": challenge_id},
        )
        for clue in clues:
            db.execute(
                text(
                    "INSERT INTO mystery_challenge_clues "
                    "    (id, challenge_id, reveal_day, clue_text) "
                    "VALUES (:id, :cid, :day, :text)"
                ),
                {
                    "id": uuid.uuid4(),
                    "cid": challenge_id,
                    "day": clue["reveal_day"],
                    "text": clue["clue_text"],
                },
            )

    db.flush()


def delete_mystery_challenge(db: Session, challenge_id: uuid.UUID) -> None:
    """Delete a scheduled challenge. Raises ChallengeNotModifiable if not 'scheduled'."""
    row = db.execute(
        text("SELECT status FROM mystery_challenges WHERE id = :id"),
        {"id": challenge_id},
    ).first()
    if not row or row.status != "scheduled":
        raise ChallengeNotModifiable(f"Challenge {challenge_id} is not in 'scheduled' status")
    db.execute(
        text("DELETE FROM mystery_challenges WHERE id = :id"),
        {"id": challenge_id},
    )
    db.flush()


# ---------------------------------------------------------------------------
# check_mystery_find
# ---------------------------------------------------------------------------


def check_mystery_find(db: Session, user_id: uuid.UUID, scan_id: uuid.UUID) -> dict | None:
    """
    Check if a scan constitutes a mystery product find.

    Returns {"rank": int, "cab_awarded": int} if find recorded, else None.

    Steps:
    1. Get product_ean from scan
    2. Find active challenge for that EAN
    3. Check user hasn't already found this challenge
    4. INSERT find with atomic rank via CTE
    """
    # Step 1 — get scan EAN
    scan_row = db.execute(
        text("SELECT product_ean FROM scans WHERE id = :sid"),
        {"sid": scan_id},
    ).first()
    if not scan_row:
        return None

    ean = scan_row.product_ean
    if not ean:
        return None

    # Step 2 — active challenge for EAN
    challenge_row = db.execute(
        text("SELECT id, reward_tiers FROM mystery_challenges WHERE status = 'active' AND product_ean = :ean"),
        {"ean": ean},
    ).first()
    if not challenge_row:
        return None

    challenge_id = challenge_row.id
    reward_tiers = challenge_row.reward_tiers

    # Step 3 — already found?
    already = db.execute(
        text("SELECT 1 FROM mystery_challenge_finds WHERE challenge_id = :cid AND user_id = :uid"),
        {"cid": challenge_id, "uid": user_id},
    ).first()
    if already:
        return None

    # Step 4 — atomic INSERT with rank.
    # Lock the challenge row first to serialize concurrent inserts, then
    # compute rank in the INSERT subquery (FOR UPDATE on aggregates is not
    # supported by PostgreSQL — lock a real row instead).
    db.execute(
        text("SELECT id FROM mystery_challenges WHERE id = :cid FOR UPDATE"),
        {"cid": challenge_id},
    )

    current_count = db.execute(
        text("SELECT COUNT(*) FROM mystery_challenge_finds WHERE challenge_id = :cid"),
        {"cid": challenge_id},
    ).scalar()
    rank_to_assign = (current_count or 0) + 1
    cab_awarded = resolve_cab_tier(reward_tiers, rank_to_assign)

    result = db.execute(
        text(
            "INSERT INTO mystery_challenge_finds "
            "    (id, challenge_id, user_id, scan_id, rank, cab_awarded, found_at) "
            "VALUES (:id, :cid, :uid, :sid, :rank, :cab, now()) "
            "RETURNING rank, cab_awarded"
        ),
        {
            "id": uuid.uuid4(),
            "cid": challenge_id,
            "uid": user_id,
            "sid": scan_id,
            "rank": rank_to_assign,
            "cab": cab_awarded,
        },
    ).first()

    if not result:
        return None

    return {"rank": result.rank, "cab_awarded": result.cab_awarded}
