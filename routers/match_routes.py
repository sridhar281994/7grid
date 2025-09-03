from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Dict

from .db import get_db
from .models import GameMatch, MatchStatus, User
from .auth import get_current_user

router = APIRouter(prefix="/matches", tags=["matches"])


@router.post("/create")
def create_or_wait_match(
    stake_amount: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    """
    First user creates WAITING match.
    Second user joins same match and it becomes ACTIVE.
    """
    # Is there a waiting match already?
    waiting_match = (
        db.query(GameMatch)
        .filter(GameMatch.stake_amount == stake_amount, GameMatch.status == MatchStatus.WAITING)
        .order_by(GameMatch.created_at.asc())
        .first()
    )

    if waiting_match and waiting_match.p1_user_id != current_user.id:
        # join as Player 2
        waiting_match.p2_user_id = current_user.id
        waiting_match.status = MatchStatus.ACTIVE
        db.commit()
        db.refresh(waiting_match)

        return {
            "ok": True,
            "match": {
                "id": waiting_match.id,
                "stake_amount": waiting_match.stake_amount,
                "status": waiting_match.status.value,
                "p1_name": waiting_match.p1_user.name or waiting_match.p1_user.email,
                "p2_name": waiting_match.p2_user.name or waiting_match.p2_user.email,
            },
        }

    # Otherwise, create a new waiting match
    new_match = GameMatch(
        stake_amount=stake_amount,
        status=MatchStatus.WAITING,
        p1_user_id=current_user.id,
        created_at=datetime.utcnow(),
    )
    db.add(new_match)
    db.commit()
    db.refresh(new_match)

    return {
        "ok": True,
        "match": {
            "id": new_match.id,
            "stake_amount": new_match.stake_amount,
            "status": new_match.status.value,
            "p1_name": current_user.name or current_user.email,
            "p2_name": None,
        },
    }


@router.get("/check")
def check_match_ready(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    match = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    return {
        "ready": match.status == MatchStatus.ACTIVE,
        "p1_name": match.p1_user.name or match.p1_user.email if match.p1_user else None,
        "p2_name": match.p2_user.name or match.p2_user.email if match.p2_user else None,
        "status": match.status.value,
    }
