import json
import aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from utils.security import get_current_user
from sqlalchemy.orm import Session
from database import get_db
from models import GameMatch

router = APIRouter()

# Redis channel name pattern
REDIS_URL = "redis://localhost:6379" # use Render/Upstash Redis in prod

# Active connections in-memory (per backend worker)
connections: dict[int, set[WebSocket]] = {}

async def broadcast(match_id: int, message: dict):
    """Send a message to all WebSocket clients in this match."""
    if match_id in connections:
        for ws in list(connections[match_id]):
            try:
                await ws.send_json(message)
            except Exception:
                pass # ignore broken sockets

@router.websocket("/ws/match/{match_id}")
async def match_ws(
    websocket: WebSocket,
    match_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await websocket.accept()

    # Register this connection
    connections.setdefault(match_id, set()).add(websocket)

    # Notify others
    await broadcast(match_id, {
        "type": "join",
        "user_id": current_user.id,
        "name": current_user.name or current_user.phone,
    })

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except Exception:
                msg = {"raw": data}

            # Example: {"type": "roll", "value": 5}
            if msg.get("type") == "roll":
                roll_value = int(msg.get("value"))
                await broadcast(match_id, {
                    "type": "roll",
                    "user_id": current_user.id,
                    "value": roll_value,
                })

            # Example: {"type": "move", "pos": 3}
            elif msg.get("type") == "move":
                await broadcast(match_id, {
                    "type": "move",
                    "user_id": current_user.id,
                    "pos": msg.get("pos"),
                })

    except WebSocketDisconnect:
        # Remove connection
        connections[match_id].discard(websocket)
        if not connections[match_id]:
            connections.pop(match_id, None)
        await broadcast(match_id, {
            "type": "leave",
            "user_id": current_user.id,
        })
