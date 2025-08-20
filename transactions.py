from fastapi import APIRouter, Depends, HTTPException
from jose import jwt
from sqlalchemy.orm import Session
from typing import Optional, List
import os

from database import get_db
from models import User, Transaction
from schemas import WalletActionIn, UserOut

router = APIRouter(prefix="/wallet", tags=["wallet"])
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

@router.get("/me", response_model=UserOut)
def me(db: Session = Depends(get_db), authorization: Optional[str] = None):
    user = get_current_user(db, authorization)
    return user

@router.post("/recharge", response_model=UserOut)
def recharge(body: WalletActionIn, db: Session = Depends(get_db), authorization: Optional[str] = None):
    user = get_current_user(db, authorization)
    user.wallet_balance = float(user.wallet_balance) + float(body.amount)
    db.add(Transaction(user_id=user.id, type="recharge", amount=body.amount, meta="Manual recharge"))
    db.commit()
    db.refresh(user)
    return user

@router.post("/withdraw", response_model=UserOut)
def withdraw(body: WalletActionIn, db: Session = Depends(get_db), authorization: Optional[str] = None):
    user = get_current_user(db, authorization)
    if float(user.wallet_balance) < float(body.amount):
        raise HTTPException(status_code=400, detail="Insufficient balance")
    user.wallet_balance = float(user.wallet_balance) - float(body.amount)
    db.add(Transaction(user_id=user.id, type="withdraw", amount=body.amount, meta="Manual withdraw"))
    db.commit()
    db.refresh(user)
    return user
