from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum, Numeric
from sqlalchemy.orm import relationship
from database import Base
import enum
from datetime import datetime


# -------------------------
# Enum for Match Status
# -------------------------
class MatchStatus(str, enum.Enum):
    WAITING = "WAITING"
    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"


# -------------------------
# User Table
# -------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True)
    phone = Column(String, unique=True, index=True)
    password = Column(String, nullable=False)
    upi_id = Column(String, nullable=True)

    # Relationships
    matches_as_p1 = relationship("Match", back_populates="p1", foreign_keys="Match.p1_id")
    matches_as_p2 = relationship("Match", back_populates="p2", foreign_keys="Match.p2_id")
    wallet = relationship("Wallet", back_populates="owner", uselist=False)


# -------------------------
# Match Table
# -------------------------
class Match(Base):
    __tablename__ = "game_matches"

    id = Column(Integer, primary_key=True, index=True)
    stake_amount = Column(Integer, nullable=False)
    status = Column(Enum(MatchStatus), default=MatchStatus.WAITING, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)

    # Player 1
    p1_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    p1 = relationship("User", foreign_keys=[p1_id], back_populates="matches_as_p1")

    # Player 2
    p2_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    p2 = relationship("User", foreign_keys=[p2_id], back_populates="matches_as_p2")


# -------------------------
# Wallet Table
# -------------------------
class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    balance = Column(Numeric(10, 2), default=0)

    owner = relationship("User", back_populates="wallet")
