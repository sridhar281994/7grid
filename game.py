from fastapi import APIRouter, Depends, HTTPException
from jose import jwt
from sqlalchemy.orm import Session
from typing import Optional
from random import randint
import os

from database import get_db
from models import User, GameMatch, Transaction
from schemas import StartGameIn, GameResultOut

router = APIRouter(prefix="/game", tags=["game"])
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
ALGO = "HS256"

def get_current_user(db: Session, authorization: Optional[str]) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGO])
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

@router.post("/start", response_model=GameResultOut)
def start_game(
    body: StartGameIn,
    db: Session = Depends(get_db),
    authorization: Optional[str] = None
):
    user = get_current_user(db, authorization)

    stake = int(body.stake_rs)
    if stake not in {4, 8, 12}:
        raise HTTPException(status_code=400, detail="Invalid stake")

    # Stake the amount first
    if float(user.wallet_balance) < stake:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    user.wallet_balance = float(user.wallet_balance) - stake
    db.add(Transaction(user_id=user.id, type="stake", amount=stake, meta=f"Stake {stake}"))
    db.commit()
    db.refresh(user)

    # Very simple result simulation:
    # 1/6 chance danger, otherwise 50/50 win/lose
    roll = randint(1, 6)
    if roll == 3:
        result = "danger"
        payout = 0
    else:
        result = "win" if randint(0, 1) == 1 else "lose"
        payout = stake * 2 if result == "win" else 0

    match = GameMatch(user_id=user.id, stake_rs=stake, result=result)
    db.add(match)

    if payout > 0:
        user.wallet_balance = float(user.wallet_balance) + payout
        db.add(Transaction(user_id=user.id, type="payout", amount=payout, meta=f"Payout {payout}"))

    db.commit()
    db.refresh(user)
    db.refresh(match)

    return GameResultOut(
        match_id=match.id,
        stake_rs=stake,
        result=result,  # "win" | "lose" | "danger"
        new_balance=float(user.wallet_balance),
        created_at=match.created_at,
    )
