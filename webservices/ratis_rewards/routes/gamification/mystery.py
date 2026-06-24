"""
Mystery product gamification endpoints.

GET  /api/v1/gamification/mystery                — état du défi mystère actif
GET  /api/v1/gamification/mystery/leaderboard    — classement du défi actif
GET  /api/v1/gamification/mystery/history        — 10 derniers défis révélés
"""

from __future__ import annotations

from deps import get_current_user
from fastapi import APIRouter, Depends, HTTPException
from ratis_core.database import get_db
from ratis_core.models.user import User
from repositories.mystery_repository import (
    get_active_challenge,
    get_challenge_clues,
    get_leaderboard,
    get_user_find,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/gamification/mystery")
def get_mystery(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Return the active (or frozen) mystery challenge with user-visible clues.

    Never leaks product_ean unless status='revealed'.
    """
    challenge = get_active_challenge(db)
    if challenge is None:
        raise HTTPException(status_code=404, detail="mystery_not_found")

    clues = get_challenge_clues(db, challenge["id"], include_unrevealed=False)
    user_find = get_user_find(db, challenge["id"], current_user.id)

    # Announced winner = rank-1 entry from leaderboard
    leaderboard = get_leaderboard(db, challenge["id"])
    announced_winner = None
    if leaderboard:
        top = leaderboard[0]
        announced_winner = {
            "username": top["username"],
            "found_at_day": top["found_at_day"],
        }

    response: dict = {
        "id": str(challenge["id"]),
        "status": challenge["status"],
        "starts_at": challenge["starts_at"].isoformat() if challenge["starts_at"] else None,
        "ends_at": challenge["ends_at"].isoformat() if challenge["ends_at"] else None,
        "clues": clues,
        "reward_tiers": challenge["reward_tiers"],
        "announced_winner": announced_winner,
        "user_find": {
            "rank": user_find["rank"],
            "cab_awarded": user_find["cab_awarded"],
            "found_at": user_find["found_at"].isoformat() if user_find["found_at"] else None,
        }
        if user_find
        else None,
    }

    # Only expose product_ean when challenge is revealed
    if challenge["status"] == "revealed":
        response["product_ean"] = challenge["product_ean"]

    return response


@router.get("/gamification/mystery/leaderboard")
def get_mystery_leaderboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Return announced finds for the active mystery challenge.

    user_rank is null when the current user has not found the mystery.
    """
    challenge = get_active_challenge(db)
    if challenge is None:
        raise HTTPException(status_code=404, detail="mystery_not_found")

    finds = get_leaderboard(db, challenge["id"])
    user_find = get_user_find(db, challenge["id"], current_user.id)

    return {
        "challenge_id": str(challenge["id"]),
        "status": challenge["status"],
        "finds": [
            {
                "rank": f["rank"],
                "username": f["username"],
                "found_at_day": f["found_at_day"],
                "cab_awarded": f["cab_awarded"],
            }
            for f in finds
        ],
        "user_rank": user_find["rank"] if user_find else None,
    }


@router.get("/gamification/mystery/history")
def get_mystery_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """
    Return the last 10 revealed mystery challenges with product and winner info.

    This is a pure read query — implemented directly in the route as it contains
    no business logic, only a reporting JOIN. Kept close to the HTTP layer per
    the 'max 5 fichiers/tâche' guideline (no need for a dedicated repository
    function for a single read path).
    """
    rows = db.execute(
        text(
            "SELECT mc.id, mc.product_ean, mc.starts_at, mc.ends_at, "
            "       p.name AS product_name, "
            "       (SELECT SPLIT_PART(u.email, '@', 1) "
            "        FROM mystery_challenge_finds f "
            "        LEFT JOIN users u ON u.id = f.user_id "
            "        WHERE f.challenge_id = mc.id AND f.rank = 1 "
            "        LIMIT 1) AS winner_username, "
            "       (SELECT COUNT(*) FROM mystery_challenge_finds f "
            "        WHERE f.challenge_id = mc.id) AS finds_count "
            "FROM mystery_challenges mc "
            "JOIN products p ON p.ean = mc.product_ean "
            "WHERE mc.status = 'revealed' "
            "ORDER BY mc.starts_at DESC "
            "LIMIT 10"
        )
    ).fetchall()

    return [
        {
            "id": str(r.id),
            "product_ean": r.product_ean,
            "product_name": r.product_name,
            "starts_at": r.starts_at.isoformat() if r.starts_at else None,
            "ends_at": r.ends_at.isoformat() if r.ends_at else None,
            "winner_username": r.winner_username,
            "finds_count": int(r.finds_count),
        }
        for r in rows
    ]
