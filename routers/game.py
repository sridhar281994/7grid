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

    stakes table:
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
    players: int = 2  # 2 or 3 (optional; default 2 if you want)


class CompleteIn(BaseModel):
    match_id: int
    winner_user_id: int


# --------------------------------------------------
# GET: Stakes List
# --------------------------------------------------
@router.get("/stakes")
def list_stakes(db: Session = Depends(get_db)):
    """
    Return all stakes (Free + 2/4/6 for 2P & 3P) from your existing stakes table.
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
# POST: Request Match (optional helper; you can ignore if you use /matches/create)
# --------------------------------------------------
@router.post("/request")
def request_match(
    payload: MatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Simple "join or create" endpoint.

    Wallet is charged entry_fee immediately for this user.
    If no matching WAITING game is found, we refund the entry_fee.
    """

    players = payload.players if payload.players in (2, 3) else 2

    rule = get_stake_rule(db, payload.stake_amount, players)
    if not rule:
        raise HTTPException(status_code=400, detail="Invalid stake selected")

    entry_fee = rule["entry_fee"]

    # Check wallet
    if entry_fee > 0 and (user.wallet_balance or 0) < entry_fee:
        raise HTTPException(status_code=400, detail="Insufficient wallet")

    # Charge entry fee
    if entry_fee > 0:
        user.wallet_balance = (user.wallet_balance or 0) - entry_fee
        db.commit()
        db.refresh(user)

    # Try to join existing WAITING match
    waiting = (
        db.execute(
            select(GameMatch).where(
                and_(
                    GameMatch.stake_amount == payload.stake_amount,
                    GameMatch.num_players == players,
                    GameMatch.status == MatchStatus.WAITING,
                )
            ).order_by(GameMatch.id.asc())
        )
        .scalars()
        .first()
    )

    if waiting and waiting.p1_user_id != user.id:
        # Fill P2 or P3
        if not waiting.p2_user_id:
            waiting.p2_user_id = user.id
            # 2-player game becomes ACTIVE now
            if players == 2:
                waiting.status = MatchStatus.ACTIVE
            db.commit()
            return {
                "ok": True,
                "match_id": waiting.id,
                "status": waiting.status.value,
                "num_players": players,
            }

        if players == 3 and not waiting.p3_user_id:
            waiting.p3_user_id = user.id
            waiting.status = MatchStatus.ACTIVE
            db.commit()
            return {
                "ok": True,
                "match_id": waiting.id,
                "status": waiting.status.value,
                "num_players": players,
            }

    # No usable match → refund
    if entry_fee > 0:
        user.wallet_balance = (user.wallet_balance or 0) + entry_fee
        db.commit()
        db.refresh(user)

    return {"ok": False, "refund": True}


# --------------------------------------------------
# POST: Complete Match
# --------------------------------------------------
@router.post("/complete")
def complete_match(
    payload: CompleteIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """
    Manual completion endpoint (e.g. admin or emergency use).
    Normally the game flow should call distribute_prize from /matches/roll
    when the winner is decided.
    """
    m = db.get(GameMatch, payload.match_id)
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    # Already done → nothing to do.
    if m.status == MatchStatus.FINISHED:
        return {"ok": True, "already_completed": True}

    # Only allow participants to trigger this
    if me.id not in {m.p1_user_id, m.p2_user_id, m.p3_user_id}:
        raise HTTPException(status_code=403, detail="Not a participant")

    # Validate winner is actually in this match
    players = [m.p1_user_id, m.p2_user_id]
    if m.num_players == 3:
        players.append(m.p3_user_id)

    if payload.winner_user_id not in players:
        raise HTTPException(status_code=400, detail="Invalid winner")

    winner_idx = players.index(payload.winner_user_id)

    # Use the same wallet_utils.prize distribution as /matches/roll
    from routers.wallet_utils import distribute_prize

    distribute_prize(db, m, winner_idx)

    return {
        "ok": True,
        "match_id": m.id,
        "winner_user_id": payload.winner_user_id,
    }
