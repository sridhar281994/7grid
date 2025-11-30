import asyncio
import random
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session
from database import SessionLocal
from models import GameMatch, MatchStatus
from routers.match_routes import roll_dice, RollIn  # reuse your existing /roll logic
from utils.security import FakeUser

# Same list used in agent_pool.py
AGENT_USER_IDS = [
    10001, 10002, 10003, 10004, 10005,
    10006, 10007, 10008, 10009, 10010,
    10011, 10012, 10013, 10014, 10015,
    10016, 10017, 10018, 10019, 10020,
]

# Agents roll every 5–7 seconds
AGENT_ROLL_INTERVAL = (5, 7)

def _now():
    return datetime.now(timezone.utc)

async def _auto_roll_worker():
    while True:
        try:
            db: Session = SessionLocal()

            # Find ACTIVE matches with agents
            matches = (
                db.query(GameMatch)
                .filter(GameMatch.status == MatchStatus.ACTIVE)
                .all()
            )

            for m in matches:
                slots = [m.p1_user_id, m.p2_user_id, m.p3_user_id][:m.num_players]

                # Only engage bots if at least one real human (id>0 and not an agent) joined
                has_human_player = any(
                    uid and uid > 0 and uid not in AGENT_USER_IDS
                    for uid in slots
                )
                if not has_human_player:
                    continue

                if not slots:
                    continue

                turn = m.current_turn or 0
                if turn < 0 or turn >= len(slots):
                    # slot data out of sync (match partially filled) – skip until backend fixes turn
                    continue

                uid_turn = slots[turn]

                if uid_turn in AGENT_USER_IDS:
                    fake_user = FakeUser(uid_turn)

                    try:
                        await roll_dice(
                            payload=RollIn(match_id=m.id),
                            db=db,
                            current_user=fake_user
                        )
                        print(f"[AGENT_AI] Auto-rolled match {m.id} by agent {uid_turn}")
                    except Exception as e:
                        print(f"[AGENT_AI][ERR] match {m.id} agent {uid_turn}: {e}")

            db.close()

        except Exception as e:
            print(f"[AGENT_AI][LOOP_ERR] {e}")

        # wait random 5–7 seconds before next scan
        await asyncio.sleep(random.randint(*AGENT_ROLL_INTERVAL))


def start_agent_ai():
    """Call once at startup (main.py)"""
    loop = asyncio.get_event_loop()
    loop.create_task(_auto_roll_worker())
    print("[AGENT_AI] Smart auto-roll started")
