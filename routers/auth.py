import os
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from jose import jwt
from passlib.hash import bcrypt   # ✅ secure hashing

from database import get_db
from models import OTP, User
from utils.email_utils import send_email_otp
from utils.security import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

# =====================
# Config
# =====================
JWT_SECRET = os.getenv("JWT_SECRET", "change_me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MIN = int(os.getenv("JWT_EXP_MIN", str(60 * 24 * 30)))  # default 30 days
OTP_EXP_MIN = int(os.getenv("OTP_EXP_MINUTES", "5"))

# =====================
# Helpers
# =====================
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

# =====================
# Request Models
# =====================
class PhoneIn(BaseModel):
    phone: str

class VerifyIn(BaseModel):
    phone: str
    otp: str

class RegisterIn(BaseModel):
    phone: str
    email: str
    password: str
    name: str         
    upi_id: Optional[str] = None


# =====================
# Routes
# =====================

@router.post("/register")
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    """Create a new user account with bcrypt-hashed password"""
    phone = payload.phone.strip()
    email = payload.email.strip().lower()
    password = payload.password.strip()
    upi_id = payload.upi_id.strip() if payload.upi_id else None
    name = payload.name.strip()

    if not name:
        raise HTTPException(400, "Name is required.")
    if not (phone.isdigit() and len(phone) == 10):
        raise HTTPException(400, "Enter a valid 10-digit phone number.")
    if not email:
        raise HTTPException(400, "Email is required.")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")

    existing = db.query(User).filter((User.phone == phone) | (User.email == email)).first()
    if existing:
        raise HTTPException(400, "Phone or Email already registered.")

    password = password[:72]
    hashed_pw = bcrypt.hash(password)

    user = User(
        phone=phone,
        email=email,
        password_hash=hashed_pw,
        name=name,               # ✅ full name saved as entered
        upi_id=upi_id
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return {"ok": True, "message": "Account created successfully. Please login using OTP."}


@router.post("/send-otp")
def send_otp_by_phone(payload: PhoneIn, db: Session = Depends(get_db)):
    """User enters phone. We look up the user's email and send the OTP there."""
    phone = payload.phone.strip()
    if not (phone.isdigit() and len(phone) == 10):
        raise HTTPException(400, "Enter a valid 10-digit phone number.")

    user: Optional[User] = db.query(User).filter(User.phone == phone).first()
    if not user or not user.email:
        raise HTTPException(404, "Account not found or email not set.")

    code = _gen_otp()
    expires = _now() + timedelta(minutes=OTP_EXP_MIN)

    # persist OTP
    db_otp = OTP(phone=phone, code=code, used=False, expires_at=expires)
    db.add(db_otp)
    db.commit()

    try:
        send_email_otp(user.email, code)
    except Exception as e:
        raise HTTPException(502, f"Failed to send OTP email: {e}")

    return {"ok": True, "message": "OTP has been sent to your registered email."}


@router.post("/verify-otp")
def verify_otp_phone(payload: VerifyIn, db: Session = Depends(get_db)):
    phone = payload.phone.strip()
    otp = payload.otp.strip()

    if not (phone.isdigit() and len(phone) == 10):
        raise HTTPException(400, "Enter a valid 10-digit phone number.")
    if not otp:
        raise HTTPException(400, "OTP required.")

    db_otp: Optional[OTP] = (
        db.query(OTP)
        .filter(OTP.phone == phone, OTP.used == False)
        .order_by(OTP.id.desc())
        .first()
    )
    if not db_otp:
        raise HTTPException(400, "No OTP found. Please request a new one.")
    if db_otp.expires_at <= _now():
        raise HTTPException(400, "OTP expired. Please request a new one.")
    if db_otp.code != otp:
        raise HTTPException(400, "Invalid OTP.")

    db_otp.used = True
    db.commit()

    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise HTTPException(400, "User not found.")

    token = _jwt_for_user(user.id)
    return {"ok": True, "user_id": user.id, "access_token": token, "token_type": "bearer"}


@router.get("/me")
def get_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return always fresh user info including wallet balance."""
    user = db.get(User, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # ✅ Always ensure a readable name
    name = user.name
    if not name or not name.strip():
        if user.email:
            name = user.email.split("@", 1)[0]
        elif user.phone:
            name = user.phone
        else:
            name = "Player"

    return {
        "id": user.id,
        "phone": user.phone,
        "email": user.email,
        "name": name.strip(),
        "upi_id": user.upi_id,
        "wallet_balance": float(user.wallet_balance or 0),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


