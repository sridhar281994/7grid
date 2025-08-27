import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from jose import jwt

from database import get_db
from models import OTP, User

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------- Config ----------
FAST2SMS_API_KEY = os.getenv("FAST2SMS_API_KEY", "")
FAST2SMS_SENDER_ID = os.getenv("FAST2SMS_SENDER_ID", "")      # e.g. "gridsT"
FAST2SMS_TEMPLATE_ID = os.getenv("FAST2SMS_TEMPLATE_ID", "")  # keep empty until DLT is ready
OTP_EXP_MIN = int(os.getenv("OTP_EXP_MINUTES", "5"))

# JWT
JWT_SECRET = os.getenv("JWT_SECRET", "change_me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MIN = int(os.getenv("JWT_EXP_MINUTES", str(60 * 24 * 30)))  # 30 days default

# ---------- Helpers ----------
def _now() -> datetime:
    return datetime.now(timezone.utc)

def _gen_otp() -> str:
    # deterministic-ish 6-digit for simplicity; replace with secrets if you wish
    ts = int(datetime.utcnow().timestamp())
    return str(100000 + (ts % 900000))

def _jwt_for_user(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": int((_now() + timedelta(minutes=JWT_EXP_MIN)).timestamp()),
        "iat": int(_now().timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

# ---------- Schemas ----------
class PhoneIn(BaseModel):
    phone: str

class VerifyIn(BaseModel):
    phone: str
    otp: str

# ---------- Endpoints ----------
@router.post("/send-otp")
def send_otp(payload: PhoneIn, db: Session = Depends(get_db)):
    if not FAST2SMS_API_KEY:
        raise HTTPException(500, "FAST2SMS_API_KEY is not configured on server.")

    phone = payload.phone.strip()
    if not (phone.isdigit() and len(phone) == 10):
        raise HTTPException(400, "Invalid phone number")

    otp = _gen_otp()

    headers = {
        "authorization": FAST2SMS_API_KEY,
        "Content-Type": "application/json",
    }

    url = "https://www.fast2sms.com/dev/bulkV2"
    if FAST2SMS_TEMPLATE_ID:
        # Use DLT template route
        body = {
            "sender_id": FAST2SMS_SENDER_ID,      # your approved DLT header (e.g., "gridsT")
            "message": FAST2SMS_TEMPLATE_ID,      # your DLT Template ID (e.g., "170717XXXXXXX")
            "variables_values": otp,              # will fill ##OTPCODE## in your template
            "route": "dlt",
            "numbers": phone,
        }
    else:
        # Fallback OTP route (works for registered/test numbers without DLT)
        body = {
            "route": "otp",
            "variables_values": otp,
            "numbers": phone,
        }

    try:
        # If your env blocks CA root, you can temporarily set verify=False
        resp = requests.post(url, json=body, headers=headers, timeout=10, verify=False)
        resp.raise_for_status()
    except Exception as e:
        raise HTTPException(502, f"Fast2SMS error: {e}")

    # Store OTP in DB (latest wins)
    expires = _now() + timedelta(minutes=OTP_EXP_MIN)
    db.add(OTP(phone=phone, code=otp, used=False, expires_at=expires))
    db.commit()

    # In non-DLT mode, return otp for testing so you can log in while SMS infra is pending
    return {
        "ok": True,
        "message": "OTP sent",
        "testing_otp": otp if not FAST2SMS_TEMPLATE_ID else None,
    }

@router.post("/verify-otp")
def verify_otp(payload: VerifyIn, db: Session = Depends(get_db)):
    phone = payload.phone.strip()
    otp_in = payload.otp.strip()

    if not (phone.isdigit() and len(phone) == 10):
        raise HTTPException(400, "Invalid phone number")
    if not otp_in:
        raise HTTPException(400, "OTP is required")

    # Get latest unused OTP
    db_obj: Optional[OTP] = (
        db.query(OTP)
        .filter(OTP.phone == phone, OTP.used == False)  # noqa: E712
        .order_by(OTP.id.desc())
        .first()
    )
    if not db_obj:
        raise HTTPException(400, "No OTP found. Please resend.")
    if db_obj.expires_at <= _now():
        raise HTTPException(400, "OTP expired. Please resend.")
    if db_obj.code != otp_in:
        raise HTTPException(400, "Invalid OTP.")

    # Mark used
    db_obj.used = True
    db.commit()

    # Upsert user
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        user = User(phone=phone, name=None, upi_id=None)
        db.add(user)
        db.commit()
        db.refresh(user)

    token = _jwt_for_user(user.id)
    return {"ok": True, "user_id": user.id, "access_token": token, "token_type": "bearer"}
