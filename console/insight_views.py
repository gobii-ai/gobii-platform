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
from django.http import HttpRequest, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views import View

from api.models import (
    CommsChannel,
    PersistentAgent,
)
from util.urls import IMMERSIVE_APP_BASE_PATH
from api.agent.core.prompt_context import get_agent_daily_credit_state
from billing.services import BillingService
from console.agent_chat.access import resolve_agent_for_request
from console.context_helpers import build_console_context
from console.phone_utils import get_pending_phone, get_primary_phone, serialize_phone
from config import settings
from config.stripe_config import get_stripe_settings
from constants.plans import PlanNamesChoices
from djstripe.models import Price
from tasks.services import TaskCreditService
from util.constants.task_constants import TASKS_UNLIMITED
from util.subscription_helper import get_organization_plan, reconcile_user_plan_from_stripe
from util.trial_enforcement import can_user_use_personal_agents_and_api
from api.services.email_verification import has_verified_email

logger = logging.getLogger(__name__)

# Feature flag
INSIGHTS_ENABLED = getattr(settings, "INSIGHTS_ENABLED", True)

DECIMAL_ZERO = Decimal("0")
CURRENCY_SYMBOLS = {
    "usd": "$",
}


def _decimal_to_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _usage_percent(used: Decimal, limit: Decimal | None, *, unlimited: bool) -> float | None:
    if unlimited:
        return None
    if limit is None or limit <= DECIMAL_ZERO:
        return 0.0
    return round(min(float((used / limit) * Decimal("100")), 100.0), 1)


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


def _build_agent_setup_metadata(
    request: HttpRequest,
    agent: PersistentAgent,
    organization: Optional[Any],
) -> dict:
    phone = get_primary_phone(request.user)
    pending_phone = get_pending_phone(request.user)
    # Check agent owner's verification status (not viewer's) since outbound
    # communications are gated by require_verified_email(agent.user)
    email_verified = has_verified_email(agent.user)
    verified_phone = phone if phone and phone.is_verified else None
    phone_payload = serialize_phone(verified_phone)
    agent_sms = agent.comms_endpoints.filter(channel=CommsChannel.SMS).first()
    agent_email = agent.comms_endpoints.filter(channel=CommsChannel.EMAIL, is_primary=True).first()
    preferred_endpoint = agent.preferred_contact_endpoint
    sms_enabled = bool(
        agent_sms
        and phone
        and phone.is_verified
        and preferred_endpoint
        and preferred_endpoint.channel == CommsChannel.SMS
        and preferred_endpoint.address.lower() == phone.phone_number.lower()
    )

    current_org = None
    if agent.organization_id:
        current_org = {
            "id": str(agent.organization_id),
            "name": agent.organization.name,
        }

    owner_plan = reconcile_user_plan_from_stripe(request.user)
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
            always_on_note = "Free plan: 30-day always-on."

    checkout = {}
    if settings.GOBII_PROPRIETARY_MODE:
        checkout = {
            "proUrl": reverse("proprietary:pro_checkout"),
            "scaleUrl": reverse("proprietary:scale_checkout"),
        }

    utm_querystring = request.session.get("utm_querystring") or ""

    return {
        "agentId": str(agent.id),
        "alwaysOn": {
            "title": "You can close this tab",
            "body": "Your agent keeps working 24/7 in the background and will message you when it has updates.",
            "note": always_on_note,
        },
        "agentName": agent.name or "",
        "agentEmail": agent_email.address if agent_email else None,
        "sms": {
            "enabled": sms_enabled,
            "agentNumber": agent_sms.address if agent_sms else None,
            "userPhone": phone_payload,
            "pendingUserPhone": serialize_phone(pending_phone),
            "emailVerified": email_verified,
        },
        "organization": {
            "currentOrg": current_org,
        },
        "upsell": {
            "items": upsell_items,
            "planId": plan_id,
        } if upsell_items else None,
        "checkout": checkout,
        "utmQuerystring": utm_querystring,
    }


def _get_agent_setup_insights(
    request: HttpRequest,
    agent: PersistentAgent,
    organization: Optional[Any],
) -> list[dict]:
    metadata = _build_agent_setup_metadata(request, agent, organization)
    insights: list[dict] = []

    def add_panel(panel: str, priority: int, title: str, body: str) -> None:
        insights.append({
            "insightId": f"agent_setup_{panel}_{agent.id}",
            "insightType": "agent_setup",
            "priority": priority,
            "title": title,
            "body": body,
            "metadata": {
                **metadata,
                "panel": panel,
            },
            "dismissible": False,
        })

    add_panel(
        "always_on",
        100,
        "Always-on",
        "Keep the agent running and stay in the loop.",
    )
    add_panel(
        "sms",
        1,
        "SMS chat",
        "Chat with your agent over SMS.",
    )

    upsell = metadata.get("upsell") or {}
    upsell_items = upsell.get("items") or []
    for item in upsell_items:
        plan = item.get("plan")
        if not plan:
            continue
        add_panel(
            f"upsell_{plan}",
            88 if plan == "pro" else 86,
            f"Upgrade to {item.get('title') or plan.title()}",
            item.get("subtitle") or "Unlock higher limits and faster routing.",
        )

    return insights


