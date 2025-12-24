"""Serialization helpers for the console LLM configuration UI."""

from __future__ import annotations

import os
from typing import Any

from django.db.models import Prefetch

from api.models import (
    BrowserLLMPolicy,
    BrowserLLMTier,
    BrowserTierEndpoint,
    BrowserModelEndpoint,
    EmbeddingsLLMTier,
    EmbeddingsTierEndpoint,
    EmbeddingsModelEndpoint,
    FileHandlerLLMTier,
    FileHandlerTierEndpoint,
    FileHandlerModelEndpoint,
    LLMProvider,
    LLMRoutingProfile,
    PersistentLLMTier,
    PersistentTierEndpoint,
    PersistentModelEndpoint,
    PersistentTokenRange,
    ProfileBrowserTier,
    ProfileBrowserTierEndpoint,
    ProfileEmbeddingsTier,
    ProfileEmbeddingsTierEndpoint,
    ProfilePersistentTier,
    ProfilePersistentTierEndpoint,
    ProfileTokenRange,
)


def _provider_key_status(provider: LLMProvider) -> str:
    has_admin_key = bool(provider.api_key_encrypted)
    has_env = bool(provider.env_var_name and os.getenv(provider.env_var_name))
    if not (has_admin_key or has_env):
        return "Missing key"
    if has_admin_key:
        return "Admin key"
    return "Env var"


def _serialize_endpoint_common(endpoint, *, label: str) -> dict[str, Any]:
    return {
        "id": str(endpoint.id),
        "label": label,
        "enabled": bool(endpoint.enabled),
    }


def _serialize_persistent_endpoint(endpoint: PersistentModelEndpoint) -> dict[str, Any]:
    label = f"{endpoint.provider.display_name} · {endpoint.litellm_model}"
    data = _serialize_endpoint_common(endpoint, label=label)
    data.update(
        {
            "key": endpoint.key,
            "model": endpoint.litellm_model,
            "temperature_override": endpoint.temperature_override,
            "supports_temperature": endpoint.supports_temperature,
            "supports_tool_choice": endpoint.supports_tool_choice,
            "use_parallel_tool_calls": endpoint.use_parallel_tool_calls,
            "supports_vision": endpoint.supports_vision,
            "supports_reasoning": endpoint.supports_reasoning,
            "reasoning_effort": endpoint.reasoning_effort,
            "api_base": endpoint.api_base,
            "provider_id": str(endpoint.provider_id),
            "type": "persistent",
        }
    )
    return data


def _serialize_browser_endpoint(endpoint: BrowserModelEndpoint) -> dict[str, Any]:
    label = f"{endpoint.provider.display_name} · {endpoint.browser_model}"
    data = _serialize_endpoint_common(endpoint, label=label)
    data.update(
        {
            "key": endpoint.key,
            "model": endpoint.browser_model,
            "browser_base_url": endpoint.browser_base_url,
            "supports_temperature": endpoint.supports_temperature,
            "supports_vision": endpoint.supports_vision,
            "max_output_tokens": endpoint.max_output_tokens,
            "provider_id": str(endpoint.provider_id),
            "type": "browser",
        }
    )
    return data


def _serialize_embedding_endpoint(endpoint: EmbeddingsModelEndpoint) -> dict[str, Any]:
    label = f"{endpoint.provider.display_name if endpoint.provider else 'Unlinked'} · {endpoint.litellm_model}"
    data = _serialize_endpoint_common(endpoint, label=label)
    data.update(
        {
            "key": endpoint.key,
            "model": endpoint.litellm_model,
            "api_base": endpoint.api_base,
            "provider_id": str(endpoint.provider_id) if endpoint.provider_id else None,
            "type": "embedding",
        }
    )
    return data


def _serialize_file_handler_endpoint(endpoint: FileHandlerModelEndpoint) -> dict[str, Any]:
    label = f"{endpoint.provider.display_name if endpoint.provider else 'Unlinked'} · {endpoint.litellm_model}"
    data = _serialize_endpoint_common(endpoint, label=label)
    data.update(
        {
            "key": endpoint.key,
            "model": endpoint.litellm_model,
            "api_base": endpoint.api_base,
            "supports_vision": endpoint.supports_vision,
            "provider_id": str(endpoint.provider_id) if endpoint.provider_id else None,
            "type": "file_handler",
        }
    )
    return data


