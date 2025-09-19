from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, conint
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from database import get_db
from models import User, GameMatch, MatchStatus, Stake
from utils.security import get_current_user

router = APIRouter(prefix="/game", tags=["game"])


# -------------------------
# Helpers (stake rules)
# -------------------------
def entry_cost(stake_rule: Stake) -> Decimal:
    """Each player contributes the defined entry_fee"""
    return Decimal(stake_rule.entry_fee or 0)


def winner_payout(stake_rule: Stake) -> Decimal:
    """Winner gets defined payout"""
    return Decimal(stake_rule.winner_payout or 0)


def system_fee(stake_rule: Stake) -> Decimal:
    """System keeps the difference between total contributed and payout"""
    total_contribution = Decimal(stake_rule.entry_fee or 0) * Decimal(3) # 3 players
    return total_contribution - Decimal(stake_rule.winner_payout or 0)


# -------------------------
# Request models
# -------------------------
class MatchIn(BaseModel):
    stake_amount: conint(strict=True, ge=0) # allow 0 for Free Stage


class CompleteIn(BaseModel):
    match_id: int
    winner_user_id: int


# -------------------------
# Endpoints
# -------------------------
@router.post("/request")
def request_match(
    payload: MatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Request to join or create a match with the given stake.
    Reads stake rules dynamically from `stakes` table.
    """
    # Fetch stake rule
    stake_rule = db.execute(
        select(Stake).where(Stake.stake_amount == payload.stake_amount)
    ).scalar_one_or_none()
    if not stake_rule:
        raise HTTPException(400, f"Invalid stake: {payload.stake_amount}")

    fee = entry_cost(stake_rule)
    if fee > 0 and (user.wallet_balance or 0) < fee:
        raise HTTPException(400, "Insufficient wallet for entry fee")

    # Try to join an existing waiting match
    waiting = db.execute(
        select(GameMatch)
        .where(and_(GameMatch.stake_amount == payload.stake_amount,
                    GameMatch.status == MatchStatus.WAITING))
        .order_by(GameMatch.id.asc())
    ).scalars().first()

    if waiting and waiting.p1_user_id != user.id and waiting.p2_user_id != user.id:
        # Charge fee only if > 0
        if fee > 0:
            user.wallet_balance = (user.wallet_balance or 0) - fee
        waiting.p3_user_id = user.id
        waiting.status = MatchStatus.ACTIVE
        db.commit()
        return {"ok": True, "match_id": waiting.id, "status": waiting.status.value}

    elif waiting and waiting.p1_user_id != user.id and not waiting.p2_user_id:
        if fee > 0:
            user.wallet_balance = (user.wallet_balance or 0) - fee
        waiting.p2_user_id = user.id
        db.commit()
        return {"ok": True, "match_id": waiting.id, "status": waiting.status.value}

    # Else create a waiting match, charge p1
    if fee > 0:
        user.wallet_balance = (user.wallet_balance or 0) - fee
    m = GameMatch(stake_amount=payload.stake_amount, p1_user_id=user.id,
                  status=MatchStatus.WAITING)
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
    """
    Mark a match as complete and award payout to the winner.
    """
    m = db.get(GameMatch, payload.match_id)
    if not m:
        raise HTTPException(404, "Match not found")

    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(400, "Match not active")

    if me.id not in {m.p1_user_id, m.p2_user_id, m.p3_user_id}:
        raise HTTPException(403, "Only participants can complete the match")

    if payload.winner_user_id not in {m.p1_user_id, m.p2_user_id, m.p3_user_id}:
        raise HTTPException(400, "Winner must be one of the players")

    winner = db.get(User, payload.winner_user_id)
    if not winner:
        raise HTTPException(404, "Winner user not found")

    # Fetch stake rule
    stake_rule = db.execute(
        select(Stake).where(Stake.stake_amount == m.stake_amount)
    ).scalar_one_or_none()
    if not stake_rule:
        raise HTTPException(400, f"Invalid stake: {m.stake_amount}")

    # Credit winner, keep system fee
    pay = winner_payout(stake_rule)
    m.system_fee = system_fee(stake_rule)

    if pay > 0:
        winner.wallet_balance = (winner.wallet_balance or 0) + pay

    m.winner_user_id = winner.id
    m.status = MatchStatus.FINISHED
    db.commit()

    return {
        "ok": True,
        "match_id": m.id,
        "winner_user_id": winner.id,
        "payout": float(pay),
    }
