from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from schemas import CreateMatchIn, JoinMatchIn, FinishMatchIn, MatchOut
from crud import create_match, join_match, finish_match
from typing import List
from models import GameMatch, MatchStatus
from sqlalchemy import select

router = APIRouter(prefix="/game", tags=["game"])

@router.post("/create", response_model=MatchOut)
def create(payload: CreateMatchIn, user_id: int, db: Session = Depends(get_db)):
    if payload.stake_amount not in (4, 8, 12):
        raise HTTPException(400, "stake_amount must be 4, 8 or 12")
    match = create_match(db, stake_amount=payload.stake_amount, creator_user_id=user_id)
    return match

@router.post("/join", response_model=MatchOut)
def join(payload: JoinMatchIn, user_id: int, db: Session = Depends(get_db)):
    match = join_match(db, match_id=payload.match_id, user_id=user_id)
    if not match:
        raise HTTPException(400, "Cannot join match (already full or invalid)")
    return match

@router.post("/finish", response_model=MatchOut)
def finish(payload: FinishMatchIn, db: Session = Depends(get_db)):
    match = finish_match(db, match_id=payload.match_id, winner_user_id=payload.winner_user_id)
    if not match:
        raise HTTPException(400, "Cannot finish match")
    return match

@router.get("/waiting", response_model=List[MatchOut])
def waiting(db: Session = Depends(get_db)):
    q = select(GameMatch).where(GameMatch.status == MatchStatus.WAITING)
    return db.execute(q).scalars().all()