def build_llm_overview() -> dict[str, Any]:
    providers = (
        LLMProvider.objects.all()
        .order_by("display_name")
        .prefetch_related(
            Prefetch("persistent_endpoints", queryset=PersistentModelEndpoint.objects.select_related("provider")),
            Prefetch("browser_endpoints", queryset=BrowserModelEndpoint.objects.select_related("provider")),
            Prefetch("embedding_endpoints", queryset=EmbeddingsModelEndpoint.objects.select_related("provider")),
            Prefetch("file_handler_endpoints", queryset=FileHandlerModelEndpoint.objects.select_related("provider")),
        )
    )

    provider_payload: list[dict[str, Any]] = []
    persistent_choices: list[dict[str, Any]] = []
    browser_choices: list[dict[str, Any]] = []
    embedding_choices: list[dict[str, Any]] = []
    file_handler_choices: list[dict[str, Any]] = []

    for provider in providers:
        persistent_endpoints = [
            _serialize_persistent_endpoint(endpoint)
            for endpoint in provider.persistent_endpoints.all()
        ]
        browser_endpoints = [
            _serialize_browser_endpoint(endpoint)
            for endpoint in provider.browser_endpoints.all()
        ]
        embedding_endpoints = [
            _serialize_embedding_endpoint(endpoint)
            for endpoint in provider.embedding_endpoints.all()
        ]
        file_handler_endpoints = [
            _serialize_file_handler_endpoint(endpoint)
            for endpoint in provider.file_handler_endpoints.all()
        ]

        persistent_choices.extend(persistent_endpoints)
        browser_choices.extend(browser_endpoints)
        embedding_choices.extend(embedding_endpoints)
        file_handler_choices.extend(file_handler_endpoints)

        provider_payload.append(
            {
                "id": str(provider.id),
                "name": provider.display_name,
                "key": provider.key,
                "enabled": bool(provider.enabled),
                "env_var": provider.env_var_name,
                "browser_backend": provider.browser_backend,
                "supports_safety_identifier": provider.supports_safety_identifier,
                "vertex_project": provider.vertex_project,
                "vertex_location": provider.vertex_location,
                "status": _provider_key_status(provider),
                "endpoints": persistent_endpoints + browser_endpoints + embedding_endpoints + file_handler_endpoints,
            }
        )

    persistent_ranges = (
        PersistentTokenRange.objects.order_by("min_tokens")
        .prefetch_related(
            Prefetch(
                "tiers",
                queryset=PersistentLLMTier.objects.order_by("is_premium", "is_max", "order").prefetch_related(
                    Prefetch(
                        "tier_endpoints",
                        queryset=PersistentTierEndpoint.objects.select_related("endpoint__provider").order_by("endpoint__litellm_model"),
                    )
                ),
            )
        )
    )

    persistent_payload: list[dict[str, Any]] = []
    for token_range in persistent_ranges:
        tiers_payload: list[dict[str, Any]] = []
        for tier in token_range.tiers.all():
            tier_endpoints = []
            for te in tier.tier_endpoints.all():
                endpoint = te.endpoint
                tier_endpoints.append(
                    {
                        "id": str(te.id),
                        "endpoint_id": str(endpoint.id),
                        "label": f"{endpoint.provider.display_name} · {endpoint.litellm_model}",
                        "weight": float(te.weight),
                        "endpoint_key": endpoint.key,
                        "reasoning_effort_override": te.reasoning_effort_override,
                        "supports_reasoning": endpoint.supports_reasoning,
                        "endpoint_reasoning_effort": endpoint.reasoning_effort,
                    }
                )
            tiers_payload.append(
                {
                    "id": str(tier.id),
                    "order": tier.order,
                    "description": tier.description,
                    "is_premium": tier.is_premium,
                    "is_max": tier.is_max,
                    "endpoints": tier_endpoints,
                }
            )
        persistent_payload.append(
            {
                "id": str(token_range.id),
                "name": token_range.name,
                "min_tokens": token_range.min_tokens,
                "max_tokens": token_range.max_tokens,
                "tiers": tiers_payload,
            }
        )

    policy = (
        BrowserLLMPolicy.objects.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "tiers",
                queryset=BrowserLLMTier.objects.order_by("is_premium", "order").prefetch_related(
            Prefetch(
                "tier_endpoints",
                queryset=BrowserTierEndpoint.objects.select_related(
                    "endpoint__provider",
                    "extraction_endpoint__provider",
                ).order_by("endpoint__browser_model"),
            )
        ),
            )
        )
        .first()
    )

    browser_payload: dict[str, Any] | None = None
    if policy:
        tiers_payload: list[dict[str, Any]] = []
        for tier in policy.tiers.all():
            tier_endpoints = []
            for te in tier.tier_endpoints.all():
                endpoint = te.endpoint
                extraction = te.extraction_endpoint
                tier_endpoints.append(
                    {
                        "id": str(te.id),
                        "endpoint_id": str(endpoint.id),
                        "label": f"{endpoint.provider.display_name} · {endpoint.browser_model}",
                        "weight": float(te.weight),
                        "endpoint_key": endpoint.key,
                        "extraction_endpoint_id": str(extraction.id) if extraction else None,
                        "extraction_endpoint_key": extraction.key if extraction else None,
                        "extraction_label": (
                            f"{extraction.provider.display_name} · {extraction.browser_model}"
                            if extraction
                            else None
                        ),
                    }
                )
            tiers_payload.append(
                {
                    "id": str(tier.id),
                    "order": tier.order,
                    "description": tier.description,
                    "is_premium": tier.is_premium,
                    "endpoints": tier_endpoints,
                }
            )
        browser_payload = {
            "id": str(policy.id),
            "name": policy.name,
            "tiers": tiers_payload,
        }

    embedding_payload: list[dict[str, Any]] = []
    embedding_tiers = EmbeddingsLLMTier.objects.prefetch_related(
        Prefetch(
            "tier_endpoints",
            queryset=EmbeddingsTierEndpoint.objects.select_related("endpoint__provider").order_by("-weight"),
        )
    ).order_by("order")
    for tier in embedding_tiers:
        tier_endpoints = []
        for te in tier.tier_endpoints.all():
            endpoint = te.endpoint
            label_provider = endpoint.provider.display_name if endpoint.provider else "Unlinked"
            tier_endpoints.append(
                {
                    "id": str(te.id),
                    "endpoint_id": str(endpoint.id),
                    "label": f"{label_provider} · {endpoint.litellm_model}",
                    "weight": float(te.weight),
                    "endpoint_key": endpoint.key,
                }
            )
        embedding_payload.append(
            {
                "id": str(tier.id),
                "order": tier.order,
                "description": tier.description,
                "endpoints": tier_endpoints,
            }
        )

    file_handler_payload: list[dict[str, Any]] = []
    file_handler_tiers = FileHandlerLLMTier.objects.prefetch_related(
        Prefetch(
            "tier_endpoints",
            queryset=FileHandlerTierEndpoint.objects.select_related("endpoint__provider").order_by("-weight"),
        )
    ).order_by("order")
    for tier in file_handler_tiers:
        tier_endpoints = []
        for te in tier.tier_endpoints.all():
            endpoint = te.endpoint
            label_provider = endpoint.provider.display_name if endpoint.provider else "Unlinked"
            tier_endpoints.append(
                {
                    "id": str(te.id),
                    "endpoint_id": str(endpoint.id),
                    "label": f"{label_provider} · {endpoint.litellm_model}",
                    "weight": float(te.weight),
                    "endpoint_key": endpoint.key,
                }
            )
        file_handler_payload.append(
            {
                "id": str(tier.id),
                "order": tier.order,
                "description": tier.description,
                "endpoints": tier_endpoints,
            }
        )

    stats = {
        "active_providers": LLMProvider.objects.filter(enabled=True).count(),
        "persistent_endpoints": PersistentModelEndpoint.objects.filter(enabled=True).count(),
        "browser_endpoints": BrowserModelEndpoint.objects.filter(enabled=True).count(),
        "premium_persistent_tiers": PersistentLLMTier.objects.filter(is_premium=True).count(),
    }

    return {
        "stats": stats,
        "providers": provider_payload,
        "persistent": {"ranges": persistent_payload},
        "browser": browser_payload,
        "embeddings": {"tiers": embedding_payload},
        "file_handlers": {"tiers": file_handler_payload},
        "choices": {
            "persistent_endpoints": persistent_choices,
            "browser_endpoints": browser_choices,
            "embedding_endpoints": embedding_choices,
            "file_handler_endpoints": file_handler_choices,
        },
    }


