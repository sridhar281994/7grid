import os
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from passlib.hash import bcrypt   # ✅ secure hashing

from database import get_db
from models import OTP, User
from utils.email_utils import send_email_otp
from utils.security import (
    get_current_user,
    create_access_token,
    hash_fingerprint,
    issue_wallet_bridge_token,
    issue_device_code,
    consume_device_code,
    issue_wallet_cookie,
    require_channel,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# =====================
# Config
# =====================
JWT_SECRET = os.getenv("JWT_SECRET", "change_me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MIN = int(os.getenv("JWT_EXP_MIN", str(60 * 24 * 30)))  # default 30 days
OTP_EXP_MIN = int(os.getenv("OTP_EXP_MINUTES", "5"))
WALLET_LINK_CHANNEL = "web"

# =====================
# Helpers
# =====================
def _now():
    return datetime.now(timezone.utc)

def _jwt_for_user(user_id: int, channel: str, fingerprint: Optional[str]) -> str:
    return create_access_token(
        user_id,
        channel=channel,
        fingerprint=fingerprint,
        expires_minutes=JWT_EXP_MIN,
    )

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
    channel: Optional[str] = None

class RegisterIn(BaseModel):
    phone: str
    email: str
    password: str
    name: str         
    upi_id: Optional[str] = None


class ResetPasswordIn(BaseModel):
    password: str


# =====================
# Routes
# =====================

class WalletLinkOut(BaseModel):
    token: str
    expires_in: int


class DeviceCodeOut(BaseModel):
    code: str
    expires_in: int


class DeviceCodeConsumeIn(BaseModel):
    code: str


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
def verify_otp_phone(payload: VerifyIn, request: Request, db: Session = Depends(get_db)):
    phone = payload.phone.strip()
    otp = payload.otp.strip()
    channel = (payload.channel or "app").lower()
    if channel not in {"app", "web"}:
        raise HTTPException(400, "Invalid channel")

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

    fingerprint = _collect_fingerprint(request)
    token = _jwt_for_user(user.id, channel=channel, fingerprint=fingerprint)
    return {
        "ok": True,
        "user_id": user.id,
        "access_token": token,
        "token_type": "bearer",
        "channel": channel,
    }


# -----------------------
# NEW: Password check + login-initiated OTP endpoints
# Phone OR Email supported for identifier
# -----------------------

class PasswordCheckIn(BaseModel):
    identifier: str
    password: str

@router.post("/login/password-check")
def login_password_check(payload: PasswordCheckIn, db: Session = Depends(get_db)):
    """
    Validates identifier + password.
    Identifier can be: email OR phone.
    Returns 200 if password matches.
    Returns 401 if wrong password.
    """
    ident = payload.identifier.strip()
    password = payload.password.strip()

    user = None
    if "@" in ident:
        user = db.query(User).filter(User.email == ident.lower()).first()
    elif ident.isdigit():
        user = db.query(User).filter(User.phone == ident).first()
    else:
        # not allowing username here per request (Phone OR Email only)
        raise HTTPException(400, "Identifier must be phone or email.")

    if not user:
        raise HTTPException(404, "Account not found.")

    # Validate password
    if not bcrypt.verify(password, user.password_hash):
        raise HTTPException(401, "Incorrect password.")

    return {"ok": True, "message": "Password valid."}


class LoginOtpRequestIn(BaseModel):
    identifier: str
    password: str

@router.post("/login/request-otp")
def login_request_otp(payload: LoginOtpRequestIn, db: Session = Depends(get_db)):
    """
    Step 1: Validate identifier + password.
    Step 2: If correct, send OTP to registered email.
    Identifier supports Phone OR Email.
    """
    ident = payload.identifier.strip()
    password = payload.password.strip()

    user = None
    if "@" in ident:
        user = db.query(User).filter(User.email == ident.lower()).first()
    elif ident.isdigit():
        user = db.query(User).filter(User.phone == ident).first()
    else:
        # per request, restrict to phone or email
        raise HTTPException(400, "Identifier must be phone or email.")

    if not user:
        raise HTTPException(404, "Account not found.")

    # Password check
    if not bcrypt.verify(password, user.password_hash):
        raise HTTPException(401, "Incorrect password.")

    # Generate OTP
    code = _gen_otp()
    expires = _now() + timedelta(minutes=OTP_EXP_MIN)

    db_otp = OTP(
        phone=user.phone,
        code=code,
        used=False,
        expires_at=expires
    )
    db.add(db_otp)
    db.commit()

    # Send email OTP
    try:
        send_email_otp(user.email, code)
    except Exception as e:
        raise HTTPException(502, f"Failed to send OTP email: {e}")

    masked = (user.email[:2] + "****@" + user.email.split("@", 1)[1]) if user.email else "your email"
    return {
        "ok": True,
        "message": f"OTP sent to {masked}"
    }


class LoginVerifyOtpIn(BaseModel):
    identifier: str
    password: str
    otp: str
    channel: Optional[str] = "app"

@router.post("/login/verify-otp")
def login_verify_otp(payload: LoginVerifyOtpIn, request: Request, db: Session = Depends(get_db)):
    """
    Verify OTP after identifier+password check. Returns JWT on success.
    Supports Phone OR Email for identifier (email->lookup user by email, phone->lookup by phone).
    """
    ident = payload.identifier.strip()
    password = payload.password.strip()
    otp = payload.otp.strip()
    channel = (payload.channel or "app").lower()

    if channel not in {"web", "app"}:
        raise HTTPException(400, "Invalid channel.")

    user = None
    if "@" in ident:
        user = db.query(User).filter(User.email == ident.lower()).first()
    elif ident.isdigit():
        user = db.query(User).filter(User.phone == ident).first()
    else:
        raise HTTPException(400, "Identifier must be phone or email.")

    if not user:
        raise HTTPException(404, "Account not found.")

    # Check password again for safety
    if not bcrypt.verify(password, user.password_hash):
        raise HTTPException(401, "Incorrect password.")

    # Check OTP
    db_otp = (
        db.query(OTP)
        .filter(OTP.phone == user.phone, OTP.used == False)
        .order_by(OTP.id.desc())
        .first()
    )
    if not db_otp:
        raise HTTPException(400, "No OTP found. Please request again.")
    if db_otp.expires_at <= _now():
        raise HTTPException(400, "OTP expired.")
    if db_otp.code != otp:
        raise HTTPException(400, "Invalid OTP.")

    # Mark OTP used
    db_otp.used = True
    db.commit()

    # Login success → generate JWT
    fingerprint = _collect_fingerprint(request)
    token = _jwt_for_user(user.id, channel=channel, fingerprint=fingerprint)

    return {
        "ok": True,
        "user_id": user.id,
        "access_token": token,
        "token_type": "bearer",
        "channel": channel,
        "user": {
            "id": user.id,
            "phone": user.phone,
            "email": user.email,
            "name": user.name,
            "upi_id": user.upi_id,
            "wallet_balance": float(user.wallet_balance or 0)
        }
    }


@router.post("/reset-password")
def reset_password_endpoint(
    payload: ResetPasswordIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update the authenticated user's password after OTP verification."""
    new_password = payload.password.strip()
    if len(new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")

    # bcrypt accepts up to 72 chars reliably
    hashed_pw = bcrypt.hash(new_password[:72])
    current_user.password_hash = hashed_pw
    db.add(current_user)
    db.commit()
    return {"ok": True, "message": "Password updated successfully."}


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


def _collect_fingerprint(request: Request) -> Optional[str]:
    user_agent = request.headers.get("user-agent", "")
    device = request.headers.get("x-device-fingerprint") or request.headers.get("x-device-id") or ""
    raw = "|".join(filter(None, [user_agent, device]))
    return hash_fingerprint(raw) if raw else None


@router.post(
    "/wallet-link",
    response_model=WalletLinkOut,
    dependencies=[Depends(require_channel("app"))],
)
def create_wallet_link(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    fingerprint = _collect_fingerprint(request)
    result = issue_wallet_bridge_token(
        db,
        user,
        channel=WALLET_LINK_CHANNEL,
        fingerprint=fingerprint,
    )
    return {"token": result["token"], "expires_in": result["expires_in"]}


@router.post(
    "/device-code",
    response_model=DeviceCodeOut,
    dependencies=[Depends(require_channel("app"))],
)
def create_device_code_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    fingerprint = _collect_fingerprint(request)
    record = issue_device_code(
        db,
        user,
        channel=WALLET_LINK_CHANNEL,
        fingerprint=fingerprint,
    )
    return {"code": record["code"], "expires_in": record["expires_in"]}


@router.post("/device-code/consume")
def consume_device_code_endpoint(
    payload: DeviceCodeConsumeIn,
    request: Request,
    db: Session = Depends(get_db),
):
    result = consume_device_code(db, payload.code)
    user = db.get(User, result["user_id"])
    if not user:
        raise HTTPException(404, "User not found")

    fingerprint = _collect_fingerprint(request)
    token = _jwt_for_user(user.id, channel=WALLET_LINK_CHANNEL, fingerprint=fingerprint)
    response = JSONResponse(
        {
            "ok": True,
            "user_id": user.id,
            "access_token": token,
            "token_type": "bearer",
            "channel": WALLET_LINK_CHANNEL,
        }
    )
    issue_wallet_cookie(response, {"user_id": user.id, "channel": WALLET_LINK_CHANNEL})
    return response
