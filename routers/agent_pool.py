import asyncio
import random
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

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


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _calc_entry_fee(match: GameMatch) -> Decimal:
    stake = Decimal(str(match.stake_amount or 0))
    players = Decimal(str(match.num_players or 2))
    if stake <= 0 or players <= 0:
        return Decimal("0")
    return (stake / players).quantize(Decimal("1"))


def _safe_slots(match: GameMatch):
    slots = [match.p1_user_id, match.p2_user_id]
    if (match.num_players or 2) == 3:
        slots.append(match.p3_user_id)
    return slots


def _pick_available_agents(db: Session, needed: int, exclude_ids: set[int], min_balance: Decimal) -> list[User]:
    agents = (
        db.query(User)
        .filter(
            User.id.in_(AGENT_USER_IDS),
            User.id.notin_(exclude_ids),
        )
        .all()
    )

    random.shuffle(agents)

    selected = []
    for u in agents:
        if (u.wallet_balance or Decimal("0")) < min_balance:
            u.wallet_balance = min_balance
        selected.append(u)
        if len(selected) >= needed:
            break

    return selected


def _fill_match_with_agents(db: Session, match: GameMatch) -> bool:
    if match.status != MatchStatus.WAITING:
        return False

    slots = _safe_slots(match)

    empty_positions = [i for i, uid in enumerate(slots) if uid is None]
    if not empty_positions:
        return False

    existing_ids = {uid for uid in slots if uid is not None}
    entry_fee = _calc_entry_fee(match)

    agents = _pick_available_agents(
        db,
        needed=len(empty_positions),
        exclude_ids=existing_ids,
        min_balance=AGENT_MIN_BALANCE,
    )

    if not agents:
        return False

    for idx, pos in enumerate(empty_positions):
        agent = agents[idx]
        if entry_fee > 0:
            agent.wallet_balance -= entry_fee
        slots[pos] = agent.id

    # Reassign players
    match.p1_user_id = slots[0]
    match.p2_user_id = slots[1]
    if len(slots) == 3:
        match.p3_user_id = slots[2]

    if all(uid is not None for uid in slots):
        match.status = MatchStatus.ACTIVE
        valid_indices = list(range(len(slots)))

        if match.current_turn is None or match.current_turn not in valid_indices:
            match.current_turn = random.choice(valid_indices)

        match.started_at = _now_utc()

    return match.status == MatchStatus.ACTIVE


async def agent_match_filler_loop():
    while True:
        try:
            db: Session = SessionLocal()
            cutoff = _now_utc() - timedelta(seconds=AGENT_JOIN_TIMEOUT)

            matches = (
                db.query(GameMatch)
                .filter(
                    GameMatch.status == MatchStatus.WAITING,
                    GameMatch.created_at <= cutoff,
                )
                .order_by(GameMatch.created_at.asc())
                .limit(20)
                .all()
            )

            for m in matches:
                if _fill_match_with_agents(db, m):
                    print(f"[AGENT_POOL] Match {m.id} filled with agents → ACTIVE")

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
    print("[AGENT_POOL] Agent auto-fill loop started")
