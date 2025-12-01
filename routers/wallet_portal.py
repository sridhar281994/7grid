from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import User
from utils.security import (
    get_current_user,
    require_channel,
    issue_wallet_cookie,
    consume_wallet_bridge_token,
)
from routers.wallet import withdraw_request, recharge_create_link

router = APIRouter(prefix="/wallet-portal", tags=["wallet-portal"])


@router.post("/sessions/bridge")
def create_session_from_bridge(
    token: str,
    db: Session = Depends(get_db),
):
    payload = consume_wallet_bridge_token(token)
    user = db.get(User, payload["user_id"])
    if not user:
        raise HTTPException(404, "User not found")
    response = {"ok": True}
    issue_wallet_cookie(response, payload)
    return response


@router.get("/ledger")
def ledger(
    user: User = Depends(get_current_user),
    _: None = Depends(require_channel("web")),
):
    # call existing wallet history logic (could import function) – placeholder
    return {"balance": float(user.wallet_balance or 0)}
