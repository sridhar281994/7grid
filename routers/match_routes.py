from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from fastapi import (
    APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
)
from pydantic import BaseModel, conint
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import GameMatch, User, MatchStatus
from utils.security import get_current_user, get_current_user_ws

router = APIRouter(prefix="/matches", tags=["matches"])

# --------- Redis ---------
import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL") or os.getenv("UPSTASH_REDIS_REST_URL") or "redis://localhost:6379/0"
_redis = redis.from_url(REDIS_URL, decode_responses=True)


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
def _apply_roll(positions: list[int], current_turn: int, roll: int):
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

    next_turn = 1 - p if winner is None else p
    return positions, next_turn, winner, msg


async def _write_state(m: GameMatch, state: dict):
    payload = {
        "positions": state.get("positions", [0, 0]),
        "current_turn": state.get("current_turn", 0),
        "last_roll": state.get("last_roll"),
        "winner": state.get("winner"),
        "last_turn_ts": _utcnow().isoformat(),
    }
    await _redis.set(f"match:{m.id}:state", json.dumps(payload), ex=86400)
    # Publish update
    await _redis.publish(f"match:{m.id}:channel", json.dumps(payload))


async def _read_state(match_id: int) -> Optional[dict]:
    raw = await _redis.get(f"match:{match_id}:state")
    return json.loads(raw) if raw else None


# -------------------------
# Create or wait for match
# -------------------------
@router.post("/create")
async def create_or_wait_match(
    payload: CreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
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
            "stake": waiting.stake_amount,
            "p1": _name_for(db.get(User, waiting.p1_user_id)),
            "p2": _name_for(db.get(User, waiting.p2_user_id)),
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
        "stake": new_match.stake_amount,
        "p1": _name_for(db.get(User, new_match.p1_user_id)),
        "p2": None,
    }


# -------------------------
# REST fallback check
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

    st = await _read_state(m.id) or {}
    return {
        "ready": m.status == MatchStatus.ACTIVE and m.p1_user_id and m.p2_user_id,
        "match_id": m.id,
        "stake": m.stake_amount,
        "p1": _name_for(db.get(User, m.p1_user_id)) if m.p1_user_id else None,
        "p2": _name_for(db.get(User, m.p2_user_id)) if m.p2_user_id else None,
        "last_roll": st.get("last_roll"),
        "turn": st.get("current_turn"),
        "positions": st.get("positions", [0, 0]),
        "winner": st.get("winner"),
    }


# -------------------------
# Dice Roll (REST API)
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

    me_turn = 0 if current_user.id == m.p1_user_id else 1
    curr = m.current_turn or 0
    if me_turn != curr:
        raise HTTPException(status_code=409, detail="Not your turn")

    roll = random.randint(1, 6)
    st = await _read_state(m.id) or {"positions": [0, 0]}
    positions, next_turn, winner, _ = _apply_roll(st["positions"], curr, roll)

    m.last_roll = roll
    m.current_turn = next_turn
    if winner is not None:
        m.status = MatchStatus.FINISHED
    db.commit()

    await _write_state(m, {
        "positions": positions,
        "current_turn": m.current_turn,
        "last_roll": roll,
        "winner": winner,
    })

    return {"ok": True, "roll": roll, "turn": m.current_turn, "positions": positions, "winner": winner}


# -------------------------
# WebSocket endpoint
# -------------------------
@router.websocket("/ws/matches/{match_id}")
async def ws_match(websocket: WebSocket, match_id: int, user: User = Depends(get_current_user_ws)):
    await websocket.accept()
    print(f"[WS] {user.id} connected to match {match_id}")

    # Subscribe to Redis channel
    pubsub = _redis.pubsub()
    await pubsub.subscribe(f"match:{match_id}:channel")

    try:
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            data = json.loads(msg["data"])
            await websocket.send_json(data)
    except WebSocketDisconnect:
        print(f"[WS] {user.id} disconnected from match {match_id}")
    finally:
        await pubsub.unsubscribe(f"match:{match_id}:channel")
        await pubsub.close()
