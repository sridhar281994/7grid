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
# Prize Distribution (Stake 2 / 4 / 6 Logic)
# -------------------------
async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    """
    Updated prize logic (stakes 2, 4, 6):

    3-player:
      2rs game → each pays 2 → winner gets 4, merchant gets 2
      4rs game → each pays 4 → winner gets 8, merchant gets 4
      6rs game → each pays 6 → winner gets 12, merchant gets 6

    2-player:
      2rs game → loser pays 2 → winner gets 3, merchant gets 1
      4rs game → loser pays 4 → winner gets 6, merchant gets 2
      6rs game → loser pays 6 → winner gets 9, merchant gets 3
    """
    stake = int(match.stake_amount or 0)
    num_players = int(match.num_players or 2)

    # Define distribution per new structure
    if num_players == 3:
        if stake == 2:
            winner_prize, system_fee, loser_loss = 4, 2, 2
        elif stake == 4:
            winner_prize, system_fee, loser_loss = 8, 4, 4
        elif stake == 6:
            winner_prize, system_fee, loser_loss = 12, 6, 6
        else:
            winner_prize, system_fee, loser_loss = int(stake * 1.0), int(stake * 0.5), int(stake / (num_players - 1))
    else:  # 2-player
        if stake == 2:
            winner_prize, system_fee, loser_loss = 3, 1, 2
        elif stake == 4:
            winner_prize, system_fee, loser_loss = 6, 2, 4
        elif stake == 6:
            winner_prize, system_fee, loser_loss = 9, 3, 6
        else:
            winner_prize, system_fee, loser_loss = int(stake * 0.75), int(stake * 0.25), int(stake / (num_players - 1))

    # Identify players
    players = [match.p1_user_id, match.p2_user_id]
    if num_players == 3:
        players.append(match.p3_user_id)

    if winner_idx < 0 or winner_idx >= len(players):
        print(f"[WARN] distribute_prize: invalid winner index {winner_idx}")
        return

    winner_id = players[winner_idx]
    winner = _lock_user(db, winner_id)
    if not winner:
        print(f"[ERROR] Winner not found for match {match.id}")
        return

    # Deduct from all losers
    for i, uid in enumerate(players):
        if not uid or i == winner_idx:
            continue
        loser = _lock_user(db, uid)
        if not loser:
            continue
        old_balance = float(loser.wallet_balance or 0)
        new_balance = max(0, old_balance - loser_loss)
        loser.wallet_balance = new_balance
        _log_transaction(db, loser.id, -loser_loss, TxType.WITHDRAW, TxStatus.SUCCESS,
                         note=f"Match #{match.id} Loss")
        print(f"[LOSER] user={loser.id}, -{loser_loss}, balance {old_balance}→{new_balance}")

    # Credit winner
    old_balance = float(winner.wallet_balance or 0)
    winner.wallet_balance = old_balance + float(winner_prize)
    _log_transaction(db, winner.id, winner_prize, TxType.WIN, TxStatus.SUCCESS,
                     note=f"Match #{match.id} Win")
    print(f"[WINNER] user={winner.id}, +{winner_prize}, balance {old_balance}→{winner.wallet_balance}")

    # Log merchant fee (virtual, no real account)
    _log_transaction(db, 0, system_fee, TxType.FEE, TxStatus.SUCCESS,
                     note=f"Match #{match.id} System Fee (Virtual Merchant)")
    print(f"[MERCHANT] +{system_fee} fee logged for audit")

    # Update match record
    match.system_fee = system_fee
    match.winner_user_id = winner.id
    match.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(match)

    print(f"[DISTRIBUTE] Completed for match {match.id}")


# -------------------------
# Refund Stake
# -------------------------
async def refund_stake(db: Session, match: GameMatch):
    """Refund entry fee to all players if the match is cancelled before completion."""
    stake = match.stake_amount
    expected_players = match.num_players or 2
    entry_fee = stake // expected_players

    # Player 1
    if match.p1_user_id:
        p1 = _lock_user(db, match.p1_user_id)
        if p1:
            p1.wallet_balance = (p1.wallet_balance or 0) + entry_fee
            _log_transaction(db, p1.id, entry_fee, TxType.RECHARGE, TxStatus.SUCCESS, note="Refund")

    # Player 2
    if match.p2_user_id:
        p2 = _lock_user(db, match.p2_user_id)
        if p2:
            p2.wallet_balance = (p2.wallet_balance or 0) + entry_fee
            _log_transaction(db, p2.id, entry_fee, TxType.RECHARGE, TxStatus.SUCCESS, note="Refund")

    # Player 3 (optional)
    if expected_players == 3 and match.p3_user_id:
        p3 = _lock_user(db, match.p3_user_id)
        if p3:
            p3.wallet_balance = (p3.wallet_balance or 0) + entry_fee
            _log_transaction(db, p3.id, entry_fee, TxType.RECHARGE, TxStatus.SUCCESS, note="Refund")

    match.status = match.status or "cancelled"
    match.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(match)
    print(f"[DEBUG] Refund processed for match {match.id} ({expected_players} players, fee={entry_fee})")
