import os
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, condecimal
from sqlalchemy.orm import Session

from database import get_db
from models import User, WalletTransaction, TxType, TxStatus
from utils.security import get_current_user

router = APIRouter(prefix="/wallet", tags=["wallet"])

# ---------------------------
# Schemas
# ---------------------------
class AmountIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)

class WithdrawIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)
    upi_id: str


# ---------------------------
# Helper functions
# ---------------------------
def credit_balance(user: User, amount: Decimal, db: Session, note: str = ""):
    """Credit wallet and log transaction."""
    user.wallet_balance = (user.wallet_balance or 0) + amount
    tx = WalletTransaction(
        user_id=user.id,
        amount=amount,
        tx_type=TxType.RECHARGE, # Using RECHARGE for credits; could add WIN type if needed
        status=TxStatus.SUCCESS,
        provider_ref=note,
    )
    db.add(tx)
    return tx


def deduct_balance(user: User, amount: Decimal, db: Session, note: str = ""):
    """Deduct wallet amount and log transaction."""
    if (user.wallet_balance or 0) < amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    user.wallet_balance = (user.wallet_balance or 0) - amount
    tx = WalletTransaction(
        user_id=user.id,
        amount=amount,
        tx_type=TxType.WITHDRAW, # Using WITHDRAW for debits; could add ENTRY type if needed
        status=TxStatus.SUCCESS,
        provider_ref=note,
    )
    db.add(tx)
    return tx


# ---------------------------
# Endpoints
# ---------------------------
@router.get("/balance")
def balance(user: User = Depends(get_current_user)):
    return {"balance": float(user.wallet_balance or 0)}


@router.post("/recharge/initiate")
def recharge_initiate(payload: AmountIn,
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """
    Create a pending recharge (DEV only).
    In production, redirect to Razorpay/UPI and mark success via callback.
    """
    tx = WalletTransaction(
        user_id=user.id,
        amount=Decimal(payload.amount),
        tx_type=TxType.RECHARGE,
        status=TxStatus.PENDING,
        provider_ref=None,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return {"ok": True, "tx_id": tx.id,
            "hint": "Call /wallet/recharge/mock-success?tx_id=... (dev only)"}


@router.post("/recharge/mock-success")
def recharge_mock_success(tx_id: int, db: Session = Depends(get_db)):
    """DEV ONLY: instantly credit wallet for given tx_id."""
    tx = db.get(WalletTransaction, tx_id)
    if not tx or tx.tx_type != TxType.RECHARGE:
        raise HTTPException(404, "Recharge tx not found")
    if tx.status == TxStatus.SUCCESS:
        return {"ok": True, "already": True}

    user = db.get(User, tx.user_id)
    if not user:
        raise HTTPException(404, "User not found")

    user.wallet_balance = (user.wallet_balance or 0) + tx.amount
    tx.status = TxStatus.SUCCESS
    db.commit()
    return {"ok": True, "credited": float(tx.amount)}


@router.post("/withdraw")
def withdraw(payload: WithdrawIn,
             db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    amount = Decimal(payload.amount)
    if (user.wallet_balance or 0) < amount:
        raise HTTPException(400, "Insufficient balance")

    # Deduct instantly
    user.wallet_balance = (user.wallet_balance or 0) - amount
    tx = WalletTransaction(
        user_id=user.id,
        amount=amount,
        tx_type=TxType.WITHDRAW,
        status=TxStatus.PENDING,
        provider_ref=payload.upi_id,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    # DEV MODE: auto mark success
    if os.getenv("MOCK_PAYOUT", "true").lower() == "true":
        tx.status = TxStatus.SUCCESS
        db.commit()

    return {"ok": True, "tx_id": tx.id, "status": tx.status.value}
