"""
Helpers for managing Vertex Gemini cached content.
"""
from __future__ import annotations

import json
import logging
import time
from typing import List, NamedTuple, Optional, Set, Tuple

import httpx
import redis
from litellm import VertexGeminiConfig
from litellm.caching.caching import Cache as LiteLLMCache, LiteLLMCacheType
from litellm.llms.vertex_ai.context_caching.transformation import (
    separate_cached_messages,
    transform_openai_messages_to_gemini_context_caching,
)
from litellm.llms.vertex_ai.vertex_llm_base import VertexBase

from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)

GEMINI_CACHE_BLOCKLIST: Set[Tuple[str, str]] = set()


def _cache_key(provider: str | None, model: str | None) -> Tuple[str, str]:
    return ((provider or "").lower(), (model or "").lower())


def should_use_gemini_cache(provider: str | None, model: str | None) -> bool:
    """Return True when the provider/model pair should use cached content."""
    provider_key, model_key = _cache_key(provider, model)
    if (provider_key, model_key) in GEMINI_CACHE_BLOCKLIST:
        return False
    return "gemini" in provider_key or "gemini" in model_key


def disable_gemini_cache_for(provider: str | None, model: str | None) -> None:
    """Remember providers that reject cached content so future calls skip it."""
    key = _cache_key(provider, model)
    if key not in GEMINI_CACHE_BLOCKLIST:
        GEMINI_CACHE_BLOCKLIST.add(key)
        logger.info(
            "Disabling Gemini cached prompts for provider=%s model=%s after API rejection",
            provider or "<unknown>",
            model or "<unknown>",
        )


def is_gemini_cache_conflict_error(exc: Exception) -> bool:
    """Return True when the exception matches Vertex's cached content constraint."""
    message = str(exc)
    if not message:
        return False
    lowered = message.lower()
    return (
        "cachedcontent can not be used with generatecontent request" in lowered
        or "cachedcontent cannot be used with generatecontent request" in lowered
    )

def mark_messages_for_cache(messages: List[dict]) -> List[dict]:
    """Return copies of ``messages`` with Gemini cache metadata on every part."""
    marked_messages: List[dict] = []
    for message in messages:
        message_copy = dict(message)
        content = message_copy.get("content")

        if isinstance(content, list):
            parts = []
            for part in content:
                part_copy = dict(part)
                part_copy.setdefault("cache_control", {"type": "ephemeral", "ttl": "3600s"})
                parts.append(part_copy)
        elif isinstance(content, str):
            parts = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral", "ttl": "3600s"},
                }
            ]
        else:
            serialized = "" if content is None else str(content)
            parts = [
                {
                    "type": "text",
                    "text": serialized,
                    "cache_control": {"type": "ephemeral", "ttl": "3600s"},
                }
            ]

        message_copy["content"] = parts
        marked_messages.append(message_copy)

    return marked_messages


class GeminiCacheRequest(NamedTuple):
    messages: List[dict]
    cached_content: str


class GeminiCachedContentError(Exception):
    """Raised when Gemini cached content preparation fails."""


