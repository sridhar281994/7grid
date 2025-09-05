from datetime import datetime, timezone
from typing import Dict, Optional
import os, json, random, asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, conint
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import GameMatch, User, MatchStatus
from utils.security import get_current_user

# Redis (async)
try:
    import redis.asyncio as redis
except Exception: # pragma: no cover
    redis = None # app still works, just without realtime sync

router = APIRouter(prefix="/matches", tags=["matches"])

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MATCH_TTL_SEC = int(os.getenv("MATCH_TTL_SEC", "7200")) # 2h safety
ROLL_LOCK_SEC = int(os.getenv("ROLL_LOCK_SEC", "2")) # short critical section


# ---------- Models ----------
class CreateIn(BaseModel):
    stake_amount: conint(gt=0)

class RollIn(BaseModel):
    match_id: int


# ---------- Helpers ----------
def _now(): return datetime.now(timezone.utc)

def _name_for(u: Optional[User]) -> str:
    if not u: return "Player"
    return (u.name or (u.email or "").split("@")[0] or u.phone or f"User#{u.id}")

async def _r() -> Optional[redis.Redis]:
    if not redis:
        return None
    return redis.from_url(REDIS_URL, decode_responses=True)

async def _init_state(r: redis.Redis, match_id: int, stake: int):
    """Initialize ephemeral match state in Redis (idempotent)."""
    key = f"match:{match_id}:state"
    exists = await r.exists(key)
    if not exists:
        await r.hset(key, mapping={
            "status": "active",
            "turn": "0", # 0 => P1, 1 => P2
            "p1_pos": "0",
            "p2_pos": "0",
            "last_roll": "0",
            "stake": str(stake),
        })
    await r.expire(key, MATCH_TTL_SEC)

async def _publish(r: redis.Redis, match_id: int, event: Dict):
    chan = f"match:{match_id}:updates"
    await r.publish(chan, json.dumps(event))


def _positions_after_roll(p_idx: int, p1_pos: int, p2_pos: int, roll: int):
    """
    Your board rule: danger at 3 -> go back to 0; exact 7 -> win; overshoot -> stay; else move.
    Returns: new_p1, new_p2, finished(bool)
    """
    pos = [p1_pos, p2_pos]
    new_pos = pos[p_idx] + roll
    finished = False

    if new_pos == 3:
        pos[p_idx] = 0
    elif new_pos == 7:
        pos[p_idx] = 7
        finished = True
    elif new_pos > 7:
        # stay
        pass
    else:
        pos[p_idx] = new_pos

    return pos[0], pos[1], finished


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
            db.commit()
            db.refresh(waiting)

            # init Redis state + publish start
            r = await _r()
            if r:
                await _init_state(r, waiting.id, waiting.stake_amount)
                p1 = db.get(User, waiting.p1_user_id)
                p2 = db.get(User, waiting.p2_user_id)
                await _publish(r, waiting.id, {
                    "type": "start",
                    "match_id": waiting.id,
                    "stake": waiting.stake_amount,
                    "p1": _name_for(p1),
                    "p2": _name_for(p2),
                    "turn": 0,
                    "p1_pos": 0,
                    "p2_pos": 0,
                })

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

        # 2) Else create a new waiting room
        new_match = GameMatch(
            stake_amount=stake_amount,
            status=MatchStatus.WAITING,
            p1_user_id=current_user.id,
        )
        db.add(new_match)
        db.commit()
        db.refresh(new_match)

        # Pre-create Redis state (optional)
        r = await _r()
        if r:
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

        # Read live state from Redis (fallbacks if Redis down)
        turn = 0
        p1_pos = p2_pos = last_roll = 0
        r = await _r()
        if r:
            key = f"match:{m.id}:state"
            state = await r.hgetall(key)
            if state:
                turn = int(state.get("turn", "0"))
                p1_pos = int(state.get("p1_pos", "0"))
                p2_pos = int(state.get("p2_pos", "0"))
                last_roll = int(state.get("last_roll", "0"))

        return {
            "ready": True,
            "match_id": m.id,
            "status": m.status.value,
            "stake": m.stake_amount,
            "p1": _name_for(p1),
            "p2": _name_for(p2),
            "turn": turn,
            "p1_pos": p1_pos,
            "p2_pos": p2_pos,
            "last_roll": last_roll,
        }

    return {"ready": False, "status": m.status.value}


