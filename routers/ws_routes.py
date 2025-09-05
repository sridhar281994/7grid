from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import os, json
import redis.asyncio as redis

router = APIRouter(prefix="/ws", tags=["websocket"])
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r: redis.Redis | None = None

async def _r():
    global r
    if r is None:
        r = redis.from_url(REDIS_URL, decode_responses=True)
    return r

@router.websocket("/match/{match_id}")
async def ws_match(websocket: WebSocket, match_id: int):
    await websocket.accept()
    rr = await _r()
    chan = f"match:{match_id}:updates"
    ps = rr.pubsub()
    await ps.subscribe(chan)

    # send snapshot
    st = await rr.hgetall(f"match:{match_id}:state")
    if st:
        await websocket.send_json({
            "type": "state",
            "match_id": match_id,
            "status": st.get("status", "active"),
            "turn": int(st.get("turn", "0")),
            "p1_pos": int(st.get("p1_pos", "0")),
            "p2_pos": int(st.get("p2_pos", "0")),
            "last_roll": int(st.get("last_roll", "0")),
        })

    try:
        while True:
            msg = await ps.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if msg:
                try:
                    data = json.loads(msg["data"])
                except Exception:
                    data = {"raw": msg["data"]}
                await websocket.send_json(data)
    except WebSocketDisconnect:
        await ps.unsubscribe(chan)
    finally:
        await ps.close()
