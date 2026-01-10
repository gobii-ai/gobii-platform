"""
Insight generation for agent chat sessions.

Insights are contextual, helpful information shown inline during the "agent working" state.
They provide value during LLM processing latency by showing time saved stats, burn rates, etc.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any, Optional

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views import View
from django.urls import reverse

from api.models import (
    BrowserUseAgentTask,
    CommsChannel,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentStep,
)
from api.agent.core.prompt_context import get_agent_daily_credit_state
from billing.services import BillingService
from console.agent_chat.access import resolve_agent
from console.context_helpers import build_console_context
from console.phone_utils import get_primary_phone, serialize_phone
from config import settings
from config.stripe_config import get_stripe_settings
from constants.plans import PlanNamesChoices
from djstripe.models import Price
from util.subscription_helper import get_organization_plan, get_user_plan

logger = logging.getLogger(__name__)

# Feature flag
INSIGHTS_ENABLED = getattr(settings, "INSIGHTS_ENABLED", True)

# Time saved estimation constants (in minutes)
TIME_SAVED_PER_SIMPLE_TASK = 5  # Web search, simple email
TIME_SAVED_PER_MEDIUM_TASK = 15  # Multi-step research
TIME_SAVED_PER_COMPLEX_TASK = 30  # Browser automation, analysis

DECIMAL_ZERO = Decimal("0")
CURRENCY_SYMBOLS = {
    "usd": "$",
}


@dataclass
class InsightContext:
    """Context for generating insights."""
    agent: PersistentAgent
    user: Any
    organization: Optional[Any]
    period_start: datetime
    period_end: datetime


def _format_price_label(price_id: str | None) -> str | None:
    if not price_id:
        return None

    try:
        price = Price.objects.get(id=price_id)
    except Price.DoesNotExist:
        return None
    except Exception:
        logger.exception("Failed to load Stripe price %s for insights", price_id)
        return None

    raw_amount = price.unit_amount
    if raw_amount is None:
        raw_amount = price.unit_amount_decimal
    if raw_amount is None:
        return None

    amount = Decimal(str(raw_amount)) / Decimal("100")
    currency = (getattr(price, "currency", "") or "usd").lower()
    symbol = CURRENCY_SYMBOLS.get(currency)

    if amount == amount.to_integral_value():
        amount_text = f"{amount:.0f}"
    else:
        amount_text = f"{amount:.2f}".rstrip("0").rstrip(".")

    if symbol:
        amount_text = f"{symbol}{amount_text}"
    else:
        amount_text = f"{amount_text} {currency.upper()}"

    interval_label = None
    recurring = getattr(price, "recurring", None)
    if isinstance(recurring, dict):
        interval = recurring.get("interval")
        interval_count = recurring.get("interval_count") or 1
        if interval:
            if interval_count and interval_count != 1:
                interval_label = f"{interval_count} {interval}"
            else:
                interval_label = interval

    if interval_label:
        return f"{amount_text} / {interval_label}"
    return amount_text


def _get_plan_price_labels() -> tuple[str | None, str | None]:
    try:
        stripe_settings = get_stripe_settings()
    except Exception:
        logger.exception("Failed to load Stripe settings for insights pricing")
        return None, None

    return (
        _format_price_label(getattr(stripe_settings, "startup_price_id", None)),
        _format_price_label(getattr(stripe_settings, "scale_price_id", None)),
    )


def _get_agent_setup_insight(request: HttpRequest, agent: PersistentAgent, organization: Optional[Any]) -> dict:
    phone = get_primary_phone(request.user)
    phone_payload = serialize_phone(phone)
    agent_sms = agent.comms_endpoints.filter(channel=CommsChannel.SMS).first()

    org_memberships = OrganizationMembership.objects.filter(
        user=request.user,
        status=OrganizationMembership.OrgStatus.ACTIVE,
        role__in=[OrganizationMembership.OrgRole.OWNER, OrganizationMembership.OrgRole.ADMIN],
    ).select_related("org").order_by("org__name")
    org_options = [
        {
            "id": str(membership.org.id),
            "name": membership.org.name,
        }
        for membership in org_memberships
    ]

    current_org = None
    if agent.organization_id:
        current_org = {
            "id": str(agent.organization_id),
            "name": agent.organization.name,
        }

    owner_plan = get_user_plan(request.user)
    if agent.organization_id and organization is not None:
        owner_plan = get_organization_plan(organization)
    plan_id = str(owner_plan.get("id", "")).lower() if owner_plan else ""

    upsell_items: list[dict] = []
    always_on_note = None
    pro_price_label = None
    scale_price_label = None
    if settings.GOBII_PROPRIETARY_MODE:
        pro_price_label, scale_price_label = _get_plan_price_labels()
        show_pro_scale = plan_id == PlanNamesChoices.FREE.value
        show_scale = plan_id in (PlanNamesChoices.FREE.value, PlanNamesChoices.STARTUP.value)

        if show_pro_scale:
            upsell_items.append({
                "plan": "pro",
                "title": "Pro",
                "subtitle": "More capacity and richer channels",
                "body": "Priority routing, higher contact limits, and agents that never expire.",
                "bullets": [
                    "Faster responses for live conversations",
                    "More contacts per agent with better deliverability",
                ],
                "price": pro_price_label,
                "ctaLabel": "Upgrade to Pro",
                "accent": "indigo",
            })

        if show_scale:
            upsell_items.append({
                "plan": "scale",
                "title": "Scale",
                "subtitle": "Dedicated throughput and resilience",
                "body": "Top-tier limits, premium support, and highest intelligence levels.",
                "bullets": [
                    "Highest credit pools and rate limits",
                ],
                "price": scale_price_label,
                "ctaLabel": "Upgrade to Scale",
                "accent": "violet",
            })

        if plan_id == PlanNamesChoices.FREE.value:
            always_on_note = "Free plan always-on runs up to 30 days."

    checkout = {}
    if settings.GOBII_PROPRIETARY_MODE:
        checkout = {
            "proUrl": reverse("proprietary:pro_checkout"),
            "scaleUrl": reverse("proprietary:scale_checkout"),
        }

    utm_querystring = request.session.get("utm_querystring") or ""

    return {
        "insightId": f"agent_setup_{agent.id}",
        "insightType": "agent_setup",
        "priority": 100,
        "title": "Always-on setup",
        "body": "Keep the agent running and stay in the loop.",
        "metadata": {
            "agentId": str(agent.id),
            "alwaysOn": {
                "title": "You can close this tab",
                "body": "Your agent keeps working 24/7 in the background and will message you when it has updates.",
                "note": always_on_note,
            },
            "sms": {
                "enabled": bool(agent_sms),
                "agentNumber": agent_sms.address if agent_sms else None,
                "userPhone": phone_payload,
            },
            "organization": {
                "currentOrg": current_org,
                "options": org_options,
            },
            "upsell": {
                "items": upsell_items,
                "planId": plan_id,
            } if upsell_items else None,
            "checkout": checkout,
            "utmQuerystring": utm_querystring,
        },
        "dismissible": False,
    }


def _estimate_time_saved_minutes(tasks_completed: int, credits_used: Decimal) -> float:
    """
    Estimate time saved based on task count and credit usage.

    Methodology:
    - Base estimate: 10 minutes per task
    - Adjusted by credit intensity (higher credits = more complex task)
    - Conservative multiplier to avoid overclaiming
    """
    if tasks_completed <= 0:
        return 0.0

    # Average credits per task indicates complexity
    avg_credits = float(credits_used) / tasks_completed if tasks_completed > 0 else 0

    # Base time per task, scaled by complexity
    if avg_credits < 0.5:
        minutes_per_task = TIME_SAVED_PER_SIMPLE_TASK
    elif avg_credits < 2.0:
        minutes_per_task = TIME_SAVED_PER_MEDIUM_TASK
    else:
        minutes_per_task = TIME_SAVED_PER_COMPLEX_TASK

    return tasks_completed * minutes_per_task


def _get_time_saved_insight(ctx: InsightContext) -> Optional[dict]:
    """Generate time saved insight for user."""
    # Query completed tasks in period
    task_filters = {
        "is_deleted": False,
        "status": BrowserUseAgentTask.StatusChoices.COMPLETED,
        "created_at__gte": ctx.period_start,
        "created_at__lte": ctx.period_end,
    }

    if ctx.organization:
        task_filters["organization"] = ctx.organization
    else:
        task_filters["user"] = ctx.user
        task_filters["organization__isnull"] = True

    # Get task count and total credits
    task_stats = BrowserUseAgentTask.objects.filter(**task_filters).aggregate(
        count=Count("id"),
        credits=Coalesce(Sum("credits_cost"), DECIMAL_ZERO),
    )

    # Also get persistent agent step credits
    step_filters = {
        "created_at__gte": ctx.period_start,
        "created_at__lte": ctx.period_end,
    }
    if ctx.organization:
        step_filters["agent__organization"] = ctx.organization
    else:
        step_filters["agent__user"] = ctx.user
        step_filters["agent__organization__isnull"] = True

    step_stats = PersistentAgentStep.objects.filter(**step_filters).aggregate(
        credits=Coalesce(Sum("credits_cost"), DECIMAL_ZERO),
    )

    tasks_completed = task_stats.get("count", 0) or 0
    task_credits = task_stats.get("credits", DECIMAL_ZERO) or DECIMAL_ZERO
    step_credits = step_stats.get("credits", DECIMAL_ZERO) or DECIMAL_ZERO
    total_credits = task_credits + step_credits

    # Need at least some activity to show this insight
    if tasks_completed < 1 and total_credits < Decimal("0.1"):
        return None

    # Use task count as proxy, or estimate from credits if no tasks
    if tasks_completed > 0:
        estimated_tasks = tasks_completed
    else:
        # Estimate ~1 task per 0.5 credits as a rough proxy
        estimated_tasks = max(1, int(float(total_credits) / 0.5))

    time_saved_minutes = _estimate_time_saved_minutes(estimated_tasks, total_credits)
    hours_saved = time_saved_minutes / 60

    # Only show if meaningful time saved
    if hours_saved < 0.1:
        return None

    # Determine period label
    period_days = (ctx.period_end - ctx.period_start).days + 1
    if period_days <= 7:
        period_label = "week"
    elif period_days <= 31:
        period_label = "month"
    else:
        period_label = "all_time"

    return {
        "insightId": f"time_saved_{uuid.uuid4().hex[:8]}",
        "insightType": "time_saved",
        "priority": 10,
        "title": "Time saved",
        "body": f"You've saved approximately {hours_saved:.1f} hours this {period_label}",
        "metadata": {
            "hoursSaved": round(hours_saved, 1),
            "tasksCompleted": estimated_tasks,
            "comparisonPeriod": period_label,
            "methodology": "Estimate based on typical manual effort per task type",
        },
        "dismissible": True,
    }


def _get_burn_rate_insight(ctx: InsightContext) -> Optional[dict]:
    """Generate burn rate insight for current agent."""
    try:
        daily_state = get_agent_daily_credit_state(ctx.agent)
    except Exception as e:
        logger.info("Failed to get daily credit state for agent %s: %s", ctx.agent.id, e)
        # Return a fallback insight with zero values
        daily_state = {
            "burn_rate_per_hour": Decimal("0"),
            "used": Decimal("0"),
            "hard_limit": None,
            "soft_target": Decimal("100"),
        }

    if not daily_state:
        daily_state = {
            "burn_rate_per_hour": Decimal("0"),
            "used": Decimal("0"),
            "hard_limit": None,
            "soft_target": Decimal("100"),
        }

    burn_rate = daily_state.get("burn_rate_per_hour")
    used_today = daily_state.get("used", DECIMAL_ZERO)
    hard_limit = daily_state.get("hard_limit")
    soft_target = daily_state.get("soft_target")

    # Calculate daily limit and percent used
    daily_limit = hard_limit or soft_target
    if daily_limit is None or daily_limit <= 0:
        daily_limit = Decimal("100")  # Default fallback

    percent_used = min(100, float(used_today / daily_limit * 100)) if daily_limit > 0 else 0

    # Get all agents' usage for today
    today = timezone.localdate()
    today_start = timezone.make_aware(datetime.combine(today, time.min))
    today_end = timezone.make_aware(datetime.combine(today, time.max))

    all_agents_filters = {
        "created_at__gte": today_start,
        "created_at__lte": today_end,
    }
    if ctx.organization:
        all_agents_filters["agent__organization"] = ctx.organization
    else:
        all_agents_filters["agent__user"] = ctx.user
        all_agents_filters["agent__organization__isnull"] = True

    all_agents_stats = PersistentAgentStep.objects.filter(**all_agents_filters).aggregate(
        total=Coalesce(Sum("credits_cost"), DECIMAL_ZERO),
    )
    all_agents_credits = float(all_agents_stats.get("total", DECIMAL_ZERO) or DECIMAL_ZERO)

    return {
        "insightId": f"burn_rate_{uuid.uuid4().hex[:8]}",
        "insightType": "burn_rate",
        "priority": 5,
        "title": "Credit usage",
        "body": f"{ctx.agent.name} is using {float(burn_rate or 0):.1f} credits/hour",
        "metadata": {
            "agentName": ctx.agent.name,
            "agentCreditsPerHour": round(float(burn_rate or 0), 2),
            "allAgentsCreditsPerDay": round(all_agents_credits, 2),
            "dailyLimit": float(daily_limit),
            "percentUsed": round(percent_used, 1),
        },
        "dismissible": True,
    }


def generate_insights_for_agent(
    agent: PersistentAgent,
    user: Any,
    organization: Optional[Any] = None,
    *,
    request: HttpRequest,
) -> list[dict]:
    """Generate all relevant insights for an agent session."""
    logger.info("Generating insights for agent %s, user %s", agent.id, user.id)
    if not INSIGHTS_ENABLED:
        logger.info("Insights disabled via feature flag")
        return []
    return [_get_agent_setup_insight(request, agent, organization)]


class AgentInsightsAPIView(LoginRequiredMixin, View):
    """API endpoint to fetch insights for an agent chat session."""

    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        if not INSIGHTS_ENABLED:
            return JsonResponse({"insights": [], "refreshAfterSeconds": 300})

        # Resolve agent with access check
        try:
            agent = resolve_agent(request.user, request.session, agent_id)
        except Exception:
            return JsonResponse({"error": "Agent not found"}, status=404)

        # Determine organization context
        resolved = build_console_context(request)
        organization = None
        if resolved.current_context.type == "organization" and resolved.current_membership:
            organization = resolved.current_membership.org

        # Generate insights
        insights = generate_insights_for_agent(
            agent=agent,
            user=request.user,
            organization=organization,
            request=request,
        )

        return JsonResponse({
            "insights": insights,
            "refreshAfterSeconds": 300,  # Re-fetch after 5 minutes
        })
