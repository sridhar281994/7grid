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
    """Always lock row before wallet update."""
    return db.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()


def deduct_entry_fee(db: Session, user: User, entry_fee: int):
    """Deduct entry fee from wallet."""
    if (user.wallet_balance or 0) < entry_fee:
        raise ValueError("Insufficient balance")

    user.wallet_balance = (user.wallet_balance or 0) - entry_fee
    _log_transaction(db, user.id, -entry_fee, TxType.WITHDRAW, TxStatus.SUCCESS, note="Entry Fee")
    db.commit()
    db.refresh(user)


# ----------------------------------------------------
# PRIZE DISTRIBUTION
# ----------------------------------------------------
async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    stake = int(match.stake_amount or 0)
    num_players = int(match.num_players or 2)

    if num_players == 3:
        if stake == 2: winner_prize, system_fee, loser_loss = 4, 2, 2
        elif stake == 4: winner_prize, system_fee, loser_loss = 8, 4, 4
        elif stake == 6: winner_prize, system_fee, loser_loss = 12, 6, 6
        else: winner_prize, system_fee, loser_loss = stake * 2, stake, stake
    else:
        if stake == 2: winner_prize, system_fee, loser_loss = 3, 1, 2
        elif stake == 4: winner_prize, system_fee, loser_loss = 6, 2, 4
        elif stake == 6: winner_prize, system_fee, loser_loss = 9, 3, 6
        else: winner_prize, system_fee, loser_loss = int(stake * 0.75), int(stake * 0.25), stake

    players = [match.p1_user_id, match.p2_user_id]
    if num_players == 3:
        players.append(match.p3_user_id)

    winner_id = players[winner_idx]
    winner = _lock_user(db, winner_id)

    # Deduct from losers
    for i, uid in enumerate(players):
        if i == winner_idx or not uid:
            continue
        loser = _lock_user(db, uid)
        old_balance = float(loser.wallet_balance or 0)
        new_balance = max(0, old_balance - loser_loss)
        loser.wallet_balance = new_balance
        _log_transaction(db, loser.id, -loser_loss, TxType.WITHDRAW, TxStatus.SUCCESS,
                         note=f"Match #{match.id} Loss")

    # Credit winner
    old_balance = float(winner.wallet_balance or 0)
    winner.wallet_balance = old_balance + winner_prize
    _log_transaction(db, winner.id, winner_prize, TxType.WIN, TxStatus.SUCCESS,
                     note=f"Match #{match.id} Win")

    # Merchant fee (virtual)
    _log_transaction(db, 0, system_fee, TxType.FEE, TxStatus.SUCCESS,
                     note=f"Match #{match.id} System Fee")

    match.system_fee = system_fee
    match.winner_user_id = winner_id
    match.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(match)
