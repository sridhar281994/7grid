import os
import random
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt
from sqlalchemy.orm import Session

from database import get_db
from models import User, OtpCode
from schemas import SendOtpIn, VerifyOtpIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
ALGO = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "43200"))  # 30 days

def create_access_token(*, sub: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": sub, "exp": expire}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGO)

def get_user_by_phone(db: Session, phone: str) -> Optional[User]:
    return db.query(User).filter(User.phone == phone).first()

@router.post("/send-otp")
def send_otp(payload: SendOtpIn, db: Session = Depends(get_db)):
    user = get_user_by_phone(db, payload.phone)
    if not user:
        user = User(phone=payload.phone, name=payload.name or None, wallet_balance=0)
        db.add(user)
        db.commit()
        db.refresh(user)
    elif payload.name and not user.name:
        user.name = payload.name
        db.commit()

    code = f"{random.randint(1000, 9999)}"
    otp = OtpCode(
        user_id=user.id,
        code=code,
        expires_at=datetime.utcnow() + timedelta(minutes=5),
        used=False,
    )
    db.add(otp)
    db.commit()

    # NOTE: integrate SMS provider here (Twilio, etc.)
    # For development we return the OTP so you can test the flow easily.
    return {"ok": True, "dev_otp": code}

@router.post("/verify", response_model=TokenOut)
def verify_otp(payload: VerifyOtpIn, db: Session = Depends(get_db)):
    user = get_user_by_phone(db, payload.phone)
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    otp = (
        db.query(OtpCode)
        .filter(
            OtpCode.user_id == user.id,
            OtpCode.code == payload.code,
            OtpCode.used == False,  # noqa
            OtpCode.expires_at > datetime.utcnow(),
        )
        .order_by(OtpCode.id.desc())
        .first()
    )
    if not otp:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    otp.used = True
    db.commit()

    token = create_access_token(sub=str(user.id))
    return TokenOut(access_token=token)
