from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, ForeignKey, Numeric, Enum
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
import enum

Base = declarative_base()


# -----------------------
# Enums
# -----------------------
class MatchStatus(enum.Enum):
    WAITING = "waiting"
    ACTIVE = "active"
    FINISHED = "finished"


class TxType(enum.Enum):
    RECHARGE = "recharge"
    WITHDRAW = "withdraw"


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
    upi_id = Column(String, nullable=True)
    wallet_balance = Column(Numeric(10, 2), default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    matches_as_p1 = relationship("GameMatch", foreign_keys="GameMatch.p1_user_id", back_populates="player1")
    matches_as_p2 = relationship("GameMatch", foreign_keys="GameMatch.p2_user_id", back_populates="player2")
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
    p1_user_id = Column(Integer, ForeignKey("users.id"))
    p2_user_id = Column(Integer, ForeignKey("users.id"))
    winner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(Enum(MatchStatus), default=MatchStatus.WAITING, nullable=False)
    system_fee = Column(Numeric(10, 2), default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    player1 = relationship("User", foreign_keys=[p1_user_id], back_populates="matches_as_p1")
    player2 = relationship("User", foreign_keys=[p2_user_id], back_populates="matches_as_p2")
    winner = relationship("User", foreign_keys=[winner_user_id])


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
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    transaction_id = Column(String, unique=True, nullable=True)
    user = relationship("User", back_populates="transactions")
