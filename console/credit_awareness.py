from datetime import timedelta

from django.conf import settings
from django.urls import reverse

from billing.services import BillingService
from api.services.burn_rate_snapshots import (
    get_burn_rate_snapshot_for_agent,
    get_burn_rate_snapshot_for_owner,
    serialize_burn_rate_snapshot,
)
from api.services.plan_usage import (
    build_work_plan_credit_snapshot,
    serialize_current_work_plan_step_from_snapshot,
    serialize_work_plan_from_snapshot,
)
from console.daily_credit import (
    build_agent_daily_credit_context,
    build_daily_credit_status,
    serialize_daily_credit_payload,
)
from console.usage_views import _build_quota_payload


def build_credit_awareness_payload(agent, *, can_manage: bool = False, include_actions: bool = True) -> dict:
    owner = agent.organization or agent.user
    organization = agent.organization
    quota_user = agent.user
    daily_context = build_agent_daily_credit_context(agent, owner)
    quota_payload, extra_tasks_enabled, _unlimited, _available = _build_quota_payload(
        owner,
        user=quota_user,
        organization=organization,
    )
    plan_snapshot = build_work_plan_credit_snapshot(agent)
    billing_period_start, billing_period_end = BillingService.get_current_billing_period_for_owner(owner)
    window_minutes = settings.BURN_RATE_SNAPSHOT_DEFAULT_WINDOW_MINUTES
    owner_burn_rate = get_burn_rate_snapshot_for_owner(
        owner,
        window_minutes=window_minutes,
        max_age_minutes=settings.BURN_RATE_SNAPSHOT_STALE_MINUTES,
    )
    agent_burn_rate = get_burn_rate_snapshot_for_agent(
        agent,
        window_minutes=window_minutes,
        max_age_minutes=settings.BURN_RATE_SNAPSHOT_STALE_MINUTES,
    )

    payload = {
        "agentId": str(agent.id),
        "currentPlan": serialize_work_plan_from_snapshot(plan_snapshot),
        "currentStep": serialize_current_work_plan_step_from_snapshot(plan_snapshot),
        "dailyCredits": serialize_daily_credit_payload(daily_context),
        "dailyCreditsStatus": build_daily_credit_status(daily_context),
        "quota": quota_payload,
        "billingPeriod": {
            "start": billing_period_start.isoformat(),
            "end": billing_period_end.isoformat(),
            "resetOn": (billing_period_end + timedelta(days=1)).isoformat(),
        },
        "extraTasks": {"enabled": extra_tasks_enabled},
        "burnRate": {
            "owner": serialize_burn_rate_snapshot(owner_burn_rate),
            "agent": serialize_burn_rate_snapshot(agent_burn_rate),
        },
    }
    if include_actions:
        payload["actions"] = {
            "canAdjustDailyLimit": bool(can_manage),
            "canOpenTaskPacks": bool(can_manage),
            "canOpenUsage": True,
            "canOpenIntelligenceSettings": bool(can_manage),
            "usageUrl": reverse("usage"),
        }
    return payload
