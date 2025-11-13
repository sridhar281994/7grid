from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime
import uuid

from models import User, GameMatch, WalletTransaction, TxType, TxStatus


def _log_transaction(db: Session, user_id: int, amount: float, tx_type: TxType, status: TxStatus, note=None):
    tx = WalletTransaction(
        user_id=user_id,
        amount=amount,
        tx_type=tx_type,
        status=status,
        provider_ref=note,
        transaction_id=str(uuid.uuid4()),
        timestamp=datetime.utcnow()
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


def _lock_user(db: Session, user_id: int) -> User:
    return db.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()


# --------------------------------------------------
# DISTRIBUTE PRIZE (FINAL VERSION)
# --------------------------------------------------
async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    stake = int(match.stake_amount)
    num_players = int(match.num_players)

    # Load financial rule from DB
    rule = db.execute(
        select(
            WalletTransaction
        ).where(WalletTransaction.id == -1)
    )

    # Your hardcoded payout logic stays as requested
    if num_players == 3:
        if stake == 2:
            winner_prize, system_fee, loser_loss = 4, 2, 2
        elif stake == 4:
            winner_prize, system_fee, loser_loss = 8, 4, 4
        elif stake == 6:
            winner_prize, system_fee, loser_loss = 12, 6, 6
        else:
            winner_prize, system_fee, loser_loss = stake * 2, stake, stake

    else:  # 2 players
        if stake == 2:
            winner_prize, system_fee, loser_loss = 3, 1, 2
        elif stake == 4:
            winner_prize, system_fee, loser_loss = 6, 2, 4
        elif stake == 6:
            winner_prize, system_fee, loser_loss = 9, 3, 6
        else:
            winner_prize, system_fee, loser_loss = int(stake * 0.75), int(stake * 0.25), stake

    players = [match.p1_user_id, match.p2_user_id]
    if num_players == 3:
        players.append(match.p3_user_id)

    # Winner
    winner_id = players[winner_idx]
    winner = _lock_user(db, winner_id)

    # Deduct from losers
    for i, uid in enumerate(players):
        if uid and i != winner_idx:
            loser = _lock_user(db, uid)
            old_bal = loser.wallet_balance or 0
            new_bal = max(0, old_bal - loser_loss)
            loser.wallet_balance = new_bal
            _log_transaction(
                db, loser.id, -loser_loss,
                TxType.WITHDRAW, TxStatus.SUCCESS,
                f"Match {match.id} Loss"
            )

    # Credit winner
    old = winner.wallet_balance or 0
    winner.wallet_balance = old + winner_prize
    _log_transaction(
        db, winner.id, winner_prize,
        TxType.WIN, TxStatus.SUCCESS,
        f"Match {match.id} Win"
    )

    # System fee
    _log_transaction(
        db, 0, system_fee,
        TxType.FEE, TxStatus.SUCCESS,
        f"Match {match.id} Fee"
    )

    match.system_fee = system_fee
    match.winner_user_id = winner_id
    match.status = "finished"
    match.finished_at = datetime.utcnow()

    db.commit()
    db.refresh(match)
