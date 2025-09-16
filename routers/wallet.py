import os
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, condecimal
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from database import get_db
from models import User, WalletTransaction, TxType, TxStatus
from utils.security import get_current_user

router = APIRouter(prefix="/wallet", tags=["wallet"])


# -----------------------------
# Request models
# -----------------------------
class AmountIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)


class WithdrawIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)
    upi_id: str


# -----------------------------
# Endpoints
# -----------------------------
@router.get("/balance")
def balance(user: User = Depends(get_current_user)):
    return {"balance": float(user.wallet_balance or 0)}


@router.post("/recharge/initiate")
def recharge_initiate(
    payload: AmountIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a pending recharge (fake gateway for dev)."""
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
    return {
        "ok": True,
        "tx_id": tx.id,
        "hint": "Call /wallet/recharge/mock-success?tx_id=... in dev",
    }


@router.post("/recharge/mock-success")
def recharge_mock_success(tx_id: int, db: Session = Depends(get_db)):
    """DEV ONLY: instantly mark a recharge as success and credit wallet."""
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
    amount = Decimal(payload.amount)
    if (user.wallet_balance or 0) < amount:
        raise HTTPException(400, "Insufficient balance")

    # hold amount
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

    # Dev: auto success
    if os.getenv("MOCK_PAYOUT", "true").lower() == "true":
        tx.status = TxStatus.SUCCESS
        db.commit()

    return {"ok": True, "tx_id": tx.id, "status": tx.status.value}


@router.get("/history")
def wallet_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Paginated wallet history for infinite scroll in frontend.
    Example: /wallet/history?skip=0&limit=20
    """
    stmt = (
        select(WalletTransaction)
        .where(WalletTransaction.user_id == user.id)
        .order_by(desc(WalletTransaction.timestamp))
        .offset(skip)
        .limit(limit)
    )
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id": tx.id,
            "amount": float(tx.amount),
            "type": tx.tx_type.value,
            "status": tx.status.value,
            "note": tx.provider_ref,
            "timestamp": tx.timestamp.isoformat(),
        }
        for tx in rows
    ]
