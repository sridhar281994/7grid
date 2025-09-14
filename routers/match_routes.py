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


async def _write_state_to_redis(m: GameMatch, *, override_turn_ts: Optional[datetime] = None) -> None:
    r = await _get_redis()
    if not r:
        return
    key = f"match:{m.id}:state"
    payload = {
        "current_turn": m.current_turn if m.current_turn is not None else 0,
        "last_roll": m.last_roll,
        "last_turn_ts": (override_turn_ts or _utcnow()).isoformat(),
    }
    try:
        await r.set(key, json.dumps(payload), ex=24 * 60 * 60)
    except Exception:
        pass


async def _read_state_from_redis(match_id: int) -> Optional[dict]:
    r = await _get_redis()
    if not r:
        return None
    try:
        raw = await r.get(f"match:{match_id}:state")
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


async def _auto_advance_if_needed(m: GameMatch, db: Session, timeout_secs: int = 10) -> None:
    r = await _get_redis()
    if not r:
        return

    st = await _read_state_from_redis(m.id) or {}
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

    roll = random.randint(1, 6)
    m.last_roll = roll
    m.current_turn = 1 - (m.current_turn or 0)

    try:
        db.commit()
        db.refresh(m)
    except SQLAlchemyError:
        db.rollback()
        return

    # ✅ Always refresh timestamp after auto-roll
    await _write_state_to_redis(m, override_turn_ts=_utcnow())
    print(f"[AUTO-ADVANCE] Match {m.id} auto-rolled {roll}, next turn {m.current_turn}")


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

            await _write_state_to_redis(waiting, override_turn_ts=_utcnow())

            p1 = db.get(User, waiting.p1_user_id)
            p2 = db.get(User, waiting.p2_user_id)

            print(f"[MATCH] Player2 joined match {waiting.id} | Stake {waiting.stake_amount}")

            return {
                "ok": True,
                "match_id": waiting.id,
                "status": _status_value(waiting),
                "stake": waiting.stake_amount,
                "p1": _name_for(p1),
                "p2": _name_for(p2),
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

        await _write_state_to_redis(new_match, override_turn_ts=_utcnow())

        p1 = db.get(User, new_match.p1_user_id)

        print(f"[MATCH] New match {new_match.id} created by Player1 | Stake {new_match.stake_amount}")

        return {
            "ok": True,
            "match_id": new_match.id,
            "status": _status_value(new_match),
            "stake": new_match.stake_amount,
            "p1": _name_for(p1),
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
        try:
            await _auto_advance_if_needed(m, db)
            db.refresh(m)
        except Exception:
            pass

    if m.status == MatchStatus.ACTIVE and m.p1_user_id and m.p2_user_id:
        p1 = db.get(User, m.p1_user_id)
        p2 = db.get(User, m.p2_user_id)

        st = await _read_state_from_redis(m.id) or {}
        current_turn = st.get("current_turn", m.current_turn if m.current_turn is not None else 0)
        last_roll = st.get("last_roll", m.last_roll)

        print(f"[CHECK] Match {m.id} polled | Turn={current_turn} | Last roll={last_roll}")

        return {
            "ready": True,
            "match_id": m.id,
            "status": _status_value(m),
            "stake": m.stake_amount,
            "p1": _name_for(p1),
            "p2": _name_for(p2),
            "last_roll": last_roll,
            "turn": current_turn,
        }

    return {"ready": False, "status": _status_value(m)}


# -------------------------
# Dice Roll (server-authoritative)
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
    curr = m.current_turn if m.current_turn is not None else 0

    if me_turn != curr:
        raise HTTPException(status_code=409, detail="Not your turn")

    roll = random.randint(1, 6)
    m.last_roll = roll
    m.current_turn = 1 - curr

    try:
        db.commit()
        db.refresh(m)
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")

    # ✅ Always refresh timestamp on manual roll
    await _write_state_to_redis(m, override_turn_ts=_utcnow())

    print(
        f"[ROLL] Match {m.id} | Player{me_turn+1} (user_id={current_user.id}) "
        f"rolled {roll} | Next turn={m.current_turn}"
    )

    return {"ok": True, "match_id": m.id, "roll": roll, "turn": m.current_turn}


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

    try:
        db.delete(m)
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")

    try:
        r = await _get_redis()
        if r:
            await r.delete(f"match:{match_id}:state")
            await r.delete(f"match:{match_id}:autoroll_lock")
    except Exception:
        pass

    print(f"[CANCEL] Match {match_id} cancelled")

    return {"ok": True, "message": "Match cancelled"}


# -------------------------
# List matches (debug/admin)
# -------------------------
@router.get("/list")
async def list_matches(db: Session = Depends(get_db)) -> Dict:
    matches = db.query(GameMatch).all()
    out = []
    for m in matches:
        try:
            st = await _read_state_from_redis(m.id) or {}
        except Exception:
            st = {}
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
                "has_state": bool(st),
            }
        )
    return out
