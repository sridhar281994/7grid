import hashlib
import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

from fastapi import Depends, HTTPException, Request, Response, WebSocket
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from models import (
    User,
    WalletBridgeToken,
    WalletDeviceCode,
    AdminMFASecret,
)

# JWT / security config
JWT_SECRET = os.getenv("JWT_SECRET", "change_me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MIN = int(os.getenv("JWT_EXP_MIN", str(60 * 24 * 30)))
WALLET_COOKIE_SECRET = os.getenv("WALLET_COOKIE_SECRET", JWT_SECRET)
WALLET_COOKIE_NAME = os.getenv("WALLET_COOKIE_NAME", "wallet_session")
WALLET_COOKIE_MAX_AGE = int(os.getenv("WALLET_COOKIE_MAX_AGE", "86400"))  # 1 day
WALLET_COOKIE_SECURE = os.getenv("WALLET_COOKIE_SECURE", "true").lower() == "true"
BRIDGE_TOKEN_TTL = int(os.getenv("WALLET_BRIDGE_TOKEN_TTL", "180"))
DEVICE_CODE_TTL = int(os.getenv("WALLET_DEVICE_CODE_TTL", "300"))
ADMIN_USER_IDS = {
    int(part) for part in os.getenv("ADMIN_USER_IDS", "").split(",") if part.strip().isdigit()
}

_auth = HTTPBearer(auto_error=False)
_wallet_serializer = URLSafeTimedSerializer(WALLET_COOKIE_SECRET, salt="wallet-cookie")


def _now():
    return datetime.now(timezone.utc)


def _hash_value(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_access_token(
    user_id: int,
    *,
    channel: str,
    fingerprint: Optional[str] = None,
    expires_minutes: Optional[int] = None,
) -> str:
    exp_minutes = expires_minutes or JWT_EXP_MIN
    payload = {
        "sub": str(user_id),
        "channel": channel,
        "fingerprint": fingerprint,
        "iat": int(_now().timestamp()),
        "exp": int((_now() + timedelta(minutes=exp_minutes)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def get_request_context(request: Request) -> dict:
    ip = getattr(request.state, "client_ip", None)
    if not ip and request.client:
        ip = request.client.host
    user_agent = getattr(request.state, "user_agent", None) or request.headers.get("user-agent")
    fingerprint = (
        getattr(request.state, "device_fingerprint", None)
        or request.headers.get("x-device-fingerprint")
        or request.headers.get("x-device-id")
    )
    return {
        "ip": ip,
        "user_agent": user_agent,
        "fingerprint": _hash_value("|".join(filter(None, [user_agent or "", fingerprint or ""]))),
    }


def hash_fingerprint(raw: Optional[str]) -> Optional[str]:
    return _hash_value(raw)


# --------------------------
# JWT payload decoding
# --------------------------
def _decode_token(token: str) -> dict:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    if not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid token (missing sub)")
    return payload


def get_token_payload(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(_auth),
) -> dict:
    token = None
    if creds and creds.scheme.lower() == "bearer":
        token = creds.credentials

    if token:
        try:
            return _decode_token(token)
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid token")

    cookie_value = request.cookies.get(WALLET_COOKIE_NAME)
    if cookie_value:
        session_payload = verify_wallet_cookie(cookie_value)
        return {
            "sub": str(session_payload["user_id"]),
            "channel": session_payload.get("channel"),
            "fingerprint": session_payload.get("fingerprint"),
            "source": "wallet_cookie",
        }

    raise HTTPException(status_code=401, detail="Missing bearer token")


def get_current_user(
    payload: dict = Depends(get_token_payload),
    db: Session = Depends(get_db),
) -> User:
    user_id = int(payload["sub"])
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_channel(expected: str) -> Callable:
    def _dependency(payload: dict = Depends(get_token_payload)) -> dict:
        channel = payload.get("channel")
        if channel != expected:
            raise HTTPException(403, f"Endpoint restricted to channel '{expected}'")
        return payload

    return _dependency


def _user_is_admin(user: User) -> bool:
    if ADMIN_USER_IDS:
        return user.id in ADMIN_USER_IDS
    return os.getenv("ALLOW_ADMIN", "false").lower() == "true"


def require_admin(mfa_required: bool = False) -> Callable:
    def _dependency(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        if not _user_is_admin(user):
            raise HTTPException(403, "Admin privileges required")

        if mfa_required:
            secret = db.get(AdminMFASecret, user.id)
            if secret and not secret.last_verified_at:
                raise HTTPException(403, "Admin MFA verification required")
        return user

    return _dependency


# --------------------------
# Wallet bridge tokens
# --------------------------
def issue_wallet_bridge_token(
    db: Session,
    user: User,
    *,
    channel: str,
    fingerprint: Optional[str],
) -> dict:
    db.query(WalletBridgeToken).filter(WalletBridgeToken.user_id == user.id).delete()
    token = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(seconds=BRIDGE_TOKEN_TTL)
    record = WalletBridgeToken(
        user_id=user.id,
        token=token,
        channel=channel,
        device_fingerprint=fingerprint,
        expires_at=expires_at,
    )
    db.add(record)
    db.commit()
    return {"token": token, "expires_in": BRIDGE_TOKEN_TTL, "expires_at": expires_at}


def consume_wallet_bridge_token(db: Session, token: str) -> dict:
    record = (
        db.query(WalletBridgeToken)
        .filter(WalletBridgeToken.token == token)
        .first()
    )
    if not record:
        raise HTTPException(400, "Invalid or expired wallet link")
    if record.expires_at <= _now():
        db.delete(record)
        db.commit()
        raise HTTPException(400, "Wallet link expired")

    payload = {
        "user_id": record.user_id,
        "channel": record.channel,
        "fingerprint": record.device_fingerprint,
    }
    db.delete(record)
    db.commit()
    return payload


# --------------------------
# Device codes
# --------------------------
def _generate_device_code(length: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))


def issue_device_code(
    db: Session,
    user: User,
    *,
    channel: str,
    fingerprint: Optional[str],
) -> dict:
    code = _generate_device_code()
    expires_at = _now() + timedelta(seconds=DEVICE_CODE_TTL)
    db.query(WalletDeviceCode).filter(WalletDeviceCode.user_id == user.id).delete()
    record = WalletDeviceCode(
        user_id=user.id,
        code=code,
        channel=channel,
        device_fingerprint=fingerprint,
        expires_at=expires_at,
    )
    db.add(record)
    db.commit()
    return {"code": code, "expires_in": DEVICE_CODE_TTL, "expires_at": expires_at}


def consume_device_code(db: Session, code: str) -> dict:
    record = (
        db.query(WalletDeviceCode)
        .filter(WalletDeviceCode.code == code)
        .first()
    )
    if not record:
        raise HTTPException(400, "Invalid device code")
    if record.expires_at <= _now():
        db.delete(record)
        db.commit()
        raise HTTPException(400, "Device code expired")

    payload = {
        "user_id": record.user_id,
        "channel": record.channel,
        "fingerprint": record.device_fingerprint,
    }
    db.delete(record)
    db.commit()
    return payload


# --------------------------
# Wallet cookies
# --------------------------
def issue_wallet_cookie(response: Response, data: dict, max_age: Optional[int] = None):
    token = _wallet_serializer.dumps(data)
    response.set_cookie(
        key=WALLET_COOKIE_NAME,
        value=token,
        max_age=max_age or WALLET_COOKIE_MAX_AGE,
        httponly=True,
        secure=WALLET_COOKIE_SECURE,
        samesite="none",
    )


def clear_wallet_cookie(response: Response):
    response.delete_cookie(WALLET_COOKIE_NAME)


def verify_wallet_cookie(value: str, max_age: Optional[int] = None) -> dict:
    try:
        return _wallet_serializer.loads(value, max_age=max_age or WALLET_COOKIE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "Wallet session expired")


# --------------------------
# WebSocket authentication
# --------------------------
async def get_current_user_ws(websocket: WebSocket) -> User:
    """
    Authenticate a WebSocket connection using a token
    from query params (?token=...) or 'Authorization: Bearer ...' header.
    """
    token = websocket.query_params.get("token")
    if not token:
        auth_header = websocket.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if not token:
        await websocket.close(code=4001)
        raise HTTPException(status_code=401, detail="Missing token")

    try:
        payload = _decode_token(token)
        user_id = int(payload["sub"])
    except JWTError:
        await websocket.close(code=4003)
        raise HTTPException(status_code=401, detail="Invalid token")

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            await websocket.close(code=4004)
            raise HTTPException(status_code=401, detail="User not found")
        return user
    finally:
        db.close()


# --------------------------
# Internal: Fake user object for agent AI
# --------------------------
class FakeUser:
    """
    Lightweight internal user placeholder for agent auto-roll logic.
    Not related to JWT or authentication.
    """

    def __init__(self, uid: int):
        self.id = uid
        self.wallet_balance = 999999
