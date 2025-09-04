from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
import redis.asyncio as redis
import json
import os

router = APIRouter(prefix="/ws", tags=["websocket"])

# Redis connection URL
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Create global Redis connection (lazy)
redis_client: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    global redis_client
    if redis_client is None:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return redis_client


@router.websocket("/match/{match_id}")
async def ws_match(websocket: WebSocket, match_id: int):
    """
    WebSocket endpoint for realtime dice updates.
    Clients subscribe to Redis pub/sub channel for their match.
    """
    await websocket.accept()

    channel_name = f"match:{match_id}:updates"
    redis_conn = await get_redis()
    pubsub = redis_conn.pubsub()
    await pubsub.subscribe(channel_name)

    try:
        # Run a loop: listen to Redis messages and forward to client
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if msg:
                try:
                    data = json.loads(msg["data"])
                except Exception:
                    data = {"raw": msg["data"]}
                await websocket.send_json(data)
    except WebSocketDisconnect:
        # Cleanup when client disconnects
        await pubsub.unsubscribe(channel_name)
    finally:
        await pubsub.close()
