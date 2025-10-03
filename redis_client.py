"""
redis_client.py
Central Redis connection handler for async publish/subscribe.
"""

import os
import redis.asyncio as redis

# Get Redis URL from environment (Render dashboard → Environment variables)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Single shared Redis connection
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

async def _get_redis():
    """Always return a live redis client, with auto-reconnect if needed."""
    global redis_client
    try:
        await redis_client.ping()
        return redis_client
    except Exception as e:
        print(f"[REDIS][WARN] Redis unavailable, reconnecting: {e}")
        try:
            redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            await redis_client.ping()
            print("[REDIS] Reconnected successfully")
            return redis_client
        except Exception as e2:
            print(f"[REDIS][ERROR] Reconnect failed: {e2}")
            return None
