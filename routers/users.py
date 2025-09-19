from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, constr
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
    description: str | None = None # ✅ match models.py column
    wallet_balance: float
    created_at: str | None = None

    class Config:
        from_attributes = True # pydantic v2


class UserUpdate(BaseModel):
    name: str | None = None
    upi_id: str | None = None
    description: constr(max_length=50) | None = None # ✅ enforce 50 chars


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
        "description": user.description, # ✅ safe fetch
        "wallet_balance": float(user.wallet_balance or 0),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UserUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    updated = False

    if payload.name is not None:
        user.name = payload.name
        updated = True
    if payload.upi_id is not None:
        user.upi_id = payload.upi_id
        updated = True
    if payload.description is not None:
        user.description = payload.description
        updated = True

    if not updated:
        raise HTTPException(400, "No fields to update")

    db.commit()
    db.refresh(user)
    return user
