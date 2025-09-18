import os
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, condecimal
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from sqlalchemy.exc import SQLAlchemyError

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
# Helpers
# -----------------------------
def _lock_user(db: Session, user_id: int) -> User:
    """🔒 Always lock row before modifying wallet balance."""
    u = db.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")
    return u


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

    try:
        user = _lock_user(db, tx.user_id) # 🔒 prevent race
        user.wallet_balance = (user.wallet_balance or 0) + tx.amount
        tx.status = TxStatus.SUCCESS
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(500, f"DB error: {e}")

    return {"ok": True, "balance": float(user.wallet_balance or 0)}


@router.post("/withdraw")
def withdraw(
    payload: WithdrawIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        u = _lock_user(db, user.id) # 🔒
        amount = Decimal(payload.amount)
        if (u.wallet_balance or 0) < amount:
            raise HTTPException(400, "Insufficient balance")

        u.wallet_balance = (u.wallet_balance or 0) - amount
        tx = WalletTransaction(
            user_id=u.id,
            amount=amount,
            tx_type=TxType.WITHDRAW,
            status=TxStatus.PENDING,
            provider_ref=payload.upi_id,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        # Dev auto success
        if os.getenv("MOCK_PAYOUT", "true").lower() == "true":
            tx.status = TxStatus.SUCCESS
            db.commit()

        return {"ok": True, "tx_id": tx.id, "status": tx.status.value,
                "balance": float(u.wallet_balance or 0)}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(500, f"DB error: {e}")


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
            # Mask UPI ID for privacy (optional)
            "note": (
                tx.provider_ref[:4] + "****" + tx.provider_ref[-4:]
                if tx.tx_type == TxType.WITHDRAW and tx.provider_ref
                else tx.provider_ref
            ),
            "timestamp": tx.timestamp.isoformat(),
        }
        for tx in rows
    ]
