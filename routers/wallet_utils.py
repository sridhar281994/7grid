from sqlalchemy.orm import Session
from models import User, GameMatch
from datetime import datetime, timezone

def _utcnow():
    return datetime.now(timezone.utc)

def deduct_wallet(db: Session, user: User, amount: int):
    """Deduct entry fee from user wallet."""
    if (user.wallet_balance or 0) < amount:
        raise ValueError("Insufficient balance")
    user.wallet_balance = (user.wallet_balance or 0) - amount
    db.add(user)
    db.commit()
    db.refresh(user)


def refund_entry(db: Session, user: User, amount: int):
    """Refund entry fee if match never started or cancelled."""
    user.wallet_balance = (user.wallet_balance or 0) + amount
    db.add(user)
    db.commit()
    db.refresh(user)


def payout_winner(db: Session, match: GameMatch, winner_idx: int):
    """Credit winnings to the winner, apply merchant/system fee."""
    stake = match.stake_amount
    if stake <= 0:
        return

    # winner prize & fee logic
    winner_prize = (stake * 3) // 4 # 75% to winner
    system_fee = stake // 4 # 25% fee

    if winner_idx == 0 and match.p1_user_id:
        winner = db.get(User, match.p1_user_id)
    elif winner_idx == 1 and match.p2_user_id:
        winner = db.get(User, match.p2_user_id)
    else:
        return

    if winner:
        winner.wallet_balance = (winner.wallet_balance or 0) + winner_prize
        db.add(winner)

    match.system_fee = system_fee
    match.winner_user_id = winner.id if winner else None
    match.finished_at = _utcnow()
    db.add(match)

    db.commit()
    db.refresh(match)
