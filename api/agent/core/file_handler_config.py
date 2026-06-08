from dataclasses import dataclass
from typing import Any, Dict, Optional

from django.db.models import Prefetch

from api.agent.core.endpoint_config_utils import resolve_endpoint_model_and_params
from api.agent.core.llm_config import (
    _get_failover_configs_from_profile,
    _resolve_active_routing_profile,
)
from api.models import FileHandlerLLMTier, FileHandlerTierEndpoint

_ROUTING_HINT_PARAM_KEYS = {
    "allow_implied_send",
    "low_latency",
    "reasoning_effort",
    "supports_reasoning",
    "supports_temperature",
    "supports_tool_choice",
    "supports_vision",
    "use_parallel_tool_calls",
}


@dataclass
class FileHandlerLLMConfig:
    model: str
    params: Dict[str, Any]
    supports_vision: bool
    endpoint_key: str


def _get_current_eval_routing_profile() -> Any | None:
    try:
        from api.evals.execution import get_current_eval_routing_profile
    except ImportError:
        return None
    return get_current_eval_routing_profile()


def _resolve_routing_profile(routing_profile: Any | None) -> Any | None:
    if routing_profile is not None:
        return routing_profile
    eval_profile = _get_current_eval_routing_profile()
    if eval_profile is not None:
        return eval_profile
    return _resolve_active_routing_profile(None, purpose="file handler")


def _strip_routing_hints(params: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in params.items() if key not in _ROUTING_HINT_PARAM_KEYS}


def _config_from_profile(profile: Any | None) -> Optional[FileHandlerLLMConfig]:
    if profile is None:
        return None

    configs = _get_failover_configs_from_profile(
        token_count=0,
        agent_id=None,
        agent=None,
        is_first_loop=None,
        routing_profile=profile,
        prefer_low_latency=False,
        ignore_agent_tier_cap=True,
    )
    for endpoint_key, model_name, params in configs:
        if not params.get("supports_vision", False):
            continue
        return FileHandlerLLMConfig(
            model=model_name,
            params=_strip_routing_hints(params),
            supports_vision=True,
            endpoint_key=endpoint_key,
        )
    return None


def get_file_handler_llm_config(
    routing_profile: Any | None = None,
) -> Optional[FileHandlerLLMConfig]:
    profile = _resolve_routing_profile(routing_profile)
    profile_config = _config_from_profile(profile)
    if profile_config is not None:
        return profile_config

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
