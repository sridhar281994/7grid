from datetime import datetime, timezone
from typing import Dict, Optional
import random
import json
import os
import asyncio

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
def _now():
    return datetime.now(timezone.utc)


def _name_for(u: Optional[User]) -> str:
    if not u:
        return "Player"
    return (u.name or (u.email or "").split("@")[0] or u.phone or f"User#{u.id}")


async def _publish_event(match_id: int, event: dict):
    """Publish JSON event to Redis channel for this match."""
    try:
        import redis.asyncio as redis
        REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_conn = redis.from_url(REDIS_URL, decode_responses=True)
        channel_name = f"match:{match_id}:updates"
        await redis_conn.publish(channel_name, json.dumps(event))
    except Exception as e:
        print(f"[WARN] Redis publish failed: {e}")


# -------------------------
# Create or wait for match
# -------------------------
@router.post("/create")
async def create_or_wait_match(
    payload: CreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    try:
        stake_amount = int(payload.stake_amount)

        # 1) Try existing waiting room (not created by me)
        waiting = (
            db.query(GameMatch)
            .filter(
                GameMatch.status == MatchStatus.WAITING,
                GameMatch.stake_amount == stake_amount,
                GameMatch.p1_user_id != current_user.id,
            )
            .order_by(GameMatch.id.asc())
            .first()
        )

        if waiting:
            waiting.p2_user_id = current_user.id
            waiting.status = MatchStatus.ACTIVE
            waiting.started_at = _now()
            waiting.last_roll = None
            waiting.current_turn = 0 # let P1 start
            db.commit()
            db.refresh(waiting)

            p1 = db.get(User, waiting.p1_user_id)
            p2 = db.get(User, waiting.p2_user_id)

            # broadcast start to both players
            asyncio.create_task(
                _publish_event(
                    waiting.id,
                    {
                        "type": "match_start",
                        "match_id": waiting.id,
                        "turn": waiting.current_turn,
                        "p1": _name_for(p1),
                        "p2": _name_for(p2),
                    },
                )
            )

            return {
                "ok": True,
                "match_id": waiting.id,
                "status": waiting.status.value,
                "stake": waiting.stake_amount,
                "p1": _name_for(p1),
                "p2": _name_for(p2),
            }

        # 2) Else create a new waiting room
        new_match = GameMatch(
            stake_amount=stake_amount,
            status=MatchStatus.WAITING,
            p1_user_id=current_user.id,
            last_roll=None,
            current_turn=0,
        )
        db.add(new_match)
        db.commit()
        db.refresh(new_match)

        p1 = db.get(User, new_match.p1_user_id)

        return {
            "ok": True,
            "match_id": new_match.id,
            "status": new_match.status.value,
            "stake": new_match.stake_amount,
            "p1": _name_for(p1),
            "p2": None,
        }

    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")


# -------------------------
# Poll match readiness
# -------------------------
@router.get("/check")
def check_match_ready(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    if m.status == MatchStatus.ACTIVE and m.p1_user_id and m.p2_user_id:
        p1 = db.get(User, m.p1_user_id)
        p2 = db.get(User, m.p2_user_id)
        return {
            "ready": True,
            "match_id": m.id,
            "status": m.status.value,
            "stake": m.stake_amount,
            "p1": _name_for(p1),
            "p2": _name_for(p2),
            "last_roll": m.last_roll,
            "turn": m.current_turn,
        }

    return {"ready": False, "status": m.status.value}


# -------------------------
# Cancel match
# -------------------------
@router.post("/{match_id}/cancel")
def cancel_match(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    if current_user.id not in [m.p1_user_id, m.p2_user_id]:
        raise HTTPException(status_code=403, detail="Not your match")

    db.delete(m)
    db.commit()
    return {"ok": True, "message": "Match cancelled"}


# -------------------------
# List matches (debug/admin)
# -------------------------
@router.get("/list")
def list_matches(db: Session = Depends(get_db)) -> Dict:
    matches = db.query(GameMatch).all()
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


# -------------------------
# Match state (resync)
# -------------------------
@router.get("/{match_id}/state")
def get_state(
    match_id: int, db: Session = Depends(get_db)
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    return {
        "match_id": m.id,
        "status": m.status.value,
        "p1": m.p1_user_id,
        "p2": m.p2_user_id,
        "turn": m.current_turn,
        "last_roll": m.last_roll,
    }


# -------------------------
# Dice Roll (Synced via Redis + DB)
# -------------------------
@router.post("/roll")
async def roll_dice(
    payload: RollIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    """Server-authoritative dice roll. Stored in DB + broadcast via Redis."""
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    if current_user.id not in [m.p1_user_id, m.p2_user_id]:
        raise HTTPException(status_code=403, detail="Not your match")

    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Match not active")

    # Roll and update match state
    roll = random.randint(1, 6)
    m.last_roll = roll
    m.current_turn = 1 - (m.current_turn or 0)
    db.commit()
    db.refresh(m)

    # Broadcast roll
    asyncio.create_task(
        _publish_event(
            m.id,
            {
                "type": "dice_roll",
                "match_id": m.id,
                "roller_id": current_user.id,
                "roll": roll,
                "turn": m.current_turn,
            },
        )
    )

    return {"ok": True, "match_id": m.id, "roll": roll, "turn": m.current_turn}
