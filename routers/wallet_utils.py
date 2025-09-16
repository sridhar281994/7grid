from sqlalchemy.orm import Session
from models import User, GameMatch, WalletTransaction, TxType, TxStatus
from datetime import datetime
import uuid


def _log_transaction(db: Session, user_id: int, amount: float, tx_type: TxType, status: TxStatus, note: str = None):
    """Helper to log wallet changes in wallet_transactions."""
    tx = WalletTransaction(
        user_id=user_id,
        amount=amount,
        tx_type=tx_type,
        status=status,
        provider_ref=note,
        transaction_id=str(uuid.uuid4()),
        timestamp=datetime.utcnow(),
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


def deduct_entry_fee(db: Session, user: User, entry_fee: int):
    """Deduct entry fee from a user's wallet."""
    if (user.wallet_balance or 0) < entry_fee:
        raise ValueError("Insufficient balance")

    user.wallet_balance = (user.wallet_balance or 0) - entry_fee
    _log_transaction(db, user.id, -entry_fee, TxType.WITHDRAW, TxStatus.SUCCESS, note="Entry Fee")
    db.commit()
    db.refresh(user)


async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    """Distribute winnings when a match finishes."""
    stake = match.stake_amount
    winner_prize = (stake * 3) // 4 # 75%
    system_fee = stake // 4 # 25%

    if winner_idx == 0 and match.p1_user_id:
        winner = db.get(User, match.p1_user_id)
    elif winner_idx == 1 and match.p2_user_id:
        winner = db.get(User, match.p2_user_id)
    else:
        return

    if winner:
        winner.wallet_balance = (winner.wallet_balance or 0) + winner_prize
        _log_transaction(db, winner.id, winner_prize, TxType.RECHARGE, TxStatus.SUCCESS, note="Match Win")

    match.system_fee = system_fee
    match.winner_user_id = winner.id if winner else None
    match.finished_at = datetime.utcnow()

    db.commit()
    db.refresh(match)


async def refund_stake(db: Session, match: GameMatch):
    """Refund entry fee to both players if the match is cancelled before completion."""
    stake = match.stake_amount
    entry_fee = stake // 2

    if match.p1_user_id:
        p1 = db.get(User, match.p1_user_id)
        if p1:
            p1.wallet_balance = (p1.wallet_balance or 0) + entry_fee
            _log_transaction(db, p1.id, entry_fee, TxType.RECHARGE, TxStatus.SUCCESS, note="Refund")

    if match.p2_user_id:
        p2 = db.get(User, match.p2_user_id)
        if p2:
            p2.wallet_balance = (p2.wallet_balance or 0) + entry_fee
            _log_transaction(db, p2.id, entry_fee, TxType.RECHARGE, TxStatus.SUCCESS, note="Refund")

    match.status = match.status or "cancelled"
    match.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(match)
