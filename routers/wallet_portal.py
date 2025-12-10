from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional

from database import get_db
from models import User, WithdrawalMethod
from utils.security import (
    get_current_user,
    require_channel,
    consume_wallet_bridge_token,
    issue_wallet_cookie,
    get_request_context,
    create_access_token,
    verify_wallet_cookie,
    WALLET_COOKIE_NAME,
)
from routers.wallet import (
    AmountIn,
    wallet_history,
    recharge_create_link,
    withdraw_request,
    WithdrawRequestIn,
    MIN_RECHARGE_INR,
    MIN_WITHDRAW_INR,
    MIN_WITHDRAW_USD,
    paypal_is_enabled,
    PAYPAL_PAYOUT_CURRENCY,
    COINS_PER_INR,
    COINS_PER_USD,
)

router = APIRouter(prefix="/wallet-portal", tags=["wallet-portal"])


@router.post("/sessions/bridge")
def bridge_session(
    request: Request,
    token_payload: Optional[str] = Body(default=None, embed=True, alias="token"),
    token: Optional[str] = None,
    db: Session = Depends(get_db),
):
    link_token = token_payload or token
    if not link_token:
        raise HTTPException(422, "Missing wallet link token")

    token_payload = consume_wallet_bridge_token(db, link_token)
    user = db.get(User, token_payload["user_id"])
    if not user:
        raise HTTPException(404, "User not found")

    ctx = get_request_context(request)
    cookie_payload = {
        "user_id": user.id,
        "channel": token_payload["channel"],
        "fingerprint": ctx.get("fingerprint"),
    }

    access_token = create_access_token(
        user_id=user.id,
        channel=token_payload["channel"],
        fingerprint=ctx.get("fingerprint"),
    )

    response = JSONResponse({"ok": True, "user_id": user.id, "access_token": access_token})
    issue_wallet_cookie(response, cookie_payload)
    return response


@router.post("/sessions/refresh")
def refresh_session(
    request: Request,
    db: Session = Depends(get_db),
):
    cookie_value = request.cookies.get(WALLET_COOKIE_NAME)
    if not cookie_value:
        raise HTTPException(401, "Missing wallet session")

    session_payload = verify_wallet_cookie(cookie_value)
    user = db.get(User, session_payload["user_id"])
    if not user:
        raise HTTPException(404, "User not found")

    access_token = create_access_token(
        user_id=user.id,
        channel=session_payload.get("channel", "web"),
        fingerprint=session_payload.get("fingerprint"),
    )

    response = JSONResponse(
        {"ok": True, "user_id": user.id, "access_token": access_token, "refreshed": True}
    )
    issue_wallet_cookie(
        response,
        {
            "user_id": user.id,
            "channel": session_payload.get("channel", "web"),
            "fingerprint": session_payload.get("fingerprint"),
        },
    )
    return response


@router.get("/profile")
def portal_profile(
    _: dict = Depends(require_channel("web")),
    user: User = Depends(get_current_user),
):
    return {
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "upi_id": user.upi_id,
            "paypal_id": user.paypal_id,
            "wallet_balance": float(user.wallet_balance or 0),
        },
        "limits": {
            "recharge_inr_min": float(MIN_RECHARGE_INR),
            "withdraw_inr_min": float(MIN_WITHDRAW_INR),
            "withdraw_usd_min": float(MIN_WITHDRAW_USD),
        },
        "payout": {
            "paypal_enabled": paypal_is_enabled(),
            "paypal_currency": PAYPAL_PAYOUT_CURRENCY,
            "has_upi_details": bool(user.upi_id),
            "has_paypal_details": bool(user.paypal_id),
            "upi_id": user.upi_id,
            "paypal_id": user.paypal_id,
        },
        "conversion": {
            "coins_per_inr": float(COINS_PER_INR),
            "coins_per_usd": float(COINS_PER_USD),
        },
    }


@router.get("/ledger")
def portal_ledger(
    skip: int = 0,
    limit: int = 20,
    _: dict = Depends(require_channel("web")),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return wallet_history(skip=skip, limit=limit, db=db, user=user)


@router.post("/recharge")
def portal_recharge(
    payload: AmountIn,
    _: dict = Depends(require_channel("web")),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return recharge_create_link(payload=payload, db=db, user=user)


@router.post("/withdraw")
def portal_withdraw(
    payload: WithdrawRequestIn,
    _: dict = Depends(require_channel("web")),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    preferred_account = None
    missing_label = None
    if payload.method == WithdrawalMethod.UPI:
        preferred_account = (user.upi_id or "").strip()
        missing_label = "UPI ID"
    elif payload.method == WithdrawalMethod.PAYPAL:
        preferred_account = (user.paypal_id or "").strip()
        missing_label = "PayPal email"

    if not preferred_account:
        raise HTTPException(
            400,
            f"Add your {missing_label or 'payout details'} inside the SR Tech app before withdrawing.",
        )

    sanitized_payload = WithdrawRequestIn(
        amount=payload.amount,
        method=payload.method,
        account=preferred_account,
    )
    return withdraw_request(payload=sanitized_payload, db=db, user=user)
