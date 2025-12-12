from __future__ import annotations

import asyncio
import json
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
from routers.wallet_utils import distribute_prize, get_system_merchant_id
from routers.game import get_stake_rule
from redis_client import redis_client, _get_redis  # ✅ shared redis instance
import logging

from sqlalchemy import or_, and_, text
from sqlalchemy.exc import SQLAlchemyError, DataError

STALE_TIMEOUT_SECS = 12

router = APIRouter()
log = logging.getLogger("matches")
log.setLevel(logging.DEBUG)

BOT_FALLBACK_SECONDS = 10

COINS_PER_PLAYER = 2
FINAL_BOX_INDEX = 8
DANGER_BOX_INDEX = 3


def _empty_positions(num_players: int) -> list[list[int]]:
    return [[-1 for _ in range(COINS_PER_PLAYER)] for _ in range(num_players)]


def _clone_positions(positions: list[list[int]]) -> list[list[int]]:
    return [list(player[:COINS_PER_PLAYER]) for player in positions]


def _normalize_positions(raw_positions, num_players: int) -> list[list[int]]:
    normalized = _empty_positions(num_players)
    if not raw_positions:
        return normalized

    for player_idx in range(min(num_players, len(raw_positions))):
        value = raw_positions[player_idx]
        if isinstance(value, (list, tuple)):
            for coin_idx in range(COINS_PER_PLAYER):
                try:
                    raw_val = value[coin_idx]
                except IndexError:
                    raw_val = None
                normalized[player_idx][coin_idx] = (
                    int(raw_val) if raw_val is not None else -1
                )
        else:
            normalized[player_idx][0] = int(value) if value is not None else -1

    return normalized


def _compute_spawned(positions: list[list[int]]) -> list[list[bool]]:
    spawned: list[list[bool]] = []
    for coins in positions:
        entry = []
        for pos in coins[:COINS_PER_PLAYER]:
            entry.append(pos >= 0)
        while len(entry) < COINS_PER_PLAYER:
            entry.append(False)
        spawned.append(entry)
    return spawned


def _count_finished(positions: list[list[int]]) -> list[int]:
    counts: list[int] = []
    for coins in positions:
        counts.append(sum(1 for pos in coins if pos == FINAL_BOX_INDEX))
    return counts


def _select_coin_for_auto(coins: list[int]) -> Optional[int]:
    for idx, pos in enumerate(coins[:COINS_PER_PLAYER]):
        if 0 <= pos < FINAL_BOX_INDEX:
            return idx
    for idx, pos in enumerate(coins[:COINS_PER_PLAYER]):
        if pos < 0:
            return idx
    return None

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
    coin_index: Optional[int] = Field(default=None, ge=0, le=1)
class ForfeitIn(BaseModel):
    match_id: int
class FinishIn(BaseModel):
    match_id: int
    winner: Optional[int] = None

class ChatIn(BaseModel):
    match_id: int
    text: str = Field(..., min_length=1, max_length=80)
    client_msg_id: Optional[str] = None

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


def _player_ids(m: GameMatch) -> list[Optional[int]]:
    num = m.num_players or 2
    return [m.p1_user_id, m.p2_user_id, m.p3_user_id][:num]


def _player_index_for_user(m: GameMatch, user_id: Optional[int]) -> Optional[int]:
    if user_id is None:
        return None
    try:
        return _player_ids(m).index(user_id)
    except ValueError:
        return None


def _status_value(m: GameMatch) -> str:
    try:
        return m.status.value
    except Exception:
        return str(m.status)


