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
async def distribute_prize(db, match: GameMatch, winner_idx: int):
    stake = match.stake_amount
    num_players = match.num_players
    entry_fee = stake // num_players if stake > 0 else 0

    players = [match.p1_user_id, match.p2_user_id, match.p3_user_id][:num_players]
    winner_uid = players[winner_idx]

    # Winner record
    winner = db.query(User).filter(User.id == winner_uid).first()
    before = winner.wallet_balance
    winner.wallet_balance += stake
    after = winner.wallet_balance

    db.add(MatchResult(
        match_id=match.id,
        user_id=winner_uid,
        is_winner=True,
        amount_change=stake,
        before_balance=before,
        after_balance=after
    ))

    # Losers
    for i, uid in enumerate(players):
        if i == winner_idx:
            continue

        user = db.query(User).filter(User.id == uid).first()
        if not user:
            continue

        before = user.wallet_balance
        user.wallet_balance -= entry_fee
        after = user.wallet_balance

        db.add(MatchResult(
            match_id=match.id,
            user_id=uid,
            is_winner=False,
            amount_change=-entry_fee,
            before_balance=before,
            after_balance=after
        ))

    match.winner_user_id = winner_uid
    match.finished_at = datetime.now(timezone.utc)

    db.commit()
