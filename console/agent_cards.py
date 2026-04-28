from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal

from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from api.agent.short_description import build_listing_description, build_mini_description
from api.models import AgentTransferInvite, PersistentAgent, PersistentAgentStep
from api.services.daily_credit_settings import get_daily_credit_settings_for_owner
from util.urls import build_immersive_chat_url


def _clamp_color(value: int) -> int:
    return max(0, min(255, value))


def _hex_to_rgb_components(hex_color: str) -> tuple[int, int, int]:
    normalized = (hex_color or "").strip().lstrip("#")
    if len(normalized) != 6:
        return (0, 116, 212)
    return tuple(int(normalized[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{_clamp_color(r):02X}{_clamp_color(g):02X}{_clamp_color(b):02X}"


def adjust_hex(hex_color: str, ratio: float) -> str:
    r, g, b = _hex_to_rgb_components(hex_color)
    if ratio >= 0:
        r = _clamp_color(int(r + (255 - r) * ratio))
        g = _clamp_color(int(g + (255 - g) * ratio))
        b = _clamp_color(int(b + (255 - b) * ratio))
    else:
        ratio = abs(ratio)
        r = _clamp_color(int(r * (1 - ratio)))
        g = _clamp_color(int(g * (1 - ratio)))
        b = _clamp_color(int(b * (1 - ratio)))
    return _rgb_to_hex(r, g, b)


def build_agent_gradient(hex_color: str) -> str:
    base = (hex_color or "#0074D4").upper()
    lighter = adjust_hex(base, 0.35)
    darker = adjust_hex(base, -0.25)
    return f"background-image: linear-gradient(135deg, {lighter} 0%, {base} 55%, {darker} 100%); background-color: {base};"


def _relative_luminance(hex_color: str) -> float:
    r, g, b = _hex_to_rgb_components(hex_color)

    def _normalize(channel: int) -> float:
        c = channel / 255.0
        if c <= 0.03928:
            return c / 12.92
        return ((c + 0.055) / 1.055) ** 2.4

    r_lin = _normalize(r)
    g_lin = _normalize(g)
    b_lin = _normalize(b)
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def text_palette_for_hex(hex_color: str) -> dict[str, str]:
    luminance = _relative_luminance(hex_color)
    use_light = luminance <= 0.55
    if use_light:
        return {
            "primary": "text-white",
            "secondary": "text-white/70",
            "status": "text-white/80",
            "badge": "bg-white/20 text-white border border-white/40",
            "icon": "text-white",
            "link_hover": "hover:text-white",
        }
    return {
        "primary": "text-slate-900",
        "secondary": "text-slate-700",
        "status": "text-slate-800",
        "badge": "bg-black/5 text-slate-800 border border-black/10",
        "icon": "text-slate-900",
        "link_hover": "hover:text-slate-900",
    }


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
        color_hex = agent.get_display_color().upper()
        agent.display_color_hex = color_hex
        agent.card_gradient_style = build_agent_gradient(color_hex)
        agent.icon_background_hex = adjust_hex(color_hex, 0.55)
        agent.icon_border_hex = adjust_hex(color_hex, -0.25)
        palette = text_palette_for_hex(color_hex)
        agent.header_text_class = palette["primary"]
        agent.header_subtext_class = palette["secondary"]
        agent.header_status_class = palette["status"]
        agent.header_badge_class = palette["badge"]
        agent.header_icon_class = palette["icon"]
        agent.header_link_hover_class = palette["link_hover"]

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
    detail_url = reverse("agent_detail", kwargs={"pk": agent.id})
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
        "cardGradientStyle": getattr(agent, "card_gradient_style", "") or "",
        "iconBackgroundHex": getattr(agent, "icon_background_hex", "") or "",
        "iconBorderHex": getattr(agent, "icon_border_hex", "") or "",
        "displayColorHex": getattr(agent, "display_color_hex", None) or agent.get_display_color(),
        "headerTextClass": getattr(agent, "header_text_class", "") or "",
        "headerSubtextClass": getattr(agent, "header_subtext_class", "") or "",
        "headerStatusClass": getattr(agent, "header_status_class", "") or "",
        "headerBadgeClass": getattr(agent, "header_badge_class", "") or "",
        "headerIconClass": getattr(agent, "header_icon_class", "") or "",
        "headerLinkHoverClass": getattr(agent, "header_link_hover_class", "") or "",
        "dailyCreditRemaining": remaining,
        "dailyCreditLow": bool(getattr(agent, "daily_credit_low", False)),
        "last24hCreditBurn": recent_burn,
        "auditUrl": reverse("console-agent-audit", kwargs={"agent_id": agent.id}) if is_staff else None,
        "isShared": bool(is_shared),
    }