def _apply_roll(
    positions: list[list[int]] | list[int],
    current_turn: int,
    roll: int,
    *,
    num_players: int,
    coin_index: Optional[int] = None,
):
    """
    Apply the complete turn for the current player with explicit coin selection.
    Rules enforced:
      - Each player controls two coins (index 0/1).
      - Coins enter box 0 only when rolling a 1 while off-board (-1).
      - Landing on box 3 (danger) immediately sends the coin back to box 0.
      - Overshooting the final box (8) keeps the coin in place.
      - Capturing sends every opponent coin on the target tile back to box 0.
      - A player wins after locking both coins at the final box.
    """
    num_players = max(2, num_players)
    board = _normalize_positions(positions, num_players)
    actor = current_turn % num_players
    coins = board[actor]
    turn_meta = {
        "actor": actor,
        "coin_index": coin_index,
        "last_roll": roll,
        "spawn": False,
        "reverse": False,
        "kills": [],
    }
    next_turn = (actor + 1) % num_players

    movable = [
        idx
        for idx, pos in enumerate(coins[:COINS_PER_PLAYER])
        if pos < FINAL_BOX_INDEX
    ]
    if not movable:
        # Already finished – treat as win safeguard.
        turn_meta["spawned"] = _compute_spawned(board)
        turn_meta["finished_counts"] = _count_finished(board)
        turn_meta["already_finished"] = True
        return board, actor, actor, turn_meta

    selected_idx = coin_index if coin_index is not None else movable[0]
    turn_meta["auto_selected"] = coin_index is None
    turn_meta["coin_index"] = selected_idx

    if selected_idx not in range(COINS_PER_PLAYER):
        raise ValueError("Invalid coin index")
    if selected_idx not in movable:
        raise ValueError("Selected coin cannot move")

    current_pos = coins[selected_idx]

    if current_pos == FINAL_BOX_INDEX:
        raise ValueError("Coin already locked at final box")

    # Off-board coin can spawn only on rolling 1.
    if current_pos < 0:
        if roll != 1:
            turn_meta["skipped"] = True
            turn_meta["spawned"] = _compute_spawned(board)
            turn_meta["finished_counts"] = _count_finished(board)
            return board, next_turn, None, turn_meta
        coins[selected_idx] = 0
        turn_meta["spawn"] = True
    else:
        target = current_pos + roll
        if target == DANGER_BOX_INDEX:
            coins[selected_idx] = 0
            turn_meta["reverse"] = True
        elif target > FINAL_BOX_INDEX:
            coins[selected_idx] = current_pos
            turn_meta["blocked"] = True
            turn_meta["spawned"] = _compute_spawned(board)
            turn_meta["finished_counts"] = _count_finished(board)
            return board, next_turn, None, turn_meta
        else:
            coins[selected_idx] = target

    final_pos = coins[selected_idx]

    # Capture opponents that occupy the landing tile (except finished coins).
    if final_pos >= 0 and final_pos != FINAL_BOX_INDEX:
        for opp_idx, opp_coins in enumerate(board):
            if opp_idx == actor:
                continue
            for opp_coin_idx, opp_pos in enumerate(opp_coins[:COINS_PER_PLAYER]):
                if opp_pos == final_pos:
                    board[opp_idx][opp_coin_idx] = 0
                    turn_meta["kills"].append(
                        {"player": opp_idx, "coin": opp_coin_idx}
                    )

    winner = None
    if all(pos == FINAL_BOX_INDEX for pos in coins[:COINS_PER_PLAYER]):
        winner = actor

    turn_meta["spawned"] = _compute_spawned(board)
    turn_meta["finished_counts"] = _count_finished(board)

    return board, (actor if winner is not None else next_turn), winner, turn_meta


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
    positions = _normalize_positions(state.get("positions"), num_players)
    spawned_state = state.get("spawned")
    if spawned_state is None:
        spawned_state = _compute_spawned(positions)
    finished_counts = state.get("finished_counts") or _count_finished(positions)

    # Preserve chat history across state writes unless explicitly set.
    chat_messages = state.get("chat_messages")
    if chat_messages is None:
        try:
            prev = await _read_state(m.id) or {}
            chat_messages = prev.get("chat_messages") or []
        except Exception:
            chat_messages = []
    if not isinstance(chat_messages, list):
        chat_messages = []
    # prevent unbounded growth
    if len(chat_messages) > 30:
        chat_messages = chat_messages[-30:]

    payload = {
        "ready": m.status == MatchStatus.ACTIVE
        and m.p1_user_id
        and m.p2_user_id
        and (num_players == 2 or m.p3_user_id),
        "finished": m.status == MatchStatus.FINISHED,
        "match_id": m.id,
        "status": _status_value(m),
        "stake": m.stake_amount,
        "positions": positions,
        "current_turn": state.get("current_turn", 0),
        "turn": state.get("current_turn", 0),
        "last_roll": state.get("last_roll"),
        "winner": state.get("winner"),
        "turn_count": state.get("turn_count", 0),
        "reverse": state.get("reverse", False),
        "spawn": state.get("spawn", False),
        "actor": state.get("actor"),
        "spawned": spawned_state,
        "finished_counts": finished_counts,
        "last_turn_ts": (override_ts or _utcnow()).isoformat(),
        "player_ids": _player_ids(m),
        "chat_messages": chat_messages,
    }
    try:
        if redis_client:
            await redis_client.set(f"match:{m.id}:state", json.dumps(payload), ex=24 * 60 * 60)
            await redis_client.publish(f"match:{m.id}:events", json.dumps(payload))
    except Exception as e:
        print(f"[WARN] Redis write failed: {e}")


