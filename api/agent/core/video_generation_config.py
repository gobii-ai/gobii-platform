import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from django.db.models import Prefetch, Q

from api.encryption import SecretsEncryption
from api.llm.utils import normalize_model_name
from api.models import (
    VideoGenerationLLMTier,
    VideoGenerationTierEndpoint,
)
from api.openrouter import get_attribution_headers

logger = logging.getLogger(__name__)

CREATE_VIDEO_USE_CASE = VideoGenerationLLMTier.UseCase.CREATE_VIDEO


@dataclass
class VideoGenerationLLMConfig:
    model: str
    params: Dict[str, Any]
    endpoint_key: str
    supports_image_to_video: bool


def _resolve_provider_api_key(provider) -> Optional[str]:
    if provider is None or not getattr(provider, "enabled", True):
        return None

    api_key: Optional[str] = None
    encrypted = getattr(provider, "api_key_encrypted", None)
    if encrypted:
        try:
            api_key = SecretsEncryption.decrypt_value(encrypted)
        except Exception as exc:
            logger.warning(
                "Failed to decrypt video generation API key for provider %s: %s",
                getattr(provider, "key", "unknown"),
                exc,
            )
            return None

    if not api_key:
        env_var = getattr(provider, "env_var_name", None)
        if env_var:
            api_key = os.getenv(env_var)

    return api_key or None


def _build_eligible_tier_endpoint_queryset(use_case: str):
    return VideoGenerationTierEndpoint.objects.filter(
        tier__use_case=use_case,
        endpoint__enabled=True,
    ).filter(
        Q(endpoint__provider__isnull=True) | Q(endpoint__provider__enabled=True)
    )


def _resolve_video_generation_tiers(use_case: str):
    tier_prefetch = Prefetch(
        "tier_endpoints",
        queryset=VideoGenerationTierEndpoint.objects.select_related("endpoint__provider").order_by("-weight"),
    )
    if not _build_eligible_tier_endpoint_queryset(use_case).exists():
        return None
    return (
        VideoGenerationLLMTier.objects.filter(use_case=use_case)
        .prefetch_related(tier_prefetch)
        .order_by("order")
    )


def is_video_generation_configured(
    *,
    use_case: str = CREATE_VIDEO_USE_CASE,
) -> bool:
    """Return True when the requested video-generation workflow has at least one eligible tier endpoint."""
    return _resolve_video_generation_tiers(use_case) is not None


def get_video_generation_llm_configs(
    *,
    use_case: str = CREATE_VIDEO_USE_CASE,
    limit: int = 5,
) -> list[VideoGenerationLLMConfig]:
    tiers = _resolve_video_generation_tiers(use_case)
    if tiers is None:
        return []

    configs: list[VideoGenerationLLMConfig] = []
    for tier in tiers:
        for entry in tier.tier_endpoints.all():
            if entry.weight <= 0:
                continue
            endpoint = entry.endpoint
            if endpoint is None or not getattr(endpoint, "enabled", False):
                continue

            provider = getattr(endpoint, "provider", None)
            if provider is not None and not getattr(provider, "enabled", True):
                continue

            model_name = normalize_model_name(provider, endpoint.litellm_model, api_base=endpoint.api_base)
            if not model_name:
                continue

            params: Dict[str, Any] = {}
            api_key = _resolve_provider_api_key(provider)
            if api_key:
                params["api_key"] = api_key

            api_base = (endpoint.api_base or "").strip()
            if api_base:
                params["api_base"] = api_base
                params.setdefault("api_key", "sk-noauth")

            if provider is not None and "google" in getattr(provider, "key", ""):
                params["vertex_project"] = provider.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT", "browser-use-458714")
                params["vertex_location"] = provider.vertex_location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-east4")

            if "api_key" not in params and not api_base:
                continue

            if provider is not None and getattr(provider, "key", "") == "openrouter":
                headers = get_attribution_headers()
                if headers:
                    params["extra_headers"] = headers

            configs.append(
                VideoGenerationLLMConfig(
                    model=model_name,
                    params=params,
                    endpoint_key=getattr(endpoint, "key", ""),
                    supports_image_to_video=bool(getattr(endpoint, "supports_image_to_video", False)),
                )
            )
            if len(configs) >= limit:
                return configs

    return configs


def is_create_video_generation_configured() -> bool:
    return is_video_generation_configured(use_case=CREATE_VIDEO_USE_CASE)


def get_create_video_generation_llm_configs(limit: int = 5) -> list[VideoGenerationLLMConfig]:
    return get_video_generation_llm_configs(use_case=CREATE_VIDEO_USE_CASE, limit=limit)
