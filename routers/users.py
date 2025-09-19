from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import User
from utils.security import get_current_user

router = APIRouter(prefix="/users", tags=["users"])


# -----------------------------
# Schemas
# -----------------------------
class UserOut(BaseModel):
    id: int
    email: str | None = None
    name: str | None = None
    upi_id: str | None = None
    desc: str | None = None # ✅ include description
    wallet_balance: float
    created_at: str | None = None

    class Config:
        from_attributes = True # pydantic v2


class UserUpdate(BaseModel):
    name: str | None = None
    upi_id: str | None = None
    desc: str | None = None # ✅ allow updating description


# -----------------------------
# Endpoints
# -----------------------------
@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "upi_id": user.upi_id,
        "desc": getattr(user, "desc", None), # ✅ safe fetch if column exists
        "wallet_balance": float(user.wallet_balance or 0),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UserUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.name is not None:
        user.name = payload.name
    if payload.upi_id is not None:
        user.upi_id = payload.upi_id
    if payload.desc is not None: # ✅ support description updates
        user.desc = payload.desc

    db.commit()
    db.refresh(user)
    return user
