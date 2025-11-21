import asyncio
import random
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import and_

from database import SessionLocal
from models import GameMatch, MatchStatus, User

AGENT_USER_IDS = [
    10001, 10002, 10003, 10004, 10005,
    10006, 10007, 10008, 10009, 10010,
    10011, 10012, 10013, 10014, 10015,
    10016, 10017, 10018, 10019, 10020,
]

AGENT_JOIN_TIMEOUT = 10
AGENT_MIN_BALANCE = Decimal("50")


def _now_utc():
    return datetime.now(timezone.utc)


def _calc_entry_fee(match: GameMatch) -> Decimal:
    stake = Decimal(str(match.stake_amount or 0))
    players = Decimal(str(match.num_players or 2))
    if stake <= 0 or players <= 0:
        return Decimal("0")
    return (stake / players).quantize(Decimal("1"))


def _pick_available_agents(db: Session, needed: int, exclude_ids: set[int]):
    agents = (
        db.query(User)
        .filter(User.id.in_(AGENT_USER_IDS), User.id.notin_(exclude_ids))
        .all()
    )

    random.shuffle(agents)
    selected = []

    for agent in agents:
        if (agent.wallet_balance or 0) < AGENT_MIN_BALANCE:
            agent.wallet_balance = AGENT_MIN_BALANCE

        selected.append(agent)
        if len(selected) >= needed:
            break

    return selected


def _fill_match_with_agents(db: Session, match: GameMatch) -> bool:
    if match.status != MatchStatus.WAITING:
        return False

    num_players = match.num_players or 2

    # ✅ SAFE slot generation
    slots = [match.p1_user_id, match.p2_user_id, match.p3_user_id][:num_players]
    while len(slots) < num_players:
        slots.append(None)

    empty_positions = [i for i, uid in enumerate(slots) if uid is None]
    if not empty_positions:
        return False

    existing_ids = {uid for uid in slots if uid and uid not in AGENT_USER_IDS}
    entry_fee = _calc_entry_fee(match)

    agents = _pick_available_agents(
        db,
        needed=len(empty_positions),
        exclude_ids=existing_ids,
    )

    if not agents:
        return False

    for index, pos in enumerate(empty_positions):
        if index >= len(agents):
            break

        agent = agents[index]

        if entry_fee > 0:
            agent.wallet_balance -= entry_fee

        slots[pos] = agent.id

    match.p1_user_id = slots[0] if num_players >= 1 else None
    match.p2_user_id = slots[1] if num_players >= 2 else None
    match.p3_user_id = slots[2] if num_players == 3 else None

    if all(uid is not None for uid in slots):
        match.status = MatchStatus.ACTIVE
        match.current_turn = random.choice(range(num_players))
        match.started_at = _now_utc()

    return match.status == MatchStatus.ACTIVE


async def agent_match_filler_loop():
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

            for match in waiting_matches:
                activated = _fill_match_with_agents(db, match)
                if activated:
                    print(f"[AGENT_POOL] Match {match.id} filled with agents -> ACTIVE")

            db.commit()
            db.close()

        except Exception as e:
            print(f"[AGENT_POOL][ERR] {e}")
            try:
                db.close()
            except Exception:
                pass

        await asyncio.sleep(3)


def start_agent_pool():
    loop = asyncio.get_event_loop()
    loop.create_task(agent_match_filler_loop())
    print("[AGENT_POOL] Background agent filler started")
