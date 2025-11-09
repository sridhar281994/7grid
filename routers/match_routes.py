from __future__ import annotations

import asyncio
import json
import os
import random
import time
import logging
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
from redis_client import redis_client, _get_redis  # ✅ shared redis instance
import logging

from sqlalchemy import or_, and_, text
from sqlalchemy.exc import SQLAlchemyError, DataError

router = APIRouter()
log = logging.getLogger("matches")
log.setLevel(logging.DEBUG)

BOT_FALLBACK_SECONDS = 10
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
    turn_count: int = 1,
    spawned: list[bool] | None = None,
):
    """
    Apply dice roll: handles spawn (1 to enter), reverse on box 3,
    overshoot >7 stays, win exactly on 7. Returns updated positions,
    next_turn, winner, and animation flags for frontend sync.
    """
    if spawned is None:
        spawned = [False] * num_players

    p = current_turn
    old = positions[p]
    new_pos = old + roll
    winner = None
    reverse = False
    spawn_flag = False
    BOARD_MAX = 7

    # --- Rule 1: Spawn only when rolling 1 ---
    if not spawned[p]:
        if roll == 1:
            spawned[p] = True
            positions[p] = 0
            spawn_flag = True
        else:
            # Stay unspawned at 0
            positions[p] = 0
        return positions, (p + 1) % num_players, None, {
            "reverse": False,
            "spawn": spawn_flag,
            "actor": p,
            "last_roll": roll,
            "spawned": spawned,
        }

    # --- Rule 2: Reverse (danger box) at 3 ---
    if new_pos == 3:
        positions[p] = 0
        reverse = True
        return positions, (p + 1) % num_players, None, {
            "reverse": True,
            "spawn": False,
            "actor": p,
            "last_roll": roll,
            "spawned": spawned,
        }

    # --- Rule 3: Overshoot (>7) → stay ---
    if new_pos > BOARD_MAX:
        positions[p] = old
        return positions, (p + 1) % num_players, None, {
            "reverse": False,
            "spawn": False,
            "actor": p,
            "last_roll": roll,
            "spawned": spawned,
        }

    # --- Rule 4: Exact win (==7) ---
    if new_pos == BOARD_MAX:
        positions[p] = new_pos
        winner = p
        return positions, p, winner, {
            "reverse": False,
            "spawn": False,
            "actor": p,
            "last_roll": roll,
            "spawned": spawned,
        }

    # --- Rule 5: Normal move ---
    positions[p] = new_pos
    return positions, (p + 1) % num_players, None, {
        "reverse": False,
        "spawn": False,
        "actor": p,
        "last_roll": roll,
        "spawned": spawned,
    }

# -------------------------
# Redis state helpers
# -------------------------
async def _write_state(m: GameMatch, state: dict, *, override_ts: Optional[datetime] = None):
    """
    Write the current match state into Redis and publish it to subscribers.
    Includes:
      - positions, turn, roll, reverse/spawn flags
      - persistent 'spawned' list for correct spawn tracking
    """
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
        "positions": state.get("positions", [0] * num_players),
        "current_turn": state.get("current_turn", 0),
        "turn": state.get("current_turn", 0),
        "last_roll": state.get("last_roll"),
        "winner": state.get("winner"),
        "turn_count": state.get("turn_count", 0),
        "reverse": state.get("reverse", False),
        "spawn": state.get("spawn", False),
        "actor": state.get("actor"),
        "spawned": state.get("spawned", [False] * num_players), # ✅ persistent spawn state
        "last_turn_ts": (override_ts or _utcnow()).isoformat(),
    }
    try:
        if redis_client:
            await redis_client.set(f"match:{m.id}:state", json.dumps(payload), ex=24 * 60 * 60)
            await redis_client.publish(f"match:{m.id}:events", json.dumps(payload))
    except Exception as e:
        print(f"[WARN] Redis write failed: {e}")


async def _read_state(match_id: int) -> Optional[dict]:
    """
    Read the match state from Redis.
    Returns a dict with positions, turn, last_roll, etc.
    """
    if not redis_client:
        return None
    try:
        raw = await redis_client.get(f"match:{match_id}:state")
        if raw:
            data = json.loads(raw)
            # ✅ ensure 'spawned' always present
            if "spawned" not in data:
                num_players = len(data.get("positions", [])) or 2
                data["spawned"] = [False] * num_players
            return data
        return None
    except Exception:
        return None


