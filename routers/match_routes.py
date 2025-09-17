from __future__ import annotations

import asyncio
import json
import os
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, conint
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from models import GameMatch, User, MatchStatus
from utils.security import get_current_user, get_current_user_ws
from routers.wallet_utils import distribute_prize, refund_stake

# --------- router ---------
router = APIRouter(prefix="/matches", tags=["matches"])

# --------- Redis ---------
_redis = None
_redis_ready = False
REDIS_URL = (
    os.getenv("REDIS_URL")
    or os.getenv("UPSTASH_REDIS_REST_URL")
    or "redis://localhost:6379/0"
)


async def _get_redis():
    """Lazy connect to Redis."""
    global _redis, _redis_ready
    if _redis_ready and _redis is not None:
        return _redis
    try:
        import redis.asyncio as redis
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
        await _redis.ping()
        _redis_ready = True
        return _redis
    except Exception as e:
        print(f"[WARN] Redis unavailable: {e}")
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


class ForfeitIn(BaseModel):
    match_id: int


# --------- helpers ---------
def _status_value(m: GameMatch) -> str:
    try:
        return m.status.value
    except Exception:
        return str(m.status)


def _apply_roll(positions: list[int], current_turn: int, roll: int):
    """Apply dice roll to board state with exact win condition"""
    p = current_turn
    old = positions[p]
    new_pos = old + roll
    winner = None

    if new_pos == 3: # danger zone
        positions[p] = 0
    elif new_pos == 7: # exact win
        positions[p] = 7
        winner = p
    elif new_pos > 7: # overshoot → stay
        positions[p] = old
    else:
        positions[p] = new_pos

    next_turn = 1 - p if winner is None else p
    return positions, next_turn, winner


async def _write_state(m: GameMatch, state: dict, *, override_ts: Optional[datetime] = None):
    """Persist state to Redis and publish"""
    r = await _get_redis()
    payload = {
        "ready": m.status == MatchStatus.ACTIVE and m.p1_user_id and m.p2_user_id,
        "finished": m.status == MatchStatus.FINISHED,
        "match_id": m.id,
        "status": _status_value(m),
        "stake": m.stake_amount,
        "p1": None,
        "p2": None,
        "positions": state.get("positions", [0, 0]),
        "current_turn": state.get("current_turn", 0),
        "last_roll": state.get("last_roll"),
        "winner": state.get("winner"),
        "last_turn_ts": (override_ts or _utcnow()).isoformat(),
    }
    try:
        if r:
            await r.set(f"match:{m.id}:state", json.dumps(payload), ex=24 * 60 * 60)
            await r.publish(f"match:{m.id}:events", json.dumps(payload))
    except Exception as e:
        print(f"[WARN] Redis write failed: {e}")


async def _read_state(match_id: int) -> Optional[dict]:
    r = await _get_redis()
    if not r:
        return None
    try:
        raw = await r.get(f"match:{match_id}:state")
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def _clear_state(match_id: int):
    """Remove Redis cache for a finished/cancelled match"""
    r = await _get_redis()
    if r:
        try:
            await r.delete(f"match:{match_id}:state")
        except Exception:
            pass


async def _auto_advance_if_needed(m: GameMatch, db: Session, timeout_secs: int = 10):
    """If last turn > timeout_secs, auto-roll for that player"""
    r = await _get_redis()
    st = await _read_state(m.id) or {
        "positions": [0, 0],
        "current_turn": m.current_turn or 0,
        "last_roll": m.last_roll,
        "winner": None,
        "last_turn_ts": _utcnow().isoformat(),
    }

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

    got_lock = False
    if r:
        try:
            got_lock = await r.set(f"match:{m.id}:autoroll_lock", "1", nx=True, ex=5)
        except Exception:
            got_lock = False
    else:
        got_lock = True
    if not got_lock:
        return

    roll = random.randint(1, 6)
    positions = st.get("positions", [0, 0])
    curr = st.get("current_turn", 0)
    positions, next_turn, winner = _apply_roll(positions, curr, roll)

    m.last_roll = roll
    m.current_turn = next_turn
    if winner is not None:
        m.status = MatchStatus.FINISHED
        await distribute_prize(db, m, winner)
        await _clear_state(m.id)

    try:
        db.commit()
        db.refresh(m)
    except SQLAlchemyError:
        db.rollback()
        return

    await _write_state(
        m,
        {"positions": positions, "current_turn": m.current_turn, "last_roll": roll, "winner": winner},
    )


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
        entry_fee = stake_amount // 2

        # Guard: does this user already have a truly active match?
        existing = (
            db.query(GameMatch)
            .filter(
                GameMatch.status.in_([MatchStatus.WAITING, MatchStatus.ACTIVE]),
                (GameMatch.p1_user_id == current_user.id) | (GameMatch.p2_user_id == current_user.id),
            )
            .first()
        )
        if existing:
            # Allow if it’s the same WAITING match they just created
            if not (existing.status == MatchStatus.WAITING and existing.p1_user_id == current_user.id):
                raise HTTPException(status_code=409, detail="You already have an active match")

        # Ensure wallet balance
        if (current_user.wallet_balance or 0) < entry_fee:
            raise HTTPException(status_code=400, detail="Insufficient balance")

        # Find existing waiting match
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
            # Deduct and join
            current_user.wallet_balance = (current_user.wallet_balance or 0) - entry_fee
            waiting.p2_user_id = current_user.id
            waiting.status = MatchStatus.ACTIVE
            waiting.last_roll = None
            waiting.current_turn = 0
            db.commit()
            db.refresh(waiting)

            await _write_state(waiting, {"positions": [0, 0], "current_turn": 0, "last_roll": None, "winner": None})
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

        # Otherwise create new match
        current_user.wallet_balance = (current_user.wallet_balance or 0) - entry_fee
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

        await _write_state(new_match, {"positions": [0, 0], "current_turn": 0, "last_roll": None, "winner": None})
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
        "finished": m.status == MatchStatus.FINISHED,
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
    positions, next_turn, winner = _apply_roll(st["positions"], curr, roll)

    m.last_roll = roll
    m.current_turn = next_turn
    if winner is not None:
        m.status = MatchStatus.FINISHED
        await distribute_prize(db, m, winner)
        await _clear_state(m.id)

    try:
        db.commit()
        db.refresh(m)
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")

    await _write_state(m, {"positions": positions, "current_turn": m.current_turn, "last_roll": roll, "winner": winner})
    return {"ok": True, "match_id": m.id, "roll": roll, "turn": m.current_turn, "positions": positions, "winner": winner}


