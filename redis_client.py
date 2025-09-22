import os
import redis.asyncio as redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
async def _get_redis():
    """Compatibility wrapper for old imports"""
    try:
        await redis_client.ping()
        return redis_client
    except Exception as e:
        print(f"[WARN] Redis unavailable: {e}")
        return None
