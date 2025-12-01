import os
import json
import hmac
import hashlib
import requests
from decimal import Decimal, ROUND_DOWN
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, condecimal, validator
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from database import get_db
from models import (
    User,
    WalletTransaction,
    TxType,
    TxStatus,
    WithdrawalMethod,
    WithdrawalStatus,
    WithdrawalRequest,
)
from utils.security import get_current_user

router = APIRouter(prefix="/wallet", tags=["wallet"])

# -----------------------------
# Request models
# -----------------------------
class AmountIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)


# -----------------------------
# Helpers
# -----------------------------
class WithdrawRequestIn(BaseModel):
    amount: condecimal(gt=0, max_digits=12, decimal_places=2)
    method: WithdrawalMethod
    account: str

    @validator("account")
    def validate_account(cls, value: str, values):
        method = values.get("method")
        value = (value or "").strip()
        if not value:
            raise ValueError("Account identifier is required")

        if method == WithdrawalMethod.UPI:
            if "@" not in value or len(value) > 80:
                raise ValueError("Enter a valid UPI ID")
        elif method == WithdrawalMethod.PAYPAL:
            if "@" not in value or "." not in value.split("@")[-1]:
                raise ValueError("Enter a valid PayPal email")
            if len(value) > 120:
                raise ValueError("PayPal email is too long")
        return value


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


def _get_withdrawal_by_tx(db: Session, tx_id: int) -> Optional[WithdrawalRequest]:
    stmt = select(WithdrawalRequest).where(WithdrawalRequest.wallet_tx_id == tx_id)
    return db.execute(stmt).scalar_one_or_none()


def _mask_payout_account(account: str) -> str:
    account = (account or "").strip()
    if len(account) <= 6:
        return account[:1] + "***" + account[-1:] if account else ""
    return f"{account[:3]}****{account[-3:]}"


def _format_withdraw_note(provider_ref: Optional[str]) -> Optional[str]:
    if not provider_ref:
        return None
    method = None
    account = provider_ref
    if ":" in provider_ref:
        method, account = provider_ref.split(":", 1)
    masked = _mask_payout_account(account)
    if method:
        return f"{method.upper()} → {masked}"
    return masked


# -----------------------------
# PayPal Config
# -----------------------------
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET")
PAYPAL_ENV = os.getenv("PAYPAL_ENV", "live").lower()
PAYPAL_API_BASE = os.getenv(
    "PAYPAL_API_BASE",
    "https://api-m.sandbox.paypal.com" if PAYPAL_ENV == "sandbox" else "https://api-m.paypal.com",
)
PAYPAL_PAYOUT_CURRENCY = os.getenv("PAYPAL_PAYOUT_CURRENCY", "USD")
PAYPAL_PAYOUT_BATCH_PREFIX = os.getenv("PAYPAL_PAYOUT_BATCH_PREFIX", "DICEPAY")


def _paypal_is_configured() -> bool:
    return bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET)


def _ensure_method_supported(method: WithdrawalMethod):
    if method == WithdrawalMethod.PAYPAL and not _paypal_is_configured():
        raise HTTPException(503, "PayPal payouts not configured")


def _paypal_get_access_token() -> str:
    if not _paypal_is_configured():
        raise HTTPException(503, "PayPal payouts not configured")

    try:
        resp = requests.post(
            f"{PAYPAL_API_BASE}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
            timeout=15,
        )
    except requests.RequestException as exc:
        raise HTTPException(502, f"PayPal auth network error: {exc}") from exc

    if resp.status_code >= 300:
        raise HTTPException(502, f"PayPal auth failed: {resp.text}")

    token = resp.json().get("access_token")
    if not token:
        raise HTTPException(502, "PayPal auth failed: missing token")
    return token


