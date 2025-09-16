import os
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, condecimal
from sqlalchemy.orm import Session
from sqlalchemy import select, func

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


@router.post("/recharge/initiate")
def recharge_initiate(
    payload: AmountIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Create a pending recharge.
    In production: redirect to UPI/PG (Cashfree, Razorpay, etc).
    Dev mode: returns tx_id and hint for mock-success.
    """
    tx = WalletTransaction(
        user_id=user.id,
        amount=Decimal(payload.amount),
        tx_type=TxType.RECHARGE,
        status=TxStatus.PENDING,
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
    return {"ok": True, "balance": float(user.wallet_balance)}


@router.post("/withdraw")
def withdraw(
    payload: WithdrawIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Deduct from wallet and create a pending withdrawal.
    In production: trigger payout API (Cashfree/RazorpayX).
    """
    amount = Decimal(payload.amount)
    if (user.wallet_balance or 0) < amount:
        raise HTTPException(400, "Insufficient balance")

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

    # Dev mode: auto-success
    if os.getenv("MOCK_PAYOUT", "true").lower() == "true":
        tx.status = TxStatus.SUCCESS
        db.commit()

    return {"ok": True, "tx_id": tx.id, "status": tx.status.value}


# --------------------------
# Admin only — System Fees
# --------------------------
@router.get("/system-fees")
def system_fees(import os
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, condecimal
from sqlalchemy.orm import Session

from database import get_db
from models import User, WalletTransaction, TxType, TxStatus
from utils.security import get_current_user
from routers.wallet_utils import deduct_wallet, refund_entry, payout_winner # central helpers

router = APIRouter(prefix="/wallet", tags=["wallet"])


# -----------------------
# Request Schemas
# -----------------------
class AmountIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)


class WithdrawIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)
    upi_id: str


# -----------------------
# Endpoints
# -----------------------
@router.get("/balance")
def balance(user: User = Depends(get_current_user)):
    return {"balance": float(user.wallet_balance or 0)}


@router.post("/recharge/initiate")
def recharge_initiate(payload: AmountIn, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """
    Create a pending recharge.
    In production: integrate with Cashfree/Razorpay.
    Dev mode: mock it with /recharge/mock-success.
    """
    tx = WalletTransaction(
        user_id=user.id,
        amount=Decimal(payload.amount),
        tx_type=TxType.RECHARGE,
        status=TxStatus.PENDING,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return {
        "ok": True,
        "tx_id": tx.id,
        "hint": "Call /wallet/recharge/mock-success?tx_id=... in dev"
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
def withdraw(payload: WithdrawIn, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    amount = Decimal(payload.amount)
    if (user.wallet_balance or 0) < amount:
        raise HTTPException(400, "Insufficient balance")

    # Deduct immediately (hold funds)
    try:
        deduct_wallet(db, user, amount)
    except ValueError:
        raise HTTPException(400, "Insufficient balance")

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

    # In production: call payout API here asynchronously
    if os.getenv("MOCK_PAYOUT", "true").lower() == "true":
        tx.status = TxStatus.SUCCESS
        db.commit()

    return {"ok": True, "tx_id": tx.id, "status": tx.status.value}

    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    ADMIN ONLY: View total system fees collected from matches.
    In production, add an admin guard (e.g., user.is_admin check).
    """
    # TODO: Add admin guard after production (user.is_admin).
    total_fee = db.query(func.coalesce(func.sum(GameMatch.system_fee), 0)).scalar()
    return {"system_fees": float(total_fee or 0)}
