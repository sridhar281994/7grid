from datetime import datetime, timedelta, timezone
import os
import random
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import OTP, User
from jose import jwt

router = APIRouter(prefix="/auth", tags=["auth"])

# ---- Fast2SMS config ----
F2S_API_KEY = os.getenv("FAST2SMS_API_KEY", "")          # REQUIRED
F2S_SENDER_ID = os.getenv("FAST2SMS_SENDER_ID", "")      # e.g. "gridsT" (DLT header)
F2S_TEMPLATE_ID = os.getenv("FAST2SMS_TEMPLATE_ID", "")  # leave blank for route=otp
F2S_ENDPOINT = os.getenv("FAST2SMS_ENDPOINT", "https://www.fast2sms.com/dev/bulkV2")

# Security / token
JWT_SECRET = os.getenv("JWT_SECRET", "change_me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MIN = int(os.getenv("JWT_EXP_MIN", str(60 * 24 * 30)))  # minutes, default 30 days

# OTP expiry (minutes)
OTP_EXP_MIN = int(os.getenv("OTP_EXP_MINUTES", "5"))

# For local/dev where cert issues happen, you can set this env var to "false"
# NOTE: For production, keep it True (default).
VERIFY_TLS = os.getenv("VERIFY_TLS", "true").lower() != "false"

class PhoneIn(BaseModel):
    phone: str

class VerifyIn(BaseModel):
    phone: str
    otp: str

def _now():
    return datetime.now(timezone.utc)

def _jwt_for_user(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": int((_now() + timedelta(minutes=JWT_EXP_MIN)).timestamp()),
        "iat": int(_now().timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def _six_digit() -> str:
    return f"{random.randint(100000, 999999)}"

def _send_via_fast2sms(phone: str, otp_code: str) -> dict:
    """
    Sends OTP using Fast2SMS.
    - If template id is set -> use DLT transactional route
    - Else -> use otp route
    Returns parsed JSON or raises HTTPException on error.
    """
    if not F2S_API_KEY:
        raise HTTPException(500, "FAST2SMS_API_KEY not configured")

    headers = {
        "authorization": F2S_API_KEY,
        "Content-Type": "application/json",
        "accept": "application/json",
    }

    if F2S_TEMPLATE_ID:
        # DLT transactional (requires approved template)
        if not F2S_SENDER_ID:
            raise HTTPException(500, "FAST2SMS_SENDER_ID not configured for DLT")
        payload = {
            "sender_id": F2S_SENDER_ID,
            "route": "dlt",
            "message": F2S_TEMPLATE_ID,   # per Fast2SMS docs: message field holds the template_id for dlt
            "variables_values": otp_code, # replaces template variable (e.g. {#var#})
            "flash": "0",
            "numbers": phone,
        }
    else:
        # Generic OTP route (good for testing and early use)
        payload = {
            "route": "otp",
            "variables_values": otp_code,
            "flash": "0",
            "numbers": phone,
        }

    try:
        resp = requests.post(
            F2S_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=10,
            verify=VERIFY_TLS,
        )
    except requests.RequestException as e:
        # Network/timeout etc
        raise HTTPException(502, f"Fast2SMS request error: {e}")

    # Fast2SMS usually responds with JSON; on error, capture text too.
    text = resp.text
    try:
        data = resp.json()
    except Exception:
        data = {"raw": text}

    if resp.status_code != 200:
        raise HTTPException(502, f"Fast2SMS {resp.status_code}: {text}")

    # Expect something like {"return": true, ...}
    ok = bool(data.get("return", False))
    if not ok:
        raise HTTPException(502, f"Fast2SMS failed: {data}")

    return data

@router.post("/send-otp")
def send_otp(payload: PhoneIn, db: Session = Depends(get_db)):
    phone = payload.phone.strip()
    if not (phone.isdigit() and len(phone) == 10):
        raise HTTPException(400, "Invalid phone")

    # 1) Generate OTP locally
    otp_code = _six_digit()

    # 2) Attempt to send SMS via Fast2SMS
    _send_via_fast2sms(phone, otp_code)

    # 3) Store in DB with expiry and mark not used
    expires = _now() + timedelta(minutes=OTP_EXP_MIN)
    db_otp = OTP(phone=phone, code=otp_code, used=False, expires_at=expires)
    db.add(db_otp)
    db.commit()

    return {"ok": True, "message": "OTP sent"}

@router.post("/verify-otp")
def verify_otp(payload: VerifyIn, db: Session = Depends(get_db)):
    phone = payload.phone.strip()
    code_in = payload.otp.strip()

    if not (phone.isdigit() and len(phone) == 10):
        raise HTTPException(400, "Invalid phone")
    if not code_in:
        raise HTTPException(400, "OTP required")

    # Find most recent unused OTP for this phone
    db_obj: Optional[OTP] = (
        db.query(OTP)
        .filter(OTP.phone == phone, OTP.used == False)  # noqa
        .order_by(OTP.id.desc())
        .first()
    )
    if not db_obj:
        raise HTTPException(400, "No OTP pending for this phone")

    if db_obj.expires_at <= _now():
        raise HTTPException(400, "OTP expired. Please resend.")

    if db_obj.code != code_in:
        raise HTTPException(400, "Invalid OTP")

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
