from datetime import datetime, timezone
from typing import Dict, Optional
import random, json, os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, conint
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import GameMatch, User, MatchStatus
from utils.security import get_current_user

router = APIRouter(prefix="/matches", tags=["matches"])

# ---- Request bodies ----
class CreateIn(BaseModel):
    stake_amount: conint(gt=0)

class RollIn(BaseModel):
    match_id: int

# ---- helpers ----
def _now(): return datetime.now(timezone.utc)
def _name_for(u: Optional[User]) -> str:
    return (u.name or (u.email or "").split("@")[0] or u.phone or f"User#{u.id}") if u else "Player"

# -------------------------
# Create / Wait for Match
# -------------------------
@router.post("/create")
def create_or_wait_match(payload: CreateIn, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)) -> Dict:
    try:
        stake = int(payload.stake_amount)
        waiting = (db.query(GameMatch)
                     .filter(GameMatch.status == MatchStatus.WAITING,
                             GameMatch.stake_amount == stake,
                             GameMatch.p1_user_id != current_user.id)
                     .order_by(GameMatch.id.asc()).first())
        if waiting:
            waiting.p2_user_id, waiting.status, waiting.started_at = current_user.id, MatchStatus.ACTIVE, _now()
            waiting.last_roll, waiting.current_turn = None, 0
            db.commit(); db.refresh(waiting)
            return {"ok": True, "match_id": waiting.id, "status": waiting.status.value,
                    "stake": waiting.stake_amount, "p1": _name_for(db.get(User, waiting.p1_user_id)),
                    "p2": _name_for(db.get(User, waiting.p2_user_id))}
        new = GameMatch(stake_amount=stake, status=MatchStatus.WAITING,
                        p1_user_id=current_user.id, last_roll=None, current_turn=0)
        db.add(new); db.commit(); db.refresh(new)
        return {"ok": True, "match_id": new.id, "status": new.status.value,
                "stake": new.stake_amount, "p1": _name_for(db.get(User, new.p1_user_id)), "p2": None}
    except SQLAlchemyError as e:
        db.rollback(); raise HTTPException(status_code=500, detail=f"DB Error: {e}")

# -------------------------
# Poll Match
# -------------------------
@router.get("/check")
def check_match_ready(match_id: int, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m: raise HTTPException(status_code=404, detail="Match not found")
    if m.status == MatchStatus.ACTIVE and m.p1_user_id and m.p2_user_id:
        return {"ready": True, "match_id": m.id, "status": m.status.value,
                "stake": m.stake_amount, "p1": _name_for(db.get(User, m.p1_user_id)),
                "p2": _name_for(db.get(User, m.p2_user_id)),
                "last_roll": m.last_roll, "turn": m.current_turn}
    return {"ready": False, "status": m.status.value}

# -------------------------
# Cancel Match
# -------------------------
@router.post("/{match_id}/cancel")
def cancel_match(match_id: int, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m: raise HTTPException(status_code=404, detail="Match not found")
    if current_user.id not in [m.p1_user_id, m.p2_user_id]:
        raise HTTPException(status_code=403, detail="Not your match")
    db.delete(m); db.commit(); return {"ok": True, "message": "Match cancelled"}

# -------------------------
# List Matches (debug/admin)
# -------------------------
@router.get("/list")
def list_matches(db: Session = Depends(get_db)) -> Dict:
    return [{"id": m.id, "stake": m.stake_amount,
             "status": m.status.value if hasattr(m.status, "value") else str(m.status),
             "p1": m.p1_user_id, "p2": m.p2_user_id,
             "created_at": m.created_at, "last_roll": m.last_roll,
             "turn": m.current_turn} for m in db.query(GameMatch).all()]

# -------------------------
# Dice Roll (DB + Redis sync)
# -------------------------
@router.post("/roll")
async def roll_dice(payload: RollIn, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m: raise HTTPException(status_code=404, detail="Match not found")
    if current_user.id not in [m.p1_user_id, m.p2_user_id]:
        raise HTTPException(status_code=403, detail="Not your match")
    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Match not active")

    roll = random.randint(1, 6)
    m.last_roll, m.current_turn = roll, 1 - (m.current_turn or 0)
    db.commit(); db.refresh(m)

    try:
        import redis.asyncio as redis
        REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_conn = redis.from_url(REDIS_URL, decode_responses=True)
        event = {"type": "dice_roll", "match_id": m.id, "roller_id": current_user.id,
                 "roll": roll, "turn": m.current_turn}
        await redis_conn.publish(f"match:{m.id}:updates", json.dumps(event))
    except Exception as e:
        print(f"[WARN] Redis publish failed: {e}")

    return {"ok": True, "match_id": m.id, "roll": roll, "turn": m.current_turn}
