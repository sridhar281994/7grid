from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import GameMatch, MatchStatus
from typing import List, Optional
from pydantic import BaseModel
router = APIRouter(prefix="/matches", tags=["matches"])
class MatchCreateIn(BaseModel):
    stake_amount: int
class JoinMatchIn(BaseModel):
    match_id: int
@router.get("/list")
def list_waiting_matches(db: Session = Depends(get_db)):
    return db.query(GameMatch).filter(GameMatch.status == MatchStatus.WAITING).all()
@router.post("/create")
def create_or_wait_match(payload: MatchCreateIn, db: Session = Depends(get_db)):
    match = GameMatch(stake_amount=payload.stake_amount, status=MatchStatus.WAITING)
    db.add(match)
    db.commit()
    db.refresh(match)
    return {"ok": True, "match_id": match.id}
@router.post("/join")
def join_match(payload: JoinMatchIn, db: Session = Depends(get_db)):
    match = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not match:
        raise HTTPException(404, "Match not found")
    match.status = MatchStatus.ACTIVE
    db.commit()
    return {"ok": True, "match_id": match.id, "status": "active"}
@router.get("/check")
def check_match_ready(match_id: int, db: Session = Depends(get_db)):
    match = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not match:
        raise HTTPException(404, "Match not found")
    return {"ok": True, "status": match.status}
