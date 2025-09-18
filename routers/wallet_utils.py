from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime
import uuid

from models import User, GameMatch, WalletTransaction, TxType, TxStatus


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


def _lock_user(db: Session, user_id: int) -> User:
    """🔒 Always lock row before wallet update."""
    u = db.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()
    return u


def deduct_entry_fee(db: Session, user: User, entry_fee: int):
    """Deduct entry fee from a user's wallet and log transaction."""
    if (user.wallet_balance or 0) < entry_fee:
        raise ValueError("Insufficient balance")

    user.wallet_balance = (user.wallet_balance or 0) - entry_fee
    _log_transaction(db, user.id, -entry_fee, TxType.WITHDRAW, TxStatus.SUCCESS, note="Entry Fee")
    db.commit()
    db.refresh(user)


# -------------------------
# Prize Distribution
# -------------------------
async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    """
    Distribute winnings when a match finishes.
    Logic:
      - 4rs game: each pays 2, winner gets 3, merchant gets 1
      - 8rs game: each pays 4, winner gets 6, merchant gets 2
      - 12rs game: each pays 6, winner gets 9, merchant gets 3
    """
    stake = match.stake_amount

    if stake == 4:
        winner_prize = 3
        system_fee = 1
    elif stake == 8:
        winner_prize = 6
        system_fee = 2
    elif stake == 12:
        winner_prize = 9
        system_fee = 3
    else:
        winner_prize = (stake * 3) // 4
        system_fee = stake // 4

    # Pick winner
    if winner_idx == 0 and match.p1_user_id:
        winner = _lock_user(db, match.p1_user_id) # 🔒
    elif winner_idx == 1 and match.p2_user_id:
        winner = _lock_user(db, match.p2_user_id) # 🔒
    else:
        print(f"[ERROR] distribute_prize: Invalid winner index {winner_idx}")
        return

    # ✅ Update winner balance + log
    if winner:
        old_balance = float(winner.wallet_balance or 0)
        new_balance = old_balance + winner_prize
        winner.wallet_balance = new_balance
        _log_transaction(db, winner.id, winner_prize, TxType.RECHARGE, TxStatus.SUCCESS, note="Match Win")
        print(f"[DEBUG] Prize distributed: user_id={winner.id}, "
              f"old_balance={old_balance}, prize={winner_prize}, new_balance={new_balance}")

    # Save in match
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
        p1 = _lock_user(db, match.p1_user_id) # 🔒
        if p1:
            p1.wallet_balance = (p1.wallet_balance or 0) + entry_fee
            _log_transaction(db, p1.id, entry_fee, TxType.RECHARGE, TxStatus.SUCCESS, note="Refund")

    if match.p2_user_id:
        p2 = _lock_user(db, match.p2_user_id) # 🔒
        if p2:
            p2.wallet_balance = (p2.wallet_balance or 0) + entry_fee
            _log_transaction(db, p2.id, entry_fee, TxType.RECHARGE, TxStatus.SUCCESS, note="Refund")

    match.status = match.status or "cancelled"
    match.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(match)