async def _clear_state(match_id: int):
    """Remove match state from Redis when finished or forfeited."""
    if redis_client:
        try:
            await redis_client.delete(f"match:{match_id}:state")
        except Exception:
            pass


# -------------------------
# Auto-advance if timeout
# -------------------------
async def _auto_advance_if_needed(m: GameMatch, db: Session, timeout_secs: int = 10):
    """Automatically roll the dice if the active player is idle beyond timeout_secs."""

    num_players = 3 if m.p3_user_id else 2
    st = await _read_state(m.id) or {
        "positions": [0] * num_players,
        "current_turn": m.current_turn or 0,
        "last_roll": m.last_roll,
        "winner": None,
        "turn_count": 0,
        "last_turn_ts": _utcnow().isoformat(),
        "spawned": [False] * num_players,
    }

    # --- Validate last turn timestamp ---
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

    # --- Generate a random roll and apply movement ---
    roll = random.randint(1, 6)
    positions = st.get("positions", [0] * num_players)
    spawned = st.get("spawned", [False] * num_players)
    curr = st.get("current_turn", 0)
    turn_count = st.get("turn_count", 0) + 1

    # ✅ correct unpack (4-tuple)
    positions, next_turn, winner, extra = _apply_roll(
        positions, curr, roll, num_players, turn_count, spawned
    )

    m.last_roll = roll
    m.current_turn = next_turn

    # --- Handle game finish ---
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

    # --- Persist and broadcast the new state ---
    new_state = {
        "positions": positions,
        "current_turn": m.current_turn,
        "last_roll": roll,
        "winner": winner,
        "reverse": extra.get("reverse", False),  # ✅ flags to frontend
        "spawn": extra.get("spawn", False),
        "actor": extra.get("actor"),
        "turn_count": turn_count,
        "spawned": extra.get("spawned", spawned),
    }

    await _write_state(m, new_state)


# -------------------------
# Request bodies
# -------------------------
class CreateIn(BaseModel):
    stake_amount: conint(ge=0)
    num_players: conint(ge=2, le=3) = Field(default=2, description="2 or 3 players")

class RollIn(BaseModel):
    match_id: int

class ForfeitIn(BaseModel):  # ✅ FIXED missing model
    match_id: int


# -------------------------
# Create or wait for match (JOIN first for free & paid)
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

        log.debug(f"[CREATE] uid={current_user.id} stake={stake_amount} players={num_players} entry_fee={entry_fee}")

        # -------- Paid balance check (only needed if user will actually join right now) --------
        if stake_amount > 0 and (current_user.wallet_balance or 0) < entry_fee:
            raise HTTPException(status_code=400, detail="Insufficient balance")

        # -------- Try to JOIN a waiting match first (works for free & paid) --------
        q = (
            db.query(GameMatch)
            .filter(
                GameMatch.status == MatchStatus.WAITING,
                GameMatch.stake_amount == stake_amount,
                GameMatch.num_players == num_players,
                GameMatch.p1_user_id != current_user.id,
            )
            .order_by(GameMatch.id.asc())
        )
        if num_players == 2:
            q = q.filter(GameMatch.p2_user_id.is_(None))
        else:
            q = q.filter(or_(GameMatch.p2_user_id.is_(None), GameMatch.p3_user_id.is_(None)))

        waiting = q.with_for_update(skip_locked=True).first()

        if waiting:
            log.debug(f"[CREATE] joining match_id={waiting.id}")
            if num_players == 2:
                # Deduct only when match becomes ACTIVE
                if stake_amount > 0:
                    current_user.wallet_balance -= entry_fee
                waiting.p2_user_id = current_user.id
                waiting.status = MatchStatus.ACTIVE
                waiting.current_turn = random.choice([0, 1])
            else:
                # 3P: fill p2 first, then p3; ACTIVE when full
                if not waiting.p2_user_id:
                    if stake_amount > 0:
                        current_user.wallet_balance -= entry_fee
                    waiting.p2_user_id = current_user.id
                elif not waiting.p3_user_id:
                    if stake_amount > 0:
                        current_user.wallet_balance -= entry_fee
                    waiting.p3_user_id = current_user.id
                    waiting.status = MatchStatus.ACTIVE
                    waiting.current_turn = random.choice([0, 1, 2])
                else:
                    raise HTTPException(status_code=400, detail="Match already full")

            db.commit()
            db.refresh(waiting)

            # initialize state on activation (or ensure present)
            await _write_state(waiting, {"positions": [0] * num_players})

            return {
                "ok": True,
                "joined": True,
                "match_id": waiting.id,
                "status": _status_value(waiting),
                "stake": waiting.stake_amount,
                "num_players": waiting.num_players,
                "p1": _name_for_id(db, waiting.p1_user_id),
                "p2": _name_for_id(db, waiting.p2_user_id),
                "p3": _name_for_id(db, waiting.p3_user_id) if num_players == 3 else None,
                "p1_id": waiting.p1_user_id,
                "p2_id": waiting.p2_user_id,
                "p3_id": waiting.p3_user_id,
                "turn": waiting.current_turn or 0,
            }

        # -------- Otherwise CREATE a new waiting match (no deduction yet) --------
        new_match = GameMatch(
            stake_amount=stake_amount,
            status=MatchStatus.WAITING,
            p1_user_id=current_user.id,
            p2_user_id=None,
            p3_user_id=None,
            last_roll=None,
            current_turn=random.choice([0, 1] if num_players == 2 else [0, 1, 2]),
            num_players=num_players,
            created_at=_utcnow(),
        )
        db.add(new_match)
        db.commit()
        db.refresh(new_match)

        await _write_state(new_match, {"positions": [0] * num_players})

        log.debug(f"[CREATE] created new WAITING match_id={new_match.id} by uid={current_user.id}")

        return {
            "ok": True,
            "joined": False,
            "match_id": new_match.id,
            "status": _status_value(new_match),
            "stake": new_match.stake_amount,
            "num_players": num_players,
            "p1": _name_for_id(db, new_match.p1_user_id),
            "p2": None,
            "p3": None,
            "p1_id": new_match.p1_user_id,
            "p2_id": None,
            "p3_id": None,
            "turn": new_match.current_turn or 0,
        }

    except SQLAlchemyError as e:
        db.rollback()
        log.exception("DB error in /matches/create")
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")


