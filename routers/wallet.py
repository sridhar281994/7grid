from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from schemas import WalletTxIn, WalletTxOut
from crud import create_wallet_tx
from models import TxType
from typing import List
from sqlalchemy import select
from models import WalletTransaction

router = APIRouter(prefix="/wallet", tags=["wallet"])

@router.post("/{user_id}/tx", response_model=WalletTxOut)
def create_tx(user_id: int, payload: WalletTxIn, db: Session = Depends(get_db)):
    type_ = TxType(payload.type)
    tx = create_wallet_tx(db, user_id=user_id, amount=payload.amount, type_=type_)
    return tx

@router.get("/{user_id}/history", response_model=List[WalletTxOut])
def history(user_id: int, db: Session = Depends(get_db)):
    q = select(WalletTransaction).where(WalletTransaction.user_id == user_id).order_by(WalletTransaction.id.desc())
    rows = db.execute(q).scalars().all()
    return rows
