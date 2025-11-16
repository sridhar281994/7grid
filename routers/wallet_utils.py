from sqlalchemy.orm import Session
from sqlalchemy import text
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


def _log_transaction(db: Session, user_id: int, amount: float,
                     tx_type: TxType, status: TxStatus, note=None):
    """
    Create a wallet transaction and commit.
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
    return tx


def _get_stake_rule_for_match(db: Session, match: GameMatch):
    """
    Read stake rule from stakes table based on stake_amount + players.
    """
    row = db.execute(
        text("""
            SELECT stake_amount, entry_fee, winner_payout, players, label
            FROM stakes
            WHERE stake_amount = :amt AND players = :p
        """),
        {"amt": int(match.stake_amount), "p": int(match.num_players or 2)}
    ).mappings().first()

    if not row:
        return None

    return {
        "stake_amount": int(row["stake_amount"]),
        "entry_fee": Decimal(str(row["entry_fee"])),
        "winner_payout": Decimal(str(row["winner_payout"])),
        "players": int(row["players"]),
        "label": row["label"],
    }


async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    """
    OPTION B — Entry fee charged at join (only once).
    End of match:
        - Winner gets winner_payout
        - Losers pay nothing more
        - Merchant gets system_fee = (entry_fee * active_humans) - winner_payout
    """
    rule = _get_stake_rule_for_match(db, match)
    if not rule:
        raise RuntimeError("Missing stake rule")

    entry_fee = rule["entry_fee"]
    winner_payout = rule["winner_payout"]
    expected_players = rule["players"]

    # Determine players from match
    slots = [match.p1_user_id, match.p2_user_id, match.p3_user_id][:expected_players]

    # Active payable players (exclude bots)
    active_ids = [uid for uid in slots if uid is not None and uid > 0]

    if winner_idx < 0 or winner_idx >= len(slots):
        raise RuntimeError("Invalid winner index")

    winner_id = slots[winner_idx]
    if winner_id not in active_ids:
        raise RuntimeError("Winner is not an active human")

    # ENTRY FEE ALREADY PAID — no loser deduction here.
    total_collected = entry_fee * Decimal(len(active_ids))
    system_fee = total_collected - winner_payout
    if system_fee < 0:
        system_fee = Decimal("0")

    # --------------------------
    # WINNER CREDIT
    # --------------------------
    winner = db.query(User).filter(User.id == winner_id).first()
    winner.wallet_balance = (winner.wallet_balance or Decimal("0")) + winner_payout

    _log_transaction(
        db,
        winner.id,
        float(winner_payout),
        TxType.WIN,
        TxStatus.SUCCESS,
        note=f"Match {match.id} win"
    )

    # --------------------------
    # LOSERS — NO DEDUCTION
    # --------------------------
    # (Do nothing here)

    # --------------------------
    # MERCHANT SYSTEM FEE
    # --------------------------
    if match.merchant_user_id and system_fee > 0:
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
    # FINALIZE MATCH
    # --------------------------
    match.system_fee = system_fee
    match.winner_user_id = winner_id
    match.status = MatchStatus.FINISHED
    match.finished_at = datetime.now(timezone.utc)

    db.commit()
