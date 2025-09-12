import os
import redis.asyncio as redis

# Get Redis URL from environment variable
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Create a single shared Redis client (connection pool)
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
