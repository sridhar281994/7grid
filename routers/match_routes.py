from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, conint
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import GameMatch, User, MatchStatus
from utils.security import get_current_user

# --------- router ---------
router = APIRouter(prefix="/matches", tags=["matches"])

# --------- Redis (optional) ---------
_redis = None
_redis_ready = False
REDIS_URL = os.getenv("REDIS_URL") or os.getenv("UPSTASH_REDIS_REST_URL") or "redis://localhost:6379/0"


async def _get_redis():
    global _redis, _redis_ready
    if _redis_ready and _redis is not None:
        return _redis
    try:
        import redis.asyncio as redis
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
        await _redis.ping()
        _redis_ready = True
        return _redis
    except Exception:
        _redis = None
        _redis_ready = False
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _name_for(u: Optional[User]) -> str:
    if not u:
        return "Player"
    base = u.name or ((u.email or "").split("@")[0] if u.email else None) or u.phone
    return base or f"User#{u.id}"


# ---- Request bodies ----
class CreateIn(BaseModel):
    stake_amount: conint(gt=0)


class RollIn(BaseModel):
    match_id: int


# --------- helpers ---------
def _status_value(m: GameMatch) -> str:
    try:
        return m.status.value
    except Exception:
        return str(m.status)


def _apply_roll(positions: list[int], current_turn: int, roll: int):
    """Apply dice roll to board state"""
    p = current_turn
    old = positions[p]
    new_pos = old + roll
    winner = None
    msg = None

    if new_pos == 3: # danger zone
        positions[p] = 0
        msg = "Danger! Back to start."
    elif new_pos >= 7: # win
        positions[p] = 7
        winner = p
        msg = "Victory!"
    else:
        positions[p] = new_pos

    # Always flip turn unless game finished
    next_turn = 1 - p if winner is None else p
    return positions, next_turn, winner, msg


async def _write_state(m: GameMatch, state: dict, *, override_ts: Optional[datetime] = None):
    """Persist state to Redis"""
    r = await _get_redis()
    if not r:
        return
    payload = {
        "positions": state.get("positions", [0, 0]),
        "current_turn": state.get("current_turn", 0),
        "last_roll": state.get("last_roll"),
        "winner": state.get("winner"),
        "last_turn_ts": (override_ts or _utcnow()).isoformat(),
    }
    try:
        await r.set(f"match:{m.id}:state", json.dumps(payload), ex=24 * 60 * 60)
    except Exception:
        pass


async def _read_state(match_id: int) -> Optional[dict]:
    r = await _get_redis()
    if not r:
        return None
    try:
        raw = await r.get(f"match:{match_id}:state")
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def _auto_advance_if_needed(m: GameMatch, db: Session, timeout_secs: int = 10):
    """If last turn > timeout_secs, auto-roll for that player"""
    r = await _get_redis()
    if not r:
        return

    st = await _read_state(m.id) or {}
    ts_str = st.get("last_turn_ts")
    if not ts_str:
        return
    try:
        last_ts = datetime.fromisoformat(ts_str)
    except Exception:
        return

    if m.status != MatchStatus.ACTIVE:
        return

    if _utcnow() - last_ts < timedelta(seconds=timeout_secs):
        return

    lock_key = f"match:{m.id}:autoroll_lock"
    try:
        got_lock = await r.set(lock_key, "1", nx=True, ex=5)
    except Exception:
        got_lock = False
    if not got_lock:
        return

    # Auto roll
    roll = random.randint(1, 6)
    positions = st.get("positions", [0, 0])
    curr = st.get("current_turn", 0)
    positions, next_turn, winner, msg = _apply_roll(positions, curr, roll)

    m.last_roll = roll
    m.current_turn = next_turn
    if winner is not None:
        m.status = MatchStatus.FINISHED

    try:
        db.commit()
        db.refresh(m)
    except SQLAlchemyError:
        db.rollback()
        return

    await _write_state(m, {
        "positions": positions,
        "current_turn": m.current_turn,
        "last_roll": roll,
        "winner": winner,
    })
    print(f"[AUTO] Match {m.id} auto-rolled {roll} | Next turn={m.current_turn}")


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
            waiting.last_roll = None
            waiting.current_turn = 0
            db.commit()
            db.refresh(waiting)

            await _write_state(waiting, {
                "positions": [0, 0],
                "current_turn": 0,
                "last_roll": None,
                "winner": None,
            })

            return {
                "ok": True,
                "match_id": waiting.id,
                "status": _status_value(waiting),
                "stake": waiting.stake_amount,
                "p1": _name_for(db.get(User, waiting.p1_user_id)),
                "p2": _name_for(db.get(User, waiting.p2_user_id)),
                "last_roll": waiting.last_roll,
                "turn": waiting.current_turn,
            }

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

        await _write_state(new_match, {
            "positions": [0, 0],
            "current_turn": 0,
            "last_roll": None,
            "winner": None,
        })

        return {
            "ok": True,
            "match_id": new_match.id,
            "status": _status_value(new_match),
            "stake": new_match.stake_amount,
            "p1": _name_for(db.get(User, new_match.p1_user_id)),
            "p2": None,
            "last_roll": new_match.last_roll,
            "turn": new_match.current_turn,
        }

    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")


# -------------------------
# Poll match readiness / state
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

    if m.status == MatchStatus.ACTIVE:
        await _auto_advance_if_needed(m, db)

    st = await _read_state(m.id) or {}
    return {
        "ready": m.status == MatchStatus.ACTIVE and m.p1_user_id and m.p2_user_id,
        "match_id": m.id,
        "status": _status_value(m),
        "stake": m.stake_amount,
        "p1": _name_for(db.get(User, m.p1_user_id)) if m.p1_user_id else None,
        "p2": _name_for(db.get(User, m.p2_user_id)) if m.p2_user_id else None,
        "last_roll": st.get("last_roll", m.last_roll),
        "turn": st.get("current_turn", m.current_turn or 0),
        "positions": st.get("positions", [0, 0]),
        "winner": st.get("winner"),
    }


# -------------------------
# Dice Roll
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
    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Match not active")

    if current_user.id not in [m.p1_user_id, m.p2_user_id]:
        raise HTTPException(status_code=403, detail="Not your match")

    me_turn = 0 if current_user.id == m.p1_user_id else 1
    curr = m.current_turn or 0
    if me_turn != curr:
        raise HTTPException(status_code=409, detail="Not your turn")

    roll = random.randint(1, 6)
    st = await _read_state(m.id) or {"positions": [0, 0]}
    positions, next_turn, winner, msg = _apply_roll(st["positions"], curr, roll)

    m.last_roll = roll
    m.current_turn = next_turn
    if winner is not None:
        m.status = MatchStatus.FINISHED

    try:
        db.commit()
        db.refresh(m)
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")

    await _write_state(m, {
        "positions": positions,
        "current_turn": m.current_turn,
        "last_roll": roll,
        "winner": winner,
    })

    return {"ok": True, "match_id": m.id, "roll": roll, "turn": m.current_turn, "positions": positions, "winner": winner}
