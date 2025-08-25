from sqlalchemy.orm import Session
from sqlalchemy import select, and_
from models import User, OTP, WalletTransaction, GameMatch, MatchStatus, TxStatus, TxType
from datetime import datetime, timedelta

# ===== Users =====
def get_or_create_user(db: Session, phone: str) -> User:
    user = db.execute(select(User).where(User.phone == phone)).scalar_one_or_none()
    if user:
        return user
    user = User(phone=phone)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def update_user(db: Session, user_id: int, name: str | None, upi_id: str | None) -> User:
    user = db.get(User, user_id)
    if name is not None:
        user.name = name
    if upi_id is not None:
        user.upi_id = upi_id
    db.commit()
    db.refresh(user)
    return user

# ===== OTP =====
def create_otp(db: Session, phone: str, code: str, ttl_sec: int = 300) -> OTP:
    expires = datetime.utcnow() + timedelta(seconds=ttl_sec)
    otp = OTP(phone=phone, code=code, expires_at=expires)
    db.add(otp)
    db.commit()
    db.refresh(otp)
    return otp

def verify_and_consume_otp(db: Session, phone: str, code: str) -> bool:
    now = datetime.utcnow()
    otp = db.execute(
        select(OTP).where(and_(OTP.phone == phone, OTP.code == code, OTP.used == False, OTP.expires_at > now))
    ).scalar_one_or_none()
    if not otp:
        return False
    otp.used = True
    db.commit()
    return True

# ===== Wallet =====
def create_wallet_tx(db: Session, user_id: int, amount: float, type_: TxType) -> WalletTransaction:
    tx = WalletTransaction(user_id=user_id, amount=amount, type=type_)
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx

# ===== Game =====
def create_match(db: Session, stake_amount: int, creator_user_id: int) -> GameMatch:
    match = GameMatch(stake_amount=stake_amount, status=MatchStatus.WAITING, p1_user_id=creator_user_id)
    db.add(match)
    db.commit()
    db.refresh(match)
    return match

def join_match(db: Session, match_id: int, user_id: int) -> GameMatch | None:
    match = db.get(GameMatch, match_id)
    if not match or match.status != MatchStatus.WAITING or match.p1_user_id == user_id:
        return None
    match.p2_user_id = user_id
    match.status = MatchStatus.ACTIVE
    db.commit()
    db.refresh(match)
    return match

def finish_match(db: Session, match_id: int, winner_user_id: int) -> GameMatch | None:
    match = db.get(GameMatch, match_id)
    if not match or match.status != MatchStatus.ACTIVE:
        return None
    match.winner_user_id = winner_user_id
    match.status = MatchStatus.FINISHED
    db.commit()
    db.refresh(match)
    return match
