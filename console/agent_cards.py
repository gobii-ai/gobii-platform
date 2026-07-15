from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal

from django.db.models import Sum
from django.utils import timezone

from api.agent.short_description import build_listing_description, build_mini_description
from api.models import AgentTransferInvite, PersistentAgent, PersistentAgentStep
from api.services.daily_credit_settings import get_daily_credit_settings_for_owner
from util.urls import IMMERSIVE_APP_BASE_PATH, build_immersive_chat_url, build_staff_developer_chat_path_for_agent


def _first_endpoint_address(endpoints) -> str | None:
    if not endpoints:
        return None
    endpoint = endpoints[0]
    return getattr(endpoint, "address", None) or None


def _coerce_decimal_to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, InvalidOperation, ValueError):
        return None


def enrich_agents_for_card_surface(agents: list[PersistentAgent], owner) -> None:
    if not agents:
        return

    today = timezone.localdate()
    day_start = datetime.combine(today, datetime.min.time())
    if timezone.is_naive(day_start):
        day_start = timezone.make_aware(day_start)
    day_end = day_start + timedelta(days=1)
    next_reset = (
        timezone.localtime(timezone.now()).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        + timedelta(days=1)
    )
    lookback_end = timezone.now()
    lookback_start = lookback_end - timedelta(hours=24)
    agent_ids = [agent.id for agent in agents]
    recent_usage_map: dict[Any, Decimal] = {}
    daily_usage_map: dict[Any, Decimal] = {}
    pending_transfer_ids: set[Any] = set()
    hard_limit_multiplier = Decimal("2")

    if owner is not None:
        credit_settings = get_daily_credit_settings_for_owner(owner)
        try:
            hard_limit_multiplier = Decimal(str(credit_settings.hard_limit_multiplier))
        except (InvalidOperation, TypeError, ValueError):
            hard_limit_multiplier = Decimal("2")

    if agent_ids:
        usage_rows = (
            PersistentAgentStep.objects.filter(
                agent_id__in=agent_ids,
                created_at__gte=lookback_start,
                created_at__lt=lookback_end,
                credits_cost__isnull=False,
            )
            .values("agent_id")
            .annotate(total=Sum("credits_cost"))
        )
        recent_usage_map = {
            row["agent_id"]: row["total"] or Decimal("0")
            for row in usage_rows
        }
        daily_usage_rows = (
            PersistentAgentStep.objects.filter(
                agent_id__in=agent_ids,
                created_at__gte=day_start,
                created_at__lt=day_end,
                credits_cost__isnull=False,
            )
            .values("agent_id")
            .annotate(total=Sum("credits_cost"))
        )
        daily_usage_map = {
            row["agent_id"]: row["total"] or Decimal("0")
            for row in daily_usage_rows
        }
        pending_transfer_ids = set(
            AgentTransferInvite.objects.filter(
                agent_id__in=agent_ids,
                status=AgentTransferInvite.Status.PENDING,
            ).values_list("agent_id", flat=True)
        )

    for agent in agents:
        description, source = build_listing_description(agent, max_length=200)
        agent.listing_description = description
        agent.listing_description_source = source
        agent.is_initializing = source == "placeholder"

        mini_description, mini_source = build_mini_description(agent)
        agent.mini_description = mini_description
        agent.mini_description_source = mini_source
        agent.display_tags = agent.tags if isinstance(agent.tags, list) else []
        agent.pending_transfer_invite = agent.id in pending_transfer_ids

        last_24h_usage = recent_usage_map.get(agent.id, Decimal("0"))

        usage = daily_usage_map.get(agent.id, Decimal("0"))
        try:
            soft_target = agent.get_daily_credit_soft_target()
        except (InvalidOperation, TypeError, ValueError):
            soft_target = None

        hard_limit = (
            (soft_target * hard_limit_multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if soft_target is not None
            else None
        )
        remaining = (
            hard_limit - usage
            if hard_limit is not None
            else None
        )
        if remaining is not None and remaining < Decimal("0"):
            remaining = Decimal("0")

        agent.daily_credit_usage = usage
        agent.daily_credit_last_24h_usage = last_24h_usage
        agent.daily_credit_remaining = remaining
        agent.daily_credit_unlimited = soft_target is None
        agent.daily_credit_next_reset = next_reset
        agent.daily_credit_low = (
            hard_limit is not None
            and remaining is not None
            and remaining < Decimal("1")
        )
        agent.daily_credit_soft_target = soft_target
        agent.daily_credit_hard_limit = hard_limit


def serialize_agent_card_payload(
    request,
    agent: PersistentAgent,
    *,
    avatar_variant: Literal["full", "thumbnail"] = "full",
    is_staff: bool = False,
    is_shared: bool = False,
) -> dict[str, Any]:
    email_endpoints_for_display = (
        getattr(agent, "email_endpoints_for_display", None)
        or getattr(agent, "primary_email_endpoints", None)
    )
    primary_email = _first_endpoint_address(email_endpoints_for_display)
    primary_sms = _first_endpoint_address(getattr(agent, "primary_sms_endpoints", None))
    remaining = _coerce_decimal_to_float(getattr(agent, "daily_credit_remaining", None))
    recent_burn = _coerce_decimal_to_float(getattr(agent, "daily_credit_last_24h_usage", None))
    chat_url = build_immersive_chat_url(request, agent.id, return_to=request.get_full_path())
    detail_url = f"{IMMERSIVE_APP_BASE_PATH}/agents/{agent.id}/settings"
    if is_shared:
        detail_url = chat_url
    avatar_url = agent.get_avatar_thumbnail_url() if avatar_variant == "thumbnail" else agent.get_avatar_url()

    return {
        "id": str(agent.id),
        "name": agent.name or "",
        "signupPreviewState": agent.signup_preview_state,
        "avatarUrl": avatar_url,
        "listingDescription": getattr(agent, "listing_description", "") or "",
        "listingDescriptionSource": getattr(agent, "listing_description_source", None),
        "miniDescription": getattr(agent, "mini_description", "") or "",
        "miniDescriptionSource": getattr(agent, "mini_description_source", None),
        "displayTags": getattr(agent, "display_tags", []) if isinstance(getattr(agent, "display_tags", []), list) else [],
        "isActive": bool(getattr(agent, "is_active", False)),
        "pendingTransfer": bool(getattr(agent, "pending_transfer_invite", None)),
        "primaryEmail": primary_email,
        "primarySms": primary_sms,
        "detailUrl": detail_url,
        "chatUrl": chat_url,
        "dailyCreditRemaining": remaining,
        "dailyCreditLow": bool(getattr(agent, "daily_credit_low", False)),
        "last24hCreditBurn": recent_burn,
        "developerChatUrl": build_staff_developer_chat_path_for_agent(agent) if is_staff else None,
        "isShared": bool(is_shared),
    }
