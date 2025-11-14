# wallet_utils.py

from sqlalchemy.orm import Session
from sqlalchemy import select, text
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from models import (
    User,
    GameMatch,
    WalletTransaction,
    TxType,
    TxStatus,
    MatchStatus,
)

# --------------------------------------------------
# Helpers
# --------------------------------------------------

def _log_transaction(
    db: Session,
    user_id: int,
    amount: float,
    tx_type: TxType,
    status: TxStatus,
    note: str | None = None,
):
    """
    Create a wallet transaction row and commit immediately.
    """
    tx = WalletTransaction(
        user_id=user_id,
        amount=amount,
        tx_type=tx_type,
        status=status,
        provider_ref=note,
        transaction_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


def _lock_user(db: Session, user_id: int) -> User | None:
    """
    Lock a user row FOR UPDATE and return it (or None).
    """
    return db.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()


def get_stake_rule(db: Session, stake_amount: int, players: int) -> dict | None:
    """
    Read stake rule from the stakes table based on stake_amount AND players.

    stakes schema (as per your data):
        id, stake_amount, entry_fee, winner_payout, players, label

    Example row (2-player, stake 2):
        stake_amount = 2
        entry_fee    = 2
        winner_payout= 3
        players      = 2
        label        = 'Dual Rush'

    System fee is derived as:
        system_fee = entry_fee * players - winner_payout
    """
    row = db.execute(
        text(
            """
            SELECT stake_amount, entry_fee, winner_payout, players, label
            FROM stakes
            WHERE stake_amount = :amt AND players = :p
            """
        ),
        {"amt": int(stake_amount), "p": int(players)},
    ).mappings().first()

    if not row:
        return None

    entry_fee = int(row["entry_fee"])
    player_count = int(row["players"])
    winner_payout = int(row["winner_payout"])
    total_entry = entry_fee * player_count
    system_fee = total_entry - winner_payout

    return {
        "stake_amount": int(row["stake_amount"]),
        "entry_fee": entry_fee,
        "winner_payout": winner_payout,
        "players": player_count,
        "label": row["label"],
        "system_fee": system_fee,
    }


# --------------------------------------------------
# DISTRIBUTE PRIZE
# --------------------------------------------------
async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    """
    Final prize distribution for 2-player / 3-player matches.

    Assumptions:
      * Each player has already paid entry_fee at JOIN time.
      * stakes table defines:
            stake_amount, entry_fee, winner_payout, players, label
      * System fee is:
            system_fee = entry_fee * players - winner_payout

    Behaviour:
      * Winner gets winner_payout
      * Merchant gets system_fee (from match.merchant_user_id or default admin=1)
      * Losers are NOT touched here – their loss is the entry_fee already deducted.
    """

    num_players = match.num_players or 2

    # Get stake rule
    rule = get_stake_rule(db, int(match.stake_amount), int(num_players))
    if not rule:
        # Fallback: just mark winner; no money moves
        players = [match.p1_user_id, match.p2_user_id, match.p3_user_id][:num_players]
        winner_uid = players[winner_idx]
        match.winner_user_id = winner_uid
        match.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    entry_fee = rule["entry_fee"]
    winner_payout = rule["winner_payout"]
    player_count = rule["players"]
    system_fee = rule["system_fee"]

    # Sanity: clamp to actual match slots
    players = [match.p1_user_id, match.p2_user_id, match.p3_user_id][:player_count]
    if winner_idx < 0 or winner_idx >= len(players):
        # invalid index → just mark finished, no distribution
        match.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    winner_uid = players[winner_idx]

    # --------------------------
    # Winner credit
    # --------------------------
    winner = db.query(User).filter(User.id == winner_uid).first()
    if winner:
        before = winner.wallet_balance or 0
        winner.wallet_balance = before + Decimal(winner_payout)
        after = winner.wallet_balance
        _log_transaction(
            db,
            user_id=winner_uid,
            amount=float(winner_payout),
            tx_type=TxType.WIN,
            status=TxStatus.SUCCESS,
            note=f"Match {match.id} win payout",
        )

    # --------------------------
    # System fee → Merchant wallet
    # --------------------------
    if system_fee > 0:
        merchant_id = match.merchant_user_id or 1  # default admin ID = 1
        merchant = db.query(User).filter(User.id == merchant_id).first()
        if merchant:
            before = merchant.wallet_balance or 0
            merchant.wallet_balance = before + Decimal(system_fee)
            after = merchant.wallet_balance
            _log_transaction(
                db,
                user_id=merchant_id,
                amount=float(system_fee),
                tx_type=TxType.FEE,
                status=TxStatus.SUCCESS,
                note=f"Match {match.id} system fee",
            )

    # --------------------------
    # Finish match
    # --------------------------
    match.winner_user_id = winner_uid
    match.system_fee = Decimal(system_fee)
    match.finished_at = datetime.now(timezone.utc)

    db.commit()
