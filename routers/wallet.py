import os
import json
import hmac
import hashlib
import requests
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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


def _verify_rzp_signature(secret: str, body_bytes: bytes, signature: str) -> bool:
    digest = hmac.new(
        key=secret.encode("utf-8"),
        msg=body_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(digest, signature)


def _amount_to_paise(amount: Decimal) -> int:
    # Razorpay expects integer paise
    return int(Decimal(amount) * 100)


# -----------------------------
# Endpoints: balance + history
# -----------------------------
@router.get("/balance")
def balance(user: User = Depends(get_current_user)):
    return {"balance": float(user.wallet_balance or 0)}


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
            "timestamp": tx.timestamp.isoformat() if tx.timestamp else None,
        }
        for tx in rows
    ]


# -----------------------------
# Razorpay Config
# -----------------------------
RZP_KEY_ID = os.getenv("RZP_KEY_ID")
RZP_KEY_SECRET = os.getenv("RZP_KEY_SECRET")
RZP_WEBHOOK_SECRET = os.getenv("RZP_WEBHOOK_SECRET")
FRONTEND_SUCCESS_URL = os.getenv("FRONTEND_SUCCESS_URL", "")
FRONTEND_FAILURE_URL = os.getenv("FRONTEND_FAILURE_URL", "")
RAZORPAY_API = "https://api.razorpay.com/v1"


def _rzp_auth():
    if not (RZP_KEY_ID and RZP_KEY_SECRET):
        raise HTTPException(500, "Payment gateway not configured")
    return (RZP_KEY_ID, RZP_KEY_SECRET)


