import asyncio
import random
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from database import SessionLocal
from models import GameMatch, MatchStatus, User

# If you added is_agent column, you can use that instead of fixed ids.
AGENT_USER_IDS = [
    10001, 10002, 10003, 10004, 10005,
    10006, 10007, 10008, 10009, 10010,
    10011, 10012, 10013, 10014, 10015,
    10016, 10017, 10018, 10019, 10020,
]

# How long to wait before agents join a waiting match (seconds)
AGENT_JOIN_TIMEOUT = 10

# Minimum balance we keep topping agents up to
AGENT_MIN_BALANCE = Decimal("50")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _calc_entry_fee(match: GameMatch) -> Decimal:
    """
    Simple fallback: entry_fee = stake_amount / num_players
    If you have a stakes table with entry_fee, replace this with that lookup.
    """
    stake = Decimal(str(match.stake_amount or 0))
    players = Decimal(str(match.num_players or 2))
    if stake <= 0 or players <= 0:
        return Decimal("0")
    return (stake / players).quantize(Decimal("1"))  # integer rupees


def _pick_available_agents(db: Session, needed: int, exclude_ids: set[int], min_balance: Decimal) -> list[User]:
    q = (
        db.query(User)
        .filter(
            User.id.in_(AGENT_USER_IDS),
            User.id.notin_(exclude_ids),
        )
    )
    agents = q.all()
    random.shuffle(agents)

    selected: list[User] = []
    for u in agents:
        bal = u.wallet_balance or Decimal("0")
        # auto-topup agents if they are low
        if bal < min_balance:
            u.wallet_balance = min_balance
            bal = min_balance

        selected.append(u)
        if len(selected) >= needed:
            break
    return selected


def _fill_match_with_agents(db: Session, match: GameMatch) -> bool:
    """
    Try to fill a WAITING match with one or more agents.
    Returns True if match became ACTIVE, False otherwise.
    """
    if match.status != MatchStatus.WAITING:
        return False

    num_players = match.num_players or 2
    slots = [match.p1_user_id, match.p2_user_id, match.p3_user_id][:num_players]

    # How many seats are empty?
    empty_positions: list[int] = [
        idx for idx, uid in enumerate(slots) if uid is None
    ]
    if not empty_positions:
        return False

    # Don’t replace existing human players
    existing_ids = {uid for uid in slots if uid is not None}

    # Compute entry fee for this match
    entry_fee = _calc_entry_fee(match)

    # Pick enough agents to fill the empty positions
    agents = _pick_available_agents(
        db,
        needed=len(empty_positions),
        exclude_ids=existing_ids,
        min_balance=AGENT_MIN_BALANCE,
    )
    if not agents:
        return False

    # Attach agents into slots
    for idx, pos in enumerate(empty_positions):
        if idx >= len(agents):
            break
        agent = agents[idx]

        # Deduct entry fee from agent wallet (like a real player)
        if entry_fee > 0:
            agent.wallet_balance = (agent.wallet_balance or Decimal("0")) - entry_fee

        slots[pos] = agent.id

    # Re-apply to match
    # Only set the first 3 fields; match.num_players already set
    match.p1_user_id = slots[0] if len(slots) > 0 else None
    match.p2_user_id = slots[1] if len(slots) > 1 else None
    if num_players == 3:
        match.p3_user_id = slots[2] if len(slots) > 2 else None

    # If all seats filled => match becomes ACTIVE
    if all(uid is not None for uid in slots):
        match.status = MatchStatus.ACTIVE
        # Random starting player among the occupied indices
        active_indices = [i for i, uid in enumerate(slots) if uid is not None]
        match.current_turn = random.choice(active_indices)
        match.started_at = _now_utc()

    return match.status == MatchStatus.ACTIVE


async def agent_match_filler_loop():
    """
    Background loop:
      - finds WAITING matches older than AGENT_JOIN_TIMEOUT
      - fills them with agent users
    """
    while True:
        try:
            db: Session = SessionLocal()
            cutoff = _now_utc() - timedelta(seconds=AGENT_JOIN_TIMEOUT)

            waiting_matches = (
                db.query(GameMatch)
                .filter(
                    GameMatch.status == MatchStatus.WAITING,
                    GameMatch.created_at <= cutoff,
                )
                .order_by(GameMatch.created_at.asc())
                .limit(20)
                .all()
            )

            for m in waiting_matches:
                became_active = _fill_match_with_agents(db, m)
                if became_active:
                    print(f"[AGENT_POOL] Filled match {m.id} with agents → ACTIVE")

            db.commit()
            db.close()

        except Exception as e:
            print(f"[AGENT_POOL][ERR] {e}")
            try:
                db.close()
            except Exception:
                pass

        # Sleep a bit before scanning again
        await asyncio.sleep(3)


def start_agent_pool():
    """
    Call this once on app startup.
    """
    import asyncio

    loop = asyncio.get_event_loop()
    loop.create_task(agent_match_filler_loop())
    print("[AGENT_POOL] Started background agent filler loop")
