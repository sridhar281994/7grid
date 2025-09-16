from datetime import datetime, timezone
from sqlalchemy.orm import Session
from models import GameMatch, User, MatchStatus

def _utcnow():
    return datetime.now(timezone.utc)

async def deduct_entry_fee(user: User, stake_amount: int, db: Session) -> int:
    """
    Deduct entry fee from user's wallet.
    Returns deducted amount.
    """
    entry_fee = stake_amount // 2
    if (user.wallet_balance or 0) < entry_fee:
        raise ValueError("Insufficient balance")

    user.wallet_balance = (user.wallet_balance or 0) - entry_fee
    db.commit()
    db.refresh(user)
    return entry_fee

async def finalize_payout(m: GameMatch, db: Session, winner_idx: int):
    """
    Distribute winnings when a match finishes.
    Winner gets 75% of stake, system keeps 25%.
    """
    stake = m.stake_amount
    winner_prize = (stake * 3) // 4
    system_fee = stake // 4

    if winner_idx == 0 and m.p1_user_id:
        winner = db.get(User, m.p1_user_id)
    elif winner_idx == 1 and m.p2_user_id:
        winner = db.get(User, m.p2_user_id)
    else:
        return

    if winner:
        winner.wallet_balance = (winner.wallet_balance or 0) + winner_prize

    m.system_fee = system_fee
    m.winner_user_id = winner.id if winner else None
    m.finished_at = _utcnow()
    db.commit()
    db.refresh(m)

async def refund_entry(m: GameMatch, db: Session):
    """
    Refund entry fees to players if match is canceled or stale.
    """
    entry_fee = m.stake_amount // 2
    if m.p1_user_id:
        u1 = db.get(User, m.p1_user_id)
        if u1:
            u1.wallet_balance = (u1.wallet_balance or 0) + entry_fee
    if m.p2_user_id:
        u2 = db.get(User, m.p2_user_id)
        if u2:
            u2.wallet_balance = (u2.wallet_balance or 0) + entry_fee

    m.status = MatchStatus.FINISHED
    m.finished_at = _utcnow()
    db.commit()
    db.refresh(m)
