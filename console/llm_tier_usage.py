"""Helpers for describing where LLM endpoints are referenced by routing tiers."""

from typing import Any, Iterable

from django.db.models import Q

from api.models import (
    BrowserModelEndpoint,
    BrowserTierEndpoint,
    LLMRoutingProfile,
    PersistentModelEndpoint,
    PersistentTierEndpoint,
    ProfileBrowserTierEndpoint,
    ProfilePersistentTierEndpoint,
)


def _empty_usage_map(endpoint_ids: Iterable[Any]) -> dict[Any, list[dict[str, Any]]]:
    return {endpoint_id: [] for endpoint_id in endpoint_ids}


def build_persistent_endpoint_tier_usage(endpoint: PersistentModelEndpoint) -> list[dict[str, Any]]:
    return build_persistent_endpoint_tier_usage_map([endpoint.id]).get(endpoint.id, [])


def build_persistent_endpoint_tier_usage_map(endpoint_ids: Iterable[Any]) -> dict[Any, list[dict[str, Any]]]:
    endpoint_ids = list(endpoint_ids)
    usage_by_endpoint = _empty_usage_map(endpoint_ids)
    if not endpoint_ids:
        return usage_by_endpoint

    legacy_refs = (
        PersistentTierEndpoint.objects.filter(endpoint_id__in=endpoint_ids)
        .select_related("tier__token_range", "tier__intelligence_tier")
        .order_by("tier__token_range__min_tokens", "tier__intelligence_tier__rank", "tier__order")
    )
    for ref in legacy_refs:
        tier = ref.tier
        usage_by_endpoint[ref.endpoint_id].append(
            {
                "id": str(ref.id),
                "source": "persistent_policy",
                "routing_profile": "Default persistent config",
                "routing_profile_active": True,
                "tier": f"{tier.token_range.name} / {tier.intelligence_tier.display_name} tier {tier.order}",
                "tier_order": tier.order,
                "intelligence_tier": tier.intelligence_tier.display_name,
                "description": tier.description,
                "weight": float(ref.weight),
                "role": "primary",
            }
        )

    profile_refs = (
        ProfilePersistentTierEndpoint.objects.filter(endpoint_id__in=endpoint_ids)
        .select_related("tier__token_range__profile", "tier__intelligence_tier")
        .order_by(
            "tier__token_range__profile__display_name",
            "tier__token_range__min_tokens",
            "tier__intelligence_tier__rank",
            "tier__order",
        )
    )
    for ref in profile_refs:
        tier = ref.tier
        profile = tier.token_range.profile
        usage_by_endpoint[ref.endpoint_id].append(
            {
                "id": str(ref.id),
                "source": "routing_profile",
                "routing_profile": profile.display_name or profile.name,
                "routing_profile_active": bool(profile.is_active),
                "tier": f"{tier.token_range.name} / {tier.intelligence_tier.display_name} tier {tier.order}",
                "tier_order": tier.order,
                "intelligence_tier": tier.intelligence_tier.display_name,
                "description": tier.description,
                "weight": float(ref.weight),
                "role": "primary",
            }
        )

    override_profiles = LLMRoutingProfile.objects.filter(
        Q(eval_judge_endpoint_id__in=endpoint_ids) | Q(summarization_endpoint_id__in=endpoint_ids)
    ).order_by("display_name")
    for profile in override_profiles:
        if profile.eval_judge_endpoint_id in usage_by_endpoint:
            usage_by_endpoint[profile.eval_judge_endpoint_id].append(
                {
                    "id": f"{profile.id}:eval_judge",
                    "source": "routing_profile",
                    "routing_profile": profile.display_name or profile.name,
                    "routing_profile_active": bool(profile.is_active),
                    "tier": "Eval judge override",
                    "tier_order": 0,
                    "intelligence_tier": "",
                    "description": "",
                    "role": "eval_judge",
                }
            )
        if profile.summarization_endpoint_id in usage_by_endpoint:
            usage_by_endpoint[profile.summarization_endpoint_id].append(
                {
                    "id": f"{profile.id}:summarization",
                    "source": "routing_profile",
                    "routing_profile": profile.display_name or profile.name,
                    "routing_profile_active": bool(profile.is_active),
                    "tier": "Summarization override",
                    "tier_order": 0,
                    "intelligence_tier": "",
                    "description": "",
                    "role": "summarization",
                }
            )
    return usage_by_endpoint


