from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from schemas import SendOtpIn, VerifyOtpIn, TokenOut
from utils.security import create_access_token
from utils.otp_utils import send_otp as srv_send_otp, verify_otp as srv_verify_otp
from crud import get_or_create_user

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/send-otp")
def send_otp(payload: SendOtpIn, db: Session = Depends(get_db)):
    ok = srv_send_otp(db, payload.phone)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to send OTP")
    return {"ok": True}

@router.post("/verify-otp", response_model=TokenOut)
def verify_otp(payload: VerifyOtpIn, db: Session = Depends(get_db)):
    ok = srv_verify_otp(db, payload.phone, payload.code)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    user = get_or_create_user(db, payload.phone)
    token = create_access_token({"sub": str(user.id)})
    return TokenOut(access_token=token)
