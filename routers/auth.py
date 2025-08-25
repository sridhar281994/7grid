import os
import requests
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import get_db
from models import OTP, User
from utils.security import create_access_token  # your existing JWT helper

router = APIRouter(prefix="/auth", tags=["auth"])

TWOFACTOR_API_KEY = os.getenv("TWOFACTOR_API_KEY", "").strip()
TWOFACTOR_BASE = "https://2factor.in/API/V1"
OTP_TTL_MINUTES = 5
REQUEST_TIMEOUT = 8  # seconds

class SendOtpBody(BaseModel):
    phone: str  # "9360xxxxxx"

class VerifyOtpBody(BaseModel):
    phone: str
    otp: str

def _normalize_phone(phone: str) -> str:
    p = phone.strip()
    # 2factor expects Indian numbers with country code; prepend 91 if 10 digits
    if p.isdigit() and len(p) == 10:
        return "91" + p
    return p

@router.post("/send-otp")
def send_otp(body: SendOtpBody, db: Session = Depends(get_db)):
    phone_raw = body.phone.strip()
    if not (phone_raw.isdigit() and len(phone_raw) == 10):
        raise HTTPException(status_code=400, detail="Enter valid 10-digit phone")

    phone_api = _normalize_phone(phone_raw)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)

    # If no 2factor key configured, dev fallback: create row but do not send SMS
    if not TWOFACTOR_API_KEY:
        otp_row = OTP(phone=phone_raw, code="999999", session_id=None, expires_at=expires_at, used=False)
        db.add(otp_row)
        db.commit()
        return {"ok": True, "dev": True, "message": "OTP mocked (no SMS)", "otp": "999999"}

    # Call 2factor AUTOGEN to send SMS
    url = f"{TWOFACTOR_BASE}/{TWOFACTOR_API_KEY}/SMS/{phone_api}/AUTOGEN"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SMS provider error: {e}")

    try:
        data = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail=f"Bad response from SMS provider: {resp.text[:200]}")

    status = (data.get("Status") or "").lower()
    if status != "success":
        # Example failure: { "Status":"Error", "Details":"Invalid API Key" }
        raise HTTPException(status_code=502, detail=f"SMS provider failed: {data}")

    session_id = data.get("Details")
    if not session_id:
        raise HTTPException(status_code=502, detail=f"Missing session id from provider: {data}")

    # Store session id so we can verify later
    otp_row = OTP(phone=phone_raw, code=None, session_id=session_id, expires_at=expires_at, used=False)
    db.add(otp_row)
    db.commit()

    return {"ok": True, "message": "OTP sent", "ttl_minutes": OTP_TTL_MINUTES}

@router.post("/verify-otp")
def verify_otp(body: VerifyOtpBody, db: Session = Depends(get_db)):
    phone_raw = body.phone.strip()
    otp_code = body.otp.strip()
    if not (phone_raw.isdigit() and len(phone_raw) == 10):
        raise HTTPException(status_code=400, detail="Enter valid 10-digit phone")
    if not otp_code:
        raise HTTPException(status_code=400, detail="Enter the OTP")

    # Get latest OTP row for this phone within TTL and not used
    otp_row = (
        db.query(OTP)
        .filter(OTP.phone == phone_raw, OTP.used == False, OTP.expires_at > datetime.now(timezone.utc))
        .order_by(OTP.id.desc())
        .first()
    )
    if not otp_row:
        raise HTTPException(status_code=400, detail="No active OTP. Please request a new one.")

    # Dev fallback: no 2factor key → accept the mocked code
    if not TWOFACTOR_API_KEY:
        if otp_row.code != otp_code:
            raise HTTPException(status_code=400, detail="Invalid OTP (dev mode).")
    else:
        # Verify with 2factor using stored session id
        if not otp_row.session_id:
            raise HTTPException(status_code=400, detail="OTP session missing. Please request a new OTP.")
        url = f"{TWOFACTOR_BASE}/{TWOFACTOR_API_KEY}/SMS/VERIFY/{otp_row.session_id}/{otp_code}"
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            data = resp.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"SMS verify error: {e}")

        status = (data.get("Status") or "").lower()
        if status != "success":
            # Example failure: {"Status":"Error","Details":"OTP Mismatch"}
            raise HTTPException(status_code=400, detail=f"Verify failed: {data.get('Details','Unknown')}")

    # Mark OTP used
    otp_row.used = True
    db.commit()

    # Upsert user by phone
    user = db.query(User).filter(User.phone == phone_raw).first()
    if not user:
        user = User(phone=phone_raw)
        db.add(user)
        db.commit()
        db.refresh(user)

    token = create_access_token({"sub": str(user.id)})

    return {"ok": True, "user_id": user.id, "access_token": token, "token_type": "bearer"}
