from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, ForeignKey,
    Numeric, Enum, func
)
from sqlalchemy.orm import relationship
from database import Base
import enum

class OTP(Base):
    __tablename__ = "otps"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), index=True, nullable=False)
    code = Column(String(10), nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(100))
    upi_id = Column(String(120))
    wallet_balance = Column(Numeric(12, 2), nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class TxType(str, enum.Enum):
    RECHARGE = "recharge"
    WITHDRAW = "withdraw"

class TxStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"

class WalletTransaction(Base):
    __tablename__ = "wallet_tx"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    type = Column(Enum(TxType), nullable=False)
    status = Column(Enum(TxStatus), default=TxStatus.PENDING, nullable=False)
    provider_ref = Column(String(128))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    user = relationship("User")

class MatchStatus(str, enum.Enum):
    WAITING = "waiting"
    ACTIVE = "active"
    FINISHED = "finished"
    CANCELLED = "cancelled"

class GameMatch(Base):
    __tablename__ = "game_matches"
    id = Column(Integer, primary_key=True)
    stake_amount = Column(Integer, nullable=False)  # 4 / 8 / 12 (points == rupees)
    status = Column(Enum(MatchStatus), default=MatchStatus.WAITING, nullable=False)

    p1_user_id = Column(Integer, ForeignKey("users.id"))
    p2_user_id = Column(Integer, ForeignKey("users.id"))
    winner_user_id = Column(Integer, ForeignKey("users.id"))

    system_fee = Column(Numeric(12, 2))  # record 25% of stake

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    p1 = relationship("User", foreign_keys=[p1_user_id])
    p2 = relationship("User", foreign_keys=[p2_user_id])
    winner = relationship("User", foreign_keys=[winner_user_id])