def _paypal_send_payout(
    withdrawal: WithdrawalRequest, token: Optional[str] = None
) -> Tuple[str, dict]:
    access_token = token or _paypal_get_access_token()
    value = Decimal(withdrawal.amount or 0).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    payload = {
        "sender_batch_header": {
            "sender_batch_id": f"{PAYPAL_PAYOUT_BATCH_PREFIX}-{withdrawal.id}",
            "email_subject": "You have a payout",
            "email_message": "You received a payout from Dice Game.",
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "amount": {"value": f"{value:.2f}", "currency": PAYPAL_PAYOUT_CURRENCY},
                "receiver": withdrawal.account,
                "note": f"Wallet withdrawal #{withdrawal.id}",
                "sender_item_id": f"withdrawal-{withdrawal.id}",
            }
        ],
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            f"{PAYPAL_API_BASE}/v1/payments/payouts",
            headers=headers,
            json=payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"PayPal payout network error: {exc}") from exc

    if resp.status_code >= 300:
        raise RuntimeError(f"PayPal payout failed: {resp.text}")

    data = resp.json()
    payout_id = data.get("batch_header", {}).get("payout_batch_id")
    item = (data.get("items") or [{}])[0]
    txn_id = item.get("transaction_id") or payout_id
    if not txn_id:
        raise RuntimeError("PayPal payout missing transaction id")
    return txn_id, data


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
    """Paginated wallet history for infinite scroll in frontend."""
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
            "note": _format_withdraw_note(tx.provider_ref)
            if tx.tx_type == TxType.WITHDRAW
            else tx.provider_ref,
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
        raise HTTPException(500, "Payment gateway not configured (missing RZP_KEY_ID/SECRET)")
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
    """Create a Payment Link and a PENDING wallet transaction."""
    amount = Decimal(payload.amount)
    if amount <= 0:
        raise HTTPException(400, "Invalid amount")

    # 1) Create the PENDING tx
    tx = WalletTransaction(
        user_id=user.id,
        amount=amount,
        tx_type=TxType.RECHARGE,
        status=TxStatus.PENDING,
        provider_ref=None,
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
        db.delete(tx)
        db.commit()
        raise HTTPException(502, f"Failed to create payment link: {e}")

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
    """Webhook to confirm payments from Razorpay."""
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

        if tx.status == TxStatus.PENDING:
            user = _lock_user(db, tx.user_id)
            user.wallet_balance = (user.wallet_balance or 0) + tx.amount
            tx.status = TxStatus.SUCCESS
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


@router.post("/withdraw/request")
def withdraw_request(
    payload: WithdrawRequestIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Deduct immediately, capture payout preference, queue for processor."""
    amount = Decimal(payload.amount)
    method = payload.method
    account = payload.account.strip()
    _ensure_method_supported(method)

    u = _lock_user(db, user.id)
    if (u.wallet_balance or 0) < amount:
        raise HTTPException(400, "Insufficient balance")

    u.wallet_balance = (u.wallet_balance or 0) - amount
    tx = WalletTransaction(
        user_id=u.id,
        amount=amount,
        tx_type=TxType.WITHDRAW,
        status=TxStatus.PENDING,
        provider_ref=f"{method.value}:{account}",
    )
    db.add(tx)
    db.flush()

    withdrawal = WithdrawalRequest(
        user_id=u.id,
        wallet_tx_id=tx.id,
        amount=amount,
        method=method,
        account=account,
        status=WithdrawalStatus.PENDING,
    )
    db.add(withdrawal)
    db.commit()
    db.refresh(tx)
    db.refresh(withdrawal)

    return {
        "ok": True,
        "tx_id": tx.id,
        "withdrawal_id": withdrawal.id,
        "method": method.value,
        "status": tx.status.value,
        "balance": float(u.wallet_balance or 0),
    }


# Admin-only finalize
@router.post("/withdraw/mark-success")
def withdraw_mark_success(
    tx_id: int,
    payout_txn_id: Optional[str] = None,
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
    withdrawal = _get_withdrawal_by_tx(db, tx.id)
    if withdrawal:
        withdrawal.status = WithdrawalStatus.PAID
        if payout_txn_id:
            withdrawal.payout_txn_id = payout_txn_id

    db.commit()
    return {"ok": True, "status": "SUCCESS"}


@router.post("/withdraw/mark-failed")
def withdraw_mark_failed(
    tx_id: int,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if os.getenv("ALLOW_ADMIN", "false").lower() != "true":
        raise HTTPException(403, "Forbidden")

    tx = db.get(WalletTransaction, tx_id)
    if not tx or tx.tx_type != TxType.WITHDRAW:
        raise HTTPException(404, "Withdraw tx not found")
    if tx.status != TxStatus.PENDING:
        return {"ok": True, "already": tx.status.value}

    user = _lock_user(db, tx.user_id)
    user.wallet_balance = (user.wallet_balance or 0) + tx.amount

    tx.status = TxStatus.FAILED
    withdrawal = _get_withdrawal_by_tx(db, tx.id)
    if withdrawal:
        withdrawal.status = WithdrawalStatus.REJECTED
        if reason:
            withdrawal.details = reason[:255]

    db.commit()
    return {"ok": True, "status": "FAILED_REFUNDED"}


@router.post("/withdraw/process/paypal")
def process_paypal_withdrawals(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    if os.getenv("ALLOW_ADMIN", "false").lower() != "true":
        raise HTTPException(403, "Forbidden")
    if not _paypal_is_configured():
        raise HTTPException(503, "PayPal payouts not configured")

    stmt = (
        select(WithdrawalRequest)
        .where(
            WithdrawalRequest.status == WithdrawalStatus.PENDING,
            WithdrawalRequest.method == WithdrawalMethod.PAYPAL,
        )
        .order_by(WithdrawalRequest.id.asc())
        .limit(limit)
    )
    pending = db.execute(stmt).scalars().all()
    if not pending:
        return {"ok": True, "processed": []}

    token = _paypal_get_access_token()
    processed = []

    for req in pending:
        tx = db.get(WalletTransaction, req.wallet_tx_id)
        if not tx or tx.status != TxStatus.PENDING:
            req.status = WithdrawalStatus.REJECTED
            req.details = "Missing or already processed tx"
            processed.append({"withdrawal_id": req.id, "skipped": True})
            db.commit()
            continue

        try:
            req.status = WithdrawalStatus.PROCESSING
            db.flush()

            payout_txn_id, raw = _paypal_send_payout(req, token)
            req.status = WithdrawalStatus.PAID
            req.payout_txn_id = payout_txn_id
            req.details = json.dumps(raw)[:1000]
            tx.status = TxStatus.SUCCESS
            processed.append(
                {
                    "withdrawal_id": req.id,
                    "payout_txn_id": payout_txn_id,
                }
            )
        except Exception as exc:
            user = _lock_user(db, req.user_id)
            user.wallet_balance = (user.wallet_balance or 0) + req.amount
            tx.status = TxStatus.FAILED
            req.status = WithdrawalStatus.REJECTED
            req.details = f"PayPal error: {exc}"[:255]
            processed.append(
                {
                    "withdrawal_id": req.id,
                    "error": str(exc),
                }
            )
        finally:
            db.commit()

    return {"ok": True, "processed": processed}
