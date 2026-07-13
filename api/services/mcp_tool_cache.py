import hashlib
import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import redis
from pottery import Redlock
from pottery.exceptions import PotteryError

from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)

CACHE_KEY_VERSION = 3
CACHE_SOFT_TTL_SECONDS = 60 * 60
CACHE_HARD_TTL_SECONDS = 7 * 24 * 60 * 60
CACHE_PREFIX = f"mcp:tools:v{CACHE_KEY_VERSION}"
DISCOVERY_LOCK_TTL_SECONDS = 5 * 60
REFRESH_MARKER_TTL_SECONDS = 10 * 60


@dataclass(frozen=True)
class MCPToolCacheEntry:
    tools: List[Dict[str, Any]]
    is_stale: bool


def build_mcp_tool_cache_fingerprint(payload: Dict[str, Any]) -> str:
    """Return a deterministic hash for the tool cache inputs."""
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_mcp_tool_cache_key(config_id: str, fingerprint: str) -> str:
    return f"{CACHE_PREFIX}:{config_id}:{fingerprint}"


def _index_cache_key(config_id: str) -> str:
    return f"{CACHE_PREFIX}:index:{config_id}"


def _discovery_lock_key(config_id: str, fingerprint: str) -> str:
    return f"{CACHE_PREFIX}:discover:{config_id}:{fingerprint}"


def _refresh_marker_key(config_id: str, fingerprint: str) -> str:
    return f"{CACHE_PREFIX}:refresh:{config_id}:{fingerprint}"


def get_cached_mcp_tool_definitions(
    config_id: str,
    fingerprint: str,
) -> Optional[MCPToolCacheEntry]:
    key = build_mcp_tool_cache_key(config_id, fingerprint)
    try:
        redis_client = get_redis_client()
        payload = redis_client.get(key)
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        envelope = json.loads(payload) if isinstance(payload, str) else payload
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError, redis.exceptions.RedisError):
        logger.debug("Failed to read MCP tool cache for %s", config_id, exc_info=True)
        return None

    if not isinstance(envelope, dict):
        return None
    tools = envelope.get("tools")
    cached_at = envelope.get("cached_at")
    if not isinstance(tools, list) or not isinstance(cached_at, (int, float)):
        logger.debug("MCP tool cache envelope invalid for %s", config_id)
        return None

    age_seconds = max(time.time() - float(cached_at), 0)
    if age_seconds >= CACHE_HARD_TTL_SECONDS:
        try:
            redis_client.delete(key)
        except redis.exceptions.RedisError:
            pass
        return None
    return MCPToolCacheEntry(tools=tools, is_stale=age_seconds >= CACHE_SOFT_TTL_SECONDS)


def set_cached_mcp_tool_definitions(
    config_id: str,
    fingerprint: str,
    tools: List[Dict[str, Any]],
) -> str:
    key = build_mcp_tool_cache_key(config_id, fingerprint)
    try:
        redis_client = get_redis_client()
        envelope = {"cached_at": time.time(), "tools": tools}
        payload = json.dumps(envelope, ensure_ascii=True, separators=(",", ":"))
        index_key = _index_cache_key(config_id)
        pipe = redis_client.pipeline()
        pipe.set(key, payload, ex=CACHE_HARD_TTL_SECONDS)
        pipe.sadd(index_key, key)
        pipe.expire(index_key, CACHE_HARD_TTL_SECONDS)
        pipe.execute()
    except redis.exceptions.RedisError:
        logger.debug("Failed to write MCP tool cache for %s", config_id, exc_info=True)
    return key


@contextmanager
def mcp_catalog_discovery_locks(
    config_id: str,
    fingerprints: List[str],
    *,
    timeout: float,
):
    """Acquire catalog shards in deterministic order; Redis failure permits local discovery."""
    locks = []
    acquired = True
    try:
        redis_client = get_redis_client()
        deadline = time.monotonic() + timeout
        for fingerprint in sorted(set(fingerprints)):
            lock = Redlock(
                key=_discovery_lock_key(config_id, fingerprint),
                masters={redis_client},
                auto_release_time=DISCOVERY_LOCK_TTL_SECONDS,
            )
            if not lock.acquire(timeout=max(deadline - time.monotonic(), 0)):
                acquired = False
                break
            locks.append(lock)
    except (redis.exceptions.RedisError, PotteryError):
        logger.debug("MCP discovery locking unavailable for %s", config_id, exc_info=True)
        acquired = True
    try:
        yield acquired
    finally:
        for lock in reversed(locks):
            try:
                lock.release()
            except (redis.exceptions.RedisError, PotteryError):
                logger.debug("Failed to release MCP discovery lock for %s", config_id, exc_info=True)


def claim_mcp_catalog_refresh(config_id: str, fingerprint: str) -> bool:
    try:
        return bool(
            get_redis_client().set(
                _refresh_marker_key(config_id, fingerprint),
                "1",
                nx=True,
                ex=REFRESH_MARKER_TTL_SECONDS,
            )
        )
    except redis.exceptions.RedisError:
        logger.debug("Failed to claim MCP refresh marker for %s", config_id, exc_info=True)
        return False


def release_mcp_catalog_refresh(config_id: str, fingerprint: str) -> None:
    try:
        get_redis_client().delete(_refresh_marker_key(config_id, fingerprint))
    except redis.exceptions.RedisError:
        logger.debug("Failed to release MCP refresh marker for %s", config_id, exc_info=True)


def invalidate_mcp_tool_cache(config_id: str) -> None:
    index_key = _index_cache_key(config_id)
    try:
        redis_client = get_redis_client()
        keys = [index_key, *redis_client.smembers(index_key)]
        for key in keys:
            redis_client.delete(key)
    except redis.exceptions.RedisError:
        logger.debug("Failed to invalidate MCP tool cache for %s", config_id, exc_info=True)
