from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text, select, and_

from database import get_db
from models import User, GameMatch, MatchStatus, Stake
from utils.security import get_current_user
from .wallet_utils import distribute_prize


router = APIRouter(prefix="/game", tags=["game"])


# -------------------------
# Helpers
# -------------------------
def get_stake_rule(db: Session, stake_amount: int):
    """Fetch stake rule dynamically from stakes table."""
    row = db.execute(
        text("SELECT stake_amount, entry_fee, winner_payout, label FROM stakes WHERE stake_amount = :amt"),
        {"amt": stake_amount}
    ).mappings().first()
    if not row:
        return None
    return {
        "stake_amount": int(row["stake_amount"]),
        "entry_fee": Decimal(row["entry_fee"]),
        "winner_payout": Decimal(row["winner_payout"]),
        "label": row["label"]
    }


# -------------------------
# Request models
# -------------------------
class MatchIn(BaseModel):
    stake_amount: int


class CompleteIn(BaseModel):
    match_id: int
    winner_user_id: int


# -------------------------
# Endpoints
# -------------------------
@router.get("/stakes")
def list_stakes(db: Session = Depends(get_db)):
    """List all available stake rules for frontend stage screen."""
    rows = db.execute(
        text("SELECT stake_amount, entry_fee, winner_payout, label FROM stakes ORDER BY stake_amount ASC")
    ).mappings().all()
    return [
        {
            "stake_amount": int(r["stake_amount"]),
            "entry_fee": float(r["entry_fee"]),
            "winner_payout": float(r["winner_payout"]),
            "label": r["label"],
        }
        for r in rows
    ]


@router.post("/request")
def request_match(
    payload: MatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Request to join/create a 3-player match with DB-driven rules.
    Deducts wallet immediately, refunds instantly if no match is available.
    """
    rule = get_stake_rule(db, payload.stake_amount)
    if not rule:
        raise HTTPException(400, "Invalid stake selected")

    entry_fee = rule["entry_fee"]

    # Skip wallet deduction for Free Play
    if entry_fee > 0 and (user.wallet_balance or 0) < entry_fee:
        raise HTTPException(400, "Insufficient wallet for entry fee")

    # Deduct entry fee first (escrow style)
    if entry_fee > 0:
        user.wallet_balance = (user.wallet_balance or 0) - entry_fee
        db.commit()
        db.refresh(user)

    # Try to join existing waiting match
    waiting = db.execute(
        select(GameMatch).where(
            and_(
                GameMatch.stake_amount == payload.stake_amount,
                GameMatch.status == MatchStatus.WAITING
            )
        ).order_by(GameMatch.id.asc())
    ).scalars().first()

    if waiting and waiting.p1_user_id != user.id and waiting.p2_user_id and not waiting.p3_user_id:
        waiting.p3_user_id = user.id
        waiting.status = MatchStatus.ACTIVE
        db.commit()
        return {"ok": True, "match_id": waiting.id, "status": waiting.status.value}

    if waiting and waiting.p1_user_id != user.id and not waiting.p2_user_id:
        waiting.p2_user_id = user.id
        db.commit()
        return {"ok": True, "match_id": waiting.id, "status": waiting.status.value}

    # No match found → refund immediately
    if entry_fee > 0:
        user.wallet_balance = (user.wallet_balance or 0) + entry_fee
        db.commit()
        db.refresh(user)

    return {"ok": False, "refund": True, "msg": "No opponent found. Entry fee refunded."}


from routers.wallet_utils import distribute_prize   # <-- REQUIRED

@router.post("/complete")
async def complete_match(
    payload: CompleteIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """Mark match complete and run prize distribution."""
    m = db.get(GameMatch, payload.match_id)
    if not m:
        raise HTTPException(404, "Match not found")

    if m.status != MatchStatus.ACTIVE:
        raise HTTPException(400, "Match not active")

    if me.id not in {m.p1_user_id, m.p2_user_id, m.p3_user_id}:
        raise HTTPException(403, "Only participants can complete the match")

    if payload.winner_user_id not in {m.p1_user_id, m.p2_user_id, m.p3_user_id}:
        raise HTTPException(400, "Winner must be p1, p2, or p3")

    # Determine winner index
    players = [m.p1_user_id, m.p2_user_id, m.p3_user_id]
    winner_idx = players.index(payload.winner_user_id)

    await distribute_prize(db, m, winner_idx)

    return {
        "ok": True,
        "match_id": m.id,
        "winner_user_id": payload.winner_user_id,
        "msg": "Prize distributed"
    }
