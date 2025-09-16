import os
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, condecimal
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import User, WalletTransaction, TxType, TxStatus, GameMatch
from utils.security import get_current_user

router = APIRouter(prefix="/wallet", tags=["wallet"])


# --------------------------
# Schemas
# --------------------------
class AmountIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)


class WithdrawIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)
    upi_id: str


# --------------------------
# Endpoints
# --------------------------
@router.get("/balance")
def balance(user: User = Depends(get_current_user)):
    """Return current wallet balance for logged-in user."""
    return {"balance": float(user.wallet_balance or 0)}


@router.get("/system-fees")
def system_fees(db: Session = Depends(get_db)):
    """
    Return total revenue (system fees) earned by the platform.
    Admin-only in production (here exposed for testing).
    """
    total_fee = db.query(func.coalesce(func.sum(GameMatch.system_fee), 0)).scalar()
    return {"total_fees": float(total_fee or 0)}


@router.post("/recharge/initiate")
def recharge_initiate(
    payload: AmountIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Create a pending recharge. In production, redirect to UPI/PG.
    For now, returns a fake reference to confirm via /recharge/mock-success.
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
    return {"ok": True, "tx_id": tx.id, "hint": "Use /wallet/recharge/mock-success?tx_id=..."}


@router.post("/recharge/mock-success")
def recharge_mock_success(tx_id: int, db: Session = Depends(get_db)):
    """DEV ONLY: instantly mark a recharge success and credit wallet."""
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
    return {"ok": True}


@router.post("/withdraw")
def withdraw(
    payload: WithdrawIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Request withdrawal from wallet."""
    amount = Decimal(payload.amount)
    if (user.wallet_balance or 0) < amount:
        raise HTTPException(400, "Insufficient balance")

    # Deduct immediately
    user.wallet_balance = (user.wallet_balance or 0) - amount
    tx = WalletTransaction(
        user_id=user.id,
        amount=amount,
        tx_type=TxType.WITHDRAW,
        status=TxStatus.PENDING,
        provider_ref=None,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    # Mock payout flow: instantly mark success if MOCK_PAYOUT is true
    if os.getenv("MOCK_PAYOUT", "true").lower() == "true":
        tx.status = TxStatus.SUCCESS
        db.commit()

    return {"ok": True, "tx_id": tx.id, "status": tx.status.value}
