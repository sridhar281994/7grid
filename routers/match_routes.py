from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import GameMatch, MatchStatus, User
from utils.redis_client import redis_client
from sqlalchemy import and_
from typing import Dict
import json
import asyncio

router = APIRouter(prefix="/matches", tags=["matches"])


# -----------------------------
# Helpers
# -----------------------------
async def _init_state(match_id: int, stake_amount: int):
    """Initialize redis state for a new match."""
    if not redis_client:
        return
    key = f"match:{match_id}:state"
    exists = await redis_client.exists(key)
    if not exists:
        state = {"stake": stake_amount, "turn": 0, "last_roll": None}
        await redis_client.set(key, json.dumps(state))


async def _broadcast_match_ready(match_id: int, p1: str, p2: str):
    """Notify both clients when match is ready."""
    if not redis_client:
        return
    event = {"event": "ready", "match_id": match_id, "p1": p1, "p2": p2}
    channel = f"match:{match_id}:updates"
    try:
        await redis_client.publish(channel, json.dumps(event))
    except Exception as e:
        print(f"[WARN] Redis publish failed on {channel}: {e}")


# -----------------------------
# Routes
# -----------------------------
@router.post("/create")
async def create_or_join_match(payload: Dict, db: Session = Depends(get_db), current_user: User = Depends(storage.get_current_user)):
    """
    Either create a new match (waiting) or join an existing waiting match with the same stake.
    """
    stake = payload.get("stake_amount")
    if not stake:
        raise HTTPException(status_code=400, detail="stake_amount required")

    # Check if there's an existing waiting match
    waiting_match = (
        db.query(GameMatch)
        .filter(and_(GameMatch.stake_amount == stake, GameMatch.status == MatchStatus.WAITING))
        .first()
    )

    if waiting_match:
        # Join existing match
        waiting_match.p2_user_id = current_user.id
        waiting_match.status = MatchStatus.ACTIVE
        db.commit()
        db.refresh(waiting_match)

        # Load player names
        p1 = db.query(User).filter(User.id == waiting_match.p1_user_id).first()
        p2 = db.query(User).filter(User.id == waiting_match.p2_user_id).first()

        # Broadcast ready
        await _init_state(waiting_match.id, stake)
        await _broadcast_match_ready(waiting_match.id, p1.name or p1.phone, p2.name or p2.phone)

        return {
            "ok": True,
            "match_id": waiting_match.id,
            "stake": stake,
            "p1": p1.name or p1.phone,
            "p2": p2.name or p2.phone,
            "ready": True,
        }

    else:
        # Create new match as waiting
        new_match = GameMatch(
            stake_amount=stake,
            p1_user_id=current_user.id,
            status=MatchStatus.WAITING,
        )
        db.add(new_match)
        db.commit()
        db.refresh(new_match)

        return {
            "ok": True,
            "match_id": new_match.id,
            "stake": stake,
            "p1": current_user.name or current_user.phone,
            "ready": False,
        }


@router.get("/check")
async def check_match(match_id: int, db: Session = Depends(get_db), current_user: User = Depends(storage.get_current_user)):
    """
    Check if match is ready (p2 has joined).
    """
    match = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    p1 = db.query(User).filter(User.id == match.p1_user_id).first()
    p2 = db.query(User).filter(User.id == match.p2_user_id).first() if match.p2_user_id else None

    return {
        "ok": True,
        "match_id": match.id,
        "stake": match.stake_amount,
        "p1": p1.name or p1.phone if p1 else None,
        "p2": p2.name or p2.phone if p2 else None,
        "ready": match.status == MatchStatus.ACTIVE,
    }
