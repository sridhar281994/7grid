from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, ForeignKey, Numeric, Enum, text as sa_text
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from database import Base


# -----------------------
# Enums
# -----------------------
class MatchStatus(enum.Enum):
    WAITING = "waiting"
    ACTIVE = "active"
    FINISHED = "finished"
    ABANDONED = "abandoned"


class TxType(enum.Enum):
    RECHARGE = "recharge"
    WITHDRAW = "withdraw"
    ENTRY = "entry"
    WIN = "win"
    FEE = "fee"


class TxStatus(enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


# -----------------------
# User Table
# -----------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)

    name = Column(String, nullable=True)
    upi_id = Column(String, nullable=True)
    description = Column(String(50), nullable=True)
    wallet_balance = Column(Numeric(10, 2), default=0)

    # ✅ New column
    profile_image = Column(String, nullable=True, default="assets/default.png")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    matches_as_p1 = relationship("GameMatch", foreign_keys="GameMatch.p1_user_id", back_populates="player1")
    matches_as_p2 = relationship("GameMatch", foreign_keys="GameMatch.p2_user_id", back_populates="player2")
    matches_as_p3 = relationship("GameMatch", foreign_keys="GameMatch.p3_user_id", back_populates="player3")
    transactions = relationship("WalletTransaction", back_populates="user")


# -----------------------
# OTP Table
# -----------------------
class OTP(Base):
    __tablename__ = "otps"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, nullable=False, index=True)
    code = Column(String, nullable=False)
    used = Column(Boolean, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# -----------------------
# Matchmaking Table
# -----------------------
class GameMatch(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    stake_amount = Column(Integer, nullable=False)

    # :busts_in_silhouette: Player slots
    p1_user_id = Column(Integer, ForeignKey("users.id"))
    p2_user_id = Column(Integer, ForeignKey("users.id"))
    p3_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # 🏆 Winner & Merchant
    winner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    merchant_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # ✅ new column

    status = Column(Enum(MatchStatus), default=MatchStatus.WAITING, nullable=False)
    system_fee = Column(Numeric(10, 2), default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)

    last_roll = Column(Integer, nullable=True)
    current_turn = Column(Integer, nullable=True)  # 0 = P1, 1 = P2, 2 = P3

    # :fire: NEW → track whether this match is 2-player or 3-player
    num_players = Column(Integer, nullable=False, default=2)

    # :moneybag: NEW → mark whether entry fee is refundable (waiting only)
    refundable = Column(Boolean, nullable=False, server_default=sa_text("true"))

    # Relationships
    player1 = relationship("User", foreign_keys=[p1_user_id], back_populates="matches_as_p1")
    player2 = relationship("User", foreign_keys=[p2_user_id], back_populates="matches_as_p2")
    player3 = relationship("User", foreign_keys=[p3_user_id], back_populates="matches_as_p3")
    winner = relationship("User", foreign_keys=[winner_user_id])
    merchant = relationship("User", foreign_keys=[merchant_user_id])  # ✅ linked relationship


# -----------------------
# Wallet Transactions
# -----------------------
class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)

    tx_type = Column(Enum(TxType), nullable=False)
    status = Column(Enum(TxStatus), default=TxStatus.PENDING, nullable=False)

    provider_ref = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    transaction_id = Column(String, unique=True, nullable=True)

    user = relationship("User", back_populates="transactions")


# -----------------------
# Stakes Table
# -----------------------
class Stake(Base):
    __tablename__ = "stakes"

    id = Column(Integer, primary_key=True, index=True)
    stake_amount = Column(Integer, unique=True, nullable=False) # stage key
    entry_fee = Column(Integer, nullable=False) # each player pays
    winner_payout = Column(Integer, nullable=False) # winner gets
    label = Column(String(50), nullable=False) # UI label
