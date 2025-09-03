from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from typing import Dict

from database import get_db
from models import Match, User, MatchStatus
from utils.security import get_current_user # assumes JWT or session check

from datetime import datetime

router = APIRouter(prefix="/matches", tags=["matches"])


# -------------------------
# Create or wait for match
# -------------------------
@router.post("/create")
def create_or_wait_match(stake_amount: int, 
                         db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)) -> Dict:
    try:
        # 1. See if a waiting match already exists
        waiting_match = (
            db.query(Match)
            .filter(Match.status == MatchStatus.WAITING, Match.stake_amount == stake_amount)
            .first()
        )

        if waiting_match:
            # Join as Player 2
            waiting_match.p2_id = current_user.id
            waiting_match.status = MatchStatus.ACTIVE
            waiting_match.started_at = datetime.utcnow()
            db.commit()
            db.refresh(waiting_match)
            return {
                "match_id": waiting_match.id,
                "status": waiting_match.status,
                "stake": waiting_match.stake_amount,
                "p1": waiting_match.p1_id,
                "p2": waiting_match.p2_id,
            }

        # 2. Otherwise create a new waiting match
        new_match = Match(
            stake_amount=stake_amount,
            status=MatchStatus.WAITING,
            p1_id=current_user.id
        )
        db.add(new_match)
        db.commit()
        db.refresh(new_match)

        return {
            "match_id": new_match.id,
            "status": new_match.status,
            "stake": new_match.stake_amount,
            "p1": new_match.p1_id,
            "p2": None,
        }

    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {str(e)}")


# -------------------------
# Poll match readiness
# -------------------------
@router.get("/check")
def check_match_ready(match_id: int,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)) -> Dict:
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    if match.status == MatchStatus.ACTIVE and match.p1_id and match.p2_id:
        return {
            "ready": True,
            "match_id": match.id,
            "stake": match.stake_amount,
            "p1": match.p1_id,
            "p2": match.p2_id,
        }
    return {"ready": False, "status": match.status}


# -------------------------
# Cancel match
# -------------------------
@router.post("/{match_id}/cancel")
def cancel_match(match_id: int,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> Dict:
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    # Only the player who created or joined can cancel
    if current_user.id not in [match.p1_id, match.p2_id]:
        raise HTTPException(status_code=403, detail="Not your match")

    db.delete(match)
    db.commit()
    return {"message": "Match cancelled"}


# -------------------------
# List matches (debug/admin)
# -------------------------
@router.get("/list")
def list_matches(db: Session = Depends(get_db)) -> Dict:
    matches = db.query(Match).all()
    return [
        {
            "id": m.id,
            "stake": m.stake_amount,
            "status": m.status,
            "p1": m.p1_id,
            "p2": m.p2_id,
            "created_at": m.created_at,
        }
        for m in matches
    ]
