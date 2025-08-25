from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Numeric, Enum, func
from sqlalchemy.orm import relationship
from database import Base
import enum

class OTP(Base):
    __tablename__ = "otps"
    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), index=True, nullable=False)
    # optional – if you ever do in-house OTPs again
    code = Column(String(10), nullable=True)
    # store 2factor session id here for AUTOGEN/VERIFY flow
    session_id = Column(String(64), nullable=True, index=True)
    used = Column(Boolean, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), unique=True, index=True, nullable=False)
    name = Column(String(100))
    upi_id = Column(String(100))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class TxType(str, enum.Enum):
    RECHARGE = "recharge"
    WITHDRAW = "withdraw"

class TxStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"

class WalletTransaction(Base):
    __tablename__ = "wallet_tx"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    type = Column(Enum(TxType), nullable=False)
    status = Column(Enum(TxStatus), default=TxStatus.PENDING, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User")

class MatchStatus(str, enum.Enum):
    WAITING = "waiting"
    ACTIVE = "active"
    FINISHED = "finished"

class GameMatch(Base):
    __tablename__ = "game_matches"
    id = Column(Integer, primary_key=True, index=True)
    stake_amount = Column(Integer, nullable=False)  # 4, 8, 12
    status = Column(Enum(MatchStatus), default=MatchStatus.WAITING, nullable=False)
    p1_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    p2_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    winner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    p1 = relationship("User", foreign_keys=[p1_user_id])
    p2 = relationship("User", foreign_keys=[p2_user_id])
    winner = relationship("User", foreign_keys=[winner_user_id])