def _sanitize_chat_text(text: str) -> str:
    # keep emojis/unicode, just normalize whitespace + enforce length
    t = (text or "").strip()
    # collapse internal newlines/tabs for UI safety
    t = " ".join(t.split())
    return t[:80]


async def _append_chat_to_state(match_id: int, message: dict):
    """Persist chat in Redis under the match state for poll/snapshot clients."""
    if not redis_client:
        return
    try:
        st = await _read_state(match_id) or {}
        msgs = st.get("chat_messages") or []
        if not isinstance(msgs, list):
            msgs = []
        msgs.append(message)
        st["chat_messages"] = msgs[-30:]
        await redis_client.set(f"match:{match_id}:state", json.dumps(st), ex=24 * 60 * 60)
    except Exception as e:
        print(f"[CHAT][WARN] Failed persisting chat: {e}")


async def _publish_chat(match_id: int, message: dict):
    """Publish chat to match-scoped subscribers (WS via Redis pubsub)."""
    if not redis_client:
        return
    try:
        await redis_client.publish(f"match:{match_id}:events", json.dumps(message))
    except Exception as e:
        print(f"[CHAT][WARN] Failed publishing chat: {e}")


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
            num_players = len(data.get("positions") or []) or 2
            data["positions"] = _normalize_positions(data.get("positions"), num_players)
            if "spawned" not in data:
                data["spawned"] = _compute_spawned(data["positions"])
            if "finished_counts" not in data:
                data["finished_counts"] = _count_finished(data["positions"])
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
# Auto advance (fixed turn skip)
# -------------------------
async def _auto_advance_if_needed(m: GameMatch, db: Session, timeout_secs=10):

    num_players = 3 if m.p3_user_id else 2
    empty_board = _empty_positions(num_players)
    st = await _read_state(m.id) or {
        "positions": empty_board,
        "current_turn": m.current_turn,
        "turn_count": 0,
        "spawned": _compute_spawned(empty_board),
        "finished_counts": _count_finished(empty_board),
        "last_turn_ts": _utcnow().isoformat()
    }

    # Timeout
    if _utcnow() - datetime.fromisoformat(st["last_turn_ts"]) < timedelta(seconds=timeout_secs):
        return

    slots = [m.p1_user_id, m.p2_user_id, m.p3_user_id]
    forfeited = set(m.forfeit_ids or [])

    active = [
        i for i, uid in enumerate(slots[:num_players])
        if uid and uid not in forfeited
    ]

    if not active:
        return

    curr = st.get("current_turn", 0)
    if curr not in active:
        curr = active[0]

    roll = random.randint(1, 6)

    positions = st["positions"]
    turn_count = st["turn_count"] + 1

    auto_coin = _select_coin_for_auto(positions[curr])
    if auto_coin is None:
        return

    board_after, next_turn, winner, extra = _apply_roll(
        _clone_positions(positions),
        curr,
        roll,
        num_players=num_players,
        coin_index=auto_coin,
    )

    # Winner
    if winner is not None:
        m.status = MatchStatus.FINISHED
        await distribute_prize(db, m, winner)
        await _write_state(
            m,
            {
                "winner": winner,
                "finished": True,
                "positions": board_after,
                "spawned": extra.get("spawned"),
                "finished_counts": extra.get("finished_counts"),
            },
        )
        await _clear_state(m.id)
        return

    # Skip forfeited
    for _ in range(num_players):
        if next_turn in active:
            break
        next_turn = (next_turn + 1) % num_players

    m.current_turn = next_turn
    db.commit()

    await _write_state(
        m,
        {
            "positions": board_after,
            "current_turn": next_turn,
            "last_roll": roll,
            "turn_count": turn_count,
            "spawned": extra.get("spawned"),
            "reverse": extra.get("reverse", False),
            "spawn": extra.get("spawn", False),
            "actor": extra.get("actor"),
            "finished_counts": extra.get("finished_counts"),
        },
    )


