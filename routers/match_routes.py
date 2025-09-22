from __future__ import annotations

import asyncio
import json
import os
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, conint, Field
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

# Track roll counts per match to occasionally force a 1
_roll_counts: dict[int, dict[str, int]] = {}

# --------- BOT IDs (pseudo users for free play) ---------
BOT_USER_ID = -1000 # Sharp (Bot)
BOT_USER_ID_ALT = -1001 # Crazy Boy (Bot)

# -------------------------
# Pydantic models
# -------------------------
class CreateIn(BaseModel):
    stake_amount: conint(ge=0) # 0 = free play
    num_players: conint(ge=2, le=3) = 2 # allow 2-player or 3-player
    

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


def _name_for_id(db: Session, user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    if user_id <= 0:
        return "🤖 Bot"
    return _name_for(db.get(User, user_id))


# ---- Request bodies ----
class CreateIn(BaseModel):
    stake_amount: conint(ge=0) # 0 = free play


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


def _apply_roll(positions: list[int], current_turn: int, roll: int, num_players: int = 2):
    """Apply dice roll to board state with danger and exact win condition."""
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

    if winner is None:
        next_turn = (p + 1) % num_players
    else:
        next_turn = p
    return positions, next_turn, winner



async def _write_state(m: GameMatch, state: dict, *, override_ts: Optional[datetime] = None):
    """Persist state to Redis and publish"""
    r = await _get_redis()
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
    r = await _get_redis()
    if r:
        try:
            await r.delete(f"match:{match_id}:state")
        except Exception:
            pass


async def _auto_advance_if_needed(m: GameMatch, db: Session, timeout_secs: int = 10):
    """If last turn > timeout_secs, auto-roll for that player"""
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

    got_lock = False
    r = await _get_redis()
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
# Create or wait for match
# -------------------------
from pydantic import BaseModel, conint, Field

# ✅ Bot IDs kept for paid/bot modes if you ever need them later
BOT_USER_ID = -1000 # Sharp (Bot)
BOT_USER_ID2 = -1001 # Crazy Boy (Bot)

class CreateIn(BaseModel):
    stake_amount: conint(ge=0) # 0 = free play
    num_players: conint(ge=2, le=3) = Field(default=2, description="2-player or 3-player mode")


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

        # -------- Free Play (NO server-side bots; front end decides after 10s) --------
        if stake_amount == 0:
            new_match = GameMatch(
                stake_amount=0,
                status=MatchStatus.WAITING, # 👈 keep waiting; don't start yet
                p1_user_id=current_user.id,
                p2_user_id=None,
                p3_user_id=None if num_players == 3 else None,
                last_roll=None,
                current_turn=0, # any seed is fine
                num_players=num_players,
            )
            db.add(new_match)
            db.commit()
            db.refresh(new_match)

            # initialize ephemeral state
            await _write_state(
                new_match,
                {
                    "positions": [0] * num_players,
                    "current_turn": new_match.current_turn or 0,
                    "last_roll": None,
                    "winner": None,
                },
            )

            return {
                "ok": True,
                "match_id": new_match.id,
                "status": _status_value(new_match),
                "stake": 0,
                "num_players": num_players,
                "p1": _name_for_id(db, new_match.p1_user_id),
                "p2": None, # 👈 don't expose bots here
                "p3": None,
                "turn": new_match.current_turn or 0,
            }

        # -------- Paid Matches --------
        # balance check + deduction happens here
        if (current_user.wallet_balance or 0) < entry_fee:
            raise HTTPException(status_code=400, detail="Insufficient balance")
        current_user.wallet_balance -= entry_fee

        # Try to join a waiting match with same config
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
                waiting.last_roll = None
                waiting.current_turn = random.choice([0, 1])
            else:
                if not waiting.p2_user_id:
                    waiting.p2_user_id = current_user.id
                elif not waiting.p3_user_id:
                    waiting.p3_user_id = current_user.id
                    waiting.status = MatchStatus.ACTIVE
                    waiting.last_roll = None
                    waiting.current_turn = random.choice([0, 1, 2])
                else:
                    raise HTTPException(status_code=400, detail="Match already full")

            db.commit()
            db.refresh(waiting)

            await _write_state(
                waiting,
                {
                    "positions": [0] * num_players,
                    "current_turn": waiting.current_turn,
                    "last_roll": None,
                    "winner": None,
                },
            )

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
        )
        db.add(new_match)
        db.commit()
        db.refresh(new_match)

        await _write_state(
            new_match,
            {
                "positions": [0] * num_players,
                "current_turn": new_match.current_turn,
                "last_roll": None,
                "winner": None,
            },
        )

        return {
            "ok": True,
            "match_id": new_match.id,
            "status": _status_value(new_match),
            "stake": new_match.stake_amount,
            "num_players": num_players,
            "p1": _name_for_id(db, new_match.p1_user_id),
            "p2": _name_for_id(db, new_match.p2_user_id) if new_match.p2_user_id else None,
            "p3": _name_for_id(db, new_match.p3_user_id) if (num_players == 3 and new_match.p3_user_id) else None,
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

    # If active, keep the game moving (timeouts etc.)
    if m.status == MatchStatus.ACTIVE:
        await _auto_advance_if_needed(m, db)

    # Use configured player-count (defaults to 2 if column not set)
    expected_players = m.num_players or 2

    # Build player list
    players = [m.p1_user_id, m.p2_user_id]
    if expected_players == 3:
        players.append(m.p3_user_id)
    present_players = [pid for pid in players if pid is not None]

    # Read ephemeral state
    st = await _read_state(m.id) or {}
    winner_idx = st.get("winner")

    # Prize info (optional)
    prize_info = None
    if winner_idx is not None:
        if expected_players == 2:
            prize_info = {
                "winner": winner_idx,
                "winner_share": "75%",
                "company": "25%",
                "rule": "2-player",
            }
        else:
            prize_info = {
                "winner": winner_idx,
                "winner_amount": 4,
                "company_amount": 2,
                "rule": "3-player",
            }

    # ✅ Fix: free play never auto-ready, let frontend popup decide
    ready_flag = (
        m.status == MatchStatus.ACTIVE
        and m.stake_amount > 0 # 👈 Only mark ready for paid matches
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
        "prize_info": prize_info,
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

    # sanitize
    players = [pid for pid in players if pid is not None]

    if current_user.id not in players:
        raise HTTPException(status_code=403, detail="Not your match")

    curr = m.current_turn or 0
    me_turn = players.index(current_user.id)
    if me_turn != curr:
        raise HTTPException(status_code=409, detail="Not your turn")

    match_id = m.id
    if match_id not in _roll_counts:
        _roll_counts[match_id] = {"count": 0}

    _roll_counts[match_id]["count"] += 1
    count = _roll_counts[match_id]["count"]

    # fairness tweak
    if count in (6, 7, 8):
        roll = 1
        _roll_counts[match_id]["count"] = 0
    else:
        roll = random.randint(1, 6)

    st = await _read_state(m.id) or {"positions": [0] * expected_players}
    positions, next_turn, winner = _apply_roll(st["positions"], curr, roll, expected_players)

    m.last_roll = roll
    m.current_turn = next_turn

    prize_info = None
    if winner is not None:
        m.status = MatchStatus.FINISHED
        await _clear_state(m.id)
        _roll_counts.pop(match_id, None)

        if expected_players == 2:
            prize_info = {
                "winner": winner,
                "winner_share": "75%",
                "company": "25%",
                "rule": "2-player",
            }
        else:
            prize_info = {
                "winner": winner,
                "winner_amount": 4,
                "company_amount": 2,
                "rule": "3-player",
            }

    try:
        db.commit()
        db.refresh(m)
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")

    await _write_state(
        m,
        {
            "positions": positions,
            "current_turn": m.current_turn,
            "last_roll": roll,
            "winner": winner,
        },
    )

    return {
        "ok": True,
        "match_id": m.id,
        "roll": roll,
        "turn": m.current_turn,
        "positions": positions,
        "winner": winner,
        "prize_info": prize_info,
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
    winner_idx = (loser_idx + 1) % expected_players

    m.status = MatchStatus.FINISHED
    m.finished_at = _utcnow()

    try:
        await distribute_prize(db, m, winner_idx)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Prize distribution failed: {e}")

    _roll_counts.pop(m.id, None)

    try:
        db.commit()
        db.refresh(m)
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")

    await _clear_state(m.id)

    def resolve_name(uid: Optional[int]) -> str:
        if uid == BOT_USER_ID:
            return "Sharp (bot)"
        if uid == BOT_USER_ID - 1:
            return "Crazy Boy (bot)"
        return _name_for_id(db, uid)

    return {
        "ok": True,
        "match_id": m.id,
        "winner": winner_idx,
        "winner_name": resolve_name(players[winner_idx]),
        "forfeit": True,
    }


# -------------------------
# Abandon match (delete free play immediately)
# -------------------------
@router.post("/abandon")
async def abandon_match(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    m = (
        db.query(GameMatch)
        .filter(
            GameMatch.status.in_([MatchStatus.WAITING, MatchStatus.ACTIVE]),
            GameMatch.p1_user_id == current_user.id,
        )
        .first()
    )

    if not m:
        return {"ok": True, "message": "No active matches"}

    # ✅ Free play still waiting → delete right away
    if m.stake_amount == 0 and m.status == MatchStatus.WAITING:
        db.delete(m)
        db.commit()
        return {"ok": True, "message": "Free play abandoned"}

    # ✅ Paid matches or active ones → mark finished
    m.status = MatchStatus.FINISHED
    db.commit()
    return {"ok": True, "message": "Match abandoned"}



# -------------------------
# WebSocket endpoint (no bot auto-fill, no auto-ready for free play)
# -------------------------
@router.websocket("/ws/{match_id}")
async def match_ws(
    websocket: WebSocket,
    match_id: int,
    current_user: User = Depends(get_current_user_ws),
):
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
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=0.2
                    )
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

                    expected_players = m.num_players or 2
                    st = await _read_state(match_id) or {
                        "positions": [0] * expected_players,
                        "current_turn": m.current_turn or 0,
                        "last_roll": m.last_roll,
                        "winner": None,
                    }

                    # ✅ only mark ready for PAID matches
                    ready_flag = (
                        m.status == MatchStatus.ACTIVE
                        and m.stake_amount > 0
                        and m.p1_user_id is not None
                        and m.p2_user_id is not None
                        and (expected_players == 2 or m.p3_user_id is not None)
                    )

                    snapshot = {
                        "ready": ready_flag,
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
                        "num_players": expected_players,
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


# -------------------------
# Finish Match (normal win)
# -------------------------
class FinishIn(BaseModel):
    match_id: int
    winner: Optional[int] = None # index: 0 = p1, 1 = p2, 2 = p3


@router.post("/finish")
async def finish_match(
    payload: FinishIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    if m.status == MatchStatus.FINISHED:
        return {"ok": True, "message": "Match already finished"}

    # Free Play: close without wallet ops
    if m.stake_amount == 0:
        m.status = MatchStatus.FINISHED
        m.finished_at = func.now()

        winner_idx = payload.winner
        if winner_idx is not None:
            winner_id = (
                m.p1_user_id if winner_idx == 0 else
                m.p2_user_id if winner_idx == 1 else
                m.p3_user_id if winner_idx == 2 else None
            )
            m.winner_user_id = winner_id # may be None if bot

        db.commit()
        return {
            "ok": True,
            "message": "Free play finished",
            "winner": payload.winner,
            "stake": 0,
        }

    # Paid Match: credit winner with entire stake
    stake = m.stake_amount
    expected_players = m.num_players or 2

    if payload.winner is None:
        raise HTTPException(status_code=400, detail="Winner index required for paid match")

    winner_id = (
        m.p1_user_id if payload.winner == 0 else
        m.p2_user_id if payload.winner == 1 else
        m.p3_user_id if payload.winner == 2 else None
    )
    if not winner_id:
        raise HTTPException(status_code=400, detail="Invalid winner index")

    winner_user = db.query(User).filter(User.id == winner_id).first()
    if not winner_user:
        raise HTTPException(status_code=404, detail="Winner not found")

    winner_user.wallet_balance += stake

    m.winner_user_id = winner_id
    m.status = MatchStatus.FINISHED
    m.finished_at = func.now()

    db.commit()

    return {
        "ok": True,
        "message": "Match finished",
        "winner": _name_for_id(db, winner_id),
        "stake": stake,
    }

#....................cleanup..........
STALE_TIMEOUT = timedelta(seconds=12) # free-play WAITING match cutoff


async def _cleanup_stale_matches():
    """Background loop to delete stale WAITING matches (free play)."""
    from database import SessionLocal

    while True:
        try:
            db = SessionLocal()
            cutoff = datetime.utcnow() - STALE_TIMEOUT

            # Find and delete free-play WAITING matches older than cutoff
            stale = (
                db.query(GameMatch)
                .filter(
                    GameMatch.status == MatchStatus.WAITING,
                    GameMatch.stake_amount == 0,
                    GameMatch.created_at < cutoff,
                )
                .all()
            )

            if stale:
                count = len(stale)
                for m in stale:
                    db.delete(m)
                db.commit()
                print(f"[CLEANUP] Removed {count} stale free-play matches")

        except Exception as e:
            print(f"[CLEANUP ERROR] {e}")
        finally:
            db.close()

        await asyncio.sleep(30) # run every 30s
