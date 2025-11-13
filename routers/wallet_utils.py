# wallet_utils.py
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime
import uuid

from models import User, GameMatch, WalletTransaction, TxType, TxStatus


# -------------------------
# Logging
# -------------------------
def _log_transaction(db: Session, user_id: int, amount: float,
                     tx_type: TxType, status: TxStatus, note: str = None):
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


def _lock_user(db: Session, user_id: int):
    """Lock user row before wallet update."""
    return db.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()


def deduct_entry_fee(db: Session, user: User, entry_fee: int):
    """Deduct entry fee & log transaction."""
    if (user.wallet_balance or 0) < entry_fee:
        raise ValueError("Insufficient balance")

    user.wallet_balance = (user.wallet_balance or 0) - entry_fee
    _log_transaction(db, user.id, -entry_fee, TxType.WITHDRAW, TxStatus.SUCCESS,
                     note="Entry Fee")
    db.commit()
    db.refresh(user)


# ------------------------------------------------------
# ✔ FINAL PRIZE LOGIC (consistent with 2/4/6 stake rules)
# ------------------------------------------------------
async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    """
    3-player:
      stake=2 → winner +4, merchant +2, losers -2
      stake=4 → winner +8, merchant +4, losers -4
      stake=6 → winner +12, merchant +6, losers -6

    2-player:
      stake=2 → winner +3, merchant +1, loser -2
      stake=4 → winner +6, merchant +2, loser -4
      stake=6 → winner +9, merchant +3, loser -6
    """
    stake = int(match.stake_amount or 0)
    num_players = int(match.num_players or 2)

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

    # Player ids
    players = [match.p1_user_id, match.p2_user_id]
    if num_players == 3:
        players.append(match.p3_user_id)

    if winner_idx < 0 or winner_idx >= len(players):
        print(f"[WARN] distribute_prize: invalid winner idx {winner_idx}")
        return

    winner_id = players[winner_idx]
    winner = _lock_user(db, winner_id)
    if not winner:
        print(f"[ERROR] Winner not found for match {match.id}")
        return

    # Deduct from losers
    for i, uid in enumerate(players):
        if not uid or i == winner_idx:
            continue
        loser = _lock_user(db, uid)
        if not loser:
            continue

        before = float(loser.wallet_balance or 0)
        after = max(0, before - loser_loss)
        loser.wallet_balance = after

        _log_transaction(db, loser.id, -loser_loss, TxType.WITHDRAW, TxStatus.SUCCESS,
                         note=f"Match #{match.id} Loss")
        print(f"[LOSER] user={uid} -{loser_loss} | {before}→{after}")

    # Credit winner
    before = float(winner.wallet_balance or 0)
    winner.wallet_balance = before + float(winner_prize)
    _log_transaction(db, winner.id, winner_prize, TxType.WIN, TxStatus.SUCCESS,
                     note=f"Match #{match.id} Win")
    print(f"[WINNER] user={winner_id} +{winner_prize} | {before}→{winner.wallet_balance}")

    # Virtual merchant income
    _log_transaction(db, 0, system_fee, TxType.FEE, TxStatus.SUCCESS,
                     note=f"Match #{match.id} System Fee")

    # Final match update
    match.system_fee = system_fee
    match.winner_user_id = winner_id
    match.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(match)

    print(f"[DISTRIBUTE] Completed match {match.id}")


# -------------------------
# Refund Stake
# -------------------------
async def refund_stake(db: Session, match: GameMatch):
    """Refunds full stake per player if match is cancelled."""
    stake = int(match.stake_amount or 0)
    expected_players = int(match.num_players or 2)

    entry_fee = stake

    for uid in [match.p1_user_id, match.p2_user_id, match.p3_user_id]:
        if uid:
            user = _lock_user(db, uid)
            if not user:
                continue
            before = float(user.wallet_balance or 0)
            user.wallet_balance = before + entry_fee
            _log_transaction(db, uid, entry_fee, TxType.RECHARGE,
                             TxStatus.SUCCESS, note="Refund")
            print(f"[REFUND] user={uid} +{entry_fee}")

    match.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(match)
