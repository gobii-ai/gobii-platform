"""
Redis connection management for Gobii platform.

Provides a centralized, production-ready Redis client for all use cases:
- Distributed locking (Redlock)
- Administrative operations (Celery beat scheduling, cleanup tasks)  
- Basic connectivity testing and one-off operations

All operations use the same underlying Redis instance configured via REDIS_URL.
"""
import logging
import fnmatch
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
        self._ops.append(("hset", args, kwargs))
        return self

    def expire(self, *args, **kwargs):
        self._ops.append(("expire", args, kwargs))
        return self

    def set(self, *args, **kwargs):
        self._ops.append(("set", args, kwargs))
        return self

    def delete(self, *args, **kwargs):
        self._ops.append(("delete", args, kwargs))
        return self

    def sadd(self, *args, **kwargs):
        self._ops.append(("sadd", args, kwargs))
        return self

    def srem(self, *args, **kwargs):
        self._ops.append(("srem", args, kwargs))
        return self

    def execute(self):
        results = []
        for name, args, kwargs in self._ops:
            results.append(getattr(self._client, name)(*args, **kwargs))
        self._ops.clear()
        return results


class _FakeRegisteredScript:
    def __init__(self, client: "_FakeRedis", script: str):
        self._client = client
        self._normalized_script = " ".join(script.split()).lower()

    def __call__(self, keys=None, args=None, client=None):
        redis_client = client or self._client
        script_keys = tuple(keys or ())
        script_args = tuple(args or ())
        key = script_keys[0] if script_keys else None
        expected_value = script_args[0] if script_args else None

        if key is None:
            return 0
        if redis_client.get(key) != expected_value:
            return 0
        if "pttl" in self._normalized_script:
            pttl = redis_client.pttl(key)
            return pttl if pttl > 0 else 0
        if "pexpire" in self._normalized_script:
            ttl_ms = int(script_args[1]) if len(script_args) > 1 else 0
            return 1 if redis_client.pexpire(key, ttl_ms) else 0
        if "del" in self._normalized_script:
            return redis_client.delete(key)
        return 0