STALE_TIMEOUT_SECS = 12  # for bot prompt timing

# -------------------------
# Check Match Readiness / Poll Sync (Updated for forfeit detection)
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

    # --- Load current Redis state ---
    st = await _read_state(m.id) or {
        "positions": [0] * expected_players,
        "turn_count": 0,
        "spawned": [False] * expected_players,
        "reverse": False,
        "spawn": False,
        "actor": None,
    }

    winner_idx = st.get("winner")
    positions = st.get("positions", [0] * expected_players)
    spawned = st.get("spawned", [False] * expected_players)
    last_roll = st.get("last_roll")
    turn = st.get("current_turn", m.current_turn or 0)

    log.debug(
        f"[CHECK] uid={current_user.id} match_id={m.id} "
        f"status={m.status} stake={m.stake_amount} players={expected_players} "
        f"turn={turn} waiting={waiting_time}s spawned={spawned}"
    )

    # ✅ Handle stale WAITING matches → offer bot
    if m.status == MatchStatus.WAITING and waiting_time >= STALE_TIMEOUT_SECS:
        if accept_bot:
            # Deduct entry fee if needed
            entry_fee = m.stake_amount // expected_players if m.stake_amount > 0 else 0
            if entry_fee > 0 and (current_user.wallet_balance or 0) < entry_fee:
                raise HTTPException(status_code=400, detail="Insufficient balance for bot match")
            if entry_fee > 0:
                current_user.wallet_balance -= entry_fee

            # Fill empty slots with bots
            if not m.p2_user_id:
                m.p2_user_id = -1000
            if expected_players == 3 and not m.p3_user_id:
                m.p3_user_id = -1001

            m.status = MatchStatus.ACTIVE
            m.current_turn = random.choice(range(expected_players))
            db.commit()
            db.refresh(m)

            # Write initial state
            await _write_state(m, {"positions": [0] * expected_players, "spawned": [False] * expected_players})

        else:
            # Ask frontend to show bot prompt
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
                "p1_id": m.p1_user_id,
                "p2_id": m.p2_user_id,
                "p3_id": m.p3_user_id,
                "turn": m.current_turn or 0,
                "positions": positions,
                "winner": winner_idx,
                "waiting_time": waiting_time,
                "prompt_bot": True,
            }

    # ✅ Auto-advance inactive turns
    if m.status == MatchStatus.ACTIVE:
        try:
            await _auto_advance_if_needed(m, db)
        except Exception:
            log.exception("[CHECK] auto-advance failed")

    # ✅ Detect if a forfeit happened (match finished but not yet shown to players)
    if m.status == MatchStatus.FINISHED and winner_idx is None:
        # Fetch winner info from DB directly
        winner_idx = 0
        for i, uid in enumerate([m.p1_user_id, m.p2_user_id, m.p3_user_id][:expected_players]):
            if uid == m.winner_user_id:
                winner_idx = i
                break
        log.debug(f"[FORFEIT DETECTED] winner_idx={winner_idx} winner_user_id={m.winner_user_id}")

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
        "p1_id": m.p1_user_id,
        "p2_id": m.p2_user_id,
        "p3_id": m.p3_user_id,
        "last_roll": last_roll,
        "turn": turn,
        "positions": positions,
        "spawned": spawned,
        "reverse": st.get("reverse", False),
        "spawn": st.get("spawn", False),
        "actor": st.get("actor"),
        "winner": winner_idx,
        "turn_count": st.get("turn_count", 0),
        "waiting_time": waiting_time,
        "prompt_bot": (m.status == MatchStatus.WAITING and waiting_time >= STALE_TIMEOUT_SECS),
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
    import copy

    # --- Get match from DB ---
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Match not active")

    # --- Determine players dynamically ---
    expected_players = m.num_players or 2
    players = [m.p1_user_id, m.p2_user_id]
    if expected_players == 3:
        players.append(m.p3_user_id)

    # ✅ Filter out forfeited / empty slots
    players = [p for p in players if p is not None]
    if not players:
        raise HTTPException(status_code=400, detail="No active players remain")

    # --- Verify turn ownership ---
    if current_user.id not in players:
        raise HTTPException(status_code=403, detail="Not your match")

    curr = m.current_turn or 0
    if curr >= len(players):
        curr = 0  # fallback if invalid index after forfeit

    me_turn = players.index(current_user.id)
    if me_turn != curr:
        raise HTTPException(status_code=409, detail="Not your turn")

    # --- Roll dice ---
    roll = random.randint(1, 6)

    # --- Load previous board state ---
    st = await _read_state(m.id) or {
        "positions": [0] * expected_players,
        "turn_count": 0,
        "spawned": [False] * expected_players
    }

    positions = [int(x) for x in st.get("positions", [0] * expected_players)]
    spawned = st.get("spawned", [False] * expected_players)
    turn_count = int(st.get("turn_count", 0)) + 1

    # --- Apply roll logic ---
    positions, next_turn, winner, extra = _apply_roll(
        copy.deepcopy(positions),
        curr,
        roll,
        len(players),
        turn_count,
        spawned
    )

    # --- Update match state ---
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

    # --- Persist updated state ---
    new_state = {
        "positions": positions,
        "current_turn": m.current_turn,
        "last_roll": roll,
        "winner": winner,
        "reverse": extra.get("reverse", False),
        "spawn": extra.get("spawn", False),
        "actor": extra.get("actor"),
        "turn_count": turn_count,
        "spawned": extra.get("spawned", spawned),
    }

    await _write_state(m, new_state)

    # --- Respond to client ---
    return {
        "ok": True,
        "match_id": m.id,
        "roll": roll,
        "turn": m.current_turn,
        "positions": positions,
        "winner": winner,
        "reverse": extra.get("reverse", False),
        "spawn": extra.get("spawn", False),
        "actor": extra.get("actor"),
        "turn_count": turn_count,
    }


