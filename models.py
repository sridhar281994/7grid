from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Boolean,
    ForeignKey,
    Numeric,
    Enum,
    Text,
    text as sa_text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
import enum

from database import Base


# -----------------------
# Enums
# -----------------------
class MatchStatus(enum.Enum):
    WAITING = "WAITING"
    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"
    ABANDONED = "ABANDONED"


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


class WithdrawalMethod(enum.Enum):
    UPI = "upi"
    PAYPAL = "paypal"


class WithdrawalStatus(enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAID = "paid"
    REJECTED = "rejected"


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

    # New column for agent identification
    is_agent = Column(Boolean, default=False)  # Mark if the user is an agent

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
    merchant_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # ✅ New column for the merchant

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

    # ✅ NEW — track forfeited players
    forfeit_ids = Column(ARRAY(Integer), nullable=True, default=[])

    # Relationships
    player1 = relationship("User", foreign_keys=[p1_user_id], back_populates="matches_as_p1")
    player2 = relationship("User", foreign_keys=[p2_user_id], back_populates="matches_as_p2")
    player3 = relationship("User", foreign_keys=[p3_user_id], back_populates="matches_as_p3")
    winner = relationship("User", foreign_keys=[winner_user_id])
    merchant = relationship("User", foreign_keys=[merchant_user_id])  # ✅ New relationship for merchant


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
    channel = Column(String(32), nullable=True)
    initiator_ip = Column(String(64), nullable=True)
    extra_data = Column(JSONB, nullable=True)

    user = relationship("User", back_populates="transactions")


class WithdrawalRequest(Base):
    __tablename__ = "withdrawals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    wallet_tx_id = Column(Integer, ForeignKey("wallet_transactions.id"), nullable=False, unique=True)
    amount = Column(Numeric(10, 2), nullable=False)
    method = Column(Enum(WithdrawalMethod), nullable=False)
    account = Column(String, nullable=False)
    status = Column(Enum(WithdrawalStatus), default=WithdrawalStatus.PENDING, nullable=False)
    payout_txn_id = Column(String, nullable=True)
    details = Column(Text, nullable=True)
    channel = Column(String(32), nullable=True)
    initiator_ip = Column(String(64), nullable=True)
    extra_data = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User")
    tx = relationship("WalletTransaction")


# -----------------------
# Stakes Table
# -----------------------
class Stake(Base):
    __tablename__ = "stakes"

    id = Column(Integer, primary_key=True, index=True)
    stake_amount = Column(Integer, unique=True, nullable=False)  # stage key
    entry_fee = Column(Integer, nullable=False)  # each player pays
    winner_payout = Column(Integer, nullable=False)  # winner gets
    label = Column(String(50), nullable=False)  # UI label


# -----------------------
# Wallet bridge tokens (short-lived auth handoff)
# -----------------------
class WalletBridgeToken(Base):
    __tablename__ = "wallet_bridge_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(String(128), unique=True, nullable=False)
    channel = Column(String(32), nullable=False)
    device_fingerprint = Column(String(128), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")


class WalletDeviceCode(Base):
    __tablename__ = "wallet_device_codes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    code = Column(String(12), unique=True, nullable=False)
    channel = Column(String(32), nullable=False)
    device_fingerprint = Column(String(128), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")


class PayoutAuditLog(Base):
    __tablename__ = "payout_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    withdrawal_id = Column(Integer, ForeignKey("withdrawals.id"), nullable=False)
    admin_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(64), nullable=False)
    status_before = Column(String(32), nullable=True)
    status_after = Column(String(32), nullable=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(256), nullable=True)
    details = Column(Text, nullable=True)
    provider_txn_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    withdrawal = relationship("WithdrawalRequest")
    admin = relationship("User")


class AdminMFASecret(Base):
    __tablename__ = "admin_mfa_secrets"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    secret = Column(String(64), nullable=False)
    enabled_at = Column(DateTime(timezone=True), server_default=func.now())
    last_verified_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")


class KYCSnapshot(Base):
    __tablename__ = "kyc_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(32), nullable=False)
    document_type = Column(String(64), nullable=True)
    document_ref = Column(String(128), nullable=True)
    data = Column(JSONB, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", foreign_keys=[user_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
