"""
Redis connection management for Gobii platform.

Provides a centralized, production-ready Redis client for all use cases:
- Distributed locking (Redlock)
- Administrative operations (Celery beat scheduling, cleanup tasks)  
- Basic connectivity testing and one-off operations

All operations use the same underlying Redis instance configured via REDIS_URL.
"""
import logging
from functools import lru_cache
from typing import Final, Any, Dict, Optional
import os

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

# Redis URL from Django settings
REDIS_URL: Final[str] = settings.REDIS_URL


class _FakePipeline:
    def __init__(self, client: "_FakeRedis"):
        self._client = client
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    # Mirror methods used in code; store and replay on execute
    def hset(self, *args, **kwargs):
        self._ops.append(("hset", args, kwargs)); return self
    def expire(self, *args, **kwargs):
        self._ops.append(("expire", args, kwargs)); return self
    def set(self, *args, **kwargs):
        self._ops.append(("set", args, kwargs)); return self
    def delete(self, *args, **kwargs):
        self._ops.append(("delete", args, kwargs)); return self

    def execute(self):
        for name, args, kwargs in self._ops:
            getattr(self._client, name)(*args, **kwargs)
        self._ops.clear()
        return True


class _FakeRedis:
    def __init__(self):
        self._kv: Dict[str, Any] = {}
        self._hash: Dict[str, Dict[str, Any]] = {}
        self._ttl: Dict[str, int] = {}
        self._lists: Dict[str, list] = {}

    # Minimal API used by our code
    def ping(self):
        return True

    def get(self, key: str) -> Optional[Any]:
        return self._kv.get(key)

    def set(self, key: str, value: Any, ex: int | None = None, nx: bool | None = None):
        if nx and self.exists(key):
            return False
        self._kv[key] = value
        if ex is not None:
            try:
                self._ttl[key] = int(ex)
            except Exception:
                self._ttl[key] = 0
        return True

    def delete(self, key: str) -> int:
        existed = 1 if key in self._kv or key in self._hash else 0
        self._kv.pop(key, None)
        self._hash.pop(key, None)
        self._ttl.pop(key, None)
        return existed

    def exists(self, key: str) -> int:
        return 1 if key in self._kv or key in self._hash else 0

    def expire(self, key: str, ttl: int) -> bool:
        # We don't enforce TTL in tests; just remember
        self._ttl[key] = ttl
        return True

    def hset(self, key: str, *args, **kwargs):
        m = self._hash.setdefault(key, {})
        if args and isinstance(args[0], dict):
            m.update(args[0])
        elif "mapping" in kwargs:
            m.update(kwargs["mapping"])  # type: ignore[index]
        elif len(args) >= 2:
            field, value = args[0], args[1]
            m[str(field)] = value
        elif len(args) == 1 and kwargs:
            # hset(key, field=value) form
            for k, v in kwargs.items():
                m[str(k)] = v
        return True

    def hgetall(self, key: str) -> Dict[str, Any]:
        return dict(self._hash.get(key, {}))

    def hget(self, key: str, field: str) -> Optional[Any]:
        return self._hash.get(key, {}).get(field)

    def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        cur = self.hget(key, field)
        try:
            n = int(cur) if cur is not None else 0
        except Exception:
            n = 0
        n += int(amount)
        self.hset(key, field, n)
        return n

    def eval(self, script: str, numkeys: int, *args):
        # Implement the specific check-then-increment used by AgentBudgetManager
        # Args: KEYS[1] -> steps_key; ARGV[1] -> max_steps
        if numkeys != 1:
            raise NotImplementedError("FakeRedis.eval only supports one key")
        steps_key = args[0]
        max_steps = int(args[1]) if len(args) > 1 else 0
        cur = self.get(steps_key)
        try:
            n = int(cur) if cur is not None else 0
        except Exception:
            n = 0
        if n >= max_steps:
            return [0, n]
        n += 1
        self.set(steps_key, n)
        return [1, n]

    def pipeline(self):
        return _FakePipeline(self)

    # Minimal queue/list ops for local/test notification flows
    def rpush(self, key: str, value: Any) -> int:
        lst = self._lists.setdefault(key, [])
        lst.append(value)
        return len(lst)

    def blpop(self, keys, timeout: int = 0):
        # Support single key or list of keys
        if isinstance(keys, (list, tuple)):
            for k in keys:
                lst = self._lists.get(k, [])
                if lst:
                    return (k, lst.pop(0))
        else:
            k = keys
            lst = self._lists.get(k, [])
            if lst:
                return (k, lst.pop(0))
        # No blocking behavior in fake; just return None
        return None


@lru_cache(maxsize=1)
def get_redis_client() -> Any:
    """Return a Redis client or a safe in-memory stub for test runs.

    In test environments (CELERY_BROKER_URL empty or USE_FAKE_REDIS=1),
    returns a fake client that avoids external connections.
    """
    # Auto-fake in tests or when explicitly requested
    if os.getenv("USE_FAKE_REDIS") == "1" or getattr(settings, "CELERY_BROKER_URL", None) in ("", None):
        logger.info("Using FakeRedis client for tests")
        return _FakeRedis()

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
