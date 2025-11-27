import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from models import (
    User,
    GameMatch,
    WalletTransaction,
    TxType,
    TxStatus,
    MatchStatus,
)

SYSTEM_MERCHANT_NAME = "System Merchant"
_SYSTEM_MERCHANT_ID_CACHE: int | None = None


def _get_system_merchant_id(db: Session) -> int | None:
    """
    Resolve and cache the ID for the System Merchant user.
    """
    global _SYSTEM_MERCHANT_ID_CACHE
    if _SYSTEM_MERCHANT_ID_CACHE:
        return _SYSTEM_MERCHANT_ID_CACHE

    merchant = (
        db.query(User.id)
        .filter(User.name == SYSTEM_MERCHANT_NAME)
        .first()
    )
    if merchant:
        _SYSTEM_MERCHANT_ID_CACHE = int(merchant[0])
    return _SYSTEM_MERCHANT_ID_CACHE


def get_system_merchant_id(db: Session) -> int | None:
    """
    Public helper so other modules (match creation) can fetch the merchant ID.
    """
    return _get_system_merchant_id(db)


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
        "entry_fee": int(row["entry_fee"]),
        "winner_payout": int(row["winner_payout"]),
        "players": int(row["players"]),
        "label": row["label"],
    }


async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    """
    FIXED OPTION B — each player paid entry_fee earlier.
    On finish:
        - Winner receives the configured winner_payout (capped by collected pot)
        - Remaining pot is assigned to the System Merchant (house rake)
    """

    rule = _get_stake_rule_for_match(db, match)
    if not rule:
        raise RuntimeError("Missing stake rule")

    entry_fee = rule["entry_fee"]
    winner_payout = rule["winner_payout"]
    expected_players = rule["players"]

    # Determine player slots
    slots = [match.p1_user_id, match.p2_user_id, match.p3_user_id][:expected_players]
    participant_ids = {uid for uid in slots if uid is not None}

    # Active paying players (exclude bots)
    active_ids = [uid for uid in slots if uid is not None and uid > 0]

    if winner_idx < 0 or winner_idx >= len(slots):
        raise RuntimeError("Invalid winner index")

    winner_id = slots[winner_idx]
    if winner_id not in active_ids:
        raise RuntimeError("Winner is not an active human")

    # ------------------------------------------
    # POT CALCULATION
    # ------------------------------------------
    collected_pot = entry_fee * len(active_ids)
    prize = min(winner_payout, collected_pot)
    if winner_payout > collected_pot:
        print(
            f"[WARN] Not enough collected pot (have {collected_pot}) "
            f"for payout {winner_payout}; paying collected amount."
        )
    system_fee = collected_pot - prize

    # ------------------------------------------
    # WINNER CREDIT
    # ------------------------------------------
    winner = db.query(User).filter(User.id == winner_id).first()
    current_balance = int(winner.wallet_balance or 0)
    winner.wallet_balance = current_balance + prize

    match.winner_user_id = winner_id
    match.status = MatchStatus.FINISHED
    match.finished_at = datetime.now(timezone.utc)

    _log_transaction(
        db,
        winner.id,
        float(prize),
        TxType.WIN,
        TxStatus.SUCCESS,
        note=f"Match {match.id} win"
    )

    # ------------------------------------------
    # MERCHANT FEE (house keeps the remainder)
    # ------------------------------------------
    fallback_id = _get_system_merchant_id(db)
    merchant_id = match.merchant_user_id or fallback_id
    if merchant_id in participant_ids:
        if fallback_id and fallback_id not in participant_ids:
            merchant_id = fallback_id
        else:
            print(f"[WARN] Merchant id {merchant_id} is part of match {match.id}; skipping fee credit.")
            merchant_id = None

    if merchant_id:
        match.merchant_user_id = merchant_id

    if system_fee > 0 and merchant_id:
        merchant = db.query(User).filter(User.id == merchant_id).first()
        if merchant:
            merchant_balance = int(merchant.wallet_balance or 0)
            merchant.wallet_balance = merchant_balance + system_fee
            _log_transaction(
                db,
                merchant.id,
                float(system_fee),
                TxType.FEE,
                TxStatus.SUCCESS,
                note=f"Match {match.id} fee",
            )

    # ------------------------------------------
    # FINALIZE MATCH
    # ------------------------------------------
    match.system_fee = float(system_fee)

    db.commit()