def _should_include_agent_setup_insights(user: Any, agent: PersistentAgent) -> bool:
    if agent.organization_id is not None:
        return True
    return can_user_use_personal_agents_and_api(user)


def _get_month_usage_payload(ctx: InsightContext) -> dict:
    owner = ctx.organization or ctx.user
    current_credits = TaskCreditService.get_current_task_credit_for_owner(owner)
    total = Decimal(TaskCreditService.get_tasks_entitled_for_owner(owner))
    used = TaskCreditService.get_owner_task_credits_used(owner, task_credits=current_credits)

    unlimited = total == Decimal(TASKS_UNLIMITED)

    return {
        "used": _decimal_to_float(used) or 0.0,
        "limit": None if unlimited else _decimal_to_float(total),
        "percentUsed": _usage_percent(used, total, unlimited=unlimited),
        "unlimited": unlimited,
    }


def _get_burn_rate_metadata(ctx: InsightContext) -> dict:
    try:
        daily_state = get_agent_daily_credit_state(ctx.agent)
    except Exception as e:
        logger.info("Failed to get daily credit state for agent %s: %s", ctx.agent.id, e)
        # Return a fallback insight with zero values
        daily_state = {
            "used": Decimal("0"),
            "hard_limit": None,
            "soft_target": Decimal("100"),
        }

    if not daily_state:
        daily_state = {
            "used": Decimal("0"),
            "hard_limit": None,
            "soft_target": Decimal("100"),
        }

    used_today = daily_state.get("used", DECIMAL_ZERO)
    hard_limit = daily_state.get("hard_limit")
    soft_target = daily_state.get("soft_target")

    daily_unlimited = hard_limit is None and soft_target is None
    daily_limit = hard_limit or soft_target
    today_usage = {
        "used": _decimal_to_float(used_today) or 0.0,
        "limit": None if daily_unlimited else _decimal_to_float(daily_limit),
        "percentUsed": _usage_percent(used_today, daily_limit, unlimited=daily_unlimited),
        "unlimited": daily_unlimited,
    }
    month_usage = _get_month_usage_payload(ctx)

    return {
        "agentName": ctx.agent.name,
        "todayUsage": today_usage,
        "monthUsage": month_usage,
        "usageUrl": f"{IMMERSIVE_APP_BASE_PATH}/usage",
    }


def build_usage_metadata_for_agent(
    agent: PersistentAgent,
    user: Any | None = None,
    organization: Optional[Any] = None,
) -> dict:
    """Return the live usage metadata shape consumed by the usage insight."""
    resolved_user = user or agent.user
    resolved_organization = organization if organization is not None else agent.organization
    try:
        owner = resolved_organization or resolved_user
        period_start_date, period_end_date = BillingService.get_current_billing_period_for_owner(owner)
        period_start = timezone.make_aware(datetime.combine(period_start_date, time.min))
        period_end = timezone.make_aware(datetime.combine(period_end_date, time.max))
    except Exception:
        logger.exception("Failed to resolve billing period for usage metadata")
        period_end = timezone.now()
        period_start = period_end - timedelta(days=30)

    return _get_burn_rate_metadata(
        InsightContext(
            agent=agent,
            user=resolved_user,
            organization=resolved_organization,
            period_start=period_start,
            period_end=period_end,
        )
    )


def _get_burn_rate_insight(ctx: InsightContext) -> Optional[dict]:
    """Generate usage insight for current agent."""
    metadata = _get_burn_rate_metadata(ctx)

    return {
        "insightId": f"burn_rate_{uuid.uuid4().hex[:8]}",
        "insightType": "burn_rate",
        "priority": 98,
        "title": "Credit usage",
        "body": "Track today's agent usage and this month's account usage.",
        "metadata": metadata,
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

    try:
        owner = organization or user
        period_start_date, period_end_date = BillingService.get_current_billing_period_for_owner(owner)
        period_start = timezone.make_aware(datetime.combine(period_start_date, time.min))
        period_end = timezone.make_aware(datetime.combine(period_end_date, time.max))
    except Exception:
        logger.exception("Failed to resolve billing period for insights")
        period_end = timezone.now()
        period_start = period_end - timedelta(days=30)

    ctx = InsightContext(
        agent=agent,
        user=user,
        organization=organization,
        period_start=period_start,
        period_end=period_end,
    )

    insights: list[dict] = []
    if _should_include_agent_setup_insights(user, agent):
        insights.extend(_get_agent_setup_insights(request, agent, organization))

    burn_rate = _get_burn_rate_insight(ctx)
    if burn_rate:
        insights.append(burn_rate)

    insights.sort(key=lambda item: item.get("priority", 0), reverse=True)
    return insights


class AgentInsightsAPIView(LoginRequiredMixin, View):
    """API endpoint to fetch insights for an agent chat session."""

    http_method_names = ["get"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        if not INSIGHTS_ENABLED:
            return JsonResponse({"insights": [], "refreshAfterSeconds": 300})

        # Resolve agent with access check
        try:
            agent = resolve_agent_for_request(
                request,
                agent_id,
                allow_delinquent_personal_chat=True,
            )
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