class _FakeRedis:
    def __init__(self):
        self._kv: Dict[str, Any] = {}
        self._hash: Dict[str, Dict[str, Any]] = {}
        self._ttl: Dict[str, int] = {}
        self._pttl: Dict[str, int] = {}
        self._lists: Dict[str, list] = {}
        self._sets: Dict[str, set] = {}
        self._streams: Dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self._stream_seq: Dict[str, int] = {}

    # Minimal API used by our code
    def ping(self):
        return True

    def get(self, key: str) -> Optional[Any]:
        return self._kv.get(key)

    def set(
        self,
        key: str,
        value: Any,
        ex: int | None = None,
        px: int | None = None,
        nx: bool | None = None,
    ):
        if nx and self.exists(key):
            return False
        self._kv[key] = value
        if ex is not None:
            try:
                self._ttl[key] = int(ex)
                self._pttl[key] = int(ex) * 1000
            except Exception:
                self._ttl[key] = 0
                self._pttl[key] = 0
        if px is not None:
            try:
                self._pttl[key] = int(px)
                self._ttl[key] = max(1, int(px) // 1000)
            except (TypeError, ValueError):
                self._pttl[key] = 0
                self._ttl[key] = 0
        return True

    def incr(self, key: str, amount: int = 1) -> int:
        current = self.get(key)
        try:
            value = int(current) if current is not None else 0
        except (TypeError, ValueError):
            value = 0
        value += int(amount)
        self._kv[key] = value
        return value

    def delete(self, key: str) -> int:
        existed = 1 if self.exists(key) else 0
        self._kv.pop(key, None)
        self._hash.pop(key, None)
        self._sets.pop(key, None)
        self._lists.pop(key, None)
        self._streams.pop(key, None)
        self._stream_seq.pop(key, None)
        self._ttl.pop(key, None)
        self._pttl.pop(key, None)
        return existed

    def exists(self, key: str) -> int:
        return 1 if (
            key in self._kv
            or key in self._hash
            or key in self._sets
            or key in self._lists
            or key in self._streams
        ) else 0

    def expire(self, key: str, ttl: int) -> bool:
        # We don't enforce TTL in tests; just remember
        self._ttl[key] = ttl
        self._pttl[key] = ttl * 1000
        return True

    def pexpire(self, key: str, ttl_ms: int) -> bool:
        self._pttl[key] = ttl_ms
        self._ttl[key] = max(1, int(ttl_ms) // 1000)
        return True

    def ttl(self, key: str) -> int:
        if not self.exists(key):
            return -2
        return int(self._ttl.get(key, -1))

    def pttl(self, key: str) -> int:
        if not self.exists(key):
            return -2
        return int(self._pttl.get(key, -1))

    def register_script(self, script: str):
        return _FakeRegisteredScript(self, script)


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

    def pipeline(self, transaction: bool = True):
        return _FakePipeline(self)

    def publish(self, channel: str, message: Any) -> int:
        return 0

    def xadd(
        self,
        key: str,
        fields: dict[str, Any],
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        seq = self._stream_seq.get(key, 0) + 1
        self._stream_seq[key] = seq
        entry_id = f"{seq}-0"
        stream = self._streams.setdefault(key, [])
        stream.append((entry_id, dict(fields)))
        if maxlen and len(stream) > maxlen:
            del stream[: len(stream) - maxlen]
        return entry_id

    def xread(self, streams: dict[str, str], count: int | None = None, block: int | None = None):
        results = []
        for key, last_id in streams.items():
            entries = [
                (entry_id, fields)
                for entry_id, fields in self._streams.get(key, [])
                if self._stream_id_greater(entry_id, last_id)
            ]
            if count is not None:
                entries = entries[:count]
            if entries:
                results.append((key, entries))
        return results

    @staticmethod
    def _stream_id_greater(candidate: str, baseline: str) -> bool:
        try:
            cand_ms, cand_seq = [int(part) for part in str(candidate).split("-", 1)]
            base_ms, base_seq = [int(part) for part in str(baseline).split("-", 1)]
        except (TypeError, ValueError):
            return str(candidate) > str(baseline)
        return (cand_ms, cand_seq) > (base_ms, base_seq)

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

    # Minimal set ops for pending agent queue
    def sadd(self, key: str, *values: Any) -> int:
        s = self._sets.setdefault(key, set())
        added = 0
        for value in values:
            if value not in s:
                s.add(value)
                added += 1
        return added

    def srem(self, key: str, *values: Any) -> int:
        s = self._sets.get(key, set())
        removed = 0
        for value in values:
            if value in s:
                s.remove(value)
                removed += 1
        if not s and key in self._sets:
            self._sets.pop(key, None)
        return removed

    def sismember(self, key: str, value: Any) -> int:
        s = self._sets.get(key, set())
        return 1 if value in s else 0

    def spop(self, key: str, count: int | None = None):
        s = self._sets.get(key, set())
        if not s:
            return None
        if count is None:
            value = s.pop()
            if not s:
                self._sets.pop(key, None)
            return value
        popped = []
        for _ in range(min(count, len(s))):
            popped.append(s.pop())
        if not s:
            self._sets.pop(key, None)
        return popped

    def scard(self, key: str) -> int:
        return len(self._sets.get(key, set()))

    def smembers(self, key: str) -> set:
        return set(self._sets.get(key, set()))

    def llen(self, key: str) -> int:
        return len(self._lists.get(key, []))

    def keys(self, pattern: str = "*") -> list[str]:
        all_keys = (
            set(self._kv.keys())
            | set(self._hash.keys())
            | set(self._lists.keys())
            | set(self._sets.keys())
            | set(self._streams.keys())
        )
        return sorted(key for key in all_keys if fnmatch.fnmatch(key, pattern))

    def scan_iter(self, match: str | None = None):
        pattern = match or "*"
        for key in self.keys(pattern):
            yield key


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
