import logging
from dataclasses import dataclass
from typing import Any, Dict

from django.db.models import Prefetch, Q

from api.agent.core.endpoint_config_utils import resolve_endpoint_model_and_params
from api.models import (
    VideoGenerationLLMTier,
    VideoGenerationTierEndpoint,
)

logger = logging.getLogger(__name__)

CREATE_VIDEO_USE_CASE = VideoGenerationLLMTier.UseCase.CREATE_VIDEO


@dataclass
class VideoGenerationLLMConfig:
    model: str
    params: Dict[str, Any]
    endpoint_key: str
    supports_image_to_video: bool


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
            result = resolve_endpoint_model_and_params(entry.endpoint)
            if result is None:
                continue
            model_name, params = result

            configs.append(
                VideoGenerationLLMConfig(
                    model=model_name,
                    params=params,
                    endpoint_key=getattr(entry.endpoint, "key", ""),
                    supports_image_to_video=bool(getattr(entry.endpoint, "supports_image_to_video", False)),
                )
            )
            if len(configs) >= limit:
                return configs

    return configs


def is_create_video_generation_configured() -> bool:
    return is_video_generation_configured(use_case=CREATE_VIDEO_USE_CASE)


def get_create_video_generation_llm_configs(limit: int = 5) -> list[VideoGenerationLLMConfig]:
    return get_video_generation_llm_configs(use_case=CREATE_VIDEO_USE_CASE, limit=limit)
