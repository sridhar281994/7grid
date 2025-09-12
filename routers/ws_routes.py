from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import json
from utils.redis_client import redis_client

router = APIRouter(prefix="/ws", tags=["websocket"])

@router.websocket("/match/{match_id}")
async def ws_match(websocket: WebSocket, match_id: int):
    """
    WebSocket endpoint for realtime dice updates.
    Clients subscribe to Redis pub/sub channel for their match.
    """
    await websocket.accept()

    channel_name = f"match:{match_id}:updates"
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel_name)

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
        await pubsub.unsubscribe(channel_name)
    finally:
        await pubsub.close()
