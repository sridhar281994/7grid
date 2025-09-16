import os
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, condecimal
from sqlalchemy.orm import Session

from database import get_db
from models import User, WalletTransaction, TxType, TxStatus
from utils.security import get_current_user
from routers.wallet_utils import deduct_wallet, refund_entry, payout_winner

router = APIRouter(prefix="/wallet", tags=["wallet"])


# -----------------------
# Request Schemas
# -----------------------
class AmountIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)


class WithdrawIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)
    upi_id: str


class RefundIn(BaseModel):
    user_id: int
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)


# -----------------------
# User Endpoints
# -----------------------
@router.get("/balance")
def balance(user: User = Depends(get_current_user)):
    return {"balance": float(user.wallet_balance or 0)}


@router.get("/history")
def history(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Paginated wallet transactions for the current user.
    Example: /wallet/history?limit=20&offset=20
    """
    q = (
        db.query(WalletTransaction)
        .filter(WalletTransaction.user_id == user.id)
        .order_by(WalletTransaction.timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    total = (
        db.query(WalletTransaction)
        .filter(WalletTransaction.user_id == user.id)
        .count()
    )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "transactions": [
            {
                "id": tx.id,
                "amount": float(tx.amount),
                "type": tx.tx_type.value,
                "status": tx.status.value,
                "timestamp": tx.timestamp.isoformat(),
                "provider_ref": tx.provider_ref,
            }
            for tx in q
        ],
    }


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
    return {"ok": True, "tx": {
        "id": tx.id,
        "user_id": tx.user_id,
        "amount": float(tx.amount),
        "type": tx.tx_type.value,
        "status": tx.status.value,
        "timestamp": tx.timestamp.isoformat(),
    }}


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

    return {"ok": True, "tx": {
        "id": tx.id,
        "user_id": tx.user_id,
        "amount": float(tx.amount),
        "type": tx.tx_type.value,
        "status": tx.status.value,
        "timestamp": tx.timestamp.isoformat(),
    }}


# -----------------------
# Admin Endpoints
# -----------------------
@router.post("/admin/refund")
def admin_refund(payload: RefundIn,
                 db: Session = Depends(get_db),
                 x_admin_secret: str = Header(None)):
    """
    Admin-only refund.
    Requires X-Admin-Secret header = ADMIN_SECRET from env.
    """
    admin_secret = os.getenv("ADMIN_SECRET", "changeme")
    if x_admin_secret != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    user = db.get(User, payload.user_id)
    if not user:
        raise HTTPException(404, "User not found")

    amount = Decimal(payload.amount)

    # Credit wallet
    refund_entry(db, user, amount)

    # Log transaction
    tx = WalletTransaction(
        user_id=user.id,
        amount=amount,
        tx_type=TxType.RECHARGE, # Treat refund as recharge
        status=TxStatus.SUCCESS,
        provider_ref="admin_refund"
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    return {
        "ok": True,
        "refunded": float(amount),
        "user_id": user.id,
        "tx": {
            "id": tx.id,
            "amount": float(tx.amount),
            "type": tx.tx_type.value,
            "status": tx.status.value,
            "timestamp": tx.timestamp.isoformat(),
        }
    }


@router.get("/admin/transactions")
def admin_transactions(db: Session = Depends(get_db),
                       x_admin_secret: str = Header(None),
                       limit: int = Query(50, ge=1, le=200),
                       offset: int = Query(0, ge=0)):
    """
    Admin-only: paginated recent wallet transactions.
    """
    admin_secret = os.getenv("ADMIN_SECRET", "changeme")
    if x_admin_secret != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    q = (
        db.query(WalletTransaction)
        .order_by(WalletTransaction.timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    total = db.query(WalletTransaction).count()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "transactions": [
            {
                "id": tx.id,
                "user_id": tx.user_id,
                "amount": float(tx.amount),
                "type": tx.tx_type.value,
                "status": tx.status.value,
                "timestamp": tx.timestamp.isoformat(),
                "provider_ref": tx.provider_ref,
            }
            for tx in q
        ],
    }
