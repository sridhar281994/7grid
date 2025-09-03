from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db  # :white_check_mark: fixed import
from models import Match, User
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
router = APIRouter()
# Pydantic models
class MatchCreate(BaseModel):
    stake_amount: int
class MatchJoin(BaseModel):
    match_id: int
# --- Create or wait for match ---
@router.post("/create")
def create_or_wait_match(data: MatchCreate, db: Session = Depends(get_db)):
    stake = data.stake_amount
    # look for waiting match
    waiting = db.query(Match).filter(
        Match.status == "WAITING",
        Match.stake_amount == stake
    ).first()
    if waiting:
        # join existing match
        waiting.status = "ACTIVE"
        waiting.started_at = datetime.utcnow()
        db.commit()
        db.refresh(waiting)
        return {"match_id": waiting.id, "status": "ACTIVE", "p1": waiting.p1_user, "p2": waiting.p2_user}
    # else create new match
    new_match = Match(
        stake_amount=stake,
        status="WAITING",
        created_at=datetime.utcnow(),
    )
    db.add(new_match)
    db.commit()
    db.refresh(new_match)
    return {"match_id": new_match.id, "status": "WAITING"}
# --- Join a match ---
@router.post("/join")
def join_match(data: MatchJoin, db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == data.match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    if match.status != "WAITING":
        raise HTTPException(status_code=400, detail="Match is not available")
    match.status = "ACTIVE"
    match.started_at = datetime.utcnow()
    db.commit()
    db.refresh(match)
    return {"match_id": match.id, "status": "ACTIVE", "p1": match.p1_user, "p2": match.p2_user}
# --- Check match status ---
@router.get("/check")
def check_match(match_id: int, db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return {
        "match_id": match.id,
        "status": match.status,
        "stake_amount": match.stake_amount,
        "p1": match.p1_user,
        "p2": match.p2_user,
        "created_at": match.created_at,
    }





