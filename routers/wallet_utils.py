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
# Prize Distribution (with Merchant Support)
# -------------------------
async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    """
    Distribute winnings when a match finishes (points-based, with merchant logging).

    2-player logic:
      - 4rs game: each pays 2 → winner gets 3, merchant gets 1
      - 8rs game: each pays 4 → winner gets 6, merchant gets 2
      - 12rs game: each pays 6 → winner gets 9, merchant gets 3

    3-player logic:
      - 4rs game: each pays 2 → winner gets 4, merchant gets 2
      - 8rs game: each pays 4 → winner gets 8, merchant gets 4
      - 12rs game: each pays 6 → winner gets 12, merchant gets 6
    """
    stake = int(match.stake_amount or 0)
    num_players = int(match.num_players or 2)

    # --- Base formula depending on num_players ---
    if num_players == 2:
        if stake == 4:
            winner_prize, system_fee = 3, 1
        elif stake == 8:
            winner_prize, system_fee = 6, 2
        elif stake == 12:
            winner_prize, system_fee = 9, 3
        else:
            winner_prize, system_fee = (stake * 3) // 4, stake // 4
    elif num_players == 3:
        if stake == 4:
            winner_prize, system_fee = 4, 2
        elif stake == 8:
            winner_prize, system_fee = 8, 4
        elif stake == 12:
            winner_prize, system_fee = 12, 6
        else:
            winner_prize, system_fee = (stake * 2) // 3, stake // 3
    else:
        winner_prize, system_fee = (stake * 3) // 4, stake // 4

    # --- Identify winner user ---
    winner_id = None
    if winner_idx == 0 and match.p1_user_id:
        winner_id = match.p1_user_id
    elif winner_idx == 1 and match.p2_user_id:
        winner_id = match.p2_user_id
    elif winner_idx == 2 and match.p3_user_id:
        winner_id = match.p3_user_id

    if not winner_id:
        print(f"[WARN] distribute_prize: no valid winner found for match {match.id}")
        return

    winner = _lock_user(db, winner_id)
    merchant_id = getattr(match, "merchant_user_id", None)

    # --- Update winner’s balance (points-based) ---
    old_balance = float(winner.wallet_balance or 0)
    winner.wallet_balance = old_balance + float(winner_prize)
    _log_transaction(db, winner.id, winner_prize, TxType.WIN, TxStatus.SUCCESS, note=f"Match #{match.id} Win")

    print(f"[PRIZE] user={winner.id}, old={old_balance}, +{winner_prize}, new={winner.wallet_balance}")

    # --- Record merchant fee (for auditing only) ---
    if merchant_id:
        merchant = _lock_user(db, merchant_id)
        if merchant:
            merchant.wallet_balance = (merchant.wallet_balance or 0) + float(system_fee)
            _log_transaction(db, merchant.id, system_fee, TxType.FEE, TxStatus.SUCCESS, note=f"Match #{match.id} Fee")
            print(f"[MERCHANT] user={merchant.id}, +{system_fee} fee")
    else:
        # Still log it for audit even if merchant_id not yet set
        _log_transaction(db, 0, system_fee, TxType.FEE, TxStatus.SUCCESS, note=f"System fee (match {match.id})")

    # --- Update match record ---
    match.system_fee = system_fee
    match.winner_user_id = winner.id
    match.finished_at = datetime.utcnow()

    db.commit()
    db.refresh(match)
    print(f"[DEBUG] distribute_prize completed for match {match.id}")


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