def build_browser_endpoint_tier_usage(endpoint: BrowserModelEndpoint) -> list[dict[str, Any]]:
    return build_browser_endpoint_tier_usage_map([endpoint.id]).get(endpoint.id, [])


def build_browser_endpoint_tier_usage_map(endpoint_ids: Iterable[Any]) -> dict[Any, list[dict[str, Any]]]:
    endpoint_ids = list(endpoint_ids)
    usage_by_endpoint = _empty_usage_map(endpoint_ids)
    if not endpoint_ids:
        return usage_by_endpoint

    legacy_refs = (
        BrowserTierEndpoint.objects.filter(Q(endpoint_id__in=endpoint_ids) | Q(extraction_endpoint_id__in=endpoint_ids))
        .select_related("tier__policy", "tier__intelligence_tier")
        .order_by("tier__policy__name", "tier__intelligence_tier__rank", "tier__order")
    )
    for ref in legacy_refs:
        _append_browser_ref_usage(
            usage_by_endpoint,
            ref.endpoint_id,
            ref.extraction_endpoint_id,
            ref,
            source="browser_policy",
            routing_profile=ref.tier.policy.name,
            routing_profile_active=bool(ref.tier.policy.is_active),
        )

    profile_refs = (
        ProfileBrowserTierEndpoint.objects.filter(
            Q(endpoint_id__in=endpoint_ids) | Q(extraction_endpoint_id__in=endpoint_ids)
        )
        .select_related("tier__profile", "tier__intelligence_tier")
        .order_by("tier__profile__display_name", "tier__intelligence_tier__rank", "tier__order")
    )
    for ref in profile_refs:
        _append_browser_ref_usage(
            usage_by_endpoint,
            ref.endpoint_id,
            ref.extraction_endpoint_id,
            ref,
            source="routing_profile",
            routing_profile=ref.tier.profile.display_name or ref.tier.profile.name,
            routing_profile_active=bool(ref.tier.profile.is_active),
        )
    return usage_by_endpoint


def _append_browser_ref_usage(
    usage_by_endpoint: dict[Any, list[dict[str, Any]]],
    endpoint_id: Any,
    extraction_endpoint_id: Any,
    ref: BrowserTierEndpoint | ProfileBrowserTierEndpoint,
    *,
    source: str,
    routing_profile: str,
    routing_profile_active: bool,
) -> None:
    appended_primary = False
    if endpoint_id in usage_by_endpoint and endpoint_id != extraction_endpoint_id:
        usage_by_endpoint[endpoint_id].append(
            _serialize_browser_ref(
                ref,
                source=source,
                routing_profile=routing_profile,
                routing_profile_active=routing_profile_active,
                role="primary",
            )
        )
        appended_primary = True
    if extraction_endpoint_id in usage_by_endpoint:
        usage_by_endpoint[extraction_endpoint_id].append(
            _serialize_browser_ref(
                ref,
                source=source,
                routing_profile=routing_profile,
                routing_profile_active=routing_profile_active,
                role="extraction",
            )
        )
    elif not appended_primary and endpoint_id in usage_by_endpoint:
        usage_by_endpoint[endpoint_id].append(
            _serialize_browser_ref(
                ref,
                source=source,
                routing_profile=routing_profile,
                routing_profile_active=routing_profile_active,
                role="primary",
            )
        )


def _serialize_browser_ref(
    ref: BrowserTierEndpoint | ProfileBrowserTierEndpoint,
    *,
    source: str,
    routing_profile: str,
    routing_profile_active: bool,
    role: str,
) -> dict[str, Any]:
    tier = ref.tier
    return {
        "id": str(ref.id),
        "source": source,
        "routing_profile": routing_profile,
        "routing_profile_active": routing_profile_active,
        "tier": f"{tier.intelligence_tier.display_name} tier {tier.order}",
        "tier_order": tier.order,
        "intelligence_tier": tier.intelligence_tier.display_name,
        "description": tier.description,
        "weight": float(ref.weight),
        "role": role,
    }
