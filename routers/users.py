from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from models import User
from utils.security import get_current_user

router = APIRouter(prefix="/users", tags=["users"])

@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "upi_id": user.upi_id,
        "wallet_balance": float(user.wallet_balance or 0),
        "created_at": user.created_at,
    }

@router.post("/me/profile")
def update_profile(name: str | None = None, upi_id: str | None = None,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    if name is not None:
        user.name = name
    if upi_id is not None:
        user.upi_id = upi_id
    db.commit()
    return {"ok": True}
