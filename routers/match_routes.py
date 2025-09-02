from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import random
from datetime import datetime, timedelta

from database import get_db
from models import GameMatch, MatchStatus, User
from utils.security import get_current_user

router = APIRouter(prefix="/matches", tags=["matches"])


# ---------- Helpers ----------
def _match_to_dict(m: GameMatch, db: Session) -> dict:
    return {
        "id": m.id,
        "stake_amount": m.stake_amount,
        "status": m.status.value if hasattr(m.status, "value") else str(m.status),
        "p1_user_id": m.p1_user_id,
        "p2_user_id": m.p2_user_id,
        "last_roll": getattr(m, "last_roll", None),
        "last_roller": getattr(m, "last_roller", None),
        "created_at": getattr(m, "created_at", None),
    }


def _user_display(db: Session, user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    u: Optional[User] = db.query(User).filter(User.id == user_id).first()
    if not u:
        return None
    return u.name or u.email or f"User {u.id}"


# ---------- Routes ----------
class MatchCreateIn(BaseModel):
    stake_amount: int


@router.post("/create")
def create_or_wait_match(
    payload: MatchCreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Try to join existing WAITING
    existing = (
        db.query(GameMatch)
        .filter(
            GameMatch.status == MatchStatus.WAITING,
            GameMatch.stake_amount == payload.stake_amount,
            GameMatch.p1_user_id.isnot(None),
            GameMatch.p1_user_id != current_user.id,
        )
        .order_by(GameMatch.id.asc())
        .first()
    )

    if existing:
        existing.p2_user_id = current_user.id
        existing.status = MatchStatus.ACTIVE
        db.commit()
        db.refresh(existing)
        return {"ok": True, "joined": True, "match": _match_to_dict(existing, db)}

    new_match = GameMatch(
        stake_amount=payload.stake_amount,
        status=MatchStatus.WAITING,
        p1_user_id=current_user.id,
    )
    db.add(new_match)
    db.commit()
    db.refresh(new_match)
    return {"ok": True, "joined": False, "match": _match_to_dict(new_match, db)}


class JoinMatchIn(BaseModel):
    match_id: int


@router.post("/join")
def join_match(
    payload: JoinMatchIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(404, "Match not found")
    if m.status != MatchStatus.WAITING:
        raise HTTPException(400, "Match not waiting")
    if m.p1_user_id == current_user.id:
        raise HTTPException(400, "Cannot join your own match")

    m.p2_user_id = current_user.id
    m.status = MatchStatus.ACTIVE
    db.commit()
    db.refresh(m)
    return {"ok": True, "match": _match_to_dict(m, db)}


@router.get("/check")
def check_match_ready(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(404, "Match not found")

    ready = m.status == MatchStatus.ACTIVE
    opponent_name = None
    if ready:
        if current_user.id == m.p1_user_id:
            opponent_name = _user_display(db, m.p2_user_id)
        elif current_user.id == m.p2_user_id:
            opponent_name = _user_display(db, m.p1_user_id)

    return {"ok": True, "ready": ready, "opponent_name": opponent_name, "match": _match_to_dict(m, db)}


# ---------- NEW: Dice roll ----------
class RollIn(BaseModel):
    match_id: int


@router.post("/roll")
def roll_dice(
    payload: RollIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(404, "Match not found")
    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(400, "Match not active")

    # Ensure only players can roll
    if current_user.id not in (m.p1_user_id, m.p2_user_id):
        raise HTTPException(403, "You are not part of this match")

    # Prevent double-roll: if another roll exists <3s ago, reuse it
    now = datetime.utcnow()
    if getattr(m, "last_roll", None) and getattr(m, "last_roll_time", None):
        if now - m.last_roll_time < timedelta(seconds=3):
            return {
                "ok": True,
                "dice_result": m.last_roll,
                "roller": m.last_roller,
                "match": _match_to_dict(m, db),
            }

    # Generate new result
    result = random.randint(1, 6)
    m.last_roll = result
    m.last_roller = current_user.id
    m.last_roll_time = now
    db.commit()
    db.refresh(m)

    return {
        "ok": True,
        "dice_result": result,
        "roller": _user_display(db, current_user.id),
        "match": _match_to_dict(m, db),
    }
