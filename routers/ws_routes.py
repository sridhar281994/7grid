from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import redis.asyncio as redis
import json
import os

router = APIRouter(prefix="/ws", tags=["websocket"])

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

def _ch(match_id: int) -> str:
    return f"match:{match_id}:updates"

def rconn() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)

@router.websocket("/match/{match_id}")
async def ws_match(websocket: WebSocket, match_id: int):
    await websocket.accept()
    r = rconn()
    pubsub = r.pubsub()
    await pubsub.subscribe(_ch(match_id))
    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if msg:
                try:
                    data = json.loads(msg["data"])
                except Exception:
                    data = {"raw": msg["data"]}
                await websocket.send_json(data)
    except WebSocketDisconnect:
        await pubsub.unsubscribe(_ch(match_id))
    finally:
        await pubsub.close()
