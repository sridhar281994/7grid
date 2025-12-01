from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from models import User
from utils.security import (
    get_current_user,
    require_channel,
    consume_wallet_bridge_token,
    issue_wallet_cookie,
    get_request_context,
)
from routers.wallet import wallet_history, recharge_create_link, withdraw_request

router = APIRouter(prefix="/wallet-portal", tags=["wallet-portal"])


@router.post("/sessions/bridge")
def bridge_session(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    payload = consume_wallet_bridge_token(db, token)
    user = db.get(User, payload["user_id"])
    if not user:
        raise HTTPException(404, "User not found")

    ctx = get_request_context(request)
    response = JSONResponse({"ok": True, "user_id": user.id})
    cookie_payload = {
        "user_id": user.id,
        "channel": payload["channel"],
        "fingerprint": ctx.get("fingerprint"),
    }
    issue_wallet_cookie(response, cookie_payload)
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
