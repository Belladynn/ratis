"""
Admin mystery product endpoints.

GET    /api/v1/admin/mystery                          — liste tous les défis
GET    /api/v1/admin/mystery/draw                     — tire un produit éligible
POST   /api/v1/admin/mystery                          — crée un défi
PATCH  /api/v1/admin/mystery/{challenge_id}           — modifie un défi scheduled
DELETE /api/v1/admin/mystery/{challenge_id}           — supprime un défi scheduled

All routes require ADMIN_API_KEY.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from db_utils import db_transaction
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from repositories.mystery_repository import (
    ChallengeNotModifiable,
    ChallengeOverlap,
    NoEligibleProduct,
    create_mystery_challenge,
    delete_mystery_challenge,
    draw_random_product,
    get_challenge_by_id,
    list_challenges,
    update_mystery_challenge,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreateMysteryRequest(BaseModel):
    starts_at: datetime
    product_ean: str | None = None
    category_filter: str | None = None
    reward_tiers: list[dict]
    clues: list[dict]  # [{"reveal_day": int, "clue_text": str}]


class UpdateMysteryRequest(BaseModel):
    starts_at: datetime | None = None
    product_ean: str | None = None
    reward_tiers: list[dict] | None = None
    clues: list[dict] | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/admin/mystery",
    dependencies=[Depends(verify_admin_key)],
)
def admin_list_mystery(db: Session = Depends(get_db)) -> list[dict]:
    """List all mystery challenges, newest first."""
    return [
        {
            "id": str(c["id"]),
            "product_ean": c["product_ean"],
            "starts_at": c["starts_at"].isoformat() if c["starts_at"] else None,
            "ends_at": c["ends_at"].isoformat() if c["ends_at"] else None,
            "status": c["status"],
            "reward_tiers": c["reward_tiers"],
            "finds_count": c["finds_count"],
        }
        for c in list_challenges(db)
    ]


@router.get(
    "/admin/mystery/draw",
    dependencies=[Depends(verify_admin_key)],
)
def admin_draw_product(
    category: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """
    Draw a random eligible product for the next mystery challenge.

    Returns product details (name, category, last_consensus_at).
    422 no_eligible_product if no product is eligible.
    """
    try:
        ean = draw_random_product(db, category_filter=category)
    except NoEligibleProduct:
        raise HTTPException(status_code=422, detail="no_eligible_product")

    row = db.execute(
        text(
            "SELECT p.name, cat.name AS category, "
            "       MAX(pc.last_seen_at) AS last_consensus_at "
            "FROM products p "
            "LEFT JOIN categories cat ON cat.id = p.category_id "
            "LEFT JOIN price_consensus pc ON pc.product_ean = p.ean "
            "WHERE p.ean = :ean "
            "GROUP BY p.name, cat.name"
        ),
        {"ean": ean},
    ).first()

    return {
        "ean": ean,
        "name": row.name if row else None,
        "category": row.category if row else None,
        "last_consensus_at": (row.last_consensus_at.isoformat() if row and row.last_consensus_at else None),
    }


@router.post(
    "/admin/mystery",
    dependencies=[Depends(verify_admin_key)],
)
def admin_create_mystery(
    body: CreateMysteryRequest,
    db: Session = Depends(get_db),
) -> dict:
    """
    Create a scheduled mystery challenge.

    - If product_ean is provided, checks existence in products table.
    - If product_ean is None, draws randomly (with optional category_filter).
    - 409 challenge_overlap if another challenge overlaps.
    - 422 no_eligible_product if auto-draw finds nothing.
    - 404 product_not_found if explicit EAN doesn't exist.
    """
    if body.product_ean is not None:
        exists = db.execute(
            text("SELECT 1 FROM products WHERE ean = :ean"),
            {"ean": body.product_ean},
        ).first()
        if not exists:
            raise HTTPException(status_code=404, detail="product_not_found")

    try:
        with db_transaction(db):
            challenge_id = create_mystery_challenge(
                db,
                starts_at=body.starts_at,
                product_ean=body.product_ean,
                reward_tiers=body.reward_tiers,
                clues=body.clues,
                category_filter=body.category_filter,
            )
    except ChallengeOverlap:
        raise HTTPException(status_code=409, detail="challenge_overlap")
    except NoEligibleProduct:
        raise HTTPException(status_code=422, detail="no_eligible_product")

    return {"id": str(challenge_id)}


@router.patch(
    "/admin/mystery/{challenge_id}",
    dependencies=[Depends(verify_admin_key)],
)
def admin_update_mystery(
    challenge_id: uuid.UUID,
    body: UpdateMysteryRequest,
    db: Session = Depends(get_db),
) -> dict:
    """
    Update a scheduled mystery challenge.

    404 if challenge doesn't exist.
    409 challenge_not_modifiable if challenge is not 'scheduled'.
    """
    if get_challenge_by_id(db, challenge_id) is None:
        raise HTTPException(status_code=404, detail="mystery_not_found")

    try:
        with db_transaction(db):
            update_mystery_challenge(
                db,
                challenge_id,
                starts_at=body.starts_at,
                product_ean=body.product_ean,
                reward_tiers=body.reward_tiers,
                clues=body.clues,
            )
    except ChallengeNotModifiable:
        raise HTTPException(status_code=409, detail="challenge_not_modifiable")

    return {"id": str(challenge_id)}


@router.delete(
    "/admin/mystery/{challenge_id}",
    dependencies=[Depends(verify_admin_key)],
)
def admin_delete_mystery(
    challenge_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """
    Delete a scheduled mystery challenge.

    404 if challenge doesn't exist.
    409 challenge_not_modifiable if challenge is not 'scheduled'.
    """
    if get_challenge_by_id(db, challenge_id) is None:
        raise HTTPException(status_code=404, detail="mystery_not_found")

    try:
        with db_transaction(db):
            delete_mystery_challenge(db, challenge_id)
    except ChallengeNotModifiable:
        raise HTTPException(status_code=409, detail="challenge_not_modifiable")

    return {"deleted": True}
