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
# Prize Distribution (Points-Based)
# -------------------------
async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    """
    Distribute winnings when a match finishes.

    2 Player Game:
      - ₹4 game: each pays 2, winner gets 3, merchant gets 1
      - ₹8 game: each pays 4, winner gets 6, merchant gets 2
      - ₹12 game: each pays 6, winner gets 9, merchant gets 3

    3 Player Game:
      - ₹4 game: each pays 2, winner gets 4, merchant gets 2
      - ₹8 game: each pays 4, winner gets 8, merchant gets 4
      - ₹12 game: each pays 6, winner gets 12, merchant gets 6
    """
    stake = match.stake_amount
    expected_players = match.num_players or 2
    merchant_user_id = 1  # 🔹 System/Merchant user for audit logs (no real wallet used)

    # --- Default logic ---
    if expected_players == 2:
        if stake == 4:
            winner_prize, system_fee = 3, 1
        elif stake == 8:
            winner_prize, system_fee = 6, 2
        elif stake == 12:
            winner_prize, system_fee = 9, 3
        else:
            winner_prize = (stake * 3) // 4
            system_fee = stake // 4

    elif expected_players == 3:
        if stake == 4:
            winner_prize, system_fee = 4, 2
        elif stake == 8:
            winner_prize, system_fee = 8, 4
        elif stake == 12:
            winner_prize, system_fee = 12, 6
        else:
            winner_prize = stake
            system_fee = stake // 2
    else:
        # fallback
        winner_prize = (stake * 3) // 4
        system_fee = stake // 4

    # --- Identify Winner ---
    winner_id = None
    if winner_idx == 0 and match.p1_user_id:
        winner_id = match.p1_user_id
    elif winner_idx == 1 and match.p2_user_id:
        winner_id = match.p2_user_id
    elif winner_idx == 2 and match.p3_user_id:
        winner_id = match.p3_user_id

    if not winner_id:
        print(f"[ERROR] distribute_prize: Invalid winner index {winner_idx} for match {match.id}")
        return

    # --- Simulate point addition for winner ---
    winner = _lock_user(db, winner_id)
    if winner:
        old_balance = float(winner.wallet_balance or 0)
        new_balance = old_balance + winner_prize
        winner.wallet_balance = new_balance
        _log_transaction(db, winner.id, winner_prize, TxType.RECHARGE, TxStatus.SUCCESS, note="Match Win (Points)")
        print(f"[DEBUG] Winner Points Added: user_id={winner.id}, old={old_balance}, prize={winner_prize}, new={new_balance}")

    # --- Log Merchant Cut for auditing ---
    _log_transaction(db, merchant_user_id, system_fee, TxType.RECHARGE, TxStatus.SUCCESS, note=f"System Fee (Match {match.id})")
    print(f"[AUDIT] Merchant Fee Logged: match_id={match.id}, fee={system_fee}, merchant_user={merchant_user_id}")

    # --- Update Match Record ---
    match.system_fee = system_fee
    match.winner_user_id = winner.id if winner else None
    match.finished_at = datetime.utcnow()

    db.commit()
    db.refresh(match)
    print(f"[DEBUG] Prize distribution complete: match_id={match.id}, players={expected_players}, fee={system_fee}")


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