# -------------------------
# Request bodies
# -------------------------
class CreateIn(BaseModel):
    stake_amount: conint(ge=0)
    num_players: conint(ge=2, le=3) = Field(default=2, description="2 or 3 players")

class RollIn(BaseModel):
    match_id: int
    # Optional for backward compatibility; when provided, must be 0 or 1 (two coins per player).
    coin_index: Optional[int] = Field(default=None, ge=0, le=1)

class ForfeitIn(BaseModel):  # ✅ FIXED missing model
    match_id: int


# -------------------------
# Create or join match (uses stakes table, proper entry_fee)
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

        # ---- Load stake rule from stakes table ----
        rule = get_stake_rule(db, stake_amount, num_players)
        if not rule:
            raise HTTPException(status_code=400, detail="Invalid stake configuration")

        entry_fee = rule["entry_fee"]

        log.debug(
            f"[CREATE] uid={current_user.id} stake={stake_amount} "
            f"players={num_players} entry_fee={entry_fee}"
        )

        # ---- Check balance BEFORE doing anything ----
        if entry_fee > 0 and (current_user.wallet_balance or 0) < entry_fee:
            raise HTTPException(status_code=400, detail="Insufficient balance")

        # ---- Try joining existing WAITING match ----
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
            # Player is joining an existing waiting match.
            # P1 should have been charged when creating; we charge this user now.
            if entry_fee > 0:
                current_user.wallet_balance = (current_user.wallet_balance or 0) - entry_fee

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

            # Initialize board state when match becomes ACTIVE
            await _write_state(waiting, {"positions": _empty_positions(num_players)})

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
                "player_ids": _player_ids(waiting),
                "player_index": _player_index_for_user(waiting, current_user.id),
            }

        # ---- No WAITING match → create new WAITING match ----
        # We still charge P1 now so everyone pays entry_fee once.
        if entry_fee > 0:
            current_user.wallet_balance = (current_user.wallet_balance or 0) - entry_fee

        merchant_id = get_system_merchant_id(db)
        if merchant_id == current_user.id:
            # Prevent players from being treated as the merchant for this match.
            merchant_id = None

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
            merchant_user_id=merchant_id,
        )
        db.add(new_match)
        db.commit()
        db.refresh(new_match)

        # Initial empty board for WAITING match
        await _write_state(new_match, {"positions": _empty_positions(num_players)})

        log.debug(f"[CREATE] new WAITING match_id={new_match.id} by {current_user.id}")

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
            "player_ids": _player_ids(new_match),
            "player_index": _player_index_for_user(new_match, current_user.id),
        }

    except SQLAlchemyError as e:
        db.rollback()
        log.exception("DB error in /matches/create")
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")


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

    # ---------- slot / fill info ----------
    slots = _player_ids(m)
    player_index = _player_index_for_user(m, current_user.id)
    filled_slots = sum(1 for uid in slots if uid is not None)

    # ---------- Redis state ----------
    base_positions = _empty_positions(expected_players)
    st = await _read_state(m.id) or {
        "positions": base_positions,
        "turn_count": 0,
        "spawned": _compute_spawned(base_positions),
        "reverse": False,
        "spawn": False,
        "actor": None,
        "finished_counts": _count_finished(base_positions),
    }
    chat_messages = st.get("chat_messages") or []
    if not isinstance(chat_messages, list):
        chat_messages = []
    if len(chat_messages) > 30:
        chat_messages = chat_messages[-30:]

    winner_idx = st.get("winner")
    positions = _normalize_positions(st.get("positions"), expected_players)
    spawned = st.get("spawned") or _compute_spawned(positions)
    last_roll = st.get("last_roll")
    turn = st.get("current_turn", m.current_turn or 0)

    log.debug(
        f"[CHECK] uid={current_user.id} match_id={m.id} "
        f"status={m.status} stake={m.stake_amount} players={expected_players} "
        f"turn={turn} waiting={waiting_time}s spawned={spawned} filled={filled_slots}"
    )

    # ======================================================
    # 0) FREE PLAY — ONLINE ONLY, NO BOT PROMPT EVER
    # ======================================================
    if m.stake_amount == 0:
        ready_flag = (
            m.status == MatchStatus.ACTIVE
            and filled_slots == expected_players
        )

        return {
            "ready": ready_flag,
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
            "prompt_bot": False,
            "player_ids": slots,
            "player_index": player_index,
            "chat_messages": chat_messages,
        }

    # ======================================================
    # 1) FULL LOBBY BUT STILL WAITING → PROMOTE TO ACTIVE
    # ======================================================
    if m.status == MatchStatus.WAITING and filled_slots == expected_players:
        m.status = MatchStatus.ACTIVE
        db.commit()
        db.refresh(m)

        st = await _read_state(m.id) or st
        positions = _normalize_positions(st.get("positions"), expected_players)
        spawned = st.get("spawned") or _compute_spawned(positions)
        last_roll = st.get("last_roll")
        turn = st.get("current_turn", m.current_turn or 0)

        return {
            "ready": True,
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
            "prompt_bot": False,
            "player_ids": slots,
            "player_index": player_index,
            "chat_messages": chat_messages,
        }

    # ======================================================
    # 2) WAITING + TIMEOUT → LET AGENT_POOL HANDLE IT (NO POPUP)
    # ======================================================
    if m.status == MatchStatus.WAITING and waiting_time >= STALE_TIMEOUT_SECS:
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
            "turn": turn,
            "positions": positions,
            "winner": winner_idx,
            "waiting_time": waiting_time,
            "prompt_bot": False,
            "player_ids": slots,
            "player_index": player_index,
        }

    # ======================================================
    # 3) AUTO-ADVANCE FOR AFK
    # ======================================================
    if m.status == MatchStatus.ACTIVE:
        try:
            await _auto_advance_if_needed(m, db)
        except Exception:
            log.exception("[CHECK] auto-advance failed")

        st = await _read_state(m.id) or st
        positions = _normalize_positions(st.get("positions"), expected_players)
        spawned = st.get("spawned") or _compute_spawned(positions)
        last_roll = st.get("last_roll", last_roll)
        turn = st.get("current_turn", m.current_turn or turn)
        winner_idx = st.get("winner", winner_idx)

    # ======================================================
    # 4) FINISHED MATCH
    # ======================================================
    if m.status == MatchStatus.FINISHED:
        if winner_idx is None:
            winner_idx = 0
            ids = [m.p1_user_id, m.p2_user_id, m.p3_user_id][:expected_players]
            for i, uid in enumerate(ids):
                if uid == m.winner_user_id:
                    winner_idx = i
                    break

        return {
            "ready": True,
            "finished": True,
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
            "winner": winner_idx,
            "finished_at": m.finished_at.isoformat() if m.finished_at else None,
            "player_ids": slots,
            "player_index": player_index,
        }

    # ======================================================
    # 5) ACTIVE NORMAL RESPONSE
    # ======================================================
    ready_flag = (
        m.status == MatchStatus.ACTIVE
        and m.p1_user_id is not None
        and m.p2_user_id is not None
        and (expected_players == 2 or m.p3_user_id is not None)
    )

    return {
        "ready": ready_flag,
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
        "prompt_bot": False,
        "player_ids": slots,
        "player_index": player_index,
        "chat_messages": chat_messages,
    }


