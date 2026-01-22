import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)

CACHE_KEY_VERSION = 1
CACHE_TTL_SECONDS = 60 * 60
CACHE_PREFIX = f"mcp:tools:v{CACHE_KEY_VERSION}"


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


def get_cached_mcp_tool_definitions(
    config_id: str,
    fingerprint: str,
) -> Optional[List[Dict[str, Any]]]:
    key = build_mcp_tool_cache_key(config_id, fingerprint)
    try:
        redis_client = get_redis_client()
        cached = redis_client.get(key)
    except Exception:
        logger.debug("Failed to read MCP tool cache for %s", config_id, exc_info=True)
        return None

    if not cached:
        return None

    if isinstance(cached, str):
        try:
            cached = json.loads(cached)
        except json.JSONDecodeError:
            logger.debug("MCP tool cache payload invalid for %s", config_id)
            return None

    if not isinstance(cached, list):
        logger.debug("MCP tool cache payload invalid for %s", config_id)
        return None
    return cached


def set_cached_mcp_tool_definitions(
    config_id: str,
    fingerprint: str,
    tools: List[Dict[str, Any]],
) -> str:
    key = build_mcp_tool_cache_key(config_id, fingerprint)
    try:
        redis_client = get_redis_client()
        payload = json.dumps(tools, ensure_ascii=True, separators=(",", ":"))
        redis_client.set(key, payload, ex=CACHE_TTL_SECONDS)
        redis_client.set(_latest_cache_key(config_id), key, ex=CACHE_TTL_SECONDS)
    except Exception:
        logger.debug("Failed to write MCP tool cache for %s", config_id, exc_info=True)
    return key


def invalidate_mcp_tool_cache(config_id: str) -> None:
    latest_key = _latest_cache_key(config_id)
    try:
        redis_client = get_redis_client()
        cached_key = redis_client.get(latest_key)
        if cached_key:
            redis_client.delete(cached_key)
        redis_client.delete(latest_key)
    except Exception:
        logger.debug("Failed to invalidate MCP tool cache for %s", config_id, exc_info=True)