# -------------------------
# Cancel match
# -------------------------
@router.post("/{match_id}/cancel")
async def cancel_match(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    if current_user.id not in [m.p1_user_id, m.p2_user_id]:
        raise HTTPException(status_code=403, detail="Not your match")

    # cleanup redis
    r = await _r()
    if r:
        await r.delete(f"match:{match_id}:state")
        await _publish(r, match_id, {"type": "player_left", "match_id": match_id, "user_id": current_user.id})

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
        }
        for m in matches
    ]


# -------------------------
# Dice Roll (server-auth, atomic, broadcast)
# -------------------------
@router.post("/roll")
async def roll_dice(
    payload: RollIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    if current_user.id not in [m.p1_user_id, m.p2_user_id]:
        raise HTTPException(status_code=403, detail="Not your match")
    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Match not active")

    # Redis required for sync
    r = await _r()
    if not r:
        # Fallback (not recommended for production sync)
        roll = random.randint(1, 6)
        return {"ok": True, "match_id": m.id, "roll": roll}

    key = f"match:{m.id}:state"
    lock_key = f"lock:match:{m.id}"

    # Acquire short lock to prevent double-roll
    got_lock = await r.set(lock_key, str(current_user.id), ex=ROLL_LOCK_SEC, nx=True)
    if not got_lock:
        raise HTTPException(status_code=409, detail="Another action in progress")

    try:
        state = await r.hgetall(key)
        if not state:
            await _init_state(r, m.id, m.stake_amount)
            state = await r.hgetall(key)

        turn = int(state.get("turn", "0"))
        p1_pos = int(state.get("p1_pos", "0"))
        p2_pos = int(state.get("p2_pos", "0"))
        status = state.get("status", "active")

        if status != "active":
            raise HTTPException(status_code=400, detail="Match not active")

        # Determine who is rolling
        me_idx = 0 if current_user.id == m.p1_user_id else 1
        if me_idx != turn:
            raise HTTPException(status_code=403, detail="Not your turn")

        roll = random.randint(1, 6)
        new_p1, new_p2, finished = _positions_after_roll(me_idx, p1_pos, p2_pos, roll)
        next_turn = 1 - turn

        # Update Redis
        upd = {
            "last_roll": str(roll),
            "p1_pos": str(new_p1),
            "p2_pos": str(new_p2),
            "turn": str(next_turn),
        }
        if finished:
            upd["status"] = "finished"
        await r.hset(key, mapping=upd)
        await r.expire(key, MATCH_TTL_SEC)

        # Broadcast
        await _publish(r, m.id, {
            "type": "dice_roll",
            "match_id": m.id,
            "roller_id": current_user.id,
            "roll": roll,
            "turn": next_turn,
            "p1_pos": new_p1,
            "p2_pos": new_p2,
            "finished": bool(finished),
            "winner_user_id": (m.p1_user_id if me_idx == 0 else m.p2_user_id) if finished else None,
        })

        # If finished, also persist winner into DB for ledger
        if finished:
            m.winner_user_id = m.p1_user_id if me_idx == 0 else m.p2_user_id
            m.status = MatchStatus.FINISHED
            m.finished_at = _now()
            db.commit()

        return {
            "ok": True,
            "match_id": m.id,
            "roll": roll,
            "turn": next_turn,
            "p1_pos": new_p1,
            "p2_pos": new_p2,
            "finished": finished,
        }

    finally:
        # Let lock expire naturally; optional explicit delete
        # await r.delete(lock_key)
        pass
