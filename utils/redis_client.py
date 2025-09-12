import os
import asyncio
import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

redis_client: redis.Redis | None = None

async def init_redis_with_retry(max_retries: int = 5, delay: float = 2.0):
    """
    Initialize Redis with retries and exponential backoff.
    :param max_retries: Maximum number of retries before failing.
    :param delay: Initial delay between retries (seconds).
    """
    global redis_client
    attempt = 0
    while attempt < max_retries:
        try:
            redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            pong = await redis_client.ping()
            if pong:
                print(f"[INFO] Redis connected successfully on attempt {attempt+1}")
                return redis_client
        except Exception as e:
            print(f"[WARN] Redis connection failed (attempt {attempt+1}/{max_retries}): {e}")
            await asyncio.sleep(delay)
            delay *= 2 # exponential backoff
            attempt += 1

    print("[ERROR] Could not connect to Redis after retries.")
    redis_client = None
    return None
