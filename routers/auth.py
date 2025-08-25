from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import get_db
from models.otp import OTP
from utils.phone import normalize_phone
from utils.twofactor import send_otp_via_2factor, verify_otp_via_2factor
import os
from datetime import datetime
import random

DEBUG = os.getenv("DEBUG","false").lower() == "true"

router = APIRouter(prefix="/auth", tags=["auth"])

class SendOtpReq(BaseModel):
    phone: str

class VerifyOtpReq(BaseModel):
    phone: str
    otp: str

@router.post("/send-otp")
def send_otp(payload: SendOtpReq, db: Session = Depends(get_db)):
    phone = normalize_phone(payload.phone)
    if DEBUG:
        # local debug path: generate + "fake send"
        code = f"{random.randint(100000, 999999)}"
        # store session_id as the code itself to keep a single verification path
        rec = db.get(OTP, phone)
        if rec:
            rec.session_id = code
            rec.expires_at = OTP.expiry(10)
        else:
            rec = OTP(phone=phone, session_id=code, expires_at=OTP.expiry(10))
            db.add(rec)
        db.commit()
        return {"success": True, "debug": True, "phone": phone, "otp": code}

    ok, session_id, err = send_otp_via_2factor(phone)
    if not ok or not session_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=err or "Failed to send OTP")

    # upsert session_id for this phone
    rec = db.get(OTP, phone)
    if rec:
        rec.session_id = session_id
        rec.expires_at = OTP.expiry(10)
    else:
        rec = OTP(phone=phone, session_id=session_id, expires_at=OTP.expiry(10))
        db.add(rec)
    db.commit()

    return {"success": True, "phone": phone, "sent": True}

@router.post("/verify-otp")
def verify_otp(payload: VerifyOtpReq, db: Session = Depends(get_db)):
    phone = normalize_phone(payload.phone)
    otp = payload.otp.strip()

    rec = db.get(OTP, phone)
    if not rec:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP not requested")

    # Expiry check (extra safety; 2Factor has its own TTL too)
    if datetime.utcnow() > rec.expires_at:
        db.delete(rec)
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP expired")

    if DEBUG:
        # In debug, session_id == code
        if otp != rec.session_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OTP")
        db.delete(rec)
        db.commit()
        return {"success": True}

    ok, err = verify_otp_via_2factor(rec.session_id, otp)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err or "Invalid OTP")

    # success: one-time use
    db.delete(rec)
    db.commit()
    return {"success": True}
