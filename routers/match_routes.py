from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, conint, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from models import GameMatch, User, MatchStatus
from utils.security import get_current_user, get_current_user_ws
from routers.wallet_utils import distribute_prize
from utils.redis_client import redis_client # ✅ shared redis instance

# --------- router ---------
router = APIRouter(prefix="/matches", tags=["matches"])

# Track roll counts per match
_roll_counts: dict[int, dict[str, int]] = {}

# --------- BOT IDs ---------
BOT_USER_ID = -1000
BOT_USER_ID_ALT = -1001

# -------------------------
# Pydantic Schemas
# -------------------------
class CreateIn(BaseModel):
    stake_amount: conint(ge=0)  # 0 = free play
    num_players: conint(ge=2, le=3) = Field(default=2, description="2 or 3 players")
class RollIn(BaseModel):
    match_id: int
class ForfeitIn(BaseModel):
    match_id: int
class FinishIn(BaseModel):
    match_id: int
    winner: Optional[int] = None

# -------------------------
# Helpers
# -------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _name_for(u: Optional[User]) -> str:
    if not u:
        return "Player"
    base = u.name or ((u.email or "").split("@")[0] if u.email else None) or u.phone
    return base or f"User#{u.id}"


def _name_for_id(db: Session, user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    if user_id <= 0:
        return "🤖 Bot"
    return _name_for(db.get(User, user_id))


def _status_value(m: GameMatch) -> str:
    try:
        return m.status.value
    except Exception:
        return str(m.status)


def _apply_roll(positions: list[int], current_turn: int, roll: int, num_players: int = 2):
    """
    Apply dice roll with special rules:

    1) roll == 1 -> reset to 0
    2) land on 3 -> forward to 3, then bounce back to 0
    3) exact 7 -> win
    4) overshoot -> stay
    6) start rule -> covered by (1): if first roll is 1, stays at 0
    Otherwise -> normal forward move

    Returns: (positions, next_turn, winner, anim)
      anim is a dict UI can use:
        - type: "reset_on_one" | "danger_bounce" | "forward" | "overshoot" | "win"
        - path: list[int] positions in sequence to animate
        - extra fields for clarity: {"from": old, "to": new}
    """
    p = current_turn
    old = positions[p]
    winner = None
    anim = {"type": "forward", "path": [], "from": old, "to": old}

    # --- Rule 1: roll == 1 -> reset to 0 (no extra turn) ---
    if roll == 1:
        positions[p] = 0
        # animate reverse from old down to 0 (if old > 0)
        path = list(range(old - 1, -1, -1)) if old > 0 else []
        anim = {
            "type": "reset_on_one",
            "path": path, # e.g., [old-1, old-2, ..., 0]
            "from": old,
            "to": 0,
        }
        next_turn = (p + 1) % num_players
        return positions, next_turn, winner, anim

    # --- Otherwise: candidate new position ---
    new_pos = old + roll

    # --- Rule 2: land on 3 -> forward then bounce back to 0 ---
    if new_pos == 3:
        positions[p] = 0
        forward_path = list(range(old + 1, 4)) if new_pos > old else [3]
        back_path = [2, 1, 0]
        anim = {
            "type": "danger_bounce",
            "path": forward_path + back_path, # UI: go to 3, then back to 0
            "from": old,
            "to": 0,
        }
        next_turn = (p + 1) % num_players
        return positions, next_turn, winner, anim

    # --- Rule 3: exact 7 -> win ---
    if new_pos == 7:
        positions[p] = 7
        anim = {
            "type": "win",
            "path": list(range(old + 1, 8)), # walk to 7
            "from": old,
            "to": 7,
        }
        winner = p
        next_turn = p # winner keeps turn but game ends
        return positions, next_turn, winner, anim

    # --- Rule 4: overshoot -> stay ---
    if new_pos > 7:
        positions[p] = old
        anim = {
            "type": "overshoot",
            "path": [], # UI may shake coin or show 'no move'
            "from": old,
            "to": old,
        }
        next_turn = (p + 1) % num_players
        return positions, next_turn, winner, anim

    # --- Normal forward move ---
    positions[p] = new_pos
    anim = {
        "type": "forward",
        "path": list(range(old + 1, new_pos + 1)),
        "from": old,
        "to": new_pos,
    }
    next_turn = (p + 1) % num_players
    return positions, next_turn, winner, anim


# -------------------------
# Redis state helpers
# -------------------------
async def _write_state(m: GameMatch, state: dict, *, override_ts: Optional[datetime] = None):
    num_players = 3 if m.p3_user_id else 2
    payload = {
        "ready": m.status == MatchStatus.ACTIVE
        and m.p1_user_id
        and m.p2_user_id
        and (num_players == 2 or m.p3_user_id),
        "finished": m.status == MatchStatus.FINISHED,
        "match_id": m.id,
        "status": _status_value(m),
        "stake": m.stake_amount,
        "p1": None,
        "p2": None,
        "p3": None,
        "positions": state.get("positions", [0] * num_players),
        "current_turn": state.get("current_turn", 0),
        "last_roll": state.get("last_roll"),
        "winner": state.get("winner"),
        "last_turn_ts": (override_ts or _utcnow()).isoformat(),
    }
    try:
        if redis_client:
            await redis_client.set(f"match:{m.id}:state", json.dumps(payload), ex=24 * 60 * 60)
            await redis_client.publish(f"match:{m.id}:events", json.dumps(payload))
    except Exception as e:
        print(f"[WARN] Redis write failed: {e}")


async def _read_state(match_id: int) -> Optional[dict]:
    if not redis_client:
        return None
    try:
        raw = await redis_client.get(f"match:{match_id}:state")
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def _clear_state(match_id: int):
    if redis_client:
        try:
            await redis_client.delete(f"match:{match_id}:state")
        except Exception:
            pass


# -------------------------
# Auto-advance if timeout
# -------------------------
async def _auto_advance_if_needed(m: GameMatch, db: Session, timeout_secs: int = 10):
    num_players = 3 if m.p3_user_id else 2
    st = await _read_state(m.id) or {
        "positions": [0] * num_players,
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

    roll = random.randint(1, 6)
    positions = st.get("positions", [0] * num_players)
    curr = st.get("current_turn", 0)
    positions, next_turn, winner = _apply_roll(positions, curr, roll, num_players)

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
# Request bodies
# -------------------------
class CreateIn(BaseModel):
    stake_amount: conint(ge=0)
    num_players: conint(ge=2, le=3) = Field(default=2, description="2 or 3 players")

class RollIn(BaseModel):
    match_id: int

class ForfeitIn(BaseModel): # ✅ FIXED missing model
    match_id: int


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
        num_players = int(payload.num_players or 2)
        entry_fee = stake_amount // num_players if stake_amount > 0 else 0

        # -------- Free Play --------
        if stake_amount == 0:
            new_match = GameMatch(
                stake_amount=0,
                status=MatchStatus.WAITING,
                p1_user_id=current_user.id,
                p2_user_id=None,
                p3_user_id=None,
                last_roll=None,
                current_turn=0,
                num_players=num_players,
                created_at=_utcnow(),
            )
            db.add(new_match)
            db.commit()
            db.refresh(new_match)

            await _write_state(new_match, {"positions": [0] * num_players})

            return {
                "ok": True,
                "match_id": new_match.id,
                "status": _status_value(new_match),
                "stake": 0,
                "num_players": num_players,
                "p1": _name_for_id(db, new_match.p1_user_id),
                "p2": None,
                "p3": None,
                "turn": new_match.current_turn or 0,
            }

        # -------- Paid Matches --------
        if (current_user.wallet_balance or 0) < entry_fee:
            raise HTTPException(status_code=400, detail="Insufficient balance")

        current_user.wallet_balance -= entry_fee

        waiting = (
            db.query(GameMatch)
            .filter(
                GameMatch.status == MatchStatus.WAITING,
                GameMatch.stake_amount == stake_amount,
                GameMatch.num_players == num_players,
                GameMatch.p1_user_id != current_user.id,
            )
            .order_by(GameMatch.id.asc())
            .first()
        )

        if waiting:
            if num_players == 2:
                waiting.p2_user_id = current_user.id
                waiting.status = MatchStatus.ACTIVE
                waiting.current_turn = random.choice([0, 1])
            else:
                if not waiting.p2_user_id:
                    waiting.p2_user_id = current_user.id
                elif not waiting.p3_user_id:
                    waiting.p3_user_id = current_user.id
                    waiting.status = MatchStatus.ACTIVE
                    waiting.current_turn = random.choice([0, 1, 2])
                else:
                    raise HTTPException(status_code=400, detail="Match already full")

            db.commit()
            db.refresh(waiting)

            await _write_state(waiting, {"positions": [0] * num_players})

            return {
                "ok": True,
                "match_id": waiting.id,
                "status": _status_value(waiting),
                "stake": waiting.stake_amount,
                "num_players": num_players,
                "p1": _name_for_id(db, waiting.p1_user_id),
                "p2": _name_for_id(db, waiting.p2_user_id),
                "p3": _name_for_id(db, waiting.p3_user_id) if num_players == 3 else None,
                "turn": waiting.current_turn,
            }

        # Otherwise create a new waiting paid match
        new_match = GameMatch(
            stake_amount=stake_amount,
            status=MatchStatus.WAITING,
            p1_user_id=current_user.id,
            num_players=num_players,
            last_roll=None,
            current_turn=random.choice([0, 1] if num_players == 2 else [0, 1, 2]),
            created_at=_utcnow(),
        )
        db.add(new_match)
        db.commit()
        db.refresh(new_match)

        await _write_state(new_match, {"positions": [0] * num_players})

        return {
            "ok": True,
            "match_id": new_match.id,
            "status": _status_value(new_match),
            "stake": new_match.stake_amount,
            "num_players": num_players,
            "p1": _name_for_id(db, new_match.p1_user_id),
            "p2": None,
            "p3": None,
            "turn": new_match.current_turn,
        }

    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")


# -------------------------
# Check readiness
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

    expected_players = m.num_players or 2
    now = int(time.time())
    waiting_time = max(0, now - int(m.created_at.timestamp()) if m.created_at else 0)

    st = await _read_state(m.id) or {}
    winner_idx = st.get("winner")

    ready_flag = (
        m.status == MatchStatus.ACTIVE
        and m.p1_user_id is not None
        and m.p2_user_id is not None
        and (expected_players == 2 or m.p3_user_id is not None)
    )

    return {
        "ready": ready_flag,
        "finished": m.status == MatchStatus.FINISHED,
        "match_id": m.id,
        "status": _status_value(m),
        "stake": m.stake_amount,
        "num_players": expected_players,
        "p1": _name_for_id(db, m.p1_user_id),
        "p2": _name_for_id(db, m.p2_user_id),
        "p3": _name_for_id(db, m.p3_user_id) if expected_players == 3 else None,
        "last_roll": st.get("last_roll", m.last_roll),
        "turn": st.get("current_turn", m.current_turn or 0),
        "positions": st.get("positions", [0] * expected_players),
        "winner": winner_idx,
        "waiting_time": waiting_time,
    }


# -------------------------
# Roll Dice (patched)
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

    expected_players = m.num_players or 2
    players = [m.p1_user_id, m.p2_user_id]
    if expected_players == 3:
        players.append(m.p3_user_id)

    if current_user.id not in players:
        raise HTTPException(status_code=403, detail="Not your match")

    curr = m.current_turn or 0
    me_turn = players.index(current_user.id)
    if me_turn != curr:
        raise HTTPException(status_code=409, detail="Not your turn")

    # ------------------------
    # Load state
    # ------------------------
    st = await _read_state(m.id) or {"positions": [0] * expected_players}
    positions = st.get("positions", [0] * expected_players)

    # Track turn count + forced 1 scheduling
    turn_count = int(st.get("turn_count", 0)) + 1
    st["turn_count"] = turn_count

    if "force_one_turn" not in st:
        st["force_one_turn"] = random.choice([6, 7, 8])
    if "forced_one_applied" not in st:
        st["forced_one_applied"] = False

    # Dice roll logic
    if not st["forced_one_applied"] and turn_count == st["force_one_turn"]:
        roll = 1
        st["forced_one_applied"] = True
    else:
        roll = random.randint(2, 6)

    # ------------------------
    # Apply roll
    # ------------------------
    def _apply_roll_backend(positions, current_turn, roll, num_players):
        p = current_turn
        old = positions[p]
        new_pos = old + roll
        winner = None
        anim = {"player": p, "from": old, "to": old, "type": "stay"}

        # Rule 1: start only with 1 → go to 0 (start box, no extra turn)
        if old == 0 and roll != 1:
            return positions, (p + 1) % num_players, None, anim
        if old == 0 and roll == 1:
            positions[p] = 0
            anim = {"player": p, "from": old, "to": 0, "type": "start"}
            return positions, (p + 1) % num_players, None, anim

        # Rule 2: danger zone at 3 → move then reverse to 0
        if new_pos == 3:
            positions[p] = 0
            anim = {"player": p, "from": old, "to": 0, "type": "danger"}
            return positions, (p + 1) % num_players, None, anim

        # Rule 3: exact 7 = win
        if new_pos == 7:
            positions[p] = 7
            winner = p
            anim = {"player": p, "from": old, "to": 7, "type": "win"}
            return positions, p, winner, anim

        # Rule 4: overshoot → stay
        if new_pos > 7:
            positions[p] = old
            anim = {"player": p, "from": old, "to": old, "type": "overshoot"}
            return positions, (p + 1) % num_players, None, anim

        # Normal forward move
        positions[p] = new_pos
        anim = {"player": p, "from": old, "to": new_pos, "type": "move"}
        return positions, (p + 1) % num_players, None, anim

    positions, next_turn, winner, anim = _apply_roll_backend(
        positions, curr, roll, expected_players
    )

    # Update DB
    m.last_roll = roll
    m.current_turn = next_turn

    if winner is not None:
        m.status = MatchStatus.FINISHED
        await _clear_state(m.id)

    try:
        db.commit()
        db.refresh(m)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="DB Error during roll")

    await _write_state(
        m,
        {
            "positions": positions,
            "current_turn": m.current_turn,
            "last_roll": roll,
            "winner": winner,
            "turn_count": st["turn_count"],
            "force_one_turn": st["force_one_turn"],
            "forced_one_applied": st["forced_one_applied"],
        },
    )

    return {
        "ok": True,
        "match_id": m.id,
        "roll": roll,
        "turn": m.current_turn,
        "positions": positions,
        "winner": winner,
        "move": anim,
    }



# -------------------------
# Forfeit / Give Up
# -------------------------
@router.post("/forfeit")
async def forfeit_match(
    payload: ForfeitIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Match not active")

    expected_players = m.num_players or 2
    players = [m.p1_user_id, m.p2_user_id]
    if expected_players == 3:
        players.append(m.p3_user_id)

    if current_user.id not in players:
        raise HTTPException(status_code=403, detail="Not your match")

    loser_idx = players.index(current_user.id)

    winner_idx = None
    for i, uid in enumerate(players):
        if i != loser_idx and uid is not None:
            winner_idx = i
            break

    m.status = MatchStatus.FINISHED
    m.finished_at = _utcnow()

    if winner_idx is not None and m.stake_amount > 0:
        await distribute_prize(db, m, winner_idx)

    try:
        db.commit()
        db.refresh(m)
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")

    await _clear_state(m.id)

    return {
        "ok": True,
        "match_id": m.id,
        "forfeit": True,
        "loser": loser_idx,
        "winner": winner_idx,
        "winner_name": _name_for_id(db, players[winner_idx]) if winner_idx is not None else None,
    }


# -------------------------
# Abandon (for free-play or waiting matches)
# -------------------------
@router.post("/abandon")
async def abandon_match(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    m = (
        db.query(GameMatch)
        .filter(GameMatch.status.in_([MatchStatus.WAITING, MatchStatus.ACTIVE]), GameMatch.p1_user_id == current_user.id)
        .first()
    )

    if not m:
        return {"ok": True, "message": "No active matches"}

    if m.stake_amount == 0 and m.status == MatchStatus.WAITING:
        db.delete(m)
        db.commit()
        return {"ok": True, "message": "Free play abandoned"}

    m.status = MatchStatus.FINISHED
    db.commit()
    return {"ok": True, "message": "Match abandoned"}


# -------------------------
# WebSocket
# -------------------------
@router.websocket("/ws/{match_id}")
async def match_ws(websocket: WebSocket, match_id: int, current_user: User = Depends(get_current_user_ws)):
    await websocket.accept()
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"match:{match_id}:events")

    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if msg and msg.get("type") == "message":
                await websocket.send_text(msg["data"])
            else:
                db = SessionLocal()
                try:
                    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
                    if not m:
                        await websocket.send_text(json.dumps({"error": "Match not found"}))
                        break

                    expected_players = m.num_players or 2
                    st = await _read_state(match_id) or {
                        "positions": [0] * expected_players,
                        "current_turn": m.current_turn or 0,
                        "last_roll": m.last_roll,
                        "winner": None,
                    }

                    snapshot = {
                        "ready": m.status == MatchStatus.ACTIVE,
                        "finished": m.status == MatchStatus.FINISHED,
                        "match_id": m.id,
                        "status": _status_value(m),
                        "stake": m.stake_amount,
                        "p1": _name_for_id(db, m.p1_user_id),
                        "p2": _name_for_id(db, m.p2_user_id),
                        "p3": _name_for_id(db, m.p3_user_id) if expected_players == 3 else None,
                        "last_roll": st.get("last_roll"),
                        "turn": st.get("current_turn", m.current_turn or 0),
                        "positions": st.get("positions", [0] * expected_players),
                        "winner": st.get("winner"),
                    }
                    await websocket.send_text(json.dumps(snapshot))
                finally:
                    db.close()

            await asyncio.sleep(0.3)
    except WebSocketDisconnect:
        print(f"[WS] Closed for match {match_id}")
    finally:
        await pubsub.unsubscribe(f"match:{match_id}:events")
        await pubsub.close()


# -------------------------
# Finish Match (manual override)
# -------------------------
@router.post("/finish")
async def finish_match(payload: FinishIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    m.status = MatchStatus.FINISHED
    m.finished_at = _utcnow()

    if payload.winner is not None:
        players = [m.p1_user_id, m.p2_user_id]
        if m.num_players == 3:
            players.append(m.p3_user_id)

        if payload.winner < 0 or payload.winner >= len(players):
            raise HTTPException(status_code=400, detail="Invalid winner index")

        winner_id = players[payload.winner]
        if winner_id:
            u = db.query(User).filter(User.id == winner_id).first()
            if u:
                u.wallet_balance += m.stake_amount
            m.winner_user_id = winner_id

    db.commit()

    return {"ok": True, "message": "Match finished", "winner": payload.winner, "stake": m.stake_amount}


# -------------------------
# Cleanup Task
# -------------------------
STALE_TIMEOUT = timedelta(seconds=12)


async def _cleanup_stale_matches():
    """Delete free-play matches older than timeout"""
    while True:
        try:
            db = SessionLocal()
            cutoff = datetime.utcnow() - STALE_TIMEOUT
            stale = (
                db.query(GameMatch)
                .filter(GameMatch.status == MatchStatus.WAITING, GameMatch.stake_amount == 0, GameMatch.created_at < cutoff)
                .all()
            )
            for m in stale:
                db.delete(m)
            if stale:
                db.commit()
                print(f"[CLEANUP] Removed {len(stale)} stale free-play matches")
        except Exception as e:
            print(f"[CLEANUP ERROR] {e}")
        finally:
            db.close()
        await asyncio.sleep(30)