def serialize_routing_profile_list_item(profile: LLMRoutingProfile) -> dict[str, Any]:
    """Serialize a profile for list views (minimal details)."""
    eval_judge = None
    if profile.eval_judge_endpoint_id:
        eval_judge = {
            "endpoint_id": str(profile.eval_judge_endpoint_id),
        }
    return {
        "id": str(profile.id),
        "name": profile.name,
        "display_name": profile.display_name,
        "description": profile.description,
        "is_active": profile.is_active,
        "is_eval_snapshot": profile.is_eval_snapshot,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        "cloned_from_id": str(profile.cloned_from_id) if profile.cloned_from_id else None,
        "eval_judge_endpoint_id": str(profile.eval_judge_endpoint_id) if profile.eval_judge_endpoint_id else None,
    }


def serialize_routing_profile_detail(profile: LLMRoutingProfile) -> dict[str, Any]:
    """
    Serialize a full routing profile with all nested config.
    Expects the profile to have prefetched related objects.
    """
    # Persistent config: token ranges -> tiers -> endpoints
    persistent_ranges: list[dict[str, Any]] = []
    for token_range in profile.persistent_token_ranges.all():
        tiers_payload: list[dict[str, Any]] = []
        for tier in token_range.tiers.all():
            tier_endpoints = []
            for te in tier.tier_endpoints.all():
                endpoint = te.endpoint
                tier_endpoints.append({
                    "id": str(te.id),
                    "endpoint_id": str(endpoint.id),
                    "label": f"{endpoint.provider.display_name} · {endpoint.litellm_model}",
                    "weight": float(te.weight),
                    "endpoint_key": endpoint.key,
                    "reasoning_effort_override": te.reasoning_effort_override,
                    "supports_reasoning": endpoint.supports_reasoning,
                    "endpoint_reasoning_effort": endpoint.reasoning_effort,
                })
            tiers_payload.append({
                "id": str(tier.id),
                "order": tier.order,
                "description": tier.description,
                "is_premium": tier.is_premium,
                "is_max": tier.is_max,
                "credit_multiplier": str(tier.credit_multiplier) if tier.credit_multiplier else None,
                "endpoints": tier_endpoints,
            })
        persistent_ranges.append({
            "id": str(token_range.id),
            "name": token_range.name,
            "min_tokens": token_range.min_tokens,
            "max_tokens": token_range.max_tokens,
            "tiers": tiers_payload,
        })

    # Browser config: tiers -> endpoints
    browser_tiers: list[dict[str, Any]] = []
    for tier in profile.browser_tiers.all():
        tier_endpoints = []
        for te in tier.tier_endpoints.all():
            endpoint = te.endpoint
            extraction = te.extraction_endpoint
            tier_endpoints.append({
                "id": str(te.id),
                "endpoint_id": str(endpoint.id),
                "label": f"{endpoint.provider.display_name} · {endpoint.browser_model}",
                "weight": float(te.weight),
                "endpoint_key": endpoint.key,
                "extraction_endpoint_id": str(extraction.id) if extraction else None,
                "extraction_endpoint_key": extraction.key if extraction else None,
                "extraction_label": (
                    f"{extraction.provider.display_name} · {extraction.browser_model}"
                    if extraction
                    else None
                ),
            })
        browser_tiers.append({
            "id": str(tier.id),
            "order": tier.order,
            "description": tier.description,
            "is_premium": tier.is_premium,
            "endpoints": tier_endpoints,
        })

    # Embeddings config: tiers -> endpoints
    embedding_tiers: list[dict[str, Any]] = []
    for tier in profile.embeddings_tiers.all():
        tier_endpoints = []
        for te in tier.tier_endpoints.all():
            endpoint = te.endpoint
            label_provider = endpoint.provider.display_name if endpoint.provider else "Unlinked"
            tier_endpoints.append({
                "id": str(te.id),
                "endpoint_id": str(endpoint.id),
                "label": f"{label_provider} · {endpoint.litellm_model}",
                "weight": float(te.weight),
                "endpoint_key": endpoint.key,
            })
        embedding_tiers.append({
            "id": str(tier.id),
            "order": tier.order,
            "description": tier.description,
            "endpoints": tier_endpoints,
        })

    # Eval judge endpoint info
    eval_judge_endpoint = None
    if profile.eval_judge_endpoint:
        ep = profile.eval_judge_endpoint
        provider_name = ep.provider.display_name if ep.provider else "Unlinked"
        eval_judge_endpoint = {
            "endpoint_id": str(ep.id),
            "endpoint_key": ep.key,
            "label": f"{provider_name} · {ep.litellm_model}",
            "model": ep.litellm_model,
        }

    return {
        "id": str(profile.id),
        "name": profile.name,
        "display_name": profile.display_name,
        "description": profile.description,
        "is_active": profile.is_active,
        "is_eval_snapshot": profile.is_eval_snapshot,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        "cloned_from_id": str(profile.cloned_from_id) if profile.cloned_from_id else None,
        "eval_judge_endpoint": eval_judge_endpoint,
        "persistent": {"ranges": persistent_ranges},
        "browser": {"tiers": browser_tiers},
        "embeddings": {"tiers": embedding_tiers},
    }


