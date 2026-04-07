import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from django.db.models import Prefetch

from api.agent.core.endpoint_config_utils import resolve_endpoint_model_and_params
from api.models import FileHandlerLLMTier, FileHandlerTierEndpoint

logger = logging.getLogger(__name__)


@dataclass
class FileHandlerLLMConfig:
    model: str
    params: Dict[str, Any]
    supports_vision: bool
    endpoint_key: str


def get_file_handler_llm_config() -> Optional[FileHandlerLLMConfig]:
    tier_prefetch = Prefetch(
        "tier_endpoints",
        queryset=FileHandlerTierEndpoint.objects.select_related("endpoint__provider").order_by("-weight"),
    )
    tiers = FileHandlerLLMTier.objects.prefetch_related(tier_prefetch).order_by("order")

    for tier in tiers:
        for entry in tier.tier_endpoints.all():
            if entry.weight <= 0:
                continue
            result = resolve_endpoint_model_and_params(entry.endpoint)
            if result is None:
                continue
            model_name, params = result

            return FileHandlerLLMConfig(
                model=model_name,
                params=params,
                supports_vision=bool(getattr(entry.endpoint, "supports_vision", False)),
                endpoint_key=getattr(entry.endpoint, "key", ""),
            )

    return None
