from datetime import datetime, timezone
from typing import Dict, Optional
import random, json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, conint
from sqlalchemy.orm import Session

from database import get_db
from models import GameMatch, User, MatchStatus
from utils.security import get_current_user
from utils.redis_client import redis_client

router = APIRouter(prefix="/matches", tags=["matches"])

# -------- Request models --------
class CreateIn(BaseModel):
    stake_amount: conint(gt=0)

class RollIn(BaseModel):
    match_id: int

def _now():
    return datetime.now(timezone.utc)

def _name_for(u: Optional[User]) -> str:
    if not u:
        return "Player"
    return (u.name or (u.email or "").split("@")[0] or u.phone or f"User#{u.id}")

# -------- Match create/join --------
@router.post("/create")
async def create_or_wait_match(
    payload: CreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    stake_amount = int(payload.stake_amount)

    # Try to find waiting opponent
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

        p1 = db.get(User, waiting.p1_user_id)
        p2 = db.get(User, waiting.p2_user_id)

        # Notify both players
        event = {
            "type": "match_ready",
            "match_id": waiting.id,
            "stake": waiting.stake_amount,
            "p1": _name_for(p1),
            "p2": _name_for(p2),
        }
        try:
            await redis_client.publish(f"match:{waiting.id}:updates", json.dumps(event))
        except Exception as e:
            print(f"[WARN] Redis publish failed: {e}")

        return {"ok": True, **event}

    # Else create new match
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

    p1 = db.get(User, new_match.p1_user_id)
    return {
        "ok": True,
        "match_id": new_match.id,
        "status": new_match.status.value,
        "stake": new_match.stake_amount,
        "p1": _name_for(p1),
        "p2": None,
    }

# -------- Poll match --------
@router.get("/check")
def check_match_ready(
    match_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    if m.status == MatchStatus.ACTIVE and m.p1_user_id and m.p2_user_id:
        return {
            "ready": True,
            "match_id": m.id,
            "stake": m.stake_amount,
            "p1": _name_for(db.get(User, m.p1_user_id)),
            "p2": _name_for(db.get(User, m.p2_user_id)),
            "last_roll": m.last_roll,
            "turn": m.current_turn,
        }
    return {"ready": False, "status": m.status.value}

# -------- Dice roll --------
@router.post("/roll")
async def roll_dice(
    payload: RollIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m or m.status != MatchStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="Match not active")

    if current_user.id not in [m.p1_user_id, m.p2_user_id]:
        raise HTTPException(status_code=403, detail="Not your match")

    roll = random.randint(1, 6)
    m.last_roll = roll
    m.current_turn = 1 - (m.current_turn or 0)
    db.commit()
    db.refresh(m)

    event = {
        "type": "dice_roll",
        "match_id": m.id,
        "roller_id": current_user.id,
        "roll": roll,
        "turn": m.current_turn,
    }
    try:
        await redis_client.publish(f"match:{m.id}:updates", json.dumps(event))
    except Exception as e:
        print(f"[WARN] Redis publish failed: {e}")

    return {"ok": True, **event}


---

3. screens/user_match_screen.py

import threading, time, requests
from kivy.uix.screenmanager import Screen
from kivy.clock import Clock

try:
    from utils import storage
except Exception:
    storage = None

try:
    from utils.otp_utils import BACKEND_BASE
except Exception:
    BACKEND_BASE = "https://your-backend.onrender.com"


class UserMatchScreen(Screen):
    selected_amount: int = 0
    _stop_polling = False

    def go_back(self):
        self._stop_polling = True
        self.manager.current = "stage"

    def start_matchmaking(self, local_player_name: str, amount: int):
        self.ids.status.text = "Matching…"
        self.selected_amount = int(amount)
        self._stop_polling = False
        token = storage.get_token() if storage else None
        if not token:
            self.ids.status.text = "⚠️ Not logged in"
            return

        def worker():
            try:
                resp = requests.post(
                    f"{BACKEND_BASE}/matches/create",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"stake_amount": self.selected_amount},
                    timeout=10,
                )
                data = resp.json()
                if not data.get("ok"):
                    Clock.schedule_once(lambda *_: self._set_status("Failed to create match"))
                    return
                match_id = data["match_id"]
                if storage:
                    storage.set_current_match(match_id)
                self._poll_match_ready(match_id)
            except Exception as e:
                Clock.schedule_once(lambda *_: self._set_status(f"Error: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_match_ready(self, match_id: int):
        token = storage.get_token() if storage else None
        if not token:
            return

        def loop():
            while not self._stop_polling:
                try:
                    resp = requests.get(
                        f"{BACKEND_BASE}/matches/check",
                        headers={"Authorization": f"Bearer {token}"},
                        params={"match_id": match_id},
                        timeout=10,
                    )
                    data = resp.json()
                    if data.get("ready"):
                        if storage:
                            storage.set_player_names(data.get("p1"), data.get("p2"))
                        Clock.schedule_once(lambda *_: self._enter_game(data))
                        return
                except Exception:
                    pass
                time.sleep(2)

        threading.Thread(target=loop, daemon=True).start()

    def _set_status(self, text: str):
        self.ids.status.text = text

    def _enter_game(self, match_data: dict):
        self._stop_polling = True
        dice_screen = self.manager.get_screen("dicegame")
        if hasattr(dice_screen, "set_stage_and_players"):
            dice_screen.set_stage_and_players(
                match_data.get("stake", 0),
                match_data.get("p1"),
                match_data.get("p2"),
            )
        self.manager.current = "dicegame"


---

4. utils/storage.py

_state = {"token": None, "match_id": None, "p1": None, "p2": None, "user": None}

def set_token(t: str): _state["token"] = t
def get_token(): return _state.get("token")

def set_user(user: dict): _state["user"] = user
def get_user(): return _state.get("user")

def set_current_match(mid: int): _state["match_id"] = mid
def get_current_match(): return _state.get("match_id")

def set_player_names(p1: str, p2: str):
    _state["p1"], _state["p2"] = p1, p2

def get_player_names():
    return _state.get("p1"), _state.get("p2")
