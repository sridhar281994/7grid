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
