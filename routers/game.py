from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text, select, and_

from database import get_db
from models import User, GameMatch, MatchStatus
from utils.security import get_current_user

router = APIRouter(prefix="/game", tags=["game"])


# --------------------------------------------------
# Helpers
# --------------------------------------------------
def get_stake_rule(db: Session, stake_amount: int, players: int):
    """
    Fetch stake rule based on stake_amount AND players (2 or 3).
    """
    row = db.execute(
        text("""
            SELECT stake_amount, entry_fee, winner_payout, players, label
            FROM stakes
            WHERE stake_amount = :amt AND players = :p
        """),
        {"amt": stake_amount, "p": players}
    ).mappings().first()

    if not row:
        return None

    return {
        "stake_amount": int(row["stake_amount"]),
        "entry_fee": Decimal(row["entry_fee"]),
        "winner_payout": Decimal(row["winner_payout"]),
        "players": int(row["players"]),
        "label": row["label"]
    }


# --------------------------------------------------
# Request Models
# --------------------------------------------------
class MatchIn(BaseModel):
    stake_amount: int


class CompleteIn(BaseModel):
    match_id: int
    winner_user_id: int


# --------------------------------------------------
# GET: Stakes List
# --------------------------------------------------
@router.get("/stakes")
def list_stakes(db: Session = Depends(get_db)):
    """
    Return all stakes (Free + 2/4/6 for 2P & 3P).
    """
    rows = db.execute(
        text("""
            SELECT stake_amount, entry_fee, winner_payout, players, label
            FROM stakes
            ORDER BY players ASC, stake_amount ASC
        """)
    ).mappings().all()

    return [
        {
            "stake_amount": int(r["stake_amount"]),
            "entry_fee": float(r["entry_fee"]),
            "winner_payout": float(r["winner_payout"]),
            "players": int(r["players"]),
            "label": r["label"],
        }
        for r in rows
    ]


# --------------------------------------------------
# POST: Request Match
# --------------------------------------------------
@router.post("/request")
def request_match(
    payload: MatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Join or create a match.
    Wallet is deducted immediately.
    """
    # Determine number of players from app (2 or 3)
    # The frontend sends selected_mode in storage
    selected_mode = user.selected_mode if hasattr(user, "selected_mode") else 3

    rule = get_stake_rule(db, payload.stake_amount, selected_mode)
    if not rule:
        raise HTTPException(400, "Invalid stake selected")

    entry_fee = rule["entry_fee"]

    # Verify wallet
    if entry_fee > 0 and (user.wallet_balance or 0) < entry_fee:
        raise HTTPException(400, "Insufficient wallet")

    # Deduct entry fee
    if entry_fee > 0:
        user.wallet_balance = (user.wallet_balance or 0) - entry_fee
        db.commit()
        db.refresh(user)

    # Try to join existing match
    waiting = db.execute(
        select(GameMatch).where(
            and_(
                GameMatch.stake_amount == payload.stake_amount,
                GameMatch.num_players == rule["players"],
                GameMatch.status == MatchStatus.WAITING
            )
        ).order_by(GameMatch.id.asc())
    ).scalars().first()

    # Fill P2 or P3
    if waiting and waiting.p1_user_id != user.id:
        if not waiting.p2_user_id:
            waiting.p2_user_id = user.id
            db.commit()
            return {"ok": True, "match_id": waiting.id, "status": waiting.status.value}

        if rule["players"] == 3 and not waiting.p3_user_id:
            waiting.p3_user_id = user.id
            waiting.status = MatchStatus.ACTIVE
            db.commit()
            return {"ok": True, "match_id": waiting.id, "status": waiting.status.value}

    # No match → refund
    if entry_fee > 0:
        user.wallet_balance = (user.wallet_balance or 0) + entry_fee
        db.commit()
        db.refresh(user)

    return {"ok": False, "refund": True}


# --------------------------------------------------
# POST: Complete Match (Final Fix)
# --------------------------------------------------
@router.post("/complete")
def complete_match(
    payload: CompleteIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user)
):
    m = db.get(GameMatch, payload.match_id)
    if not m:
        raise HTTPException(404, "Match not found")

    # If match already completed, do nothing
    if m.status == MatchStatus.FINISHED:
        return {"ok": True, "already_completed": True}

    # Only allow participants
    if me.id not in {m.p1_user_id, m.p2_user_id, m.p3_user_id}:
        raise HTTPException(403, "Not a participant")

    # Validate winner
    players = [m.p1_user_id, m.p2_user_id]
    if m.num_players == 3:
        players.append(m.p3_user_id)

    if payload.winner_user_id not in players:
        raise HTTPException(400, "Invalid winner")

    winner_idx = players.index(payload.winner_user_id)

    # Distribute prize
    from routers.wallet_utils import distribute_prize
    import asyncio
    asyncio.run(distribute_prize(db, m, winner_idx))

    return {
        "ok": True,
        "match_id": m.id,
        "winner_user_id": payload.winner_user_id
    }
