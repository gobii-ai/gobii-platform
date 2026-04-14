from django.core.exceptions import ObjectDoesNotExist

from api.models import DailyCreditConfig, PersistentAgent
from api.services.daily_credit_limits import (
    calculate_default_daily_credit_limit,
    calculate_daily_credit_slider_bounds,
    get_tier_credit_multiplier,
)
from api.services.daily_credit_settings import (
    daily_credit_settings_from_payload,
    serialize_daily_credit_configs,
)
from api.services.plan_settings import select_plan_settings_payload
from constants.plans import PlanNamesChoices


def _owner_plan_identifiers(owner):
    try:
        billing = owner.billing
    except ObjectDoesNotExist:
        billing = None

    if billing is None:
        return PlanNamesChoices.FREE, None

    plan_name = (billing.subscription or PlanNamesChoices.FREE).lower()
    plan_version_id = str(billing.plan_version_id) if billing.plan_version_id else None
    return plan_name, plan_version_id


def _config_applies_to_owner(config, settings_map, *, plan_name, plan_version_id):
    if config.plan_version_id:
        return plan_version_id == str(config.plan_version_id)

    if plan_version_id and plan_version_id in settings_map.get("by_plan_version", {}):
        return False

    return (plan_name or PlanNamesChoices.FREE).lower() == (config.plan_name or "").lower()


def _default_limit_from_payload(payload, *, plan_name, tier_multiplier):
    credit_settings = daily_credit_settings_from_payload(payload, plan_name=plan_name)
    slider_bounds = calculate_daily_credit_slider_bounds(
        credit_settings,
        tier_multiplier=tier_multiplier,
    )
    return calculate_default_daily_credit_limit(
        credit_settings.default_daily_credit_target,
        tier_multiplier=tier_multiplier,
        slider_min=slider_bounds["slider_min"],
        slider_max=slider_bounds["slider_limit_max"],
    )


def apply_default_daily_credit_target_to_matching_agents(config, previous_settings_map) -> int:
    current_settings_map = serialize_daily_credit_configs(
        DailyCreditConfig.objects.select_related("plan_version__plan").all()
    )

    pending_updates = []
    updated_count = 0
    agents = (
        PersistentAgent.objects.filter(is_deleted=False)
        .select_related("preferred_llm_tier", "user__billing", "organization__billing")
        .iterator(chunk_size=200)
    )
    for agent in agents:
        owner = agent.organization or agent.user
        plan_name, plan_version_id = _owner_plan_identifiers(owner)
        if not _config_applies_to_owner(
            config,
            previous_settings_map,
            plan_name=plan_name,
            plan_version_id=plan_version_id,
        ):
            continue

        old_payload = select_plan_settings_payload(previous_settings_map, plan_version_id, plan_name)
        new_payload = select_plan_settings_payload(current_settings_map, plan_version_id, plan_name)
        tier_multiplier = get_tier_credit_multiplier(agent.preferred_llm_tier)

        old_default_limit = _default_limit_from_payload(
            old_payload,
            plan_name=plan_name,
            tier_multiplier=tier_multiplier,
        )
        if agent.daily_credit_limit != old_default_limit:
            continue

        new_default_limit = _default_limit_from_payload(
            new_payload,
            plan_name=plan_name,
            tier_multiplier=tier_multiplier,
        )
        if new_default_limit == old_default_limit:
            continue

        agent.daily_credit_limit = new_default_limit
        pending_updates.append(agent)
        if len(pending_updates) >= 200:
            PersistentAgent.objects.bulk_update(pending_updates, ["daily_credit_limit"], batch_size=200)
            updated_count += len(pending_updates)
            pending_updates.clear()

    if pending_updates:
        PersistentAgent.objects.bulk_update(pending_updates, ["daily_credit_limit"], batch_size=200)
        updated_count += len(pending_updates)

    return updated_count
