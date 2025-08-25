from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from schemas import UserOut, UpdateProfileIn
from models import User
from sqlalchemy import select
from typing import Optional

router = APIRouter(prefix="/users", tags=["users"])

def _get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()

@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = _get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return user

@router.patch("/{user_id}", response_model=UserOut)
def update_profile(user_id: int, payload: UpdateProfileIn, db: Session = Depends(get_db)):
    user = _get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if payload.name is not None:
        user.name = payload.name
    if payload.upi_id is not None:
        user.upi_id = payload.upi_id
    db.commit()
    db.refresh(user)
    return user
