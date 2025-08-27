import os
import random
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import OTP, User
from utils.email_utils import send_email_otp
from utils.security import make_token

router = APIRouter(prefix="/auth", tags=["auth"])

OTP_EXP_MIN = int(os.getenv("OTP_EXP_MINUTES", "5"))

def _now():
    return datetime.now(timezone.utc)

class EmailIn(BaseModel):
    email: EmailStr

class VerifyIn(BaseModel):
    email: EmailStr
    otp: str

@router.post("/send-otp")
def send_otp(payload: EmailIn, db: Session = Depends(get_db)):
    email = payload.email.lower()
    code = f"{random.randint(0, 999999):06d}"

    # persist
    db_otp = OTP(email=email, code=code, used=False, expires_at=_now() + timedelta(minutes=OTP_EXP_MIN))
    db.add(db_otp)
    db.commit()

    # send
    try:
        send_email_otp(email, code)
    except Exception as e:
        raise HTTPException(502, f"Email send failed: {e}")

    return {"ok": True}

@router.post("/verify-otp")
def verify_otp(payload: VerifyIn, db: Session = Depends(get_db)):
    email = payload.email.lower()
    code = payload.otp.strip()

    otp = (
        db.query(OTP)
        .filter(OTP.email == email, OTP.used == False)  # noqa
        .order_by(OTP.id.desc())
        .first()
    )
    if not otp:
        raise HTTPException(400, "No OTP found. Please request again.")
    if otp.expires_at <= _now():
        raise HTTPException(400, "OTP expired.")
    if otp.code != code:
        raise HTTPException(400, "Invalid OTP.")

    otp.used = True
    db.commit()

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email)
        db.add(user)
        db.commit()
        db.refresh(user)

    token = make_token(user.id)
    return {"ok": True, "user_id": user.id, "access_token": token, "token_type": "bearer"}
