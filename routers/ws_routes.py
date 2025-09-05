from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
import redis.asyncio as redis
import json
import os

from sqlalchemy.orm import Session
from database import get_db
from models import GameMatch, MatchStatus
from utils.security import get_current_user

router = APIRouter(prefix="/ws", tags=["websocket"])

# Redis connection URL
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Global Redis connection
redis_client: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    global redis_client
    if redis_client is None:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return redis_client


@router.websocket("/match/{match_id}")
async def ws_match(
    websocket: WebSocket,
    match_id: int,
    db: Session = Depends(get_db),
):
    """
    WebSocket endpoint for realtime dice updates.
    - Subscribes to Redis channel for live updates
    - Sends latest DB state immediately on connect
    """
    await websocket.accept()

    redis_conn = await get_redis()
    channel_name = f"match:{match_id}:updates"
    pubsub = redis_conn.pubsub()
    await pubsub.subscribe(channel_name)

    # Send latest DB state immediately
    m = db.query(GameMatch).filter(GameMatch.id == match_id).first()
    if m and m.status == MatchStatus.ACTIVE:
        state = {
            "type": "state",
            "match_id": m.id,
            "turn": m.current_turn,
            "last_roll": m.last_roll,
            "p1_id": m.p1_user_id,
            "p2_id": m.p2_user_id,
        }
        await websocket.send_json(state)

    try:
        # Loop: forward Redis messages to client
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if msg:
                try:
                    data = json.loads(msg["data"])
                except Exception:
                    data = {"raw": msg["data"]}
                await websocket.send_json(data)

    except WebSocketDisconnect:
        await pubsub.unsubscribe(channel_name)
    finally:
        await pubsub.close()