# -------------------------
# Roll Dice (STRICT rotation + forfeit skip)
# -------------------------
@router.post("/roll")
async def roll_dice(
    payload: RollIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict:

    import copy

    # Fetch match
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(404, "Match not found")
    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(400, "Match not active")

    # Player slots
    slots = [m.p1_user_id, m.p2_user_id, m.p3_user_id]
    forfeited = set(m.forfeit_ids or [])
    num_players = m.num_players or 2

    active_indices = [
        i for i, uid in enumerate(slots[:num_players])
        if uid and uid not in forfeited
    ]

    if not active_indices:
        raise HTTPException(400, "No active players remain")

    # Validate requester belongs
    if current_user.id not in slots:
        raise HTTPException(403, "Not your match")

    # -------------------------
    # FIX TURN LOGIC
    # -------------------------
    curr = m.current_turn or 0

    # If current turn is forfeited OR user missing → move to first active
    if curr not in active_indices:
        curr = active_indices[0]
        m.current_turn = curr
        db.commit()

    # Check turn
    me_idx = slots.index(current_user.id)
    if me_idx != curr:
        raise HTTPException(409, "Not your turn")

    # -------------------------
    # Roll
    # -------------------------
    roll = random.randint(1, 6)

    selection_required = current_user.id > 0
    coin_index = payload.coin_index
    if selection_required and coin_index is None:
        raise HTTPException(422, "Select a coin before rolling")

    base_positions = _empty_positions(num_players)
    st = await _read_state(m.id) or {
        "positions": base_positions,
        "turn_count": 0,
        "spawned": _compute_spawned(base_positions),
        "finished_counts": _count_finished(base_positions),
    }

    positions = _normalize_positions(st.get("positions"), num_players)
    spawned = st.get("spawned") or _compute_spawned(positions)
    turn_count = int(st.get("turn_count", 0)) + 1

    try:
        board_after, next_turn, winner, extra = _apply_roll(
            _clone_positions(positions),
            curr,
            roll,
            num_players=num_players,
            coin_index=coin_index,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    positions = board_after

    # -------------------------
    # Winner case
    # -------------------------
    if winner is not None:
        m.last_roll = roll

        try:
            await distribute_prize(db, m, winner)
        except Exception as e:
            db.rollback()
            raise HTTPException(500, f"Prize distribution failed: {e}")

        final_state = {
            "positions": positions,
            "current_turn": winner,
            "last_roll": roll,
            "winner": winner,
            "reverse": extra.get("reverse", False),
            "spawn": extra.get("spawn", False),
            "actor": extra.get("actor"),
            "turn_count": turn_count,
            "spawned": extra.get("spawned", spawned),
            "finished_counts": extra.get("finished_counts"),
            "kills": extra.get("kills", []),
            "coin_index": extra.get("coin_index"),
            "finished": True
        }

        await _write_state(m, final_state)
        await asyncio.sleep(1)
        await _clear_state(m.id)

        return {
            "ok": True,
            **final_state,
            "player_ids": _player_ids(m),
            "player_index": me_idx,
        }

    # -------------------------
    # Normal turn advance
    # -------------------------
    # Auto-skip forfeited users
    for _ in range(num_players):
        if next_turn in active_indices:
            break
        next_turn = (next_turn + 1) % num_players

    m.last_roll = roll
    m.current_turn = next_turn
    db.commit()

    new_state = {
        "positions": positions,
        "current_turn": next_turn,
        "last_roll": roll,
        "winner": None,
        "reverse": extra.get("reverse", False),
        "spawn": extra.get("spawn", False),
        "actor": extra.get("actor"),
        "turn_count": turn_count,
        "spawned": extra.get("spawned", spawned),
        "finished_counts": extra.get("finished_counts"),
        "kills": extra.get("kills", []),
        "coin_index": extra.get("coin_index"),
    }

    await _write_state(m, new_state)
    return {
        "ok": True,
        **new_state,
        "player_ids": _player_ids(m),
        "player_index": me_idx,
    }


# -------------------------
# Chat (match-scoped)
# -------------------------
@router.post("/chat")
async def send_match_chat(
    payload: ChatIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(404, "Match not found")

    slots = _player_ids(m)
    if current_user.id not in slots:
        raise HTTPException(403, "Not your match")

    sender_index = slots.index(current_user.id)
    text = _sanitize_chat_text(payload.text)
    if not text:
        raise HTTPException(400, "Empty message")

    msg = {
        "type": "chat",
        "match_id": m.id,
        "text": text,
        "client_msg_id": payload.client_msg_id,
        "sender_index": sender_index,
        "ts": time.time(),
    }

    # Persist for polling clients + broadcast for WS clients.
    await _append_chat_to_state(m.id, msg)
    await _publish_chat(m.id, msg)
    return {"ok": True}


# -------------------------
# Forfeit / Give Up (FULL FIX)
# -------------------------
@router.post("/forfeit")
async def forfeit_match(
    payload: ForfeitIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict:

    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(404, "Match not found")
    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(400, "Match not active")

    expected_players = m.num_players or 2

    # Player slots (do NOT mutate the DB columns; keep for history)
    slots = _player_ids(m)
    player_index = _player_index_for_user(m, current_user.id)

    if current_user.id not in slots:
        raise HTTPException(403, "Not your match")

    loser_idx = slots.index(current_user.id)
    base_positions = _empty_positions(expected_players)
    state = await _read_state(m.id) or {
        "positions": base_positions,
        "current_turn": m.current_turn or 0,
        "last_roll": m.last_roll,
        "turn_count": 0,
        "spawned": _compute_spawned(base_positions),
        "finished_counts": _count_finished(base_positions),
    }
    positions = _normalize_positions(state.get("positions"), expected_players)
    spawned = state.get("spawned") or _compute_spawned(positions)
    turn_count = state.get("turn_count", 0)
    last_roll = state.get("last_roll", m.last_roll)

    # Mark forfeiter
    forfeited = set(m.forfeit_ids or [])
    forfeited.add(current_user.id)
    m.forfeit_ids = list(forfeited)

    # Active players
    active_indices = [
        i for i, uid in enumerate(slots)
        if uid is not None and uid not in forfeited
    ]

    # ---------------------------
    # CASE 1 — Only one left → WINNER
    # ---------------------------
    if len(active_indices) == 1:
        winner_idx = active_indices[0]
        winner_uid = slots[winner_idx]

        m.status = MatchStatus.FINISHED
        m.finished_at = datetime.now(timezone.utc)
        m.winner_user_id = winner_uid

        try:
            await distribute_prize(db, m, winner_idx)
        except:
            db.rollback()
            raise

        final_state = {
            "positions": positions,
            "current_turn": winner_idx,
            "last_roll": last_roll,
            "winner": winner_idx,
            "finished": True,
            "forfeit": True,
            "forfeit_actor": loser_idx,
            "active_players": [slots[winner_idx]],
            "forfeit_ids": list(forfeited),
            "spawned": spawned,
            "turn_count": turn_count,
            "finished_counts": _count_finished(positions),
        }

        await _write_state(m, final_state)
        await asyncio.sleep(1)
        await _clear_state(m.id)

        return {
            "ok": True,
            "forfeit": True,
            "continuing": False,
            "winner": winner_idx,
            "player_ids": slots,
            "player_index": player_index,
        }

    # ---------------------------
    # CASE 2 — Continue (find next turn)
    # ---------------------------
    curr = m.current_turn or 0

    # If current turn invalid or forfeited → jump to first active
    if curr not in active_indices:
        m.current_turn = active_indices[0]
    else:
        # Move to next active
        nxt = curr
        for _ in range(expected_players):
            nxt = (nxt + 1) % expected_players
            if nxt in active_indices:
                break
        m.current_turn = nxt

    db.commit()

    active_player_ids = [slots[i] for i in active_indices]

    await _write_state(
        m,
        {
            "forfeit": True,
            "forfeit_actor": loser_idx,
            "continuing": True,
            "current_turn": m.current_turn,
            "positions": positions,
            "last_roll": last_roll,
            "turn_count": turn_count,
            "winner": None,
            "active_players": active_player_ids,
            "forfeit_ids": list(forfeited),
            "spawned": spawned,
            "finished_counts": _count_finished(positions),
        },
    )

    return {
        "ok": True,
        "forfeit": True,
        "continuing": True,
        "current_turn": m.current_turn,
        "player_ids": slots,
        "player_index": player_index,
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

    r = await _get_redis()
    if not r:
        err = "Redis unavailable - closing socket"
        print(f"[WS][ERROR] {err}")
        try:
            await websocket.send_text(json.dumps({"error": err}))
        except:
            pass
        await websocket.close()
        return

    pubsub = r.pubsub()
    await pubsub.subscribe(f"match:{match_id}:events")
    print(f"[WS] Subscribed to Redis channel match:{match_id}:events")

    last_snapshot = 0.0
    try:
        while True:
            # -------------------------
            # 1) INCOMING messages from client (chat)
            # -------------------------
            try:
                incoming = await asyncio.wait_for(websocket.receive_text(), timeout=0.01)
            except asyncio.TimeoutError:
                incoming = None
            except WebSocketDisconnect:
                break
            except Exception:
                incoming = None

            if incoming:
                try:
                    data = json.loads(incoming)
                except Exception:
                    data = None
                if isinstance(data, dict) and (data.get("type") or "").lower() == "chat":
                    # enforce match scope
                    try:
                        if int(data.get("match_id")) != int(match_id):
                            data = None
                    except Exception:
                        data = None
                if isinstance(data, dict) and (data.get("type") or "").lower() == "chat":
                    # Validate sender belongs to match and compute sender_index from DB slots (authoritative)
                    db = SessionLocal()
                    try:
                        m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
                        if m:
                            slots = _player_ids(m)
                            if current_user.id in slots:
                                sender_index = slots.index(current_user.id)
                                text = _sanitize_chat_text(str(data.get("text") or ""))
                                if text:
                                    msg = {
                                        "type": "chat",
                                        "match_id": match_id,
                                        "text": text,
                                        "client_msg_id": data.get("client_msg_id"),
                                        "sender_index": sender_index,
                                        "ts": time.time(),
                                    }
                                    await _append_chat_to_state(match_id, msg)
                                    await _publish_chat(match_id, msg)
                    finally:
                        db.close()

            # -------------------------
            # 2) Redis Event (broadcast)
            # -------------------------
            try:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)
            except Exception:
                msg = None

            if msg and msg.get("type") == "message":
                try:
                    event = json.loads(msg["data"])
                    # SAFE SEND
                    await websocket.send_text(json.dumps(event))
                except Exception:
                    break

            # -------------------------
            # 3) Snapshot fallback (throttled)
            # -------------------------
            now = time.monotonic()
            if now - last_snapshot >= 1.5:
                last_snapshot = now
                db = SessionLocal()
                try:
                    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
                    if not m:
                        try:
                            await websocket.send_text(json.dumps({"error": "Match not found"}))
                        except Exception:
                            pass
                        break

                    expected_players = m.num_players or 2
                    base_positions = _empty_positions(expected_players)
                    st = await _read_state(match_id) or {
                        "positions": base_positions,
                        "current_turn": m.current_turn or 0,
                        "last_roll": m.last_roll,
                        "winner": None,
                        "turn_count": 0,
                        "spawned": _compute_spawned(base_positions),
                        "finished_counts": _count_finished(base_positions),
                        "chat_messages": [],
                    }
                    chat_messages = st.get("chat_messages") or []
                    if not isinstance(chat_messages, list):
                        chat_messages = []
                    if len(chat_messages) > 30:
                        chat_messages = chat_messages[-30:]

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
                        "positions": _normalize_positions(st.get("positions"), expected_players),
                        "winner": st.get("winner"),
                        "turn_count": st.get("turn_count", 0),
                        "reverse": st.get("reverse", False),
                        "spawn": st.get("spawn", False),
                        "actor": st.get("actor"),
                        "player_ids": _player_ids(m),
                        "player_index": _player_index_for_user(m, current_user.id),
                        "chat_messages": chat_messages,
                    }
                    await websocket.send_text(json.dumps(snapshot))
                finally:
                    db.close()

            await asyncio.sleep(0.05)

    except WebSocketDisconnect:
        print(f"[WS] Closed for match {match_id} (user={current_user.id})")

    finally:
        try:
            await pubsub.unsubscribe(f"match:{match_id}:events")
            await pubsub.close()
        except Exception as e:
            print(f"[WS][WARN] PubSub cleanup failed: {e}")

        print(f"[WS] Unsubscribed + closed Redis pubsub for match {match_id}")
