from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, conint
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from models import GameMatch, User, MatchStatus
from utils.security import get_current_user

router = APIRouter(prefix="/matches", tags=["matches"])


# ---- Request bodies ----
class CreateIn(BaseModel):
    stake_amount: conint(gt=0)


# ---- helpers ----
def _now():
    return datetime.now(timezone.utc)


def _name_for(u: Optional[User]) -> str:
    if not u:
        return "Player"
    return (u.name or (u.email or "").split("@")[0] or u.phone or f"User#{u.id}")


# -------------------------
# Create or wait for match
# -------------------------
@router.post("/create")
def create_or_wait_match(
    payload: CreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    try:
        stake_amount = int(payload.stake_amount)

        # 1) Try existing waiting room (not created by me)
        waiting = (
            db.query(GameMatch)
            .filter(
                GameMatch.status == MatchStatus.WAITING,
                GameMatch.stake_amount == stake_amount,
                GameMatch.p1_user_id != current_user.id,
            )
            .order_by(GameMatch.id.asc())
            .first()
        )

        if waiting:
            waiting.p2_user_id = current_user.id
            waiting.status = MatchStatus.ACTIVE
            waiting.started_at = _now()
            db.commit()
            db.refresh(waiting)

            p1 = db.get(User, waiting.p1_user_id)
            p2 = db.get(User, waiting.p2_user_id)

            return {
                "ok": True,
                "match_id": waiting.id,
                "status": waiting.status.value,
                "stake": waiting.stake_amount,
                "p1": _name_for(p1),
                "p2": _name_for(p2),
            }

        # 2) Else create a new waiting room
        new_match = GameMatch(
            stake_amount=stake_amount,
            status=MatchStatus.WAITING,
            p1_user_id=current_user.id,
        )
        db.add(new_match)
        db.commit()
        db.refresh(new_match)

        p1 = db.get(User, new_match.p1_user_id)

        return {
            "ok": True,
            "match_id": new_match.id,
            "status": new_match.status.value,
            "stake": new_match.stake_amount,
            "p1": _name_for(p1),
            "p2": None,
        }

    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB Error: {e}")


# -------------------------
# Poll match readiness
# -------------------------
@router.get("/check")
def check_match_ready(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    if m.status == MatchStatus.ACTIVE and m.p1_user_id and m.p2_user_id:
        p1 = db.get(User, m.p1_user_id)
        p2 = db.get(User, m.p2_user_id)
        return {
            "ready": True,
            "match_id": m.id,
            "status": m.status.value,
            "stake": m.stake_amount,
            "p1": _name_for(p1),
            "p2": _name_for(p2),
        }

    return {"ready": False, "status": m.status.value}


# -------------------------
# Cancel match
# -------------------------
@router.post("/{match_id}/cancel")
def cancel_match(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict:
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    if current_user.id not in [m.p1_user_id, m.p2_user_id]:
        raise HTTPException(status_code=403, detail="Not your match")

    db.delete(m)
    db.commit()
    return {"ok": True, "message": "Match cancelled"}


# -------------------------
# List matches (debug/admin)
# -------------------------
@router.get("/list")
def list_matches(db: Session = Depends(get_db)) -> Dict:
    matches = db.query(GameMatch).all()
    return [
        {
            "id": m.id,
            "stake": m.stake_amount,
            "status": m.status.value if hasattr(m.status, "value") else str(m.status),
            "p1": m.p1_user_id,
            "p2": m.p2_user_id,
            "created_at": m.created_at,
        }
        for m in matches
    ]
