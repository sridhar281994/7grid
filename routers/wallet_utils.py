from sqlalchemy.orm import Session
from sqlalchemy import select, text
from datetime import datetime, timezone
import uuid
from decimal import Decimal

from models import User, GameMatch, WalletTransaction, TxType, TxStatus, MatchStatus


def _log_transaction(db: Session, user_id: int, amount: float,
                     tx_type: TxType, status: TxStatus, note=None):
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
        timestamp=datetime.utcnow(),
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


def _get_stake_rule_for_match(db: Session, match: GameMatch):
    """
    Read stake rule from the stakes table based on match.stake_amount
    and match.num_players (2 or 3).

    stakes schema:
      stake_amount, entry_fee, winner_payout, players, label
    """
    row = db.execute(
        text(
            """
            SELECT stake_amount, entry_fee, winner_payout, players, label
            FROM stakes
            WHERE stake_amount = :amt AND players = :p
            """
        ),
        {"amt": int(match.stake_amount), "p": int(match.num_players or 2)},
    ).mappings().first()

    if not row:
        return None

    return {
        "stake_amount": int(row["stake_amount"]),
        "entry_fee": Decimal(row["entry_fee"]),
        "winner_payout": Decimal(row["winner_payout"]),
        "players": int(row["players"]),
        "label": row["label"],
    }


# --------------------------------------------------
# DISTRIBUTE PRIZE (uses stakes + system_fee)
# --------------------------------------------------
async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    """
    Final prize logic using stakes table.

    For a given stake row:
      - Each active human player pays `entry_fee`
      - Winner receives `winner_payout`
      - System fee = (entry_fee * active_players) - winner_payout
      - match.system_fee is recorded and, if merchant_user_id is set,
        the fee is credited to that user.
    """
    rule = _get_stake_rule_for_match(db, match)
    if not rule:
        raise RuntimeError(f"No stake rule for stake={match.stake_amount}, players={match.num_players}")

    entry_fee: Decimal = rule["entry_fee"]
    winner_payout: Decimal = rule["winner_payout"]
    expected_players: int = rule["players"]

    # Player ID list (may contain None or bot IDs)
    slots = [match.p1_user_id, match.p2_user_id, match.p3_user_id][:expected_players]

    if winner_idx < 0 or winner_idx >= len(slots) or slots[winner_idx] is None:
        raise RuntimeError("Invalid winner index for this match")

    # Ignore bots / empty slots when doing money moves
    active_ids = [uid for uid in slots if uid is not None and uid > 0]
    if not active_ids:
        raise RuntimeError("No active payable players in match")

    winner_id = slots[winner_idx]
    if winner_id not in active_ids:
        raise RuntimeError("Winner is not an active payable player")

    # Money math
    total_collected: Decimal = entry_fee * Decimal(len(active_ids))
    system_fee: Decimal = total_collected - winner_payout
    if system_fee < 0:
        system_fee = Decimal("0")

    # --------------------------
    # Winner update
    # --------------------------
    winner = db.query(User).filter(User.id == winner_id).first()
    if not winner:
        raise RuntimeError("Winner user not found")

    winner.wallet_balance = (winner.wallet_balance or Decimal("0")) + winner_payout
    _log_transaction(
        db,
        winner.id,
        float(winner_payout),
        TxType.WIN,
        TxStatus.SUCCESS,
        note=f"Match {match.id} win",
    )

    # --------------------------
    # Losers update
    # --------------------------
    for uid in active_ids:
        if uid == winner_id:
            continue
        user = db.query(User).filter(User.id == uid).first()
        if not user:
            continue

        user.wallet_balance = (user.wallet_balance or Decimal("0")) - entry_fee
        _log_transaction(
            db,
            user.id,
            float(entry_fee),
            TxType.ENTRY,
            TxStatus.SUCCESS,
            note=f"Match {match.id} entry",
        )

    # --------------------------
    # System fee → Merchant (if set)
    # --------------------------
    if system_fee > 0 and match.merchant_user_id:
        merchant = db.query(User).filter(User.id == match.merchant_user_id).first()
        if merchant:
            merchant.wallet_balance = (merchant.wallet_balance or Decimal("0")) + system_fee
            _log_transaction(
                db,
                merchant.id,
                float(system_fee),
                TxType.FEE,
                TxStatus.SUCCESS,
                note=f"Match {match.id} system fee",
            )

    # --------------------------
    # Finish match
    # --------------------------
    match.system_fee = system_fee
    match.winner_user_id = winner_id
    match.status = MatchStatus.FINISHED
    match.finished_at = datetime.now(timezone=timezone.utc)

    db.commit()
