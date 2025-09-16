import os
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, condecimal
from sqlalchemy.orm import Session
from database import get_db
from models import User, WalletTransaction, TxType, TxStatus
from utils.security import get_current_user

router = APIRouter(prefix="/wallet", tags=["wallet"])


class AmountIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)


class WithdrawIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)
    upi_id: str


@router.get("/balance")
def balance(user: User = Depends(get_current_user)):
    return {"balance": float(user.wallet_balance or 0)}


@router.post("/recharge/initiate")
def recharge_initiate(
    payload: AmountIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tx = WalletTransaction(
        user_id=user.id,
        amount=Decimal(payload.amount),
        tx_type=TxType.RECHARGE, # ✅ fixed field name
        status=TxStatus.PENDING,
        provider_ref=None,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return {
        "ok": True,
        "tx_id": tx.id,
        "hint": "Call /wallet/recharge/mock-success?tx_id=... in dev",
    }


@router.post("/recharge/mock-success")
def recharge_mock_success(tx_id: int, db: Session = Depends(get_db)):
    tx = db.get(WalletTransaction, tx_id)
    if not tx or tx.tx_type != TxType.RECHARGE: # ✅ fixed
        raise HTTPException(404, "Recharge tx not found")
    if tx.status == TxStatus.SUCCESS:
        return {"ok": True, "already": True}

    user = db.get(User, tx.user_id)
    if not user:
        raise HTTPException(404, "User not found")

    user.wallet_balance = (user.wallet_balance or 0) + tx.amount
    tx.status = TxStatus.SUCCESS
    db.commit()
    return {"ok": True}


@router.post("/withdraw")
def withdraw(
    payload: WithdrawIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    amount = Decimal(payload.amount)
    if (user.wallet_balance or 0) < amount:
        raise HTTPException(400, "Insufficient balance")

    user.wallet_balance = (user.wallet_balance or 0) - amount
    tx = WalletTransaction(
        user_id=user.id,
        amount=amount,
        tx_type=TxType.WITHDRAW, # ✅ fixed
        status=TxStatus.PENDING,
        provider_ref=payload.upi_id, # ✅ save UPI ID
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    if os.getenv("MOCK_PAYOUT", "true").lower() == "true":
        tx.status = TxStatus.SUCCESS
        db.commit()

    return {"ok": True, "tx_id": tx.id, "status": tx.status.value}


@router.get("/transactions")
def transactions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    txs = (
        db.query(WalletTransaction)
        .filter(WalletTransaction.user_id == user.id)
        .order_by(WalletTransaction.timestamp.desc())
        .all()
    )
    return [
        {
            "id": tx.id,
            "amount": float(tx.amount),
            "type": tx.tx_type.value,
            "status": tx.status.value,
            "timestamp": tx.timestamp.isoformat(),
        }
        for tx in txs
    ]
