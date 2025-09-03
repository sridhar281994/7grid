from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from models import GameMatch, MatchStatus, User
from utils.security import get_current_user

router = APIRouter(prefix="/matches", tags=["matches"])


# ---------- Helpers ----------
def _match_to_dict(m: GameMatch) -> dict:
    return {
        "id": m.id,
        "stake_amount": m.stake_amount,
        "status": m.status.value if hasattr(m.status, "value") else str(m.status),
        "p1_user_id": m.p1_user_id,
        "p2_user_id": m.p2_user_id,
        "created_at": getattr(m, "created_at", None),
    }


def _user_display(db: Session, user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    u: Optional[User] = db.query(User).filter(User.id == user_id).first()
    if not u:
        return None
    return (u.name or u.email or f"User {u.id}")


# ---------- Routes ----------
@router.get("/list")
def list_waiting_matches(
    stake_amount: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(GameMatch).filter(GameMatch.status == MatchStatus.WAITING)
    if stake_amount is not None:
        q = q.filter(GameMatch.stake_amount == stake_amount)
    matches = q.order_by(GameMatch.id.desc()).all()
    items = [_match_to_dict(m) for m in matches]
    return {"ok": True, "items": items}


class MatchCreateIn(BaseModel):
    stake_amount: int


@router.post("/create")
def create_or_wait_match(
    payload: MatchCreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = (
        db.query(GameMatch)
        .filter(
            GameMatch.status == MatchStatus.WAITING,
            GameMatch.stake_amount == payload.stake_amount,
            GameMatch.p1_user_id.isnot(None),
            GameMatch.p1_user_id != current_user.id,
        )
        .order_by(GameMatch.id.asc())
        .first()
    )

    if existing:
        existing.p2_user_id = current_user.id
        existing.status = MatchStatus.ACTIVE
        db.commit()
        db.refresh(existing)
        return {
            "ok": True,
            "joined": True,
            "match": _match_to_dict(existing),
            "p1_name": _user_display(db, existing.p1_user_id),
            "p2_name": _user_display(db, existing.p2_user_id),
        }

    new_match = GameMatch(
        stake_amount=payload.stake_amount,
        status=MatchStatus.WAITING,
        p1_user_id=current_user.id,
        p2_user_id=None,
    )
    db.add(new_match)
    db.commit()
    db.refresh(new_match)
    return {
        "ok": True,
        "joined": False,
        "match": _match_to_dict(new_match),
        "p1_name": _user_display(db, new_match.p1_user_id),
        "p2_name": _user_display(db, new_match.p2_user_id),
    }


class JoinMatchIn(BaseModel):
    match_id: int


@router.post("/join")
def join_match(
    payload: JoinMatchIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    m: Optional[GameMatch] = db.query(GameMatch).filter(GameMatch.id == payload.match_id).first()
    if not m:
        raise HTTPException(404, "Match not found")
    if m.status != MatchStatus.WAITING:
        raise HTTPException(400, "Match is not waiting")
    if m.p1_user_id == current_user.id:
        raise HTTPException(400, "You cannot join your own match")

    m.p2_user_id = current_user.id
    m.status = MatchStatus.ACTIVE
    db.commit()
    db.refresh(m)
    return {
        "ok": True,
        "match": _match_to_dict(m),
        "p1_name": _user_display(db, m.p1_user_id),
        "p2_name": _user_display(db, m.p2_user_id),
    }


@router.get("/check")
def check_match_ready(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    m: Optional[GameMatch] = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not m:
        raise HTTPException(404, "Match not found")

    ready = m.status == MatchStatus.ACTIVE
    opponent_name: Optional[str] = None

    if ready:
        if current_user.id == m.p1_user_id:
            opponent_name = _user_display(db, m.p2_user_id)
        elif current_user.id == m.p2_user_id:
            opponent_name = _user_display(db, m.p1_user_id)

    return {
        "ok": True,
        "ready": ready,
        "status": m.status.value if hasattr(m.status, "value") else str(m.status),
        "p1_name": _user_display(db, m.p1_user_id),
        "p2_name": _user_display(db, m.p2_user_id),
        "opponent_name": opponent_name,
        "match": _match_to_dict(m),
    }
