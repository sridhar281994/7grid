import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from jose import jwt

from database import get_db
from models import OTP, User
from utils.email_utils import send_email_otp
import random

router = APIRouter(prefix="/auth", tags=["auth"])

JWT_SECRET = os.getenv("JWT_SECRET", "change_me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MIN = int(os.getenv("JWT_EXP_MIN", str(60 * 24 * 30)))  # default 30 days
OTP_EXP_MIN = int(os.getenv("OTP_EXP_MINUTES", "5"))

def _now():
    return datetime.now(timezone.utc)

def _jwt_for_user(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": int((_now() + timedelta(minutes=JWT_EXP_MIN)).timestamp()),
        "iat": int(_now().timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def _gen_otp():
    return f"{random.randint(100000, 999999)}"

class PhoneIn(BaseModel):
    phone: str

class VerifyIn(BaseModel):
    phone: str
    otp: str

@router.post("/send-otp")
def send_otp_by_phone(payload: PhoneIn, db: Session = Depends(get_db)):
    """User enters phone. We look up the user's email and send the OTP there."""
    phone = payload.phone.strip()
    if not (phone.isdigit() and len(phone) == 10):
        raise HTTPException(400, "Enter a valid 10-digit phone number.")

    user: Optional[User] = db.query(User).filter(User.phone == phone).first()
    if not user or not user.email:
        # For security, don't reveal which part is missing.
        raise HTTPException(404, "Account not found or email not set.")

    code = _gen_otp()
    expires = _now() + timedelta(minutes=OTP_EXP_MIN)

    # persist OTP
    db_otp = OTP(phone=phone, code=code, used=False, expires_at=expires)
    db.add(db_otp)
    db.commit()

    # send to the registered email
    try:
        send_email_otp(user.email, code)
    except Exception as e:
        # optional: roll back OTP row if email fails
        # db.delete(db_otp); db.commit()
        raise HTTPException(502, f"Failed to send OTP email: {e}")

    # do not leak the email in response
    return {"ok": True, "message": "OTP has been sent to your registered email."}

@router.post("/verify-otp")
def verify_otp_phone(payload: VerifyIn, db: Session = Depends(get_db)):
    phone = payload.phone.strip()
    otp = payload.otp.strip()

    if not (phone.isdigit() and len(phone) == 10):
        raise HTTPException(400, "Enter a valid 10-digit phone number.")
    if not otp:
        raise HTTPException(400, "OTP required.")

    # find latest unused OTP for this phone
    db_otp: Optional[OTP] = (
        db.query(OTP)
        .filter(OTP.phone == phone, OTP.used == False)  # noqa
        .order_by(OTP.id.desc())
        .first()
    )
    if not db_otp:
        raise HTTPException(400, "No OTP found. Please request a new one.")
    if db_otp.expires_at <= _now():
        raise HTTPException(400, "OTP expired. Please request a new one.")
    if db_otp.code != otp:
        raise HTTPException(400, "Invalid OTP.")

    # mark used
    db_otp.used = True
    db.commit()

    # upsert user is not required here (phone must already exist for email),
    # but if you want to be safe:
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise HTTPException(400, "User not found.")

    token = _jwt_for_user(user.id)
    return {"ok": True, "user_id": user.id, "access_token": token, "token_type": "bearer"}