def build_routing_profiles_list() -> list[dict[str, Any]]:
    """Build a list of all routing profiles (minimal details).

    Excludes eval snapshots which are frozen copies created for eval runs.
    """
    profiles = LLMRoutingProfile.objects.filter(is_eval_snapshot=False).order_by("-is_active", "-updated_at")
    return [serialize_routing_profile_list_item(p) for p in profiles]


def get_routing_profile_with_prefetch(profile_id: str) -> LLMRoutingProfile:
    """
    Fetch a routing profile with all nested relations prefetched for serialization.
    """
    # Prefetch for persistent: token_ranges -> tiers -> tier_endpoints -> endpoint.provider
    persistent_tier_endpoint_prefetch = Prefetch(
        "tier_endpoints",
        queryset=ProfilePersistentTierEndpoint.objects.select_related("endpoint__provider").order_by("endpoint__litellm_model"),
    )
    persistent_tier_prefetch = Prefetch(
        "tiers",
        queryset=ProfilePersistentTier.objects.prefetch_related(persistent_tier_endpoint_prefetch).order_by("is_premium", "is_max", "order"),
    )
    persistent_range_prefetch = Prefetch(
        "persistent_token_ranges",
        queryset=ProfileTokenRange.objects.prefetch_related(persistent_tier_prefetch).order_by("min_tokens"),
    )

    # Prefetch for browser: browser_tiers -> tier_endpoints -> endpoint.provider
    browser_tier_endpoint_prefetch = Prefetch(
        "tier_endpoints",
        queryset=ProfileBrowserTierEndpoint.objects.select_related(
            "endpoint__provider",
            "extraction_endpoint__provider",
        ).order_by("endpoint__browser_model"),
    )
    browser_tier_prefetch = Prefetch(
        "browser_tiers",
        queryset=ProfileBrowserTier.objects.prefetch_related(browser_tier_endpoint_prefetch).order_by("is_premium", "order"),
    )

    # Prefetch for embeddings: embeddings_tiers -> tier_endpoints -> endpoint.provider
    embedding_tier_endpoint_prefetch = Prefetch(
        "tier_endpoints",
        queryset=ProfileEmbeddingsTierEndpoint.objects.select_related("endpoint__provider").order_by("-weight"),
    )
    embedding_tier_prefetch = Prefetch(
        "embeddings_tiers",
        queryset=ProfileEmbeddingsTier.objects.prefetch_related(embedding_tier_endpoint_prefetch).order_by("order"),
    )

    return LLMRoutingProfile.objects.select_related(
        "eval_judge_endpoint__provider",
    ).prefetch_related(
        persistent_range_prefetch,
        browser_tier_prefetch,
        embedding_tier_prefetch,
    ).get(id=profile_id)


__all__ = [
    "build_llm_overview",
    "build_routing_profiles_list",
    "get_routing_profile_with_prefetch",
    "serialize_routing_profile_detail",
    "serialize_routing_profile_list_item",
]
