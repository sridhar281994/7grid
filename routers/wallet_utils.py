# wallet_utils.py

from datetime import datetime, timezone
from decimal import Decimal
import uuid

from sqlalchemy.orm import Session
from sqlalchemy import select, text

from models import User, GameMatch, WalletTransaction, TxType, TxStatus, MatchStatus


def _to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _log_transaction(
    db: Session,
    user_id: int,
    amount,
    tx_type: TxType,
    status: TxStatus,
    note: str | None = None,
):
    """
    Create a wallet transaction row and commit immediately.
    """
    amount_dec = _to_decimal(amount)

    tx = WalletTransaction(
        user_id=user_id,
        amount=amount_dec,
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


def _lock_user(db: Session, user_id: int | None) -> User | None:
    """
    Lock a user row FOR UPDATE and return it (or None).
    """
    if not user_id or user_id <= 0:
        return None

    return (
        db.execute(select(User).where(User.id == user_id).with_for_update())
        .scalar_one_or_none()
    )


def get_stake_rule(db: Session, stake_amount: int, players: int) -> dict | None:
    """
    Read stake rule from the stakes table based on:
        stake_amount (stage key) AND players (2 or 3)

    stakes table structure (per your DB):
        stake_amount | entry_fee | winner_payout | players | label
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

    return {
        "stake_amount": int(row["stake_amount"]),
        "entry_fee": _to_decimal(row["entry_fee"]),
        "winner_payout": _to_decimal(row["winner_payout"]),
        "players": int(row["players"]),
        "label": row["label"],
    }


def _get_stake_rule_for_match(db: Session, match: GameMatch) -> dict | None:
    return get_stake_rule(db, match.stake_amount, match.num_players or 2)


def distribute_prize(db: Session, match: GameMatch, winner_idx: int) -> None:
    """
    Final, simple prize logic.

    Assumptions:
    - Each human player was already charged `entry_fee` when they joined the match
      (in /matches/create or /game/request).
    - stakes table defines:
          entry_fee      → amount each player pays to enter
          winner_payout  → what the winner should receive
          players        → 2 or 3
    - System fee = total_collected - winner_payout
                 = entry_fee * players - winner_payout

    What this does:
      - Marks the match as FINISHED.
      - Credits winner with winner_payout.
      - Credits merchant with system_fee.
      - Does NOT touch loser balances again (they already lost entry_fee).
    """

    # Free play → just mark finished, no money movement.
    if match.stake_amount == 0:
        match.status = MatchStatus.FINISHED
        match.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    rule = _get_stake_rule_for_match(db, match)
    if not rule:
        # No stake rule – safest fallback: just finish the match without wallet moves.
        match.status = MatchStatus.FINISHED
        match.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    entry_fee = rule["entry_fee"]       # Decimal
    players_count = rule["players"]     # 2 or 3
    winner_payout = rule["winner_payout"]

    if entry_fee <= 0 or players_count <= 0 or winner_payout < 0:
        match.status = MatchStatus.FINISHED
        match.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    total_collected = entry_fee * players_count
    system_fee = total_collected - winner_payout

    # Build players list based on match.num_players
    num_players = int(match.num_players or players_count)
    slots = [match.p1_user_id, match.p2_user_id, match.p3_user_id][:num_players]

    if winner_idx < 0 or winner_idx >= len(slots):
        # Invalid index – just finish without payouts instead of corrupting wallets.
        match.status = MatchStatus.FINISHED
        match.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    winner_uid = slots[winner_idx]

    # ---------- Winner credit ----------
    winner = _lock_user(db, winner_uid)
    if winner:
        before = _to_decimal(winner.wallet_balance)
        winner.wallet_balance = before + winner_payout

        _log_transaction(
            db,
            winner.id,
            winner_payout,
            TxType.WIN,
            TxStatus.SUCCESS,
            note=f"Match {match.id} win",
        )

    # ---------- Merchant (system fee) ----------
    if system_fee > 0:
        # Use match.merchant_user_id if set, else default to user id 1 as admin.
        merchant_id = match.merchant_user_id or 1
        merchant = _lock_user(db, merchant_id)

        if merchant:
            before_m = _to_decimal(merchant.wallet_balance)
            merchant.wallet_balance = before_m + system_fee

            _log_transaction(
                db,
                merchant.id,
                system_fee,
                TxType.FEE,
                TxStatus.SUCCESS,
                note=f"Match {match.id} fee",
            )

    # ---------- Match finalization ----------
    match.system_fee = system_fee
    match.winner_user_id = winner_uid
    match.status = MatchStatus.FINISHED
    match.finished_at = datetime.now(timezone.utc)

    db.commit()
