from datetime import datetime
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
    description: str | None = None  # ✅ matches models.py column
    wallet_balance: float
    created_at: datetime | None = None  # ✅ accept datetime directly

    class Config:
        from_attributes = True  # pydantic v2


class UserUpdate(BaseModel):
    name: str | None = None
    upi_id: str | None = None
    description: constr(max_length=50) | None = None  # ✅ enforce 50 chars


# -----------------------------
# Endpoints
# -----------------------------
@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UserUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update only the provided fields. Missing fields stay unchanged."""
    if payload.name is not None:
        user.name = payload.name.strip() or None
    if payload.upi_id is not None:
        user.upi_id = payload.upi_id.strip() or None
    if payload.description is not None:
        user.description = payload.description.strip() or None

    db.commit()
    db.refresh(user)
    return user