# -------------------------
# Forfeit / Give Up (Supports 3-Player Continuation)
# -------------------------
@router.post("/forfeit")
async def forfeit_match(
    payload: ForfeitIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    """Handle player giving up (forfeit) — fully removes them from match and notifies all others."""
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
    log.debug(f"[FORFEIT] uid={current_user.id} (idx={loser_idx}) forfeiting match_id={m.id}")

    # --- Remove forfeited player from DB ---
    if loser_idx == 0:
        m.p1_user_id = None
    elif loser_idx == 1:
        m.p2_user_id = None
    elif loser_idx == 2:
        m.p3_user_id = None

    # --- Build new player list ---
    new_players = [uid for uid in [m.p1_user_id, m.p2_user_id, m.p3_user_id] if uid]
    remaining_count = len(new_players)

    # --- Case 1: Only one player left → declare winner ---
    if remaining_count <= 1:
        m.status = MatchStatus.FINISHED
        m.finished_at = datetime.now(timezone.utc)
        winner_idx = 0 if new_players else None
        if winner_idx is not None and m.stake_amount > 0:
            await distribute_prize(db, m, winner_idx)
        db.commit()
        await _write_state(
            m,
            {
                "positions": [0] * expected_players,
                "forfeit": True,
                "forfeit_actor": loser_idx,
                "finished": True,
                "status": "FINISHED",
                "winner": winner_idx,
                "message": f"Player {loser_idx + 1} gave up. Match ended.",
            },
        )
        await asyncio.sleep(1.0)
        await _clear_state(m.id)
        return {
            "ok": True,
            "match_id": m.id,
            "forfeit": True,
            "forfeit_actor": loser_idx,
            "winner": winner_idx,
            "continuing": False,
        }

    # --- Case 2: Two or more remain → continue game ---
    m.status = MatchStatus.ACTIVE
    m.finished_at = None
    m.num_players = remaining_count

    # --- Update current_turn if it was the forfeited player ---
    current_turn = m.current_turn or 0
    if current_turn == loser_idx or (current_turn >= remaining_count):
        current_turn = 0
    m.current_turn = current_turn

    db.commit()
    db.refresh(m)

    # --- Broadcast reduced player list and remove forfeited player from Redis ---
    await _write_state(
        m,
        {
            "positions": [0] * remaining_count,
            "current_turn": m.current_turn,
            "forfeit": True,
            "forfeit_actor": loser_idx,
            "continuing": True,
            "status": "ACTIVE",
            "visible_players": new_players,
            "message": f"Player {loser_idx + 1} gave up and left the game.",
        },
    )

    return {
        "ok": True,
        "match_id": m.id,
        "forfeit": True,
        "forfeit_actor": loser_idx,
        "continuing": True,
        "remaining_players": remaining_count,
        "current_turn": m.current_turn,
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
    print(f"[WS] New connection: user={current_user.id} match_id={match_id}")

    # ✅ Always fetch a live Redis client
    r = await _get_redis()
    if not r:
        err = "Redis unavailable - closing socket"
        print(f"[WS][ERROR] {err}")
        await websocket.send_text(json.dumps({"error": err}))
        await websocket.close()
        return

    # ✅ PubSub subscription
    pubsub = r.pubsub()
    await pubsub.subscribe(f"match:{match_id}:events")
    print(f"[WS] Subscribed to Redis channel match:{match_id}:events")

    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if msg and msg.get("type") == "message":
                try:
                    event = json.loads(msg["data"])  # ✅ ensure valid JSON
                    print(f"[WS][EVENT] Redis → {event}")
                    await websocket.send_text(json.dumps(event))
                except Exception as e:
                    print(f"[WS][WARN] Raw Redis msg: {msg['data']} ({e})")
                    await websocket.send_text(msg["data"])
            else:
                # --- Fallback: snapshot sync from DB ---
                db = SessionLocal()
                try:
                    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
                    if not m:
                        print(f"[WS][ERROR] Match not found: {match_id}")
                        await websocket.send_text(json.dumps({"error": "Match not found"}))
                        break

                    expected_players = m.num_players or 2
                    st = await _read_state(match_id) or {
                        "positions": [0] * expected_players,
                        "current_turn": m.current_turn or 0,
                        "last_roll": m.last_roll,
                        "winner": None,
                        "turn_count": 0,
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
                        "turn_count": st.get("turn_count", 0),
                        "reverse": st.get("reverse", False),  # ✅ include flags in snapshots too
                        "spawn": st.get("spawn", False),
                        "actor": st.get("actor"),
                    }
                    print(f"[WS][SNAPSHOT] {snapshot}")
                    await websocket.send_text(json.dumps(snapshot))
                finally:
                    db.close()

            await asyncio.sleep(0.3)

    except WebSocketDisconnect:
        print(f"[WS] Closed for match {match_id} (user={current_user.id})")
    finally:
        try:
            await pubsub.unsubscribe(f"match:{match_id}:events")
            await pubsub.close()
        except Exception as e:
            print(f"[WS][WARN] PubSub cleanup failed: {e}")
        print(f"[WS] Unsubscribed + closed Redis pubsub for match {match_id}")


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
