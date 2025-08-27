import os
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from database import get_db
from models import User

JWT_SECRET = os.getenv("JWT_SECRET", "change_me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MIN = int(os.getenv("JWT_EXP_MIN", "43200"))  # default 30 days

bearer = HTTPBearer()

def _now() -> datetime:
    return datetime.now(timezone.utc)

def make_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "iat": int(_now().timestamp()),
        "exp": int((_now() + timedelta(minutes=JWT_EXP_MIN)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def get_current_user(
    cred: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    token = cred.credentials
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        uid = int(data.get("sub"))
    except (JWTError, ValueError):
        raise HTTPException(401, "Invalid token")

    user = db.get(User, uid)
    if not user:
        raise HTTPException(401, "User not found")
    return user
