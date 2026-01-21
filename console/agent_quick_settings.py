from django.conf import settings
from django.urls import NoReverseMatch, reverse

from constants.plans import PlanNamesChoices
from util.subscription_helper import get_organization_plan, get_user_plan

from console.daily_credit import (
    build_agent_daily_credit_context,
    build_daily_credit_status,
    serialize_daily_credit_payload,
)


def build_agent_quick_settings_payload(agent, owner=None) -> dict:
    context = build_agent_daily_credit_context(agent, owner)
    plan_payload = None
    upgrade_url = None
    if agent.organization_id:
        plan_payload = get_organization_plan(agent.organization)
    else:
        plan_payload = get_user_plan(agent.user)
    plan_id = str(plan_payload.get("id", "")).lower() if plan_payload else ""
    plan_name = plan_payload.get("name") if plan_payload else ""
    is_free_plan = plan_id == PlanNamesChoices.FREE.value

    if is_free_plan and settings.GOBII_PROPRIETARY_MODE:
        try:
            upgrade_url = reverse("proprietary:pricing")
        except NoReverseMatch:
            upgrade_url = None

    return {
        "settings": {
            "dailyCredits": serialize_daily_credit_payload(context),
        },
        "status": {
            "dailyCredits": build_daily_credit_status(context),
        },
        "meta": {
            "plan": {
                "id": plan_id,
                "name": plan_name,
                "isFree": is_free_plan,
            },
            "upgradeUrl": upgrade_url,
        },
    }
