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
    FIXED OPTION B — each player paid entry_fee earlier.
    On finish:
        - Winner receives FULL POT = entry_fee * active_humans
        - Merchant system_fee is ALWAYS 0 (unless you re-enable commission)
    """

    rule = _get_stake_rule_for_match(db, match)
    if not rule:
        raise RuntimeError("Missing stake rule")

    entry_fee = Decimal(rule["entry_fee"])
    expected_players = rule["players"]

    # Determine player slots
    slots = [match.p1_user_id, match.p2_user_id, match.p3_user_id][:expected_players]

    # Active paying players (exclude bots)
    active_ids = [uid for uid in slots if uid is not None and uid > 0]

    if winner_idx < 0 or winner_idx >= len(slots):
        raise RuntimeError("Invalid winner index")

    winner_id = slots[winner_idx]
    if winner_id not in active_ids:
        raise RuntimeError("Winner is not an active human")

    # ------------------------------------------
    # POT CALCULATION  (main fix)
    # ------------------------------------------
    pot = entry_fee * Decimal(len(active_ids))     #  <-- WINNER GETS FULL POT
    system_fee = Decimal("0")                      #  <-- for your model (no commission)

    # ------------------------------------------
    # WINNER CREDIT
    # ------------------------------------------
    winner = db.query(User).filter(User.id == winner_id).first()
    winner.wallet_balance = (winner.wallet_balance or Decimal("0")) + pot

    _log_transaction(
        db,
        winner.id,
        float(pot),
        TxType.WIN,
        TxStatus.SUCCESS,
        note=f"Match {match.id} win"
    )

    # ------------------------------------------
    # MERCHANT FEE DISABLED (set to 0)
    # ------------------------------------------
    # If you want to enable fee, change system_fee above.

    # ------------------------------------------
    # FINALIZE MATCH
    # ------------------------------------------
    match.system_fee = system_fee
    match.winner_user_id = winner_id
    match.status = MatchStatus.FINISHED
    match.finished_at = datetime.now(timezone.utc)

    db.commit()