# -------------------------
# Forfeit / Give Up
# -------------------------
@router.post("/forfeit")
async def forfeit_match(
    payload: ForfeitIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    """Current player gives up → opponent wins, prize distribution + state cleanup."""
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Match not active")

    if current_user.id == m.p1_user_id:
        winner = 1
    elif current_user.id == m.p2_user_id:
        winner = 0
    else:
        raise HTTPException(status_code=403, detail="Not your match")

    m.status = MatchStatus.FINISHED
    m.finished_at = _utcnow()

    try:
        await distribute_prize(db, m, winner)
        db.commit() # ✅ commit here
        db.refresh(m)
    except Exception as e:
        db.rollback()
        print(f"[ERR] Forfeit prize distribution failed: {e}")
        raise HTTPException(status_code=500, detail="Prize distribution failed")

    await _clear_state(m.id)
    await _write_state(
        m,
        {
            "positions": [0, 0],
            "current_turn": m.current_turn,
            "last_roll": m.last_roll,
            "winner": winner,
        },
    )

    return {"ok": True, "match_id": m.id, "winner": winner, "forfeit": True}


# -------------------------
# Abandon / Cancel stale match
# -------------------------
@router.post("/abandon")
async def abandon_match(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    """Cancel any WAITING/ACTIVE matches for this user and refund if needed."""
    matches = (
        db.query(GameMatch)
        .filter(
            GameMatch.status.in_([MatchStatus.WAITING, MatchStatus.ACTIVE]),
            ((GameMatch.p1_user_id == current_user.id) | (GameMatch.p2_user_id == current_user.id)),
        )
        .all()
    )

    if not matches:
        return {"ok": True, "message": "No active matches"}

    for m in matches:
        if m.status == MatchStatus.WAITING:
            # Refund entry fee only if second player never joined
            entry_fee = m.stake_amount // 2
            current_user.wallet_balance = (current_user.wallet_balance or 0) + entry_fee

        m.status = MatchStatus.FINISHED
        await _clear_state(m.id)

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="DB Error during abandon")

    return {"ok": True, "message": "Stale matches cleared and refunded if applicable"}


# -------------------------
# WebSocket endpoint
# -------------------------
@router.websocket("/ws/{match_id}")
async def match_ws(websocket: WebSocket, match_id: int, current_user: User = Depends(get_current_user_ws)):
    await websocket.accept()

    r = await _get_redis()
    pubsub = None
    if r:
        try:
            pubsub = r.pubsub()
            await pubsub.subscribe(f"match:{match_id}:events")
            print(f"[WS] Subscribed to match:{match_id}:events")
        except Exception as e:
            print(f"[WS] Redis pubsub subscribe error: {e}")
            pubsub = None

    try:
        while True:
            sent = False
            if pubsub:
                try:
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
                    if msg and msg.get("type") == "message":
                        await websocket.send_text(msg["data"])
                        sent = True
                except Exception as e:
                    print(f"[WS] Redis pubsub error: {e}")

            if not sent:
                db = SessionLocal()
                try:
                    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
                    if not m:
                        await websocket.send_text(json.dumps({"error": "Match not found"}))
                        break
                    if m.status == MatchStatus.ACTIVE:
                        await _auto_advance_if_needed(m, db)

                    st = await _read_state(match_id) or {
                        "positions": [0, 0],
                        "current_turn": m.current_turn or 0,
                        "last_roll": m.last_roll,
                        "winner": None,
                    }
                    snapshot = {
                        "ready": m.status == MatchStatus.ACTIVE and m.p1_user_id and m.p2_user_id,
                        "finished": m.status == MatchStatus.FINISHED,
                        "match_id": m.id,
                        "status": _status_value(m),
                        "stake": m.stake_amount,
                        "p1": _name_for(db.get(User, m.p1_user_id)) if m.p1_user_id else None,
                        "p2": _name_for(db.get(User, m.p2_user_id)) if m.p2_user_id else None,
                        "last_roll": st.get("last_roll"),
                        "turn": st.get("current_turn", m.current_turn or 0),
                        "positions": st.get("positions", [0, 0]),
                        "winner": st.get("winner"),
                    }
                    await websocket.send_text(json.dumps(snapshot))
                finally:
                    db.close()

            await asyncio.sleep(0.3)
    except WebSocketDisconnect:
        print(f"[WS] Closed for match {match_id}")
    finally:
        if pubsub:
            try:
                await pubsub.unsubscribe(f"match:{match_id}:events")
                await pubsub.close()
            except Exception:
                pass
