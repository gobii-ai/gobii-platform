import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import redis

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
    cached_at: float
    freshness: str

    @property
    def is_stale(self) -> bool:
        return self.freshness == "stale"


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


def _latest_cache_key(config_id: str) -> str:
    return f"{CACHE_PREFIX}:latest:{config_id}"


def _index_cache_key(config_id: str) -> str:
    return f"{CACHE_PREFIX}:index:{config_id}"


def _discovery_lock_key(config_id: str, fingerprint: str) -> str:
    return f"{CACHE_PREFIX}:discover:{config_id}:{fingerprint}"


def _refresh_marker_key(config_id: str, fingerprint: str) -> str:
    return f"{CACHE_PREFIX}:refresh:{config_id}:{fingerprint}"


def _parse_json(payload: Any) -> Any:
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        if not isinstance(payload, str):
            return payload
        return json.loads(payload)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None


def _parse_index_payload(payload: Any) -> List[str]:
    parsed = _parse_json(payload)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item]


def get_cached_mcp_tool_definitions(
    config_id: str,
    fingerprint: str,
) -> Optional[MCPToolCacheEntry]:
    key = build_mcp_tool_cache_key(config_id, fingerprint)
    try:
        redis_client = get_redis_client()
        envelope = _parse_json(redis_client.get(key))
    except redis.exceptions.RedisError:
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
    freshness = "fresh" if age_seconds < CACHE_SOFT_TTL_SECONDS else "stale"
    return MCPToolCacheEntry(tools=tools, cached_at=float(cached_at), freshness=freshness)


def set_cached_mcp_tool_definitions(
    config_id: str,
    fingerprint: str,
    tools: List[Dict[str, Any]],
) -> str:
    key = build_mcp_tool_cache_key(config_id, fingerprint)
    try:
        redis_client = get_redis_client()
        envelope = {
            "cached_at": time.time(),
            "tools": tools,
        }
        payload = json.dumps(envelope, ensure_ascii=True, separators=(",", ":"))
        index_key = _index_cache_key(config_id)
        known_keys = _parse_index_payload(redis_client.get(index_key))
        if key not in known_keys:
            known_keys.append(key)
        pipe = redis_client.pipeline()
        pipe.set(key, payload, ex=CACHE_HARD_TTL_SECONDS)
        pipe.set(_latest_cache_key(config_id), key, ex=CACHE_HARD_TTL_SECONDS)
        pipe.set(
            index_key,
            json.dumps(known_keys, ensure_ascii=True, separators=(",", ":")),
            ex=CACHE_HARD_TTL_SECONDS,
        )
        pipe.execute()
    except redis.exceptions.RedisError:
        logger.debug("Failed to write MCP tool cache for %s", config_id, exc_info=True)
    return key


def acquire_mcp_catalog_discovery_lock(config_id: str, fingerprint: str) -> Optional[str]:
    token = uuid.uuid4().hex
    try:
        acquired = get_redis_client().set(
            _discovery_lock_key(config_id, fingerprint),
            token,
            nx=True,
            ex=DISCOVERY_LOCK_TTL_SECONDS,
        )
    except redis.exceptions.RedisError:
        logger.debug("Failed to acquire MCP discovery lock for %s", config_id, exc_info=True)
        return token
    return token if acquired else None


def release_mcp_catalog_discovery_lock(config_id: str, fingerprint: str, token: str) -> None:
    key = _discovery_lock_key(config_id, fingerprint)
    try:
        redis_client = get_redis_client()
        release = redis_client.register_script(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """
        )
        release(keys=[key], args=[token])
    except redis.exceptions.RedisError:
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
    latest_key = _latest_cache_key(config_id)
    index_key = _index_cache_key(config_id)
    try:
        redis_client = get_redis_client()
        keys_to_delete = [latest_key, index_key]
        cached_key = redis_client.get(latest_key)
        if cached_key:
            keys_to_delete.append(cached_key)
        keys_to_delete.extend(_parse_index_payload(redis_client.get(index_key)))
        for key in dict.fromkeys(keys_to_delete):
            redis_client.delete(key)
    except redis.exceptions.RedisError:
        logger.debug("Failed to invalidate MCP tool cache for %s", config_id, exc_info=True)
