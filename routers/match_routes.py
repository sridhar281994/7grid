from datetime import datetime, timezone
from typing import Dict, Optional
import random

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, conint
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from database import get_db
from models import GameMatch, User, MatchStatus
from utils.security import get_current_user

router = APIRouter(prefix="/matches", tags=["matches"])


# ---------- Pydantic ----------
class CreateIn(BaseModel):
    stake_amount: conint(gt=0)


class RollIn(BaseModel):
    match_id: int


# ---------- Helpers ----------
def _now():
    return datetime.now(timezone.utc)


def _name_for(u: Optional[User]) -> str:
    if not u:
        return "Player"
    # Prefer real name, then email local-part, then phone, then fallback
    return (u.name or (u.email or "").split("@")[0] or u.phone or f"User#{u.id}")


def _match_payload(m: GameMatch, db: Session) -> Dict:
    p1 = db.get(User, m.p1_user_id) if m.p1_user_id else None
    p2 = db.get(User, m.p2_user_id) if m.p2_user_id else None
    return {
        "match_id": m.id,
        "status": m.status.value if hasattr(m.status, "value") else str(m.status),
        "stake": m.stake_amount,
        "p1": _name_for(p1) if p1 else None,
        "p2": _name_for(p2) if p2 else None,
        "last_roll": m.last_roll,
        "turn": m.current_turn,
    }


# ============================================================
# Create (or join) a match – DB only, race-safe “second chance”
# ============================================================
@router.post("/create")
def create_or_wait_match(
    payload: CreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    stake_amount = int(payload.stake_amount)

    try:
        # 1) Try to join an existing waiting match (not created by me)
        waiting = (
            db.query(GameMatch)
            .filter(
                GameMatch.status == MatchStatus.WAITING,
                GameMatch.stake_amount == stake_amount,
                GameMatch.p1_user_id != current_user.id,
            )
            .order_by(GameMatch.created_at.asc(), GameMatch.id.asc())
            .first()
        )

        if waiting:
            waiting.p2_user_id = current_user.id
            waiting.status = MatchStatus.ACTIVE
            # Initialize shared state for gameplay (mirrors your columns)
            waiting.last_roll = None
            waiting.current_turn = 0 # P1 starts by convention
            db.commit()
            db.refresh(waiting)
            return {"ok": True, **_match_payload(waiting, db)}

        # 2) No one waiting → create a new waiting room as P1
        my_wait = GameMatch(
            stake_amount=stake_amount,
            status=MatchStatus.WAITING,
            p1_user_id=current_user.id,
            last_roll=None,
            current_turn=0,
        )
        db.add(my_wait)
        db.commit()
        db.refresh(my_wait)

        # 3) Second-chance join: tiny race reduction.
        # If someone else created a waiting room right around the same time,
        # try to join theirs and remove my placeholder.
        other = (
            db.query(GameMatch)
            .filter(
                GameMatch.status == MatchStatus.WAITING,
                GameMatch.stake_amount == stake_amount,
                GameMatch.p1_user_id != current_user.id,
                GameMatch.id != my_wait.id,
            )
            .order_by(GameMatch.created_at.asc(), GameMatch.id.asc())
            .first()
        )
        if other:
            other.p2_user_id = current_user.id
            other.status = MatchStatus.ACTIVE
            other.last_roll = None
            other.current_turn = 0
            # Remove my placeholder room to avoid zombies
            db.delete(my_wait)
            db.commit()
            db.refresh(other)
            return {"ok": True, **_match_payload(other, db)}

        # 4) Still no opponent → return my waiting room
        return {"ok": True, **_match_payload(my_wait, db)}

    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")


# ======================
# Poll match readiness
# ======================
@router.get("/check")
def check_match_ready(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    # Only participants can poll this match
    if current_user.id not in [m.p1_user_id, m.p2_user_id]:
        # Allow waiting creator to poll even if p2 not set yet
        if not (m.status == MatchStatus.WAITING and current_user.id == m.p1_user_id):
            raise HTTPException(status_code=403, detail="Not your match")

    if m.status == MatchStatus.ACTIVE and m.p1_user_id and m.p2_user_id:
        return {"ready": True, **_match_payload(m, db)}

    return {"ready": False, "status": m.status.value if hasattr(m.status, "value") else str(m.status)}


# =============
# Cancel match
# =============
@router.post("/{match_id}/cancel")
def cancel_match(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    # Only P1/P2 can cancel
    if current_user.id not in [m.p1_user_id, m.p2_user_id]:
        raise HTTPException(status_code=403, detail="Not your match")

    # If already finished, just say OK
    if m.status == MatchStatus.FINISHED:
        return {"ok": True, "message": "Match already finished"}

    db.delete(m)
    db.commit()
    return {"ok": True, "message": "Match cancelled"}


# =========================
# List matches (debug only)
# =========================
@router.get("/list")
def list_matches(db: Session = Depends(get_db)) -> Dict:
    matches = db.query(GameMatch).order_by(GameMatch.id.desc()).all()
    return [
        {
            "id": m.id,
            "stake": m.stake_amount,
            "status": m.status.value if hasattr(m.status, "value") else str(m.status),
            "p1": m.p1_user_id,
            "p2": m.p2_user_id,
            "created_at": m.created_at,
            "last_roll": m.last_roll,
            "turn": m.current_turn,
        }
        for m in matches
    ]


# ==========================
# Server-authoritative roll
# ==========================
@router.post("/roll")
def roll_dice(
    payload: RollIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    """
    Optional: If your frontend wants the server to roll & toggle turns.
    Safe even if your current game is local-only (you can ignore this route).
    """
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    if m.status != MatchStatus.ACTIVE or not (m.p1_user_id and m.p2_user_id):
        raise HTTPException(status_code=400, detail="Match not active")

    if current_user.id not in [m.p1_user_id, m.p2_user_id]:
        raise HTTPException(status_code=403, detail="Not your match")

    roll = random.randint(1, 6)
    m.last_roll = roll
    # Toggle 0 <-> 1; default to 0 if None
    m.current_turn = 1 - (m.current_turn or 0)
    db.commit()
    db.refresh(m)

    return {"ok": True, "match_id": m.id, "roll": roll, "turn": m.current_turn}
