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

from api.models import (
    BrowserUseAgentTask,
    PersistentAgent,
    PersistentAgentStep,
)
from api.agent.core.prompt_context import get_agent_daily_credit_state
from billing.services import BillingService
from console.agent_chat.access import resolve_agent
from console.context_helpers import build_console_context
from config import settings

logger = logging.getLogger(__name__)

# Feature flag
INSIGHTS_ENABLED = getattr(settings, "INSIGHTS_ENABLED", True)

# Time saved estimation constants (in minutes)
TIME_SAVED_PER_SIMPLE_TASK = 5  # Web search, simple email
TIME_SAVED_PER_MEDIUM_TASK = 15  # Multi-step research
TIME_SAVED_PER_COMPLEX_TASK = 30  # Browser automation, analysis

DECIMAL_ZERO = Decimal("0")


@dataclass
class InsightContext:
    """Context for generating insights."""
    agent: PersistentAgent
    user: Any
    organization: Optional[Any]
    period_start: datetime
    period_end: datetime


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
) -> list[dict]:
    """Generate all relevant insights for an agent session."""
    logger.info("Generating insights for agent %s, user %s", agent.id, user.id)
    if not INSIGHTS_ENABLED:
        logger.info("Insights disabled via feature flag")
        return []

    # Determine billing period for time-based insights
    owner = organization or user
    try:
        period_start, period_end = BillingService.get_current_billing_period_for_owner(owner)
    except Exception:
        # Fallback to last 7 days
        period_end = timezone.now().date()
        period_start = period_end - timedelta(days=6)

    tz = timezone.get_current_timezone()
    period_start_dt = timezone.make_aware(datetime.combine(period_start, time.min), tz)
    period_end_dt = timezone.make_aware(datetime.combine(period_end, time.max), tz)

    ctx = InsightContext(
        agent=agent,
        user=user,
        organization=organization,
        period_start=period_start_dt,
        period_end=period_end_dt,
    )

    insights: list[dict] = []

    # Generate each insight type
    generators = [
        _get_time_saved_insight,
        _get_burn_rate_insight,
    ]

    for generator in generators:
        try:
            insight = generator(ctx)
            logger.info("Generator %s returned: %s", generator.__name__, "insight" if insight else "None")
            if insight:
                insights.append(insight)
        except Exception:
            logger.exception("Failed to generate insight from %s", generator.__name__)

    # Sort by priority (higher first)
    insights.sort(key=lambda x: x.get("priority", 0), reverse=True)

    return insights


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
        )

        return JsonResponse({
            "insights": insights,
            "refreshAfterSeconds": 300,  # Re-fetch after 5 minutes
        })
