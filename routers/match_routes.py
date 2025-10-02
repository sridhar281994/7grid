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


def _apply_roll(
    positions: list[int],
    current_turn: int,
    roll: int,
    num_players: int = 2,
    turn_count: int = 1
):
    """
    Apply dice roll with full rules:
    1. Roll=1 at start → stay at 0th, next turn
    2. Box 3 → step into 3, then reverse to 0
    3. Exact 7 → win
    4. Overshoot → stay
    5. Normal forward otherwise
    Includes reverse flag for frontend animation.
    Ensures roll=1 is forced on 6th–8th turn if not yet rolled.
    """
    p = current_turn
    old = positions[p]
    new_pos = old + roll
    winner = None
    reverse = False

    # --- Force "1" at least once during turns 6–8 ---
    if turn_count in (6, 7, 8) and roll != 1:
        roll = 1
        new_pos = old + roll

    # --- Rule 1: Roll=1 at start (must stay at 0th) ---
    if roll == 1 and old == 0:
        positions[p] = 0
        return positions, (p + 1) % num_players, None, {"reverse": True}

    # --- Rule 2: Land on 3 → go to 3, then reverse to 0 ---
    if new_pos == 3:
        # IMPORTANT: persist final state as 0 so UI won't snap back to 3 on next sync
        positions[p] = 0
        reverse = True # frontend will animate 3 -> 0
        return positions, (p + 1) % num_players, None, {"reverse": reverse}

    # --- Rule 3: Exact win at 7 ---
    if new_pos == 7:
        positions[p] = 7
        winner = p
        return positions, p, winner, {"reverse": False}

    # --- Rule 4: Overshoot beyond 7 → stay ---
    if new_pos > 7:
        positions[p] = old
        return positions, (p + 1) % num_players, None, {"reverse": False}

    # --- Rule 5: Normal move ---
    positions[p] = new_pos
    return positions, (p + 1) % num_players, None, {"reverse": False}




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
# Create or wait for match (transaction-safe for 2P & 3P)
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

        # Try to join a waiting match (transaction-safe)
        waiting = (
            db.query(GameMatch)
            .filter(
                GameMatch.status == MatchStatus.WAITING,
                GameMatch.stake_amount == stake_amount,
                GameMatch.num_players == num_players,
                GameMatch.p1_user_id != current_user.id,
            )
            .with_for_update(skip_locked=True) # ✅ prevents two users grabbing same row
            .order_by(GameMatch.id.asc())
            .first()
        )

        if waiting:
            if num_players == 2:
                current_user.wallet_balance -= entry_fee
                waiting.p2_user_id = current_user.id
                waiting.status = MatchStatus.ACTIVE
                waiting.current_turn = random.choice([0, 1])
            else:
                if not waiting.p2_user_id:
                    current_user.wallet_balance -= entry_fee
                    waiting.p2_user_id = current_user.id
                elif not waiting.p3_user_id:
                    current_user.wallet_balance -= entry_fee
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

        # Otherwise create new waiting match (❌ no deduction yet)
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
    accept_bot: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    expected_players = m.num_players or 2
    now = int(time.time())
    waiting_time = max(0, now - int(m.created_at.timestamp()) if m.created_at else 0)

    st = await _read_state(m.id) or {}
    winner_idx = st.get("winner")

    # ✅ If waiting >= 12s → offer bot option
    if m.status == MatchStatus.WAITING and waiting_time >= 12:
        if accept_bot:
            # Deduct entry fee now if needed
            entry_fee = m.stake_amount // expected_players if m.stake_amount > 0 else 0
            if entry_fee > 0 and (current_user.wallet_balance or 0) < entry_fee:
                raise HTTPException(status_code=400, detail="Insufficient balance for bot match")

            if entry_fee > 0:
                current_user.wallet_balance -= entry_fee

            # Fill missing slots with bots
            if not m.p2_user_id:
                m.p2_user_id = -1000  # bot placeholder
            if expected_players == 3 and not m.p3_user_id:
                m.p3_user_id = -1001

            m.status = MatchStatus.ACTIVE
            m.current_turn = random.choice([0, 1] if expected_players == 2 else [0, 1, 2])

            db.commit()
            db.refresh(m)

            await _write_state(m, {"positions": [0] * expected_players})

        else:
            # tell frontend: show popup
            return {
                "ready": False,
                "finished": False,
                "match_id": m.id,
                "status": _status_value(m),
                "stake": m.stake_amount,
                "num_players": expected_players,
                "p1": _name_for_id(db, m.p1_user_id),
                "p2": _name_for_id(db, m.p2_user_id),
                "p3": _name_for_id(db, m.p3_user_id) if expected_players == 3 else None,
                "turn": m.current_turn or 0,
                "positions": st.get("positions", [0] * expected_players),
                "winner": winner_idx,
                "waiting_time": waiting_time,
                "prompt_bot": True,   # ✅ trigger popup
            }

    # If active, keep auto-advance logic
    if m.status == MatchStatus.ACTIVE:
        await _auto_advance_if_needed(m, db)

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
        "prompt_bot": False,  # default
    }


# -------------------------
# Roll Dice
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

    roll = random.randint(1, 6)

    # load current board state
    st = await _read_state(m.id) or {"positions": [0] * expected_players, "turn_count": 0}
    turn_count = st.get("turn_count", 0) + 1

    positions, next_turn, winner, extra = _apply_roll(
        st["positions"], curr, roll, expected_players, turn_count
    )

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
            "reverse": extra.get("reverse", False),
            "turn_count": turn_count,
        },
    )

    return {
        "ok": True,
        "match_id": m.id,
        "roll": roll,
        "turn": m.current_turn,
        "positions": positions,
        "winner": winner,
        "reverse": extra.get("reverse", False), # ✅ frontend uses this for animation
        "turn_count": turn_count,
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
