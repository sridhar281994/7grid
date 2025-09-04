from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, conint
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from database import get_db
from models import User, GameMatch, MatchStatus
from utils.security import get_current_user

router = APIRouter(prefix="/game", tags=["game"])

VALID_STAKES = {4, 8, 12}


def entry_cost(stake: int) -> Decimal:
    return Decimal(stake) / Decimal(2)


def winner_payout(stake: int) -> Decimal:
    return Decimal(stake) * Decimal("0.75")


def system_fee(stake: int) -> Decimal:
    return Decimal(stake) - winner_payout(stake)


class MatchIn(BaseModel):
    stake_amount: conint(strict=True, ge=1)


class CompleteIn(BaseModel):
    match_id: int
    winner_user_id: int


@router.post("/request")
def request_match(
    payload: MatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stake = int(payload.stake_amount)
    if stake not in VALID_STAKES:
        raise HTTPException(400, f"Invalid stake, choose one of {sorted(VALID_STAKES)}")

    fee = entry_cost(stake)
    if (user.wallet_balance or 0) < fee:
        raise HTTPException(400, "Insufficient wallet for entry fee")

    waiting = db.execute(
        select(GameMatch)
        .where(and_(GameMatch.stake_amount == stake, GameMatch.status == MatchStatus.WAITING))
        .order_by(GameMatch.id.asc())
    ).scalars().first()

    if waiting and waiting.p1_id != user.id:
        user.wallet_balance = (user.wallet_balance or 0) - fee
        waiting.p2_id = user.id
        waiting.status = MatchStatus.ACTIVE
        db.commit()
        return {"ok": True, "match_id": waiting.id, "status": waiting.status.value}

    user.wallet_balance = (user.wallet_balance or 0) - fee
    m = GameMatch(stake_amount=stake, p1_id=user.id, status=MatchStatus.WAITING)
    db.add(m)
    db.commit()
    db.refresh(m)

    return {"ok": True, "match_id": m.id, "status": m.status.value}


@router.post("/complete")
def complete_match(
    payload: CompleteIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    m = db.get(GameMatch, payload.match_id)
    if not m:
        raise HTTPException(404, "Match not found")
    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(400, "Match not active")
    if me.id not in {m.p1_id, m.p2_id}:
        raise HTTPException(403, "Only participants can complete the match")
    if payload.winner_user_id not in {m.p1_id, m.p2_id}:
        raise HTTPException(400, "Winner must be p1 or p2")

    winner = db.get(User, payload.winner_user_id)
    if not winner:
        raise HTTPException(404, "Winner user not found")

    pay = winner_payout(m.stake_amount)
    m.system_fee = system_fee(m.stake_amount)
    winner.wallet_balance = (winner.wallet_balance or 0) + pay

    m.winner_user_id = winner.id
    m.status = MatchStatus.FINISHED
    db.commit()

    return {"ok": True, "match_id": m.id, "winner_user_id": winner.id, "payout": float(pay)}
