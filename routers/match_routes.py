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

# -----------------------------------
# Router
# -----------------------------------
router = APIRouter(prefix="/matches", tags=["matches"])

# -----------------------------------
# Redis (optional) + in-memory fallback
# -----------------------------------
_redis = None
_redis_ready = False
REDIS_URL = os.getenv("REDIS_URL") or os.getenv("UPSTASH_REDIS_REST_URL") or "redis://localhost:6379/0"

# In-process memory fallback (works even if Redis is down)
# NOTE: ephemeral; fine for a single Render instance, but not multi-replica.
_mem_state: Dict[int, dict] = {} # match_id -> state dict
_mem_autoroll_lock: Dict[int, datetime] = {} # match_id -> lock_expiry

async def _get_redis():
    """Return a pinged redis client or None."""
    global _redis, _redis_ready
    if _redis_ready and _redis is not None:
        return _redis
    try:
        import redis.asyncio as redis # type: ignore
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

# -----------------------------------
# Request bodies
# -----------------------------------
class CreateIn(BaseModel):
    stake_amount: conint(gt=0)

class RollIn(BaseModel):
    match_id: int

# -----------------------------------
# Helpers
# -----------------------------------
def _status_value(m: GameMatch) -> str:
    try:
        return m.status.value
    except Exception:
        return str(m.status)

def _apply_roll(positions: list[int], current_turn: int, roll: int):
    """
    Apply a dice roll to board state (danger at 3, win at >=7).
    Returns: (positions, next_turn, winner_index|None, message)
    """
    p = current_turn
    old = positions[p]
    new_pos = old + roll
    winner = None
    msg = None

    if new_pos == 3: # danger
        positions[p] = 0
        msg = "Danger! Back to start."
        next_turn = 1 - p # still pass turn
    elif new_pos >= 7: # win
        positions[p] = 7
        winner = p
        msg = "Victory!"
        next_turn = p # game finished; keep same (unused)
    else:
        positions[p] = new_pos
        next_turn = 1 - p

    return positions, next_turn, winner, msg

# ---------- state read/write (Redis first, memory fallback) ----------
async def _save_state(match_id: int, state: dict, *, override_ts: Optional[datetime] = None):
    """
    Persist state to Redis if available; otherwise to in-memory store.
    State schema:
      {
        "positions": [int, int],
        "current_turn": 0|1,
        "last_roll": int|None,
        "winner": 0|1|None,
        "last_turn_ts": iso8601 str
      }
    """
    ts = (override_ts or _utcnow()).isoformat()
    payload = {
        "positions": state.get("positions", [0, 0]),
        "current_turn": state.get("current_turn", 0),
        "last_roll": state.get("last_roll"),
        "winner": state.get("winner"),
        "last_turn_ts": ts,
    }

    r = await _get_redis()
    if r:
        try:
            await r.set(f"match:{match_id}:state", json.dumps(payload), ex=24 * 60 * 60)
            return
        except Exception:
            pass

    # Fallback to memory
    _mem_state[match_id] = payload

async def _load_state(match_id: int) -> Optional[dict]:
    r = await _get_redis()
    if r:
        try:
            raw = await r.get(f"match:{match_id}:state")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    # fallback
    return _mem_state.get(match_id)

async def _del_state(match_id: int):
    r = await _get_redis()
    if r:
        try:
            await r.delete(f"match:{match_id}:state")
            await r.delete(f"match:{match_id}:autoroll_lock")
        except Exception:
            pass
    _mem_state.pop(match_id, None)
    _mem_autoroll_lock.pop(match_id, None)

