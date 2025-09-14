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

router = APIRouter(prefix="/matches", tags=["matches"])

# ------------------------
# Redis (for shared state + 10s auto-roll)
# ------------------------
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


# ------------------------
# Request bodies
# ------------------------
class CreateIn(BaseModel):
    stake_amount: conint(gt=0)


class RollIn(BaseModel):
    match_id: int


# ------------------------
# State helpers
# ------------------------
async def _state_key(match_id: int) -> str:
    return f"match:{match_id}:state"


async def _write_state(m: GameMatch, state: dict) -> None:
    """Persist the whole state for the match in Redis (24h TTL)."""
    r = await _get_redis()
    if not r:
        return
    payload = {
        "positions": state.get("positions", [0, 0]),
        "current_turn": state.get("current_turn", 0),
        "last_roll": state.get("last_roll"),
        "winner": state.get("winner"),
        "last_turn_ts": _utcnow().isoformat(),
    }
    try:
        await r.set(await _state_key(m.id), json.dumps(payload), ex=24 * 60 * 60)
    except Exception:
        pass


async def _read_state(match_id: int) -> Optional[dict]:
    r = await _get_redis()
    if not r:
        return None
    try:
        raw = await r.get(await _state_key(match_id))
        return json.loads(raw) if raw else None
    except Exception:
        return None


# ------------------------
# Game rules (server-authoritative)
# ------------------------
def _apply_roll(positions, current_turn, roll):
    """Deterministic 8-tile board:
       - tile 3 = danger -> reset to 0
       - tile 7 = win
       - >7 = ignore movement (no change), but turn still switches to other player
    """
    p = current_turn
    old = positions[p]
    new_pos = old + roll
    msg = None
    winner = None

    if new_pos == 3:
        positions[p] = 0
        msg = "Danger! Back to start."
        next_turn = 1 - p
    elif new_pos >= 7:
        positions[p] = 7
        winner = p
        msg = "Victory!"
        next_turn = p # winner keeps (game ends in UI)
    elif new_pos > 7:
        # out of range: stay, just pass turn
        positions[p] = old
        next_turn = 1 - p
    else:
        positions[p] = new_pos
        next_turn = 1 - p

    return positions, next_turn, winner, msg


async def _auto_advance_if_needed(m: GameMatch, timeout_secs=10):
    """If 10s since last turn stamp, auto-roll once on server."""
    r = await _get_redis()
    if not r or m.status != MatchStatus.ACTIVE:
        return

    st = await _read_state(m.id) or {}
    ts_str = st.get("last_turn_ts")
    if not ts_str:
        return

    try:
        last_ts = datetime.fromisoformat(ts_str)
    except Exception:
        return

    if _utcnow() - last_ts < timedelta(seconds=timeout_secs):
        return

    # lock so only one instance auto-rolls
    lock_key = f"match:{m.id}:autoroll_lock"
    try:
        got_lock = await r.set(lock_key, "1", nx=True, ex=5)
    except Exception:
        got_lock = False
    if not got_lock:
        return

    roll = random.randint(1, 6)
    positions, next_turn, winner, _ = _apply_roll(st.get("positions", [0, 0]), st.get("current_turn", 0), roll)
    new_state = {"positions": positions, "current_turn": next_turn, "last_roll": roll, "winner": winner}
    await _write_state(m, new_state)
    print(f"[AUTO] match={m.id} roll={roll} next_turn={next_turn} winner={winner}")


# ------------------------
# Endpoints
# ------------------------
@router.post("/create")
async def create_or_wait_match(
    payload: CreateIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> Dict:
    try:
        stake = int(payload.stake_amount)

        waiting = (
            db.query(GameMatch)
            .filter(
                GameMatch.status == MatchStatus.WAITING,
                GameMatch.stake_amount == stake,
                GameMatch.p1_user_id != current_user.id,
            )
            .order_by(GameMatch.id.asc())
            .first()
        )

        if waiting:
            waiting.p2_user_id = current_user.id
            waiting.status = MatchStatus.ACTIVE
            db.commit()
            db.refresh(waiting)

            state = {"positions": [0, 0], "current_turn": 0, "last_roll": None, "winner": None}
            await _write_state(waiting, state)

            print(f"[MATCH] join match={waiting.id} stake={waiting.stake_amount}")
            return {"ok": True, "match_id": waiting.id, "stake": waiting.stake_amount}

        # create
        new_m = GameMatch(stake_amount=stake, status=MatchStatus.WAITING, p1_user_id=current_user.id)
        db.add(new_m)
        db.commit()
        db.refresh(new_m)

        state = {"positions": [0, 0], "current_turn": 0, "last_roll": None, "winner": None}
        await _write_state(new_m, state)

        print(f"[MATCH] new match={new_m.id} stake={stake}")
        return {"ok": True, "match_id": new_m.id, "stake": new_m.stake_amount}

    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")


@router.get("/check")
async def check_match(
    match_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    if m.status == MatchStatus.ACTIVE:
        await _auto_advance_if_needed(m)

    st = await _read_state(m.id) or {}
    return {"ready": m.status == MatchStatus.ACTIVE, "match_id": m.id, **st}


@router.post("/roll")
async def roll_dice(
    payload: RollIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Match not active")

    st = await _read_state(m.id) or {"positions": [0, 0], "current_turn": 0}

    # turn check (0=P1, 1=P2)
    me_turn = 0 if current_user.id == m.p1_user_id else 1
    if st.get("current_turn", 0) != me_turn:
        raise HTTPException(status_code=409, detail="Not your turn")

    # server-authoritative roll
    roll = random.randint(1, 6)
    positions, next_turn, winner, msg = _apply_roll(st["positions"], st["current_turn"], roll)
    await _write_state(m, {"positions": positions, "current_turn": next_turn, "last_roll": roll, "winner": winner})
    print(f"[ROLL] match={m.id} by=P{me_turn+1} roll={roll} next={next_turn} winner={winner}")
    return {"ok": True, "roll": roll, "positions": positions, "turn": next_turn, "winner": winner, "message": msg}


@router.post("/{match_id}/cancel")
async def cancel_match(
    match_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
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

    # best-effort cleanup
    try:
        r = await _get_redis()
        if r:
            await r.delete(await _state_key(match_id))
            await r.delete(f"match:{match_id}:autoroll_lock")
    except Exception:
        pass

    print(f"[CANCEL] match={match_id}")
    return {"ok": True, "message": "Match cancelled"}


@router.get("/list")
async def list_matches(db: Session = Depends(get_db)) -> Dict:
    rows = db.query(GameMatch).all()
    out = []
    for m in rows:
        st = await _read_state(m.id) or {}
        out.append(
            {
                "id": m.id,
                "stake": m.stake_amount,
                "status": m.status.value if hasattr(m.status, "value") else str(m.status),
                "p1": m.p1_user_id,
                "p2": m.p2_user_id,
                "positions": st.get("positions"),
                "turn": st.get("current_turn"),
                "last_roll": st.get("last_roll"),
                "winner": st.get("winner"),
                "has_state": bool(st),
            }
        )
    return out
