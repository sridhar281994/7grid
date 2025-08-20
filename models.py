from sqlalchemy import Column, Integer, String, Numeric, DateTime, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta
from database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=True)
    upi_id = Column(String(120), nullable=True)
    wallet_balance = Column(Numeric(12, 2), nullable=False, default=0)

    transactions = relationship("Transaction", back_populates="user", lazy="selectin")
    otp_codes = relationship("OtpCode", back_populates="user", lazy="selectin")

class OtpCode(Base):
    __tablename__ = "otp_codes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    code = Column(String(6), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="otp_codes")

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(20), nullable=False)  # "recharge" | "withdraw" | "stake" | "payout"
    amount = Column(Numeric(12, 2), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    meta = Column(String(255), nullable=True)

    user = relationship("User", back_populates="transactions")

class GameMatch(Base):
    __tablename__ = "game_matches"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    stake_rs = Column(Integer, nullable=False)  # 4 | 8 | 12
    result = Column(String(20), nullable=False)  # "win" | "lose" | "danger"
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
