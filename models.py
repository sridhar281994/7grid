from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Enum, Float, func
from sqlalchemy.orm import relationship
from database import Base
import enum
# --------------------
# Match Status Enum
# --------------------
class MatchStatus(str, enum.Enum):
    WAITING = "WAITING"
    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"
# --------------------
# User Model
# --------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=True)  # stored as bcrypt
    name = Column(String, nullable=True)
    upi_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Relationships
    matches_as_p1 = relationship("Match", back_populates="player1", foreign_keys="Match.p1_user_id")
    matches_as_p2 = relationship("Match", back_populates="player2", foreign_keys="Match.p2_user_id")
# --------------------
# Match Model
# --------------------
class Match(Base):
    __tablename__ = "game_matches"
    id = Column(Integer, primary_key=True, index=True)
    stake_amount = Column(Float, nullable=False)
    status = Column(Enum(MatchStatus), default=MatchStatus.WAITING, nullable=False)
    p1_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    p2_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Relationships
    player1 = relationship("User", foreign_keys=[p1_user_id], back_populates="matches_as_p1")
    player2 = relationship("User", foreign_keys=[p2_user_id], back_populates="matches_as_p2")
# --------------------
# OTP Model
# --------------------
class OTP(Base):
    __tablename__ = "otps"
    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=False)
    code = Column(String, nullable=False)
    used = Column(Boolean, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())





