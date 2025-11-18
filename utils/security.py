import os
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, WebSocket
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

from database import SessionLocal, get_db
from models import User

# JWT config
JWT_SECRET = os.getenv("JWT_SECRET", "change_me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")

_auth = HTTPBearer(auto_error=False)


def _now():
    return datetime.now(timezone.utc)


# --------------------------
# REST authentication
# --------------------------
def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_auth),
    db=Depends(get_db),
) -> User:
    if not creds or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = creds.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid token (no sub)")
        user_id = int(sub)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


# --------------------------
# WebSocket authentication
# --------------------------
async def get_current_user_ws(websocket: WebSocket) -> User:
    """
    Authenticate a WebSocket connection using a token
    from query params (?token=...) or 'Authorization: Bearer ...' header.
    Closes DB session immediately after lookup (no leaks).
    """
    token = None

    # 1. Query param
    token = websocket.query_params.get("token")

    # 2. Authorization header
    if not token:
        auth_header = websocket.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if not token:
        await websocket.close(code=4001)
        raise HTTPException(status_code=401, detail="Missing token")

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        sub = payload.get("sub")
        if not sub:
            await websocket.close(code=4002)
            raise HTTPException(status_code=401, detail="Invalid token (no sub)")
        user_id = int(sub)
    except JWTError:
        await websocket.close(code=4003)
        raise HTTPException(status_code=401, detail="Invalid token")

    # Open/close session immediately (no connection leaks)
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
