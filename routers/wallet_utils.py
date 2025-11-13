from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime
import uuid

from models import User, GameMatch, WalletTransaction, TxType, TxStatus


def _log_transaction(db: Session, user_id: int, amount: float, tx_type: TxType, status: TxStatus, note: str = None):
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
    return db.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()


def deduct_entry_fee(db: Session, user: User, entry_fee: int):
    if (user.wallet_balance or 0) < entry_fee:
        raise ValueError("Insufficient balance")

    user.wallet_balance = (user.wallet_balance or 0) - entry_fee
    _log_transaction(db, user.id, -entry_fee, TxType.WITHDRAW, TxStatus.SUCCESS, "Entry Fee")
    db.commit()
    db.refresh(user)


# -------------------------
# Prize Distribution Logic
# -------------------------
async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    """
    FINAL prize logic based on DB:

    2-player:
      stake=2 → +3 winner, -2 loser, fee=1
      stake=4 → +6 winner, -4 loser, fee=2
      stake=6 → +9 winner, -6 loser, fee=3

    3-player:
      stake=2 → +4 winner, -2 others, fee=2
      stake=4 → +8 winner, -4 others, fee=4
      stake=6 → +12 winner, -6 others, fee=6
    """

    stake = int(match.stake_amount or 0)
    num_players = int(match.num_players or 2)

    # -------------- 3 PLAYER --------------
    if num_players == 3:
        if stake == 2:
            winner_prize, system_fee, loser_loss = 4, 2, 2
        elif stake == 4:
            winner_prize, system_fee, loser_loss = 8, 4, 4
        elif stake == 6:
            winner_prize, system_fee, loser_loss = 12, 6, 6
        else:
            winner_prize, system_fee, loser_loss = stake * 2, stake, stake

    # -------------- 2 PLAYER --------------
    else:
        if stake == 2:
            winner_prize, system_fee, loser_loss = 3, 1, 2
        elif stake == 4:
            winner_prize, system_fee, loser_loss = 6, 2, 4
        elif stake == 6:
            winner_prize, system_fee, loser_loss = 9, 3, 6
        else:
            winner_prize, system_fee, loser_loss = int(stake * 0.75), int(stake * 0.25), stake

    # Identify all players
    players = [match.p1_user_id, match.p2_user_id]
    if num_players == 3:
        players.append(match.p3_user_id)

    winner_id = players[winner_idx]
    winner = _lock_user(db, winner_id)

    # Deduct from losers
    for i, uid in enumerate(players):
        if not uid or i == winner_idx:
            continue
        loser = _lock_user(db, uid)
        old = float(loser.wallet_balance or 0)
        new = max(0, old - loser_loss)
        loser.wallet_balance = new
        _log_transaction(db, loser.id, -loser_loss, TxType.WITHDRAW, TxStatus.SUCCESS, f"Match #{match.id} Loss")
        print(f"[LOSER] {uid} -{loser_loss} | {old}->{new}")

    # Credit winner
    old = float(winner.wallet_balance or 0)
    winner.wallet_balance = old + winner_prize
    _log_transaction(db, winner.id, winner_prize, TxType.WIN, TxStatus.SUCCESS, f"Match #{match.id} Win")
    print(f"[WINNER] {winner.id} +{winner_prize} | {old}->{winner.wallet_balance}")

    # System fee (virtual merchant)
    _log_transaction(db, 0, system_fee, TxType.FEE, TxStatus.SUCCESS, f"Match #{match.id} System Fee")

    # Update match
    match.system_fee = system_fee
    match.winner_user_id = winner.id
    match.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(match)
    print(f"[DISTRIBUTE] Completed match {match.id}")
