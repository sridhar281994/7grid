from datetime import datetime, timedelta, timezone
import os
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import OTP, User
from jose import jwt

router = APIRouter(prefix="/auth", tags=["auth"])

API_KEY = os.getenv("TWOFACTOR_API_KEY", "")
TEMPLATE = os.getenv("TWOFACTOR_TEMPLATE", "7grids")  # from your screenshot
OTP_EXP_MIN = int(os.getenv("OTP_EXP_MINUTES", "5"))

JWT_SECRET = os.getenv("JWT_SECRET", "change_me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MIN = 60 * 24 * 30  # 30 days

BASE_URL = "https://2factor.in/API/V1"

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

@router.post("/send-otp")
def send_otp(payload: PhoneIn, db: Session = Depends(get_db)):
    phone = payload.phone.strip()
    if not (phone.isdigit() and len(phone) == 10):
        raise HTTPException(400, "Invalid phone")
    # ---- 2FACTOR SMS + TEMPLATE (forces SMS, not voice) ----
    # prepend +91 and pass your approved template & sender id
    sender_id = os.getenv("TWOFACTOR_SENDER_ID", "gridsT")  # from your template
    msisdn = f"+91{phone}"
    url = f"{BASE_URL}/{API_KEY}/SMS/{msisdn}/AUTOGEN/{TEMPLATE}?sender={sender_id}"
    try:
        r = requests.get(url, timeout=10)
    except Exception as e:
        raise HTTPException(502, f"2Factor error: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"2Factor status {r.status_code}: {r.text}")
    # many accounts return JSON; keep a safe parse
    data = {}
    ctype = r.headers.get("Content-Type", "")
    if "json" in ctype.lower():
        try:
            data = r.json()
        except Exception:
            pass
    status = str(data.get("Status", "")).lower()
    if status != "success":
        # if for some reason it returns text, surface it
        raise HTTPException(502, f"2Factor failure: {data or r.text}")
    session_id = data.get("Details")
    if not session_id:
        raise HTTPException(502, "2Factor did not return session id")
    # store only the session; code is delivered by SMS
    expires = _now() + timedelta(minutes=OTP_EXP_MIN)
    db.add(OTP(phone=phone, code=None, session_id=session_id, used=False, expires_at=expires))
    db.commit()
    return {"ok": True, "session_id": session_id}


@router.post("/verify-otp")
def verify_otp(payload: VerifyIn, db: Session = Depends(get_db)):
    phone = payload.phone.strip()
    otp_in = payload.otp.strip()

    if not (phone.isdigit() and len(phone) == 10):
        raise HTTPException(400, "Invalid phone")
    if not otp_in:
        raise HTTPException(400, "OTP required")

    # Find latest un-used OTP row for this phone
    db_obj: Optional[OTP] = (
        db.query(OTP)
        .filter(OTP.phone == phone, OTP.used == False)  # noqa
        .order_by(OTP.id.desc())
        .first()
    )
    if not db_obj or not db_obj.session_id:
        raise HTTPException(400, "No OTP session. Please resend OTP.")

    if db_obj.expires_at <= _now():
        raise HTTPException(400, "OTP expired. Please resend.")

    # Verify with 2Factor
    url = f"{BASE_URL}/{API_KEY}/SMS/VERIFY/{db_obj.session_id}/{otp_in}"
    try:
        r = requests.get(url, timeout=10)
    except Exception as e:
        raise HTTPException(502, f"2Factor error: {e}")

    if r.status_code != 200:
        raise HTTPException(502, f"2Factor status {r.status_code}: {r.text}")

    data = r.json() if "application/json" in r.headers.get("Content-Type", "") else {}
    if str(data.get("Status", "")).lower() != "success":
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
