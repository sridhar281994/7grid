from sqlalchemy.orm import Session
from sqlalchemy import select, text
from datetime import datetime
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


def _lock_user(db: Session, user_id: int) -> User:
    """
    Lock a user row FOR UPDATE and return it (or None).
    """
    return db.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()


def _get_stake_rule_for_match(db: Session, match: GameMatch):
    """
    Read stake rule from the stakes table based on match.stake_amount
    and match.num_players (2 or 3), same schema as used in game.py:

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
# DISTRIBUTE PRIZE (FIXED TO USE STAKES TABLE)
# --------------------------------------------------
async def distribute_prize(db: Session, match: GameMatch, winner_idx: int):
    """
    Distribute prize for a finished match.

    IMPORTANT:
    - Entry fee has ALREADY been deducted in /game/request (and/or match join).
    - Here we ONLY:
        * Credit the winner with winner_payout from stakes table
        * Record system fee = sum(entry_fee for all players) - winner_payout
        * DO NOT deduct again from losers (no double-charging).
    """
    num_players = int(match.num_players or 2)
    stake = int(match.stake_amount)

    # --- Determine players in slot order ---
    players = [match.p1_user_id, match.p2_user_id]
    if num_players == 3:
        players.append(match.p3_user_id)

    winner_user_id = players[winner_idx]

    # --- Load stake rule from DB (align with /game/request logic) ---
    rule = _get_stake_rule_for_match(db, match)

    if rule:
        entry_fee = rule["entry_fee"]          # Decimal
        winner_payout = rule["winner_payout"]  # Decimal

        total_entry = entry_fee * Decimal(num_players)
        system_fee_amount = total_entry - winner_payout
        if system_fee_amount < 0:
            # Safety: never negative system fee
            system_fee_amount = Decimal(0)
    else:
        # Fallback if stakes row missing: keep old-ish behavior but WITHOUT
        # touching losers again. Winner gets stake * players, fee = 0.
        entry_fee = Decimal(stake)
        winner_payout = Decimal(stake * num_players)
        system_fee_amount = Decimal(0)

    # --- CREDIT WINNER (no second loss for losers) ---
    winner = _lock_user(db, winner_user_id)
    if not winner:
        # Should never happen, but don't crash whole match
        return

    old_balance = Decimal(winner.wallet_balance or 0)
    new_balance = old_balance + winner_payout
    winner.wallet_balance = float(new_balance)

    _log_transaction(
        db,
        winner.id,
        float(winner_payout),
        TxType.WIN,
        TxStatus.SUCCESS,
        f"Match {match.id} Win (stake={stake}, players={num_players})",
    )

    # --- SYSTEM FEE RECORD ---
    if system_fee_amount > 0:
        _log_transaction(
            db,
            0,  # system / house account
            float(system_fee_amount),
            TxType.FEE,
            TxStatus.SUCCESS,
            f"Match {match.id} Fee",
        )

    # --- UPDATE MATCH RECORD CORRECTLY ---
    match.system_fee = float(system_fee_amount)
    match.winner_user_id = winner_user_id
    match.status = MatchStatus.FINISHED
    match.finished_at = datetime.utcnow()

    db.commit()
    db.refresh(match)