class GeminiCachedContentManager:
    """Manage Vertex Gemini cached content lifecycle for persistent agents."""

    CACHE_TTL_SECONDS = 3600
    REDIS_KEY_PREFIX = "agent:gemini_cached_content:v1"

    def __init__(self) -> None:
        self._redis_client: redis.Redis | None = None
        self._lite_cache = LiteLLMCache(type=LiteLLMCacheType.LOCAL)
        self._vertex_base = VertexBase()
        self._tool_converter = VertexGeminiConfig()

    def prepare_request(
        self,
        *,
        messages: List[dict],
        tools: List[dict] | None,
        provider: str | None,
        model: str | None,
        params: dict,
        agent_id: str | None = None,
    ) -> Optional[GeminiCacheRequest]:
        """Return cached-content metadata when context caching can be applied."""
        if not messages or not should_use_gemini_cache(provider, model) or not params.get("vertex_project"):
            return None

        marked_messages = mark_messages_for_cache(list(messages))
        cached_messages, non_cached_messages = separate_cached_messages(marked_messages)
        if not cached_messages:
            return None

        cache_key = self._lite_cache.get_cache_key(
            messages=cached_messages,
            tools=tools or [],
        )
        cached_name = self._get_or_create_cached_content(
            cache_key=cache_key,
            cached_messages=cached_messages,
            tools=tools or [],
            model=model,
            params=params,
            agent_id=agent_id,
        )

        return GeminiCacheRequest(non_cached_messages or [], cached_name)

    def _get_redis(self) -> Optional[redis.Redis]:
        if self._redis_client is not None:
            return self._redis_client
        try:
            self._redis_client = get_redis_client()
        except Exception:
            logger.debug("Gemini cache: unable to connect to redis for metadata caching", exc_info=True)
            self._redis_client = None
        return self._redis_client

    def _redis_key(self, project: str, location: str, model: str, cache_key: str) -> str:
        base_model = self._short_model_name(model)
        return f"{self.REDIS_KEY_PREFIX}:{project}:{location}:{base_model}:{cache_key}"

    def _get_or_create_cached_content(
        self,
        *,
        cache_key: str,
        cached_messages: List[dict],
        tools: List[dict],
        model: str | None,
        params: dict,
        agent_id: str | None,
    ) -> str:
        project = params.get("vertex_project")
        location = params.get("vertex_location") or "us-central1"
        if not project:
            raise GeminiCachedContentError("Vertex project is required for Gemini cached content.")

        redis_client = self._get_redis()
        redis_key = self._redis_key(project, location, model or "", cache_key)
        cached_entry = None
        if redis_client:
            try:
                raw = redis_client.get(redis_key)
                if raw:
                    cached_entry = json.loads(raw)
            except Exception:
                logger.debug("Gemini cache: failed to read cached metadata", exc_info=True)
        if cached_entry and cached_entry.get("expires_at", 0) > time.time():
            return cached_entry["name"]

        cached_name = self._create_cached_content(
            cache_key=cache_key,
            cached_messages=cached_messages,
            tools=tools,
            model=model,
            params=params,
            agent_id=agent_id,
        )
        if redis_client:
            payload = {
                "name": cached_name,
                "expires_at": time.time() + self.CACHE_TTL_SECONDS,
            }
            try:
                redis_client.setex(redis_key, self.CACHE_TTL_SECONDS, json.dumps(payload))
            except Exception:
                logger.debug("Gemini cache: failed to persist metadata to redis", exc_info=True)
        return cached_name

    def _create_cached_content(
        self,
        *,
        cache_key: str,
        cached_messages: List[dict],
        tools: List[dict],
        model: str | None,
        params: dict,
        agent_id: str | None,
    ) -> str:
        project = params.get("vertex_project")
        location = params.get("vertex_location") or "us-central1"
        credentials = params.get("vertex_credentials")
        base_model = self._short_model_name(model or "")
        if not project:
            raise GeminiCachedContentError("Vertex project is required for cached content.")

        request_body = transform_openai_messages_to_gemini_context_caching(
            model=base_model or (model or ""),
            messages=cached_messages,
            custom_llm_provider="vertex_ai",
            cache_key=cache_key,
            vertex_project=project,
            vertex_location=location,
        )
        request_body.setdefault("ttl", f"{self.CACHE_TTL_SECONDS}s")
        request_body["model"] = self._vertex_model_resource(
            project=project,
            location=location,
            model_path=request_body.get("model", ""),
        )
        vertex_tools = self._convert_tools(tools, base_model or (model or ""))
        if vertex_tools:
            request_body["tools"] = vertex_tools

        token, resolved_project = self._vertex_base._ensure_access_token(
            credentials=credentials,
            project_id=project,
            custom_llm_provider="vertex_ai",
        )
        project = resolved_project or project
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        url = self._cached_content_url(project=project, location=location)

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=request_body, headers=headers)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GeminiCachedContentError(
                f"Vertex cached content creation failed: {exc.response.text}"
            ) from exc
        except Exception as exc:
            raise GeminiCachedContentError(str(exc)) from exc

        payload = response.json()
        name = payload.get("name")
        if not name:
            raise GeminiCachedContentError("Vertex cached content response missing name.")

        logger.debug(
            "Created Gemini cached content for agent %s (model=%s, name=%s)",
            agent_id or "unknown",
            model,
            name,
        )
        return name

    def _vertex_model_resource(self, *, project: str, location: str, model_path: str) -> str:
        sanitized_path = model_path or ""
        if sanitized_path.startswith("projects/"):
            return sanitized_path
        return f"projects/{project}/locations/{location}/publishers/google/{sanitized_path}"

    def _cached_content_url(self, *, project: str, location: str) -> str:
        host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
        return f"https://{host}/v1beta1/projects/{project}/locations/{location}/cachedContents"

    def _short_model_name(self, model: str) -> str:
        if not model:
            return ""
        return model.split("/", 1)[1] if "/" in model else model

    def _convert_tools(self, tools: List[dict], model: str) -> Optional[dict]:
        if not tools:
            return None
        optional_params: dict = {}
        try:
            self._tool_converter.map_openai_params(
                non_default_params={"tools": tools},
                optional_params=optional_params,
                model=model,
                drop_params=False,
            )
        except Exception:
            logger.exception("Failed to convert tools for Gemini cached content.")
            return None
        return optional_params.get("tools")


__all__ = [
    "GEMINI_CACHE_BLOCKLIST",
    "GeminiCacheRequest",
    "GeminiCachedContentError",
    "GeminiCachedContentManager",
    "disable_gemini_cache_for",
    "is_gemini_cache_conflict_error",
    "mark_messages_for_cache",
    "should_use_gemini_cache",
]
