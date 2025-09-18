"""
Redis connection management for Gobii platform.

Provides a centralized, production-ready Redis client for all use cases:
- Distributed locking (Redlock)
- Administrative operations (Celery beat scheduling, cleanup tasks)  
- Basic connectivity testing and one-off operations

All operations use the same underlying Redis instance configured via REDIS_URL.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Final, Any
import os

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

# Redis URL from Django settings
REDIS_URL: Final[str] = settings.REDIS_URL


@lru_cache(maxsize=1)
def get_redis_client() -> Any:
    """Return a configured Redis client.

    Tests should patch this function if they require an in-memory substitute.
    """
    if os.getenv("USE_FAKE_REDIS") == "1":
        raise RuntimeError(
            "USE_FAKE_REDIS is no longer supported in production code. "
            "Patch config.redis_client.get_redis_client() in tests instead."
        )

    if not REDIS_URL:
        raise RuntimeError("REDIS_URL is not configured; cannot create Redis client.")

    client = redis.from_url(
        REDIS_URL,
        decode_responses=True,     # Return str instead of bytes for cleaner code
        health_check_interval=30,  # PING every 30s when idle for auto-reconnection
    )

    try:
        client.ping()  # Fail fast if Redis is unavailable
    except redis.RedisError:
        logger.error("Failed to connect to Redis at %s", REDIS_URL)
        raise

    logger.info("Created Redis client: %s", REDIS_URL)
    return client
