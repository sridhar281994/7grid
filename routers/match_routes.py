from datetime import datetime, timezone
from typing import Dict, Optional
import random
import json
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, conint
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import GameMatch, User, MatchStatus
from utils.security import get_current_user

import redis.asyncio as redis

router = APIRouter(prefix="/matches", tags=["matches"])

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ---------- Redis helpers ----------
def _ch(match_id: int) -> str:
    return f"match:{match_id}:updates"

def _key(match_id: int) -> str:
    return f"match:{match_id}:state"

async def _r() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)

async def _init_state(r: redis.Redis, match_id: int, stake: int):
    key = _key(match_id)
    exists = await r.exists(key)
    if not exists:
        # canonical, server-authoritative state in Redis
        await r.hset(
            key,
            mapping={
                "p1_pos": 0,
                "p2_pos": 0,
                "turn": 0, # 0 = P1, 1 = P2
                "last_roll": 0,
                "stake": stake,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        await r.publish(
            _ch(match_id),
            json.dumps({"type": "started", "match_id": match_id, "turn": 0})
        )

async def _load_state(r: redis.Redis, match_id: int) -> Dict:
    d = await r.hgetall(_key(match_id))
    if not d:
        return {}
    # coerce ints
    for k in ("p1_pos","p2_pos","turn","last_roll","stake"):
        if k in d:
            try:
                d[k] = int(d[k])
            except Exception:
                d[k] = 0
    return d


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

        # existing waiting (not me)
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

        r = await _r()

        if waiting:
            waiting.p2_user_id = current_user.id
            waiting.status = MatchStatus.ACTIVE
            waiting.last_roll = None
            waiting.current_turn = 0 # P1 starts
            db.commit()
            db.refresh(waiting)

            # make Redis state authoritative
            await _init_state(r, waiting.id, waiting.stake_amount)

            # announce ready
            await r.publish(_ch(waiting.id), json.dumps({
                "type": "ready",
                "match_id": waiting.id,
                "stake": waiting.stake_amount,
                "p1_id": waiting.p1_user_id,
                "p2_id": waiting.p2_user_id,
                "turn": 0,
            }))

            p1 = db.get(User, waiting.p1_user_id)
            p2 = db.get(User, waiting.p2_user_id)
            return {
                "ok": True,
                "match_id": waiting.id,
                "status": waiting.status.value,
                "stake": waiting.stake_amount,
                "p1": _name_for(p1),
                "p2": _name_for(p2),
            }

        # else create new waiting room
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

        # pre-create Redis state so both sides see consistent turn on join
        await _init_state(r, new_match.id, new_match.stake_amount)

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
async def check_match_ready(
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
        state = await _load_state(await _r(), m.id)
        return {
            "ready": True,
            "match_id": m.id,
            "status": m.status.value,
            "stake": m.stake_amount,
            "p1": _name_for(p1),
            "p2": _name_for(p2),
            "last_roll": state.get("last_roll", 0),
            "turn": state.get("turn", 0),
            "p1_pos": state.get("p1_pos", 0),
            "p2_pos": state.get("p2_pos", 0),
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
# Dice Roll (authoritative)
# -------------------------
@router.post("/roll")
async def roll_dice(
    payload: RollIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    """Server-authoritative roll. Persist to Redis, mirror to DB, broadcast via pub/sub."""
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Match not active")
    if current_user.id not in [m.p1_user_id, m.p2_user_id]:
        raise HTTPException(status_code=403, detail="Not your match")

    r = await _r()
    state = await _load_state(r, m.id)
    if not state:
        # initialize if missing (defensive)
        await _init_state(r, m.id, m.stake_amount)
        state = await _load_state(r, m.id)

    # Turn enforcement (optional; enable if required)
    # expected_turn_user = m.p1_user_id if state.get("turn", 0) == 0 else m.p2_user_id
    # if current_user.id != expected_turn_user:
    # raise HTTPException(status_code=409, detail="Not your turn")

    roll = random.randint(1, 6)

    # Apply minimal board logic on server if you want positions synced too.
    # For now we just flip the turn; clients compute board animation.
    new_turn = 1 - int(state.get("turn", 0))

    # Persist in Redis
    await r.hset(_key(m.id), mapping={"last_roll": roll, "turn": new_turn})
    # Mirror to DB (useful for admin/debug)
    m.last_roll = roll
    m.current_turn = new_turn
    db.commit()
    db.refresh(m)

    # Broadcast to both clients
    await r.publish(
        _ch(m.id),
        json.dumps({
            "type": "dice_roll",
            "match_id": m.id,
            "roller_id": current_user.id,
            "roll": roll,
            "turn": new_turn
        })
    )

    return {"ok": True, "match_id": m.id, "roll": roll, "turn": new_turn}
