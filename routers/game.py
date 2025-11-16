from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from models import User, GameMatch, MatchStatus
from utils.security import get_current_user
from routers.wallet_utils import distribute_prize

router = APIRouter(prefix="/game", tags=["game"])


# --------------------------------------------------
# Helper: read stake rule from existing stakes table
# --------------------------------------------------
def get_stake_rule(db: Session, stake_amount: int, players: int):
    """
    Fetch stake rule based on stake_amount AND players (2 or 3).

    stakes table schema (already in DB):
      stake_amount | entry_fee | winner_payout | players | label
    """
    row = db.execute(
        text(
            """
            SELECT stake_amount, entry_fee, winner_payout, players, label
            FROM stakes
            WHERE stake_amount = :amt AND players = :p
            """
        ),
        {"amt": stake_amount, "p": players},
    ).mappings().first()

    if not row:
        return None

    # Use Decimal to keep money math consistent
    return {
        "stake_amount": int(row["stake_amount"]),
        "entry_fee": Decimal(str(row["entry_fee"])),
        "winner_payout": Decimal(str(row["winner_payout"])),
        "players": int(row["players"]),
        "label": row["label"],
    }


# --------------------------------------------------
# Request Models
# --------------------------------------------------
class MatchIn(BaseModel):
    stake_amount: int
    players: int = 2  # 2 or 3


class CompleteIn(BaseModel):
    match_id: int
    winner_user_id: int


# --------------------------------------------------
# GET: Stakes List (for UI)
# --------------------------------------------------
@router.get("/stakes")
def list_stakes(db: Session = Depends(get_db)):
    """
    Return all stakes (Free + 2/4/6 for 2P & 3P) from the existing stakes table.

    This is used by the app to render stage cards (2-player + 3-player rows).
    """
    rows = db.execute(
        text(
            """
            SELECT stake_amount, entry_fee, winner_payout, players, label
            FROM stakes
            ORDER BY players ASC, stake_amount ASC
            """
        )
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
# POST: Request Match (SAFE PREVIEW ONLY – no DB writes)
# --------------------------------------------------
@router.post("/request")
def request_match(
    payload: MatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    SAFE helper endpoint.

    This DOES NOT create or join a match and DOES NOT touch wallet.
    It just:
      - looks up the stake rule from stakes table
      - checks if user's wallet has enough for entry_fee
      - returns data to the app

    Real match creation is handled by /matches/create.
    """

    players = payload.players if payload.players in (2, 3) else 2

    rule = get_stake_rule(db, payload.stake_amount, players)
    if not rule:
        raise HTTPException(status_code=400, detail="Invalid stake selected")

    entry_fee = rule["entry_fee"]
    wallet_balance = Decimal(str(user.wallet_balance or 0))
    wallet_ok = wallet_balance >= entry_fee

    return {
        "ok": True,
        "stake_amount": rule["stake_amount"],
        "entry_fee": float(entry_fee),
        "winner_payout": float(rule["winner_payout"]),
        "players": rule["players"],
        "label": rule["label"],
        "wallet_balance": float(wallet_balance),
        "wallet_ok": wallet_ok,
        "reason": None if wallet_ok else "Insufficient wallet",
    }


# --------------------------------------------------
# POST: Complete Match (manual override / admin)
# --------------------------------------------------
@router.post("/complete")
async def complete_match(
    payload: CompleteIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """
    Manual completion endpoint.

    Normal flow:
      - /matches/roll decides the winner and calls wallet_utils.distribute_prize
      - You DO NOT need this for standard gameplay

    This exists only as a safety / admin override:
      - validates that the caller is a participant
      - validates winner_user_id belongs to the match
      - calls the SAME distribute_prize() logic used by /matches/roll
    """
    m: GameMatch | None = db.get(GameMatch, payload.match_id)
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    # Already finalized → do nothing
    if m.status == MatchStatus.FINISHED:
        return {"ok": True, "already_completed": True}

    # Only allow participants to trigger manual complete
    if me.id not in {m.p1_user_id, m.p2_user_id, m.p3_user_id}:
        raise HTTPException(status_code=403, detail="Not a participant")

    # Validate winner is one of the players
    players = [m.p1_user_id, m.p2_user_id]
    if m.num_players == 3:
        players.append(m.p3_user_id)

    if payload.winner_user_id not in players:
        raise HTTPException(status_code=400, detail="Invalid winner")

    winner_idx = players.index(payload.winner_user_id)

    # Use the same async prize logic as /matches/roll
    await distribute_prize(db, m, winner_idx)

    return {
        "ok": True,
        "match_id": m.id,
        "winner_user_id": payload.winner_user_id,
    }