# -------------------------------------------------
# RECHARGE (Add Money) via Razorpay Payment Links
# -------------------------------------------------
@router.post("/recharge/create-link")
def recharge_create_link(
    payload: AmountIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Create a Payment Link and a PENDING wallet transaction.
    Wallet is credited ONLY by the webhook after Razorpay confirms payment.
    """
    amount = Decimal(payload.amount)
    if amount <= 0:
        raise HTTPException(400, "Invalid amount")

    # 1) Create the PENDING tx
    tx = WalletTransaction(
        user_id=user.id,
        amount=amount,
        tx_type=TxType.RECHARGE,
        status=TxStatus.PENDING,
        provider_ref=None, # will store Razorpay payment_link_id after creation
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    # 2) Create Razorpay Payment Link
    payload_rzp = {
        "amount": _amount_to_paise(amount),
        "currency": "INR",
        "description": f"Wallet recharge (TX#{tx.id})",
        "reference_id": f"wallet_tx_{tx.id}",
        "callback_url": FRONTEND_SUCCESS_URL or "https://razorpay.com",
        "callback_method": "get",
        "notify": {"sms": False, "email": False},
        "customer": {
            "name": user.name or f"User {user.id}",
            "contact": user.phone or "",
            "email": user.email or "",
        },
    }

    try:
        r = requests.post(
            f"{RAZORPAY_API}/payment_links",
            auth=_rzp_auth(),
            json=payload_rzp,
            timeout=15,
        )
        if r.status_code >= 300:
            raise Exception(r.text)
        data = r.json()
    except Exception as e:
        # rollback tx if payment link creation fails
        db.delete(tx)
        db.commit()
        raise HTTPException(502, f"Failed to create payment link: {e}")

    # Save Razorpay payment_link_id
    tx.provider_ref = data.get("id")
    db.commit()

    return {
        "ok": True,
        "tx_id": tx.id,
        "payment_link_id": data.get("id"),
        "short_url": data.get("short_url"),
        "status": tx.status.value,
    }


@router.post("/recharge/webhook")
async def recharge_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Razorpay webhook receiver — verifies signature and credits wallet for successful payments.
    Configure this URL in Razorpay Dashboard with secret RZP_WEBHOOK_SECRET.
    We process:
      - payment_link.paid → credit wallet
      - payment_link.expired → mark tx as FAILED (no credit)
    """
    body = await request.body()
    sig = request.headers.get("X-Razorpay-Signature") or ""

    if not (RZP_WEBHOOK_SECRET and _verify_rzp_signature(RZP_WEBHOOK_SECRET, body, sig)):
        raise HTTPException(400, "Invalid signature")

    payload = json.loads(body.decode("utf-8"))
    event = payload.get("event", "")
    payload_data = payload.get("payload", {})

    # --- Payment Link paid ---
    if event == "payment_link.paid":
        pl = payload_data.get("payment_link", {}).get("entity", {})
        reference_id = pl.get("reference_id")
        if not reference_id or not reference_id.startswith("wallet_tx_"):
            return {"ok": True, "ignored": "no wallet reference"}

        tx_id = int(reference_id.split("_")[-1])
        tx = db.get(WalletTransaction, tx_id)
        if not tx or tx.tx_type != TxType.RECHARGE:
            return {"ok": True, "ignored": "tx missing or wrong type"}

        # Idempotent: only process if still pending
        if tx.status == TxStatus.PENDING:
            user = _lock_user(db, tx.user_id) # 🔒 lock before credit
            user.wallet_balance = (user.wallet_balance or 0) + tx.amount
            tx.status = TxStatus.SUCCESS
            # Keep provider_ref as the payment_link_id already saved on creation
            db.commit()

        return {"ok": True, "updated": True}

    # --- Payment Link expired ---
    if event == "payment_link.expired":
        pl = payload_data.get("payment_link", {}).get("entity", {})
        reference_id = pl.get("reference_id") or ""
        if reference_id.startswith("wallet_tx_"):
            tx_id = int(reference_id.split("_")[-1])
            tx = db.get(WalletTransaction, tx_id)
            if tx and tx.status == TxStatus.PENDING:
                tx.status = TxStatus.FAILED
                db.commit()
        return {"ok": True, "expired": True}

    # Ignore other events safely
    return {"ok": True, "ignored_event": event}


@router.get("/tx/{tx_id}")
def recharge_tx_status(
    tx_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tx = db.get(WalletTransaction, tx_id)
    if not tx or tx.user_id != user.id:
        raise HTTPException(404, "Transaction not found")
    return {
        "id": tx.id,
        "amount": float(tx.amount),
        "type": tx.tx_type.value,
        "status": tx.status.value,
        "provider_ref": tx.provider_ref,
    }


# -------------------------------------------------
# WITHDRAWAL (Payout)
# -------------------------------------------------
class WithdrawRequestIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)
    upi_id: str


@router.post("/withdraw/request")
def withdraw_request(
    payload: WithdrawRequestIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Production flow:
      1) Put amount on hold (deduct immediately).
      2) Create a PENDING withdrawal tx.
      3) A background worker / admin approves and sends payout via provider.
      4) On payout-success webhook, mark tx SUCCESS. On failure, refund user.
    """
    amount = Decimal(payload.amount)

    u = _lock_user(db, user.id) # 🔒
    if (u.wallet_balance or 0) < amount:
        raise HTTPException(400, "Insufficient balance")

    # hold amount
    u.wallet_balance = (u.wallet_balance or 0) - amount
    tx = WalletTransaction(
        user_id=u.id,
        amount=amount,
        tx_type=TxType.WITHDRAW,
        status=TxStatus.PENDING,
        provider_ref=payload.upi_id, # store UPI ID here (or elsewhere)
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    # TODO: enqueue job to your payout worker (or call RazorpayX/Cashfree payouts)

    return {"ok": True, "tx_id": tx.id, "status": tx.status.value, "balance": float(u.wallet_balance or 0)}


# Admin tool (protect in prod!)
@router.post("/withdraw/mark-success")
def withdraw_mark_success(
    tx_id: int,
    db: Session = Depends(get_db),
):
    if os.getenv("ALLOW_ADMIN", "false").lower() != "true":
        raise HTTPException(403, "Forbidden")

    tx = db.get(WalletTransaction, tx_id)
    if not tx or tx.tx_type != TxType.WITHDRAW:
        raise HTTPException(404, "Withdraw tx not found")
    if tx.status != TxStatus.PENDING:
        return {"ok": True, "already": tx.status.value}

    tx.status = TxStatus.SUCCESS
    db.commit()
    return {"ok": True, "status": "SUCCESS"}


@router.post("/withdraw/mark-failed")
def withdraw_mark_failed(
    tx_id: int,
    db: Session = Depends(get_db),
):
    if os.getenv("ALLOW_ADMIN", "false").lower() != "true":
        raise HTTPException(403, "Forbidden")

    tx = db.get(WalletTransaction, tx_id)
    if not tx or tx.tx_type != TxType.WITHDRAW:
        raise HTTPException(404, "Withdraw tx not found")
    if tx.status != TxStatus.PENDING:
        return {"ok": True, "already": tx.status.value}

    # Refund the hold
    user = db.get(User, tx.user_id)
    if user:
        user.wallet_balance = (user.wallet_balance or 0) + tx.amount

    tx.status = TxStatus.FAILED
    db.commit()
    return {"ok": True, "status": "FAILED_REFUNDED"}
