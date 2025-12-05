import random
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, constr
from sqlalchemy.orm import Session

from database import get_db
from models import User
from utils.security import get_current_user

router = APIRouter(prefix="/users", tags=["users"])



# ============================================================
#                     Pydantic Schemas
# ============================================================

class UserOut(BaseModel):
    id: int
    email: str | None = None
    phone: str | None = None
    name: str | None = None
    upi_id: str | None = None
    paypal_id: str | None = None
    description: str | None = None
    wallet_balance: float
    created_at: datetime | None = None
    profile_image: str | None = None   # supports bots + real users

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    name: str | None = None
    upi_id: str | None = None
    paypal_id: str | None = None
    description: constr(max_length=50) | None = None



# ============================================================
#                        BOT PROFILES
# ============================================================

BOT_PROFILES = [
    {
        "id": -1000,
        "name": "Sharp",
        "wallet_balance": 0,
        "description": "AI Opponent",
        "email": None,
        "upi_id": None,
        "created_at": None,
        "profile_image": "assets/bot_sharp.png",
    },
    {
        "id": -1001,
        "name": "Crazy Boy",
        "wallet_balance": 0,
        "description": "AI Opponent",
        "email": None,
        "upi_id": None,
        "created_at": None,
        "profile_image": "assets/bot_crazy.png",
    },
    {
        "id": -1002,
        "name": "Kurfi",
        "wallet_balance": 0,
        "description": "AI Opponent",
        "email": None,
        "upi_id": None,
        "created_at": None,
        "profile_image": "assets/bot_kurfi.png",
    },
]


def _bot_profile(user_id: int) -> dict:
    """
    Return the specified bot profile.
    If no direct match, return a random bot.
    """
    for bot in BOT_PROFILES:
        if bot["id"] == user_id:
            return bot
    return random.choice(BOT_PROFILES)



# ============================================================
#                      USER ENDPOINTS
# ============================================================

@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    """
    Return current authenticated user.
    If ID <= 0 => treat as bot.
    """
    if user.id <= 0:
        return _bot_profile(user.id)

    return {
        **user.__dict__,
        "profile_image": user.profile_image or "assets/default.png",
    }



@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UserUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Update name, upi_id, description.
    Bot accounts cannot be modified.
    """
    if user.id <= 0:
        raise HTTPException(status_code=400, detail="Bots cannot be updated")

    if payload.name is not None:
        user.name = payload.name.strip() or None

    if payload.upi_id is not None:
        user.upi_id = payload.upi_id.strip() or None

    if payload.paypal_id is not None:
        user.paypal_id = payload.paypal_id.strip() or None

    if payload.description is not None:
        user.description = payload.description.strip() or None

    db.commit()
    db.refresh(user)

    return {
        **user.__dict__,
        "profile_image": user.profile_image or "assets/default.png",
    }



@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db)):
    """
    Fetch any user by ID.
    Supports:
        • Bot IDs (<= 0)
        • Real users
    """
    # BOT USER
    if user_id <= 0:
        return _bot_profile(user_id)

    # REAL USER
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        **user.__dict__,
        "profile_image": user.profile_image or "assets/default.png",
    }