# ---------- auto-advance (10s) ----------
async def _auto_advance_if_needed(m: GameMatch, db: Session, timeout_secs: int = 10):
    """
    If last_turn_ts is older than timeout, server performs an auto-roll.
    Works with Redis; falls back to in-memory timers when Redis is absent.
    """
    st = await _load_state(m.id) or {
        "positions": [0, 0],
        "current_turn": m.current_turn or 0,
        "last_roll": m.last_roll,
        "winner": None,
        "last_turn_ts": _utcnow().isoformat(),
    }

    if m.status != MatchStatus.ACTIVE:
        return

    # parse last_turn_ts
    ts_str = st.get("last_turn_ts")
    try:
        last_ts = datetime.fromisoformat(ts_str) if ts_str else _utcnow()
    except Exception:
        last_ts = _utcnow()

    if _utcnow() - last_ts < timedelta(seconds=timeout_secs):
        return

    # lock (Redis NX or in-memory expiry)
    r = await _get_redis()
    if r:
        try:
            got_lock = await r.set(f"match:{m.id}:autoroll_lock", "1", nx=True, ex=5)
        except Exception:
            got_lock = False
        if not got_lock:
            return
    else:
        # memory lock
        expiry = _mem_autoroll_lock.get(m.id)
        now = _utcnow()
        if expiry and expiry > now:
            return
        _mem_autoroll_lock[m.id] = now + timedelta(seconds=5)

    # perform auto-roll
    roll = random.randint(1, 6)
    positions = st.get("positions", [0, 0])
    curr = st.get("current_turn", m.current_turn or 0)

    positions, next_turn, winner, _msg = _apply_roll(positions, curr, roll)

    # update DB "authoritative" fields as well
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

    await _save_state(m.id, {
        "positions": positions,
        "current_turn": m.current_turn,
        "last_roll": roll,
        "winner": winner,
    })
    print(f"[AUTO] Match {m.id} auto-rolled {roll} | next turn={m.current_turn} | winner={winner}")

# ---------- cleanup WAITING matches older than 60s ----------
MATCH_WAIT_TIMEOUT = 60 # seconds

async def _cleanup_stale_matches(db: Session):
    cutoff = _utcnow() - timedelta(seconds=MATCH_WAIT_TIMEOUT)
    stale = (
        db.query(GameMatch)
        .filter(GameMatch.status == MatchStatus.WAITING)
        .filter(GameMatch.created_at < cutoff)
        .all()
    )
    for m in stale:
        try:
            mid = m.id
            db.delete(m)
            db.commit()
            await _del_state(mid)
            print(f"[CLEANUP] Auto-cancelled stale waiting match {mid}")
        except Exception as e:
            db.rollback()
            print(f"[WARN] Cleanup failed for match {m.id}: {e}")

# -----------------------------------
# Endpoints
# -----------------------------------

@router.post("/create")
async def create_or_wait_match(
    payload: CreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    try:
        stake_amount = int(payload.stake_amount)

        # Try join a waiting match (not mine)
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
            waiting.current_turn = 0 # P1 starts
            db.commit()
            db.refresh(waiting)

            await _save_state(waiting.id, {
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

        # Or create a new waiting match
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

        await _save_state(new_match.id, {
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

@router.get("/check")
async def check_match_ready(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    # keep your pool tidy
    await _cleanup_stale_matches(db)

    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    if m.status == MatchStatus.ACTIVE:
        await _auto_advance_if_needed(m, db)

    st = await _load_state(m.id) or {}
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
    st = await _load_state(m.id) or {"positions": [0, 0], "current_turn": curr}
    positions = st.get("positions", [0, 0])

    positions, next_turn, winner, _msg = _apply_roll(positions, curr, roll)

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

    await _save_state(m.id, {
        "positions": positions,
        "current_turn": m.current_turn,
        "last_roll": roll,
        "winner": winner,
    })

    return {
        "ok": True,
        "match_id": m.id,
        "roll": roll,
        "turn": m.current_turn,
        "positions": positions,
        "winner": winner,
    }

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

    try:
        db.delete(m)
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")

    await _del_state(match_id)
    print(f"[CANCEL] Match {match_id} cancelled")

    return {"ok": True, "message": "Match cancelled"}

@router.get("/list")
async def list_matches(db: Session = Depends(get_db)) -> Dict:
    matches = db.query(GameMatch).all()
    out = []
    for m in matches:
        st = await _load_state(m.id) or {}
        out.append(
            {
                "id": m.id,
                "stake": m.stake_amount,
                "status": _status_value(m),
                "p1": m.p1_user_id,
                "p2": m.p2_user_id,
                "created_at": m.created_at,
                "last_roll": st.get("last_roll", m.last_roll),
                "turn": st.get("current_turn", m.current_turn),
                "positions": st.get("positions", [0, 0]),
                "winner": st.get("winner"),
                "has_state": bool(st),
            }
        )
    return out
