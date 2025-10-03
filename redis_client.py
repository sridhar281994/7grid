"""
redis_client.py
Central Redis connection handler for async publish/subscribe.
"""
import os
import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client = None

async def _get_redis():
    """Always return a live Redis client, with auto-reconnect if needed."""
    global redis_client
    if redis_client is None:
        print(f"[REDIS] Initializing client {REDIS_URL}")
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)

    try:
        await redis_client.ping()
        return redis_client
    except Exception as e:
        print(f"[REDIS][WARN] Ping failed, reconnecting: {e}")
        try:
            redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            await redis_client.ping()
            print("[REDIS] Reconnected successfully")
            return redis_client
        except Exception as e2:
            print(f"[REDIS][ERROR] Reconnect failed: {e2}")
            redis_client = None
            return None
