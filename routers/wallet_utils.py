from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime
import uuid

from models import User, GameMatch, WalletTransaction, TxType, TxStatus, MatchStatus


def _log_transaction(db: Session, user_id: int, amount: float, tx_type: TxType, status: TxStatus, note=None):
    tx = WalletTransaction(
        user_id=user_id,
        amount=amount,
        tx_type=tx_type,
        status=status,
        provider_ref=note,
        transaction_id=str(uuid.uuid4()),
    )
    db.add(tx)
    db.flush()
    return tx


def _lock_user(db: Session, user_id: int) -> User:
    """Lock a user row FOR UPDATE and return it."""
    return db.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one()


async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    """
    Distribute prize & system fee for a finished match.

    - Supports 2-player and 3-player matches.
    - Uses your requested hard-coded payout rules (2,4,6 etc).
    - Logs wallet transactions for winner, losers, and system fee.
    """

    stake = int(match.stake_amount)
    num_players = int(match.num_players)

    # Optional: load rule from DB (currently unused, but kept for future)
    _ = db.execute(
        select(WalletTransaction).where(WalletTransaction.id == -1)
    )

    # ---- Payout logic ----
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
                db,
                loser.id,
                -float(loser_loss),
                TxType.MATCH_LOSS,
                TxStatus.SUCCESS,
                f"Match {match.id} Loss",
            )

    # Credit winner
    old_winner_bal = winner.wallet_balance or 0
    new_winner_bal = old_winner_bal + winner_prize
    winner.wallet_balance = new_winner_bal

    _log_transaction(
        db,
        winner.id,
        float(winner_prize),
        TxType.WIN,
        TxStatus.SUCCESS,
        f"Match {match.id} Win",
    )

    # System fee
    _log_transaction(
        db,
        0,
        float(system_fee),
        TxType.FEE,
        TxStatus.SUCCESS,
        f"Match {match.id} Fee",
    )

    # Update match record correctly using enum
    match.system_fee = float(system_fee)
    match.winner_user_id = winner_id
    match.status = MatchStatus.FINISHED
    match.finished_at = datetime.utcnow()

    db.commit()
    db.refresh(match)
