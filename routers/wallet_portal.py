from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from database import get_db
from models import User
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
from routers.wallet import wallet_history, recharge_create_link, withdraw_request

router = APIRouter(prefix="/wallet-portal", tags=["wallet-portal"])


class BridgeSessionIn(BaseModel):
    token: str


@router.post("/sessions/bridge")
def bridge_session(
    request: Request,
    payload: Optional[BridgeSessionIn] = Body(default=None),
    token: Optional[str] = None,
    db: Session = Depends(get_db),
):
    link_token = (payload.token if payload else None) or token
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
    payload,
    _: dict = Depends(require_channel("web")),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return recharge_create_link(payload=payload, db=db, user=user)


@router.post("/withdraw")
def portal_withdraw(
    payload,
    request: Request,
    _: dict = Depends(require_channel("web")),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ctx = get_request_context(request)
    # The withdraw_request endpoint already locks balance; we can extend it later to accept metadata.
    return withdraw_request(payload=payload, db=db, user=user)
