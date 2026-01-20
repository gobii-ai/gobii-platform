import json
import mimetypes
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Any

import stripe
from django.template.loader import render_to_string
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.core.mail import send_mail
from django.utils.html import strip_tags
from django.utils.html import format_html
from django.views.generic import TemplateView, ListView, View, DetailView
from django.views.generic.edit import FormMixin
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, get_object_or_404, render
from django.urls import NoReverseMatch, reverse, reverse_lazy
from django.contrib import messages
from django.db import transaction, models, IntegrityError
from django.db.models import Q, Sum
from django.http import (
    FileResponse,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    HttpResponse,
    JsonResponse,
    Http404,
    HttpRequest,
)
from django.core import signing
from django.core.exceptions import ValidationError, PermissionDenied, ImproperlyConfigured
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.text import slugify
from django.middleware.csrf import get_token
from datetime import timedelta, datetime, timezone as dt_timezone
from functools import cached_property, wraps
import uuid

from agents.services import AgentService, PretrainedWorkerTemplateService
from config.socialaccount_adapter import OAUTH_CHARTER_COOKIE
from billing.services import BillingService
from api.services.agent_transfer import AgentTransferService, AgentTransferError, AgentTransferDenied
from api.services.dedicated_proxy_service import (
    DedicatedProxyService,
    DedicatedProxyUnavailableError,
    is_multi_assign_enabled,
)
from api.agent.core.llm_config import AgentLLMTier, TIER_ORDER, get_llm_tier_multipliers, max_allowed_tier_for_plan
from api.agent.short_description import build_listing_description, build_mini_description, \
    maybe_schedule_short_description
from api.agent.tags import maybe_schedule_agent_tags
from api.services.daily_credit_settings import get_daily_credit_settings_for_owner
from console.daily_credit import (
    build_agent_daily_credit_context,
    get_daily_credit_slider_bounds,
    serialize_daily_credit_payload,
)

from api.models import (
    ApiKey,
    UserBilling,
    BrowserUseAgent,
    BrowserUseAgentTask,
    ProxyServer,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentEmailEndpoint,
    PersistentAgentWebhook,
    PersistentAgentMessage,
    IntelligenceTier,
    AgentEmailAccount,
    AgentPeerLink,
    AgentCommPeerState,
    PersistentAgentConversation,
    PersistentAgentConversationParticipant,
    PersistentAgentSmsEndpoint,
    PersistentAgentStep,
    CommsChannel,
    UserPhoneNumber,
    Organization,
    AgentColor,
    OrganizationMembership,
    OrganizationInvite,
    TaskCredit,
    AgentCollaborator,
    AgentCollaboratorInvite,
    get_agent_contact_counts,
)
from console.mixins import ConsoleViewMixin, StripeFeatureRequiredMixin, SystemAdminRequiredMixin
from observability import traced
from pages.mixins import PhoneNumberMixin

from .context_helpers import build_console_context
from .org_billing_helpers import build_org_billing_overview
from tasks.services import TaskCreditService
from billing.addons import AddonEntitlementService
from util import sms
from util.payments_helper import PaymentsHelper
from util.integrations import stripe_status
from util.sms import find_unused_number, get_user_primary_sms_number
from util.subscription_helper import (
    get_user_plan,
    get_active_subscription,
    allow_user_extra_tasks,
    calculate_extra_tasks_used_during_subscription_period,
    get_user_extra_task_limit,
    get_or_create_stripe_customer,
    get_organization_plan,
    has_unlimited_agents,
    is_community_unlimited_mode,
    get_user_max_contacts_per_agent,
    get_subscription_base_price,
)
from util.urls import IMMERSIVE_RETURN_TO_SESSION_KEY, build_immersive_chat_url
from console.agent_chat.access import resolve_agent_for_request, user_can_manage_agent, user_is_collaborator
from config import settings
from config.stripe_config import get_stripe_settings
from config.plans import PLAN_CONFIG, AGENTS_UNLIMITED
from waffle import flag_is_active
from api.services.email_verification import has_verified_email


def _clamp_color(value: int) -> int:
    return max(0, min(255, value))


def _hex_to_rgb_components(hex_color: str) -> tuple[int, int, int]:
    normalized = (hex_color or "").strip().lstrip("#")
    if len(normalized) != 6:
        return (0, 116, 212)
    return tuple(int(normalized[i:i + 2], 16) for i in (0, 2, 4))


def _normalize_agent_color_hex(hex_color: str) -> str | None:
    normalized = (hex_color or "").strip().lstrip("#")
    if len(normalized) == 8:
        normalized = normalized[:6]
    if len(normalized) != 6:
        return None
    try:
        int(normalized, 16)
    except ValueError:
        return None
    return f"#{normalized.upper()}"


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{_clamp_color(r):02X}{_clamp_color(g):02X}{_clamp_color(b):02X}"


def _adjust_hex(hex_color: str, ratio: float) -> str:
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


def _format_validation_error(error: ValidationError) -> str:
    if hasattr(error, "message_dict") and error.message_dict:
        messages = []
        for field_errors in error.message_dict.values():
            messages.extend(field_errors)
        if messages:
            return " ".join(messages)
    if hasattr(error, "messages") and error.messages:
        return " ".join(error.messages)
    return str(error)


def _build_agent_gradient(hex_color: str) -> str:
    base = (hex_color or "#0074D4").upper()
    lighter = _adjust_hex(base, 0.35)
    darker = _adjust_hex(base, -0.25)
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


def _text_palette_for_hex(hex_color: str) -> dict[str, str]:
    luminance = _relative_luminance(hex_color)
    # Threshold chosen to meet WCAG contrast guidance for normal text (~4.5:1).
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


def _safe_getattr(source, attr: str, default=None):
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(attr, default)
    return getattr(source, attr, default)


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


def build_llm_intelligence_props(owner, owner_type: str, organization, upgrade_url: str | None) -> dict[str, Any]:
    plan = None
    if owner is not None:
        try:
            if owner_type == 'organization':
                plan = get_organization_plan(organization)
            else:
                plan = get_user_plan(owner)
        except Exception:
            plan = None

    allowed_tier = max_allowed_tier_for_plan(plan, is_organization=(owner_type == 'organization'))
    can_edit = bool(
        settings.GOBII_PROPRIETARY_MODE
        and owner is not None
        and (owner_type == 'organization' or allowed_tier != AgentLLMTier.STANDARD)
    )
    disabled_reason = None
    if not can_edit:
        if settings.GOBII_PROPRIETARY_MODE:
            disabled_reason = "Upgrade to a paid plan to adjust intelligence levels."
        else:
            disabled_reason = "Intelligence levels are managed by deployment settings."

    tier_descriptions = {
        AgentLLMTier.STANDARD.value: "Balanced routing that uses 1× credits.",
        AgentLLMTier.PREMIUM.value: "Premium routing with improved reasoning.",
        AgentLLMTier.MAX.value: "Top-tier models with the best reasoning.",
        AgentLLMTier.ULTRA.value: "Ultra-tier models for advanced reasoning.",
        AgentLLMTier.ULTRA_MAX.value: "Highest tier for maximum reasoning depth.",
    }
    tiers = list(IntelligenceTier.objects.order_by("credit_multiplier", "rank"))
    if tiers:
        options = [
            {
                "key": tier.key,
                "label": tier.display_name,
                "description": tier_descriptions.get(tier.key, ""),
                "multiplier": float(tier.credit_multiplier),
            }
            for tier in tiers
        ]
    else:
        multipliers = get_llm_tier_multipliers()
        options = [
            {
                "key": tier.value,
                "label": tier.value.replace("_", " ").title(),
                "description": tier_descriptions.get(tier.value, ""),
                "multiplier": float(multipliers.get(tier.value, 1)),
            }
            for tier in sorted(TIER_ORDER.keys(), key=lambda entry: TIER_ORDER[entry])
        ]

    return {
        "options": options,
        "canEdit": can_edit,
        "disabledReason": disabled_reason,
        "upgradeUrl": upgrade_url,
    }


def _resolve_dedicated_ip_pricing(plan):
    plan = plan or {}
    currency = plan.get("currency")
    unit_price = plan.get("dedicated_ip_price")
    plan_id = plan.get("id")

    if (unit_price is None) and plan_id:
        fallback = PLAN_CONFIG.get(str(plan_id).lower())
        if fallback:
            if unit_price is None:
                unit_price = fallback.get("dedicated_ip_price")
            if not currency:
                currency = fallback.get("currency", currency)

    if unit_price is None:
        unit_price = 0

    try:
        price_decimal = Decimal(str(unit_price))
    except Exception:
        price_decimal = Decimal("0")

    normalized_currency = (currency or "USD").upper()
    return price_decimal, normalized_currency


from .forms import (
    ApiKeyForm,
    PersistentAgentForm,
    PersistentAgentContactForm,
    MCPServerConfigForm,
    UserProfileForm,
    UserPhoneNumberForm,
    PhoneVerifyForm,
    PhoneAddForm,
    OrganizationForm,
    OrganizationInviteForm,
    OrganizationSeatPurchaseForm,
    OrganizationSeatReductionForm,
    DedicatedIpAddForm,
    AddonQuantityForm,
)
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_http_methods
from util.analytics import Analytics, AnalyticsCTAs, AnalyticsEvent, AnalyticsSource
from django.core.paginator import Paginator
from waffle.mixins import WaffleFlagMixin
from constants.feature_flags import ORGANIZATIONS
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNames, PlanNamesChoices
from constants.stripe import (
    ORG_OVERAGE_STATE_META_KEY,
    ORG_OVERAGE_STATE_DETACHED_PENDING,
)
from opentelemetry import trace, baggage, context
from api.agent.tools.mcp_manager import get_mcp_manager
from api.agent.tasks import process_agent_events_task
from api.services import mcp_servers as mcp_server_service
from console.agent_creation import create_persistent_agent_from_charter, enable_agent_sms_contact
from console.agent_reassignment import reassign_agent_organization
from console.forms import PersistentAgentEditSecretForm, PersistentAgentSecretsRequestForm, PersistentAgentAddSecretForm
import logging
from api.agent.comms.message_service import _get_or_create_conversation, _ensure_participant
from api.models import CommsAllowlistEntry, AgentAllowlistInvite, AgentTransferInvite, OrganizationMembership, MCPServerConfig
from console.forms import AllowlistEntryForm
from console.forms import AgentEmailAccountConsoleForm
from django.apps import apps

User = get_user_model()
logger = logging.getLogger(__name__)

tracer = trace.get_tracer("gobii.utils")


def _assign_stripe_api_key() -> str:
    """Ensure Stripe secret key is configured before making API calls."""
    key = PaymentsHelper.get_stripe_key()
    if not key:
        raise ImproperlyConfigured("Stripe secret key missing while billing is enabled.")
    stripe.api_key = key
    return key

# Whether to skip the phone number setup screen when the user already has a
# verified phone number on their account. Toggle this to force showing the
# phone screen even when a verified number exists.
SKIP_VERIFIED_SMS_SCREEN = True

BILLING_MANAGE_ROLES = {
    OrganizationMembership.OrgRole.OWNER,
    OrganizationMembership.OrgRole.ADMIN,
    OrganizationMembership.OrgRole.BILLING,
}

MEMBER_MANAGE_ROLES = {
    OrganizationMembership.OrgRole.OWNER,
    OrganizationMembership.OrgRole.ADMIN,
}

API_KEY_MANAGE_ROLES = {
    OrganizationMembership.OrgRole.OWNER,
    OrganizationMembership.OrgRole.ADMIN,
}

API_KEY_VIEW_ROLES = API_KEY_MANAGE_ROLES | {
    OrganizationMembership.OrgRole.BILLING,
}


class ApiKeyOwnerMixin:
    """Utilities for resolving API key ownership based on console context."""

    @cached_property
    def api_key_context(self):
        resolved = build_console_context(self.request)
        if resolved.current_context.type == "organization":
            membership = resolved.current_membership
            if membership is None:
                raise PermissionDenied("Organization context is no longer available.")

            can_view = membership.role in API_KEY_VIEW_ROLES
            if not can_view:
                raise PermissionDenied("You do not have access to organization API keys.")

            return {
                "type": "organization",
                "organization": membership.org,
                "membership": membership,
                "can_manage": membership.role in API_KEY_MANAGE_ROLES,
            }

        return {
            "type": "user",
            "user": self.request.user,
            "can_manage": True,
        }

    def _ensure_can_manage_api_keys(self):
        ctx = self.api_key_context
        if not ctx.get("can_manage"):
            raise PermissionDenied("You do not have permission to manage API keys for this organization.")
        return ctx


def _resolve_org_from_request(request):
    """Return the Organization for the active console context, if any."""
    try:
        resolved = build_console_context(request)
    except Exception:  # pragma: no cover - defensive guard
        return None

    membership = getattr(resolved, "current_membership", None)
    if membership is not None and getattr(membership, "org", None) is not None:
        return membership.org
    return None


def _org_event_properties(request, properties: dict | None = None, *, organization=None) -> dict:
    """Attach organization metadata to analytics properties for console events."""
    org = organization or _resolve_org_from_request(request)
    return Analytics.with_org_properties(properties, organization=org)


def _track_org_event_for_console(
    request,
    event: AnalyticsEvent,
    extra_props: dict | None = None,
    *,
    organization=None,
) -> dict:
    """Track an analytics event with organization context for console actions."""
    props = _org_event_properties(request, extra_props or {}, organization=organization)

    transaction.on_commit(lambda: Analytics.track_event(
        user_id=request.user.id,
        event=event,
        source=AnalyticsSource.WEB,
        properties=props.copy(),
    ))

    return props


def _mcp_server_event_properties(
    request: HttpRequest,
    server: MCPServerConfig,
    owner_scope: str | None = None,
) -> dict[str, object]:
    return {
        "actor_id": str(request.user.id),
        "server_id": str(server.id),
        "server_name": server.name,
        "server_scope": server.scope,
        "owner_scope": owner_scope or server.scope,
        "has_command": bool(server.command),
        "has_url": bool(server.url),
        "is_active": server.is_active,
    }


def _set_overage_detach_session(request, org_id: str, subscription_id: str, price_id: str) -> None:
    """Record that the org's overage SKU was temporarily detached for seat updates."""
    if not subscription_id or not price_id:
        return

    key = str(org_id)
    detach_map = dict(request.session.get("org_overage_detach", {}))
    detach_map[key] = {
        "subscription_id": subscription_id,
        "price_id": price_id,
    }
    request.session["org_overage_detach"] = detach_map
    request.session.modified = True


def _pop_overage_detach_session(request, org_id: str) -> dict | None:
    """Remove and return any stored detach info for the org."""
    key = str(org_id)
    detach_map = dict(request.session.get("org_overage_detach", {}))
    info = detach_map.pop(key, None)
    if detach_map:
        request.session["org_overage_detach"] = detach_map
    else:
        request.session.pop("org_overage_detach", None)
    if info is not None:
        request.session.modified = True
    return info


def _detach_org_overage_item(subscription: dict, overage_price_id: str | None, org_id: str, request) -> bool:
    """Remove the org overage SKU from the subscription and mark the detach state."""
    if not overage_price_id:
        return False

    items = (subscription.get("items") or {}).get("data", []) or []
    overage_item = None
    for item in items:
        price = item.get("price") or {}
        if price.get("id") == overage_price_id:
            overage_item = item
            break

    if not overage_item:
        return False

    try:
        stripe.SubscriptionItem.delete(overage_item.get("id"))
    except Exception as exc:  # pragma: no cover - network failure path
        logger.warning(
            "Failed to detach org overage subscription item %s for org %s: %s",
            overage_item.get("id"),
            org_id,
            exc,
        )
        return False

    metadata = {**(subscription.get("metadata") or {})}
    metadata[ORG_OVERAGE_STATE_META_KEY] = ORG_OVERAGE_STATE_DETACHED_PENDING
    try:
        stripe.Subscription.modify(subscription.get("id"), metadata=metadata)
    except Exception as exc:  # pragma: no cover - network failure path
        logger.warning(
            "Failed to mark overage detach state on subscription %s for org %s: %s",
            subscription.get("id"),
            org_id,
            exc,
        )

    _set_overage_detach_session(request, org_id, subscription.get("id"), overage_price_id)
    return True


def _reattach_org_overage_subscription(subscription_id: str | None, price_id: str | None) -> bool:
    """Reattach the org overage SKU to the subscription if missing and clear the detach flag."""
    if not subscription_id or not price_id:
        return False

    try:
        subscription = stripe.Subscription.retrieve(
            subscription_id,
            expand=["items.data.price"],
        )
    except Exception as exc:  # pragma: no cover - network failure path
        logger.warning(
            "Failed to retrieve subscription %s while reattaching overage SKU: %s",
            subscription_id,
            exc,
        )
        return False

    items = (subscription.get("items") or {}).get("data", []) or []
    has_overage = any((item.get("price") or {}).get("id") == price_id for item in items)

    if not has_overage:
        try:
            stripe.SubscriptionItem.create(subscription=subscription_id, price=price_id)
            has_overage = True
        except Exception as exc:  # pragma: no cover - network failure path
            logger.warning(
                "Failed to reattach overage SKU %s to subscription %s: %s",
                price_id,
                subscription_id,
                exc,
            )
            has_overage = False

    try:
        stripe.Subscription.modify(subscription_id, metadata={ORG_OVERAGE_STATE_META_KEY: ""})
    except Exception as exc:  # pragma: no cover - network failure path
        logger.warning(
            "Failed to clear overage detach flag on subscription %s: %s",
            subscription_id,
            exc,
        )

    return has_overage


def _reattach_overage_from_session(request, org_id: str) -> bool:
    """If the org had its overage SKU detached, reattach it and clear session state."""
    info = _pop_overage_detach_session(request, org_id)
    if not info:
        return False

    subscription_id = info.get("subscription_id")
    price_id = info.get("price_id")
    return _reattach_org_overage_subscription(subscription_id, price_id)


def _apply_subscribe_success_context(request, context: dict, plan_id: str | None = None) -> None:
    """Populate context for subscription success notifications based on query params."""
    if request.GET.get("subscribe_success") == "1":
        context["subscribe_notification"] = True
        price_str = request.GET.get("p", "0.0")
        try:
            context["sub_price"] = float(price_str)
        except ValueError:
            context["sub_price"] = 0.0

        event_id = (request.GET.get("eid") or "").strip()
        if event_id and len(event_id) <= 64:
            context["subscribe_event_id"] = event_id
        else:
            context["subscribe_event_id"] = ""

        # Prefer plan from URL params (set at checkout time) over current DB state
        # to avoid race conditions with webhook processing
        url_plan = (request.GET.get("plan") or "").strip()
        if url_plan:
            resolved_plan = url_plan
        elif plan_id:
            resolved_plan = plan_id
        else:
            plan_config = context.get("subscription_plan") or {}
            resolved_plan = plan_config.get("id") if isinstance(plan_config, dict) else None

        context["subscribe_plan"] = resolved_plan if isinstance(resolved_plan, str) else ""
        return

    context["subscribe_notification"] = False
    context["subscribe_event_id"] = ""
    context["subscribe_plan"] = ""


class ConsoleHome(ConsoleViewMixin, TemplateView):
    """Dashboard homepage for the console."""
    template_name = "index.html"

    @tracer.start_as_current_span("CONSOLE Home")
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        current_ctx = context.get('current_context', {}) or {}

        if current_ctx.get('type') != 'organization':
            # Get the oldest non-revoked API key that has a raw key value
            default_key = ApiKey.objects.filter(
                user=self.request.user,
                revoked_at__isnull=True,
                raw_key__isnull=False
            ).exclude(
                raw_key=""
            ).order_by('created_at').first()

            if default_key and default_key.raw_key:
                context['default_api_key'] = default_key.raw_key
                context['has_api_key'] = True
            else:
                context['has_api_key'] = False
        else:
            context['has_api_key'] = False

        pending_transfers_qs = AgentTransferInvite.objects.filter(
            status=AgentTransferInvite.Status.PENDING,
        ).filter(
            Q(to_user=self.request.user) | Q(to_user__isnull=True, to_email__iexact=self.request.user.email)
        ).select_related('agent', 'agent__user')

        pending_transfers: list[AgentTransferInvite] = list(pending_transfers_qs)
        if pending_transfers:
            unsassigned_ids = [invite.id for invite in pending_transfers if invite.to_user_id is None]
            if unsassigned_ids:
                AgentTransferInvite.objects.filter(id__in=unsassigned_ids).update(to_user=self.request.user)
                for invite in pending_transfers:
                    if invite.id in unsassigned_ids:
                        invite.to_user = self.request.user
            context['pending_agent_transfer_invites'] = pending_transfers

        # Add agent statistics (personal vs organization)
        from api.models import BrowserUseAgentTask, Organization

        ctx_type = current_ctx.get('type', 'personal')

        if ctx_type == 'organization' and current_ctx.get('id'):
            org_id = current_ctx.get('id')
            membership = context.get('current_membership')
            organization = None
            if membership and str(membership.org_id) == org_id:
                organization = getattr(membership, "org", None)
            if organization is None:
                organization = Organization.objects.filter(pk=org_id).first()
            # Verify active membership; if missing, fall back to personal context values
            if (
                organization is not None
                and OrganizationMembership.objects.filter(
                    user=self.request.user,
                    org_id=organization.id,
                    status=OrganizationMembership.OrgStatus.ACTIVE,
                ).exists()
            ):
                # Agents (org-owned persistent agents)
                context['agent_count'] = AgentService.get_agents_in_use(organization)

                # Task status for org-owned agents
                from django.db.models import Count, Sum
                pa_browser_ids = (
                    PersistentAgent.objects.non_eval().filter(organization_id=organization.id)
                    .values_list('browser_use_agent_id', flat=True)
                )
                task_stats = (
                    BrowserUseAgentTask.objects.filter(
                        agent_id__in=pa_browser_ids,
                        is_deleted=False,
                    )
                    .values('status')
                    .annotate(count=Count('status'))
                )

                # Initialize counters
                completed_count = in_progress_count = pending_count = failed_count = cancelled_count = 0
                for stat in task_stats:
                    status = stat['status']
                    count = stat['count']
                    if status == 'completed':
                        completed_count = count
                    elif status == 'in_progress':
                        in_progress_count = count
                    elif status == 'pending':
                        pending_count = count
                    elif status == 'failed':
                        failed_count = count
                    elif status == 'cancelled':
                        cancelled_count = count

                context['completed_tasks'] = completed_count
                context['in_progress_tasks'] = in_progress_count
                context['pending_tasks'] = pending_count
                context['failed_tasks'] = failed_count
                context['cancelled_tasks'] = cancelled_count
                context['total_active_tasks'] = in_progress_count + pending_count

                # Credits available for organization
                from django.apps import apps
                TaskCredit = apps.get_model('api', 'TaskCredit')
                now = timezone.now()
                qs = TaskCredit.objects.filter(
                    organization_id=organization.id,
                    granted_date__lte=now,
                    expiration_date__gte=now,
                    voided=False,
                )
                agg = qs.aggregate(
                    avail=Sum('available_credits'),
                    total=Sum('credits'),
                    used=Sum('credits_used'),
                )

                def _to_decimal(value):
                    if value is None:
                        return Decimal("0")
                    return value if isinstance(value, Decimal) else Decimal(value)

                org_tasks_available = agg['avail'] if agg['avail'] is not None else Decimal("0")
                total = _to_decimal(agg['total'])
                used = _to_decimal(agg['used'])

                if total == 0:
                    tasks_used_pct = Decimal("0")
                else:
                    usage_pct = (used / total) * Decimal("100")
                    tasks_used_pct = min(usage_pct, Decimal("100"))

                tasks_used_pct = float(tasks_used_pct)

                # Expose org metrics for dashboard rendering
                context['org_tasks_available'] = org_tasks_available
                context['org_tasks_used_pct'] = tasks_used_pct
            else:
                # Fallback to personal if no membership
                context['agent_count'] = AgentService.get_agents_in_use(self.request.user)
        else:
            # Personal context defaults
            context['agent_count'] = AgentService.get_agents_in_use(self.request.user)

        # Get the user's subscription plan (defaults to 'free' if not set)
        context['subscription_plan'] = get_user_plan(self.request.user)

        # Get number of available tasks
        context['available_tasks'] = TaskCreditService.calculate_available_tasks(self.request.user)

        context['addl_tasks_enabled'] = allow_user_extra_tasks(self.request.user)
        context['addl_tasks_used'] = calculate_extra_tasks_used_during_subscription_period(self.request.user)
        context['addl_tasks_max'] = get_user_extra_task_limit(self.request.user)
        context['addl_tasks_unlimited'] = context['addl_tasks_max'] == -1  # -1 indicates unlimited tasks
        context['addl_tasks_remaining'] = context['addl_tasks_max'] - context['addl_tasks_used']

        # If enabled but not unlimited calculate percent. else 0
        if context['addl_tasks_enabled'] and not context['addl_tasks_unlimited']:
            context['addl_tasks_percent'] = min(max((context['addl_tasks_used'] / context['addl_tasks_max'] * 100), 0), 100)
        else:
            context['addl_tasks_percent'] = 0

        _apply_subscribe_success_context(self.request, context)


        # Get the user's active subscription
        sub = get_active_subscription(self.request.user)
        context['subscription'] = sub
        context['paid_subscriber'] = sub is not None

        if sub:
            start = sub.stripe_data['current_period_start']
            end = sub.stripe_data['current_period_end']

            dt_start = datetime.fromtimestamp(int(start), tz=dt_timezone.utc)
            dt_end = datetime.fromtimestamp(int(end), tz=dt_timezone.utc)

            context['period_start_date'] = dt_start.strftime("%B %d, %Y")
            context['period_end_date'] = dt_end.strftime("%B %d, %Y")

        # Get task status breakdown
        from django.db.models import Count

        # If not in org context above, compute personal task stats
        if not (ctx_type == 'organization' and current_ctx.get('id')):
            with traced("CONSOLE Task Stats") as task_span:
                from django.db.models import Count
                task_stats = BrowserUseAgentTask.objects.filter(
                    user=self.request.user,
                    is_deleted=False
                ).values('status').annotate(count=Count('status'))

                # Initialize counters
                completed_count = in_progress_count = pending_count = failed_count = cancelled_count = 0

                # Populate counters from query results
                for stat in task_stats:
                    status = stat['status']
                    count = stat['count']
                    if status == 'completed':
                        completed_count = count
                    elif status == 'in_progress':
                        in_progress_count = count
                    elif status == 'pending':
                        pending_count = count
                    elif status == 'failed':
                        failed_count = count
                    elif status == 'cancelled':
                        cancelled_count = count

                # Add task statistics to context
                context['completed_tasks'] = completed_count
                context['in_progress_tasks'] = in_progress_count
                context['pending_tasks'] = pending_count
                context['failed_tasks'] = failed_count
                context['cancelled_tasks'] = cancelled_count
                context['total_active_tasks'] = in_progress_count + pending_count

        return context

class ExampleConsolePage(LoginRequiredMixin, TemplateView):
    """Example console page."""
    template_name = "example_console_page.html"

class ApiKeyListView(ApiKeyOwnerMixin, ConsoleViewMixin, FormMixin, ListView):
    """List all API keys for the current user and handle creation."""
    model = ApiKey
    template_name = "api_keys.html"
    context_object_name = 'api_keys'
    form_class = ApiKeyForm
    success_url = reverse_lazy('api_keys')

    @tracer.start_as_current_span("CONSOLE API Key List - get_queryset")
    def get_queryset(self):
        ctx = self.api_key_context
        if ctx["type"] == "organization":
            return (
                ApiKey.objects.select_related("created_by")
                .filter(organization=ctx["organization"])
                .order_by('-created_at')
            )

        return (
            ApiKey.objects.select_related("created_by")
            .filter(user=self.request.user)
            .order_by('-created_at')
        )

    @tracer.start_as_current_span("CONSOLE API Key List - get_context_data")
    def get_context_data(self, **kwargs):
        """Add form to context."""
        context = super().get_context_data(**kwargs)
        context['form'] = self.get_form() # Add form instance from FormMixin
        context['api_key_context'] = self.api_key_context
        context['can_manage_api_keys'] = self.api_key_context.get("can_manage", False)
        context['email_verified'] = has_verified_email(self.request.user)
        return context

    @tracer.start_as_current_span("CONSOLE API Key List - get_form_kwargs")
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        ctx = self.api_key_context
        if ctx["type"] == "organization":
            kwargs["organization"] = ctx["organization"]
        else:
            kwargs["user"] = self.request.user
        return kwargs

    @tracer.start_as_current_span("CONSOLE API Key List - Create API Key")
    def post(self, request, *args, **kwargs):
        """Handle POST requests for creating a new API key."""
        # Check if user is authenticated (redundant due to LoginRequiredMixin, but good practice)
        if not request.user.is_authenticated:
            return HttpResponseForbidden()

        self._ensure_can_manage_api_keys()

        form = self.get_form()
        ctx = self.api_key_context
        if ctx["type"] == "organization":
            form.organization = ctx["organization"]
            form.user = None
        else:
            form.user = self.request.user
            form.organization = None
        if form.is_valid():
            try:
                return self.form_valid(form)
            except ValidationError as e:
                # Extract the actual error message from the ValidationError
                # ValidationError can be a dict, list, or string
                if hasattr(e, 'message_dict'):
                    # Get the first error from the '__all__' key if it exists
                    error_message = e.message_dict.get('__all__', ['An error occurred'])[0]
                elif hasattr(e, 'messages'):
                    error_message = e.messages[0]
                else:
                    error_message = str(e)
                
                # Add the clean error message to the form
                form.add_error(None, error_message)
                
                # Re-render the form with errors
                if request.htmx:
                    # On validation error, re-render the form and swap it in place
                    # This maintains the original behavior
                    response = render(request, "partials/_api_key_form.html", {"form": form})
                    response["HX-Retarget"] = "#create-api-key-form"
                    response['HX-Reswap'] = 'outerHTML'
                    return response
                else:
                    self.object_list = self.get_queryset()
                    return self.render_to_response(self.get_context_data(form=form))
        else:
            # If form is invalid, return the modal with errors for HTMX
            if request.htmx:
                # On validation error, re-render the form and swap it in place
                response = render(request, "partials/_api_key_form.html", {"form": form})
                response["HX-Retarget"] = "#create-api-key-form"
                response['HX-Reswap'] = 'outerHTML'
                return response
            else:
                # ListView doesn't have form_invalid, so we manually call get()
                # to reconstruct the context including the invalid form.
                self.object_list = self.get_queryset() # Need to set this for get()
                return self.render_to_response(self.get_context_data(form=form))

    @transaction.atomic
    def form_valid(self, form):
        """Process a valid form to create an API key."""
        if not has_verified_email(self.request.user):
            form.add_error(None, "Email verification required to create API keys. Please verify your email address in your account settings.")
            return self.form_invalid(form)

        name = form.cleaned_data['name']
        ctx = self.api_key_context

        if ctx["type"] == "organization":
            raw_key, api_key = ApiKey.create_for_org(
                ctx["organization"],
                created_by=self.request.user,
                name=name,
            )
        else:
            # create_for_user bypasses model validation by using objects.create
            # The validation will now happen in the model's save method
            # which could raise ValidationError (e.g., if key limit is reached)
            raw_key, api_key = ApiKey.create_for_user(
                self.request.user,
                name=name,
                created_by=self.request.user,
            )

        base_props = {
            'key_id': str(api_key.id),
            'key_name': name,
        }
        props = _org_event_properties(self.request, base_props)
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=self.request.user.id,
            event=AnalyticsEvent.API_KEY_CREATED,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        if props.get('organization'):
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=self.request.user.id,
                event=AnalyticsEvent.ORGANIZATION_API_KEY_CREATED,
                source=AnalyticsSource.WEB,
                properties=props.copy(),
            ))

        if self.request.htmx:
            # Return the newly created API key notification for HTMX
            response = render(self.request, "partials/_api_key_created.html", {
                "raw_key": raw_key,
                "key_id": api_key.id
            })
            # Trigger events to refresh the table and close the modal
            response["HX-Trigger"] = json.dumps({
                "refreshApiKeysTable": None,
                "close-modal": {"id": "create-api-key-modal"},
            })
            return response
        else:
            # Traditional flow with message and redirect
            messages.success(
                self.request,
                f"New API key created: {raw_key}. Copy this key now, you won't be able to see it again!"
            )
            return redirect(self.get_success_url())


class ApiKeyDetailView(ApiKeyOwnerMixin, LoginRequiredMixin, View):
    """Handle Revoke (PATCH) and Delete (DELETE) for a specific API key."""
    http_method_names = ['get', 'patch', 'delete', 'options'] # Added GET for HTMX refresh

    @tracer.start_as_current_span("API Key Get Object")
    def get_object(self):
        """Helper to get the API key or raise 404."""
        ctx = self.api_key_context
        base_qs = ApiKey.objects.select_related("created_by")

        if ctx["type"] == "organization":
            return get_object_or_404(
                base_qs,
                id=self.kwargs['pk'],
                organization=ctx["organization"],
            )

        return get_object_or_404(
            base_qs,
            id=self.kwargs['pk'],
            user=self.request.user,
        )

    @tracer.start_as_current_span("API Key Detail View - GET")
    def get(self, request, *args, **kwargs):
        """Handle GET requests to refresh a row via HTMX."""
        if not request.htmx:
            # If not HTMX, redirect to the list view
            return redirect(reverse('api_keys'))
            
        # Get the API key and render just the row
        api_key = self.get_object()

        # Not tracking here as it's a small segment of larger page

        return render(
            request,
            "partials/_api_key_row.html",
            {
                "key": api_key,
                "api_key_context": self.api_key_context,
                "can_manage_api_keys": self.api_key_context.get("can_manage", False),
            },
        )

    @transaction.atomic
    def patch(self, request, *args, **kwargs):
        """Handle PATCH requests to revoke an API key."""
        self._ensure_can_manage_api_keys()
        api_key = self.get_object()
        api_key.revoke()

        props = _org_event_properties(request, {
            'key_id': str(api_key.id),
            'key_name': api_key.name,
        })
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_API_KEY_REVOKED if props.get('organization', None) else AnalyticsEvent.API_KEY_REVOKED,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        
        if request.htmx:
            # First return success message
            response = render(request, "partials/_api_key_success.html", {
                "message": f"API key '{api_key.name}' has been revoked.",
                "id": api_key.id
            })
            # Set HX-Trigger to refresh the table row
            response["HX-Trigger"] = f"refresh-row-{api_key.id}"
            return response
        else:
            # Traditional response with message and redirect
            messages.success(request, f"API key '{api_key.name}' has been revoked.")
            return redirect(reverse('api_keys'))


    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        """Handle DELETE requests to permanently delete an API key."""
        self._ensure_can_manage_api_keys()
        api_key = self.get_object()
        key_name = api_key.name # Store name before deleting
        key_id = api_key.id     # Store ID before deleting
        api_key.delete()

        props = _org_event_properties(request, {
            'key_id': str(key_id),
            'key_name': key_name,
        })
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.API_KEY_DELETED,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        if props.get('organization'):
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_API_KEY_DELETED,
                source=AnalyticsSource.WEB,
                properties=props.copy(),
            ))
        
        if request.htmx:
            # Render the success message partial
            response = render(request, "partials/_api_key_deleted_message.html", {"key_name": key_name})
            # Trigger table refresh and modal close
            response['HX-Trigger'] = '{"refreshApiKeysTable": null, "closeDeleteModal": null}'

            return response
        else:
            # Traditional response
            messages.success(request, f"API key '{key_name}' has been permanently deleted.")
            return redirect(reverse('api_keys'))

    def http_method_not_allowed(self, request, *args, **kwargs):
        """Handle disallowed methods."""
        # Log or handle the error as needed
        return HttpResponseNotAllowed(self._allowed_methods())

class ApiKeyTableView(ApiKeyOwnerMixin, LoginRequiredMixin, ListView):
    model = ApiKey
    template_name = "partials/_api_key_table_body.html"  # New partial for just the table body
    context_object_name = "api_keys"

    @tracer.start_as_current_span("API Key Table View - GET")
    def get_queryset(self):
        ctx = self.api_key_context
        if ctx["type"] == "organization":
            return (
                ApiKey.objects.select_related("created_by")
                .filter(organization=ctx["organization"])
                .order_by('-created_at')
            )

        return (
            ApiKey.objects.select_related("created_by")
            .filter(user=self.request.user)
            .order_by('-created_at')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['api_key_context'] = self.api_key_context
        context['can_manage_api_keys'] = self.api_key_context.get("can_manage", False)
        return context

class ApiKeyBlankFormView(ApiKeyOwnerMixin, LoginRequiredMixin, View):
    @tracer.start_as_current_span("API Key Blank Form View - GET")
    def get(self, request, *args, **kwargs):
        ctx = self.api_key_context
        if ctx["type"] == "organization":
            self._ensure_can_manage_api_keys()
            form = ApiKeyForm(organization=ctx["organization"])
        else:
            form = ApiKeyForm(user=request.user)
        return render(request, "partials/_api_key_form.html", {"form": form})

class ApiKeyCreateModalView(ApiKeyOwnerMixin, LoginRequiredMixin, View):
    @tracer.start_as_current_span("API Key Create Modal View - GET")
    def get(self, request, *args, **kwargs):
        ctx = self.api_key_context
        self._ensure_can_manage_api_keys()
        if ctx["type"] == "organization":
            form = ApiKeyForm(organization=ctx["organization"])
        else:
            form = ApiKeyForm(user=request.user)
        return render(request, "partials/_api_key_modal.html", {"form": form})

class BillingView(StripeFeatureRequiredMixin, ConsoleViewMixin, TemplateView):
    """View for billing information."""
    template_name = "billing.html"

    @tracer.start_as_current_span("CONSOLE Billing View")
    def get(self, request, *args, **kwargs):
        context = super().get_context_data(**kwargs)

        if request.GET.get("seats_success"):
            target_info = request.session.pop("org_seat_portal_target", None)
            success_message = "Seat checkout started successfully. Features will unlock once payment completes."
            if target_info and target_info.get("requested"):
                requested = target_info.get("requested")
                success_message = (
                    f"Seat checkout started successfully. In Stripe, update your licensed seat quantity to {requested}."
                )

            org_id_for_reattach = None
            if target_info and target_info.get("org_id"):
                org_id_for_reattach = target_info.get("org_id")

            if org_id_for_reattach:
                try:
                    _assign_stripe_api_key()
                    if not _reattach_overage_from_session(request, org_id_for_reattach):
                        logger.debug(
                            "No pending overage SKU detach found for org %s on success redirect.",
                            org_id_for_reattach,
                        )
                except Exception as exc:  # pragma: no cover - unexpected Stripe error
                    logger.warning(
                        "Failed to reattach overage SKU after success redirect for org %s: %s",
                        org_id_for_reattach,
                        exc,
                    )

            messages.success(request, success_message)

        if request.GET.get("seats_cancelled"):
            target_info = request.session.pop("org_seat_portal_target", None)
            org_id_for_reattach = None
            if target_info and target_info.get("org_id"):
                org_id_for_reattach = target_info.get("org_id")

            if org_id_for_reattach:
                try:
                    _assign_stripe_api_key()
                    if not _reattach_overage_from_session(request, org_id_for_reattach):
                        logger.debug(
                            "No pending overage SKU detach found for org %s on cancel redirect.",
                            org_id_for_reattach,
                        )
                except Exception as exc:  # pragma: no cover - unexpected Stripe error
                    logger.warning(
                        "Failed to reattach overage SKU after cancellation for org %s: %s",
                        org_id_for_reattach,
                        exc,
                    )

            messages.info(
                request,
                "Seat checkout was cancelled before completion.",
            )

        requested_org_id = request.GET.get("org_id")
        if requested_org_id:
            try:
                membership_for_switch = OrganizationMembership.objects.select_related("org").get(
                    user=request.user,
                    org_id=requested_org_id,
                    status=OrganizationMembership.OrgStatus.ACTIVE,
                )
            except OrganizationMembership.DoesNotExist:
                messages.error(request, "You don't have access to that organization.")
            else:
                request.session['context_type'] = 'organization'
                request.session['context_id'] = str(membership_for_switch.org.id)
                request.session['context_name'] = membership_for_switch.org.name
                request.session.modified = True

                resolved_context = build_console_context(request)
                context['current_context'] = {
                    'type': resolved_context.current_context.type,
                    'id': resolved_context.current_context.id,
                    'name': resolved_context.current_context.name,
                }
                if resolved_context.current_membership is not None:
                    context['current_membership'] = resolved_context.current_membership
                context['can_manage_org_agents'] = resolved_context.can_manage_org_agents

        current_context = context.get('current_context', {}) or {}
        if current_context.get('type') == 'organization' and current_context.get('id'):
            try:
                organization = Organization.objects.select_related('billing').get(id=current_context['id'])
            except Organization.DoesNotExist:
                messages.error(request, 'Organization not found. Switching back to personal billing.')
                request.session['context_type'] = 'personal'
                request.session['context_id'] = str(request.user.id)
                request.session['context_name'] = request.user.get_full_name() or request.user.email
                return redirect('billing')
            else:
                overview = build_org_billing_overview(organization)
                membership = context.get('current_membership')
                can_manage_billing = bool(membership and membership.role in BILLING_MANAGE_ROLES)

                configured_limit = overview['extra_tasks']['configured_limit'] or 0
                auto_purchase_state = {
                    'enabled': configured_limit not in (0,),
                    'infinite': configured_limit == -1,
                    'max_tasks': configured_limit if configured_limit not in (0, -1) else 1000,
                }

                billing = getattr(organization, "billing", None)
                seat_purchase_required = bool(getattr(billing, "purchased_seats", 0) <= 0)
                has_stripe_subscription = bool(getattr(billing, "stripe_subscription_id", None))
                seat_purchase_form = OrganizationSeatPurchaseForm(org=organization)
                seat_reduction_form = OrganizationSeatReductionForm(org=organization)

                dedicated_total = DedicatedProxyService.allocated_count(organization)
                dedicated_proxies = list(
                    DedicatedProxyService.allocated_proxies(organization).select_related("dedicated_allocation")
                )
                dedicated_allowed = overview.get('plan', {}).get('id') != PlanNamesChoices.FREE.value

                context.update({
                    'dedicated_ip_add_form': DedicatedIpAddForm(),
                    'dedicated_ip_total': dedicated_total,
                    'dedicated_ip_available': dedicated_total,
                    'dedicated_ip_proxies': dedicated_proxies,
                    'dedicated_ip_multi_assign': is_multi_assign_enabled(),
                    'dedicated_ip_allowed': dedicated_allowed,
                    'dedicated_ip_error': None,
                })

                unit_price, price_currency = _resolve_dedicated_ip_pricing(overview.get('plan'))
                context.update({
                    'dedicated_ip_unit_price': unit_price,
                    'dedicated_ip_total_cost': unit_price * Decimal(dedicated_total),
                    'dedicated_ip_currency': price_currency,
                })

                granted = Decimal(str(overview['credits']['granted'])) if overview['credits']['granted'] else Decimal('0')
                used = Decimal(str(overview['credits']['used'])) if overview['credits']['used'] else Decimal('0')
                usage_pct = 0
                if granted > 0:
                    usage_pct = min(100, float((used / granted) * 100))

                addon_context = AddonEntitlementService.get_addon_context_for_owner(
                    organization,
                    "organization",
                    overview.get("plan", {}).get("id"),
                )
                org_addons_disabled = (not can_manage_billing) or (not has_stripe_subscription)

                context.update({
                    'organization': organization,
                    'org_billing_overview': overview,
                    'org_can_manage_billing': can_manage_billing,
                    'org_auto_purchase_state': auto_purchase_state,
                    'org_credit_usage_pct': usage_pct,
                    'org_can_open_stripe': can_manage_billing and bool(overview['billing_record']['stripe_customer_id']),
                    'seat_purchase_form': seat_purchase_form,
                    'seat_reduction_form': seat_reduction_form,
                    'seat_purchase_required': seat_purchase_required,
                    'org_has_stripe_subscription': has_stripe_subscription,
                    'org_pending_seat_change': overview.get('pending_seats', {}),
                    'addon_context': addon_context,
                    'org_addons_disabled': org_addons_disabled,
                })
                billing_view_props = Analytics.with_org_properties(
                    {
                        'actor_id': str(request.user.id),
                        'has_stripe_subscription': bool(getattr(billing, "stripe_subscription_id", None)),
                    },
                    organization=organization,
                )
                Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.ORGANIZATION_BILLING_VIEWED,
                    source=AnalyticsSource.WEB,
                    properties=billing_view_props.copy(),
                )
                _apply_subscribe_success_context(
                    request,
                    context,
                    plan_id=(overview.get('plan') or {}).get('id'),
                )
                return render(request, self.template_name, context)

        # Personal billing fallback
        subscription_plan = get_user_plan(self.request.user)
        sub = get_active_subscription(self.request.user)
        actual_price, actual_currency = get_subscription_base_price(sub)

        if subscription_plan is None:
            subscription_plan = {}
        if actual_price is not None or actual_currency:
            subscription_plan = subscription_plan.copy()
            if actual_price is not None:
                subscription_plan["price"] = float(actual_price)
            if actual_currency:
                subscription_plan["currency"] = actual_currency

        context['subscription_plan'] = subscription_plan
        paid_subscriber = sub is not None

        if paid_subscriber:
            context['period_start_date'] = sub.current_period_start.strftime("%B %d, %Y")
            context['period_end_date'] = sub.current_period_end.strftime("%B %d, %Y")
            context['subscription_active'] = sub.is_status_current()
            context['cancel_at'] = sub.cancel_at.strftime("%B %d, %Y") if sub.cancel_at else None
            context['cancel_at_period_end'] = sub.cancel_at_period_end

        context['subscription'] = sub
        context['paid_subscriber'] = paid_subscriber
        context['personal_addons_disabled'] = not paid_subscriber

        dedicated_plan = subscription_plan
        dedicated_allowed = (dedicated_plan or {}).get('id') != PlanNamesChoices.FREE.value
        dedicated_total = DedicatedProxyService.allocated_count(request.user)
        dedicated_proxies = list(
            DedicatedProxyService.allocated_proxies(request.user).select_related("dedicated_allocation")
        )
        context.update({
            'dedicated_ip_add_form': DedicatedIpAddForm(),
            'dedicated_ip_total': dedicated_total,
            'dedicated_ip_available': dedicated_total,
            'dedicated_ip_proxies': dedicated_proxies,
            'dedicated_ip_multi_assign': is_multi_assign_enabled(),
            'dedicated_ip_allowed': dedicated_allowed,
            'dedicated_ip_error': None,
        })

        unit_price, price_currency = _resolve_dedicated_ip_pricing(dedicated_plan)
        context.update({
            'dedicated_ip_unit_price': unit_price,
            'dedicated_ip_total_cost': unit_price * Decimal(dedicated_total),
            'dedicated_ip_currency': price_currency,
        })

        addon_context = AddonEntitlementService.get_addon_context_for_owner(
            request.user,
            "user",
            subscription_plan.get("id"),
        )
        context["addon_context"] = addon_context

        _apply_subscribe_success_context(request, context)
        return render(request, self.template_name, context)

    @tracer.start_as_current_span("CONSOLE Billing Post (not allowed)")
    def post(self, request, *args, **kwargs):
        # Handle any POST requests related to billing here
        return HttpResponseNotAllowed(['GET'])


class ProfileView(ConsoleViewMixin, PhoneNumberMixin, TemplateView):
    """Allow users to manage basic profile information and phone number."""

    template_name = "console/profile.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["profile_form"] = UserProfileForm(instance=user)

        return context

    def post(self, request, *args, **kwargs):
        """
        • PhoneNumberMixin handles add / verify / delete.
          If it returns an HttpResponse, we’re done.
        • Otherwise we process the normal profile form.
        •   If that fails validation, re-render the page with errors.
        """

        # 1️⃣ phone-related actions (HTMX or regular) ------------------------
        resp = self._handle_phone_post()  # provided by the mixin
        if resp is not None:  # mixin already produced a response
            return resp

        # 2️⃣ profile form ---------------------------------------------------
        profile_form = UserProfileForm(request.POST, instance=request.user)
        if profile_form.is_valid():
            profile_form.save()
            return redirect("profile")

        # 3️⃣ invalid profile form → rebuild full context --------------------
        context = self.get_context_data()
        context["profile_form"] = profile_form  # include bound form with errors
        return self.render_to_response(context)

@login_required
@require_POST
@transaction.atomic
@tracer.start_as_current_span("BILLING Update Billing Settings")
def update_billing_settings(request):
    try:
        data = json.loads(request.body)
        auto_purchase = data.get('enabled', False)
        infinite = data.get('infinite', False)
        max_tasks = data.get('maxTasks', 5)
        resolved = build_console_context(request)

        if resolved.current_context.type == 'organization' and resolved.current_membership:
            membership = resolved.current_membership
            if membership.role not in BILLING_MANAGE_ROLES:
                return JsonResponse({'success': False, 'error': 'Not permitted'}, status=403)

            OrgBilling = apps.get_model('api', 'OrganizationBilling')
            defaults = {'max_extra_tasks': 0, 'billing_cycle_anchor': timezone.now().day}
            org_billing, _ = OrgBilling.objects.get_or_create(
                organization=membership.org,
                defaults=defaults,
            )

            if not auto_purchase:
                org_billing.max_extra_tasks = 0
            elif infinite:
                org_billing.max_extra_tasks = -1
            else:
                org_billing.max_extra_tasks = max(1, int(max_tasks))

            org_billing.save(update_fields=['max_extra_tasks', 'updated_at'])

            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.BILLING_UPDATED,
                source=AnalyticsSource.WEB,
                properties={
                    'max_extra_tasks': org_billing.max_extra_tasks,
                    'auto_purchase': auto_purchase,
                    'infinite': infinite,
                    'owner_type': 'organization',
                    'organization_id': str(membership.org.id),
                }
            ))

            return JsonResponse({
                'success': True,
                'max_extra_tasks': org_billing.max_extra_tasks,
                'owner_type': 'organization',
            })

        user_billing, _ = UserBilling.objects.get_or_create(
            user=request.user,
            defaults={'max_extra_tasks': 0}
        )

        if not auto_purchase:
            user_billing.max_extra_tasks = 0
        elif infinite:
            user_billing.max_extra_tasks = -1
        else:
            user_billing.max_extra_tasks = max(1, int(max_tasks))

        user_billing.save(update_fields=['max_extra_tasks'])

        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.BILLING_UPDATED,
            source=AnalyticsSource.WEB,
            properties={
                'max_extra_tasks': user_billing.max_extra_tasks,
                'auto_purchase': auto_purchase,
                'infinite': infinite,
                'owner_type': 'user',
            }
        ))

        return JsonResponse({
            'success': True,
            'max_extra_tasks': user_billing.max_extra_tasks,
            'owner_type': 'user',
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)

@login_required
@tracer.start_as_current_span("BILLING Get Billing Settings")
def get_billing_settings(request):
    try:
        resolved = build_console_context(request)

        if resolved.current_context.type == 'organization' and resolved.current_membership:
            membership = resolved.current_membership
            if membership.role not in BILLING_MANAGE_ROLES and membership is not None:
                # Allow read-only access even without manage role, but disable editing client side
                permitted = False
            else:
                permitted = True

            OrgBilling = apps.get_model('api', 'OrganizationBilling')
            defaults = {'max_extra_tasks': 0, 'billing_cycle_anchor': timezone.now().day}
            org_billing, _ = OrgBilling.objects.get_or_create(
                organization=membership.org,
                defaults=defaults,
            )

            return JsonResponse({
                'max_extra_tasks': org_billing.max_extra_tasks,
                'owner_type': 'organization',
                'can_modify': permitted,
            })

        user_billing, _ = UserBilling.objects.get_or_create(
            user=request.user,
            defaults={'max_extra_tasks': 0}
        )

        return JsonResponse({
            'max_extra_tasks': user_billing.max_extra_tasks,
            'owner_type': 'user',
            'can_modify': True,
        })
    except Exception as e:
        return JsonResponse({
            'error': str(e)
        }, status=400)


@login_required
@tracer.start_as_current_span("Get User Plan")
def get_user_plan_api(request):
    """Return the user's current subscription plan for frontend use."""
    from util.subscription_helper import get_user_plan
    from constants.plans import PlanNames

    try:
        plan = get_user_plan(request.user)
        plan_id = str(plan.get("id", "")).lower() if plan else ""
        # Map internal plan IDs to frontend-friendly values
        plan_map = {
            PlanNames.FREE: 'free',
            PlanNames.STARTUP: 'startup',
            PlanNames.SCALE: 'scale',
        }
        return JsonResponse({
            'plan': plan_map.get(plan_id, 'free'),
            'is_proprietary_mode': settings.GOBII_PROPRIETARY_MODE,
        })
    except Exception as e:
        return JsonResponse({
            'plan': 'free',
            'is_proprietary_mode': settings.GOBII_PROPRIETARY_MODE,
            'error': str(e),
        })


@login_required
@require_POST
@tracer.start_as_current_span("BILLING Cancel Subscription")
def cancel_subscription(request):
    """Endpoint to cancel the user's subscription."""
    if not stripe_status().enabled:
        return JsonResponse({
            'success': False,
            'error': 'Stripe billing is not available in this deployment.'
        }, status=404)

    sub = get_active_subscription(request.user)
    if sub:
        try:
            _assign_stripe_api_key()
            stripe.Subscription.modify(sub.id, cancel_at_period_end=True)

            Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.BILLING_CANCELLATION,
                source=AnalyticsSource.WEB,
                properties={},
            )

            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({
                    'success': False,
                    'error': 'Error cancelling subscription'
                },
                status=500)
    else:
        return JsonResponse({
            'success': False,
            'error': "You do not have an active subscription to cancel."
        }, status=400)

@login_required
def tasks_view(request):
    # Get current context from session
    context_type = request.session.get('context_type', 'personal')
    context_id = request.session.get('context_id', str(request.user.id))
    
    # Get tasks for the current context
    with traced("CONSOLE Tasks View") as span:
        if context_type == 'organization':
            # Ensure the requester is an active member of the organization context
            if not OrganizationMembership.objects.filter(
                user=request.user,
                org_id=context_id,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).exists():
                return HttpResponseForbidden("You do not have access to this organization.")

            tasks_queryset = (
                BrowserUseAgentTask.objects.filter(
                    models.Q(organization_id=context_id) |
                    models.Q(agent__persistent_agent__organization_id=context_id),
                    is_deleted=False,
                )
                .distinct()
                .order_by('-created_at')
            )
        else:
            # For personal context, show user's personal tasks only
            tasks_queryset = (
                BrowserUseAgentTask.objects.filter(
                    user=request.user,
                    is_deleted=False,
                    organization__isnull=True,
                )
                .exclude(agent__persistent_agent__organization__isnull=False)
                .order_by('-created_at')
            )

        # Handle filtering by status
        status_filter = request.GET.get('status')
        if status_filter:
            span.set_attribute('tasks.status_filter', status_filter)
            tasks_queryset = tasks_queryset.filter(status=status_filter)

        # Handle search
        search_query = request.GET.get('search')
        if search_query:
            span.set_attribute('tasks.search_query', search_query)
            tasks_queryset = tasks_queryset.filter(prompt__icontains=search_query)

        # Pagination
        paginator = Paginator(tasks_queryset, 10)  # Show 10 tasks per page
        page_number = request.GET.get('page', 1)

        with traced("CONSOLE Tasks View Pagination") as span:
            span.set_attribute('tasks.page_number', page_number)
            tasks = paginator.get_page(page_number)

        # Get user's organization memberships for context switcher
        user_organizations = OrganizationMembership.objects.filter(
            user=request.user,
            status=OrganizationMembership.OrgStatus.ACTIVE
        ).select_related('org').order_by('org__name')
        
        context = {
            'tasks': tasks,
            'status_filter': status_filter,
            'user_organizations': user_organizations,
            'current_context': {
                'type': context_type,
                'id': context_id,
                'name': request.session.get('context_name', request.user.get_full_name() or request.user.username)
            }
        }
        
        return render(request, 'tasks.html', context)

@login_required
def task_detail_view(request, task_id):
    # Get the task with related steps
    with traced("CONSOLE Task Detail View") as span:
        span.set_attribute('task.id', str(task_id))
        with traced("CONSOLE Task Detail Fetch Task"):
            task = get_object_or_404(
                BrowserUseAgentTask.objects.prefetch_related('steps'),
                id=task_id,
                user=request.user,
                is_deleted=False
            )

        return render(request, 'task_detail.html', {'task': task})

@login_required
def task_cancel_view(request, task_id):
    if request.method == 'POST':
        with traced("CONSOLE Task Cancel", user_id=request.user.id) as span:
            # Get the task
            task = get_object_or_404(
                BrowserUseAgentTask,
                id=task_id,
                user=request.user,
                is_deleted=False
            )

            # Only allow cancelling tasks that are pending or in_progress
            if task.status in [BrowserUseAgentTask.StatusChoices.PENDING, BrowserUseAgentTask.StatusChoices.IN_PROGRESS]:
                # Update task status
                task.status = BrowserUseAgentTask.StatusChoices.CANCELLED
                task.save()

                Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.WEB_TASK_CANCELLED,
                    source=AnalyticsSource.WEB,
                    properties={
                        'task_id': str(task.id),
                        'task_status': task.status
                    }
                )

                messages.success(request, "Task successfully cancelled.")
            else:
                messages.error(request, "This task cannot be cancelled.")

            return redirect('task_detail', task_id=task_id)

    # If not POST, redirect to task detail
    return redirect('task_detail', task_id=task_id)

@login_required
@tracer.start_as_current_span("CONSOLE Task Result View")
def task_result_view(request, task_id):
    # Get the task
    span = trace.get_current_span()
    span.set_attribute('task.id', str(task_id))
    span.set_attribute('user.id', str(request.user.id))
    with traced("CONSOLE Task Result Fetch Task"):
        task = get_object_or_404(
            BrowserUseAgentTask.objects.prefetch_related('steps'),
            id=task_id,
            user=request.user,
            is_deleted=False
        )

    span.set_attribute('task.status', task.status)

    # Ensure the task is completed
    if task.status != BrowserUseAgentTask.StatusChoices.COMPLETED:
        messages.error(request, "Task result is not available yet.")
        return redirect('task_detail', task_id=task_id)

    # Find the result step
    with traced("CONSOLE Task Result Fetch Step"):
        result_step = task.steps.filter(is_result=True).first()

    # Handle JSON download format
    if request.GET.get('format') == 'json' and result_step and result_step.result_value:
        # if result_step.result_value is a string, parse it as JSON
        response = None

        # Some shenanigans to handle both JSON and invalid JSON gracefully (send as text if invalid)
        try:
            response = JsonResponse(result_step.result_value)
            span.set_attribute('task.result_format', 'json')
            response['Content-Disposition'] = f'attachment; filename="task_{task_id}_result.json"'
        except TypeError:
            span.set_attribute('task.result_format', 'text')
            response = HttpResponse(result_step.result_value, content_type='text/plain; charset=utf-8')
            response['Content-Disposition'] = f'attachment; filename="task_{task_id}_result.txt"'

        # Track the download event
        download_props = _org_event_properties(
            request,
            {
                'task_id': str(task.id),
                'task_status': task.status,
                'result_step_id': str(result_step.id),
            },
        )
        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.WEB_TASK_RESULT_DOWNLOADED,
            source=AnalyticsSource.WEB,
            properties=download_props.copy(),
        )

        return response

    span.set_attribute('task.result_format', 'html')

    # For regular HTML rendering
    import json
    context = {
        'task': task,
        'result_step': result_step,
    }

    return render(request, 'task_result.html', context)

# ────────── Persistent Agents (Feature-Flagged) ──────────
class PersistentAgentsView(ConsoleViewMixin, TemplateView):
    template_name = "console/persistent_agents.html"

    def _resolve_context_owner(self, context: dict[str, Any]) -> tuple[Any | None, str, Any | None]:
        current_context = context.get('current_context', {})
        membership = context.get('current_membership')
        owner = self.request.user
        owner_type = 'user'
        organization = None

        if current_context.get('type') == 'organization':
            organization = getattr(membership, 'org', None)
            if organization is None:
                org_id = current_context.get('id')
                if org_id:
                    organization = Organization.objects.filter(id=org_id).first()
            owner = organization
            owner_type = 'organization'

        return owner, owner_type, organization

    def _resolve_agent_capacity(self, context: dict[str, Any]) -> dict[str, Any]:
        owner, owner_type, organization = self._resolve_context_owner(context)

        if owner is None:
            return {
                'agents_available': 0,
                'agents_unlimited': False,
                'can_spawn_agents': False,
            }

        try:
            agents_available = AgentService.get_agents_available(owner)
        except Exception:
            agents_available = 0

        try:
            can_spawn_agents = AgentService.has_agents_available(owner)
        except Exception:
            can_spawn_agents = False

        community_unlimited = is_community_unlimited_mode()
        if owner_type == 'organization':
            plan = get_organization_plan(organization) if organization else None
            agents_unlimited = community_unlimited or (plan and plan.get('agent_limit') == AGENTS_UNLIMITED)
        else:
            agents_unlimited = community_unlimited or has_unlimited_agents(owner)

        return {
            'agents_available': max(int(agents_available), 0),
            'agents_unlimited': bool(agents_unlimited),
            'can_spawn_agents': bool(can_spawn_agents),
        }

    def _serialize_agent_for_frontend(
        self,
        agent: PersistentAgent,
        *,
        is_staff: bool = False,
        is_shared: bool = False,
    ) -> dict[str, Any]:
        display_tags = agent.display_tags if isinstance(agent.display_tags, list) else []
        primary_email = _first_endpoint_address(getattr(agent, 'primary_email_endpoints', None))
        primary_sms = _first_endpoint_address(getattr(agent, 'primary_sms_endpoints', None))
        remaining = _coerce_decimal_to_float(getattr(agent, 'daily_credit_remaining', None))
        recent_burn = _coerce_decimal_to_float(getattr(agent, 'daily_credit_last_24h_usage', None))

        detail_url = reverse('agent_detail', kwargs={'pk': agent.id})
        if is_shared:
            detail_url = build_immersive_chat_url(self.request, agent.id, return_to=self.request.get_full_path())

        return {
            'id': str(agent.id),
            'name': agent.name or '',
            'avatarUrl': agent.get_avatar_url(),
            'listingDescription': agent.listing_description or '',
            'listingDescriptionSource': getattr(agent, 'listing_description_source', None),
            'miniDescription': agent.mini_description or '',
            'miniDescriptionSource': getattr(agent, 'mini_description_source', None),
            'displayTags': display_tags,
            'isActive': bool(getattr(agent, 'is_active', False)),
            'pendingTransfer': bool(getattr(agent, 'pending_transfer_invite', None)),
            'primaryEmail': primary_email,
            'primarySms': primary_sms,
            'detailUrl': detail_url,
            'chatUrl': build_immersive_chat_url(self.request, agent.id, return_to=self.request.get_full_path()),
            'cardGradientStyle': getattr(agent, 'card_gradient_style', '') or '',
            'iconBackgroundHex': getattr(agent, 'icon_background_hex', '') or '',
            'iconBorderHex': getattr(agent, 'icon_border_hex', '') or '',
            'displayColorHex': getattr(agent, 'display_color_hex', None) or agent.get_display_color(),
            'headerTextClass': getattr(agent, 'header_text_class', '') or '',
            'headerSubtextClass': getattr(agent, 'header_subtext_class', '') or '',
            'headerStatusClass': getattr(agent, 'header_status_class', '') or '',
            'headerBadgeClass': getattr(agent, 'header_badge_class', '') or '',
            'headerIconClass': getattr(agent, 'header_icon_class', '') or '',
            'headerLinkHoverClass': getattr(agent, 'header_link_hover_class', '') or '',
            'dailyCreditRemaining': remaining,
            'dailyCreditLow': bool(getattr(agent, 'daily_credit_low', False)),
            'last24hCreditBurn': recent_burn,
            'auditUrl': reverse('console-agent-audit', kwargs={'agent_id': agent.id}) if is_staff else None,
            'isShared': bool(is_shared),
        }

    def _build_agent_list_props(
        self,
        context: dict[str, Any],
        agents: list[PersistentAgent],
        shared_agents: list[PersistentAgent] | None = None,
    ) -> dict[str, Any]:
        capacity = self._resolve_agent_capacity(context)
        can_spawn_agents = capacity['can_spawn_agents']
        spawn_url = f"{reverse('pages:home')}?spawn=1"

        upgrade_url = None
        if settings.GOBII_PROPRIETARY_MODE:
            try:
                upgrade_url = reverse('proprietary:pricing')
            except NoReverseMatch:
                upgrade_url = None

        owner, owner_type, organization = self._resolve_context_owner(context)

        llm_intelligence = build_llm_intelligence_props(owner, owner_type, organization, upgrade_url)
        is_staff = bool(self.request.user and (self.request.user.is_staff or self.request.user.is_superuser))
        shared_agents = shared_agents or []

        return {
            'agents': [self._serialize_agent_for_frontend(agent, is_staff=is_staff) for agent in agents],
            'sharedAgents': [
                self._serialize_agent_for_frontend(agent, is_staff=is_staff, is_shared=True)
                for agent in shared_agents
            ],
            'hasAgents': bool(agents or shared_agents),
            'hasSharedAgents': bool(shared_agents),
            'spawnAgentUrl': spawn_url,
            'upgradeUrl': upgrade_url,
            'canSpawnAgents': can_spawn_agents,
            'showUpgradeCta': bool(upgrade_url) and settings.GOBII_PROPRIETARY_MODE and not can_spawn_agents,
            'createFirstAgentEvent': AnalyticsCTAs.CTA_CREATE_FIRST_AGENT_CLICKED.value,
            'agentsAvailable': capacity['agents_available'],
            'agentsUnlimited': capacity['agents_unlimited'],
            'llmIntelligence': llm_intelligence,
            'isStaff': is_staff,
            'emailVerified': has_verified_email(self.request.user),
        }

    @tracer.start_as_current_span("CONSOLE Persistent Agents View")
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['pin_console_nav'] = True
        
        # Define a prefetch for the primary email endpoint to avoid N+1 queries
        primary_email_prefetch = models.Prefetch(
            'comms_endpoints',
            queryset=PersistentAgentCommsEndpoint.objects.filter(channel=CommsChannel.EMAIL, is_primary=True),
            to_attr='primary_email_endpoints'  # Use a plural name as it's a list
        )

        primary_sms_prefetch = models.Prefetch(
            'comms_endpoints',
            queryset=PersistentAgentCommsEndpoint.objects.filter(channel=CommsChannel.SMS, is_primary=True),
            to_attr='primary_sms_endpoints'  # Use a plural name as it's a list
        )

        # Filter agents based on current context
        current_context = context.get('current_context', {})
        if current_context.get('type') == 'organization':
            # Show organization's agents
            persistent_agents = PersistentAgent.objects.non_eval().filter(
                organization_id=current_context.get('id')
            ).select_related('browser_use_agent', 'agent_color').prefetch_related(primary_email_prefetch).prefetch_related(primary_sms_prefetch).order_by('-created_at')
        else:
            # Show personal agents
            persistent_agents = PersistentAgent.objects.non_eval().filter(
                user=self.request.user,
                organization__isnull=True  # Only personal agents
            ).select_related('browser_use_agent', 'agent_color').prefetch_related(primary_email_prefetch).prefetch_related(primary_sms_prefetch).order_by('-created_at')
        
        persistent_agents = list(persistent_agents)
        shared_agents_qs = (
            PersistentAgent.objects
            .non_eval()
            .filter(collaborators__user=self.request.user)
            .select_related('browser_use_agent', 'agent_color')
            .prefetch_related(primary_email_prefetch)
            .prefetch_related(primary_sms_prefetch)
            .order_by('-created_at')
        )
        if persistent_agents:
            shared_agents_qs = shared_agents_qs.exclude(
                id__in=[agent.id for agent in persistent_agents]
            )
        shared_agents = list(shared_agents_qs)
        all_agents = persistent_agents + shared_agents
        today = timezone.localdate()
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
        agent_ids = [agent.id for agent in all_agents]
        recent_usage_map: dict[Any, Decimal] = {}
        if agent_ids:
            usage_rows = (
                PersistentAgentStep.objects.filter(
                    agent_id__in=agent_ids,
                    created_at__gte=lookback_start,
                    created_at__lt=lookback_end,
                    credits_cost__isnull=False,
                )
                .values('agent_id')
                .annotate(total=Sum('credits_cost'))
            )
            recent_usage_map = {
                row['agent_id']: row['total'] or Decimal("0")
                for row in usage_rows
            }

        for agent in all_agents:
            description, source = build_listing_description(agent, max_length=200)
            agent.listing_description = description
            agent.listing_description_source = source
            agent.is_initializing = source == "placeholder"
            color_hex = agent.get_display_color().upper()
            agent.display_color_hex = color_hex
            agent.card_gradient_style = _build_agent_gradient(color_hex)
            agent.icon_background_hex = _adjust_hex(color_hex, 0.55)
            agent.icon_border_hex = _adjust_hex(color_hex, -0.25)
            palette = _text_palette_for_hex(color_hex)
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
            agent.pending_transfer_invite = AgentTransferInvite.objects.filter(
                agent=agent,
                status=AgentTransferInvite.Status.PENDING,
            ).first()

            last_24h_usage = recent_usage_map.get(agent.id, Decimal("0"))

            try:
                soft_target = agent.get_daily_credit_soft_target()
                hard_limit = agent.get_daily_credit_hard_limit()
                usage = agent.get_daily_credit_usage(usage_date=today)
                remaining = agent.get_daily_credit_remaining(usage_date=today)
            except Exception:
                soft_target = None
                hard_limit = None
                usage = Decimal("0")
                remaining = None

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

        context['persistent_agents'] = persistent_agents
        context['shared_agents'] = shared_agents
        context['has_agents'] = bool(all_agents)
        context['agent_list_props'] = self._build_agent_list_props(
            context,
            persistent_agents,
            shared_agents,
        )

        pending_transfers_qs = AgentTransferInvite.objects.filter(
            status=AgentTransferInvite.Status.PENDING,
        ).filter(
            Q(to_user=self.request.user) | Q(to_user__isnull=True, to_email__iexact=self.request.user.email)
        ).select_related('agent', 'agent__user')

        pending_transfers: list[AgentTransferInvite] = list(pending_transfers_qs)
        if pending_transfers:
            unsassigned_ids = [invite.id for invite in pending_transfers if invite.to_user_id is None]
            if unsassigned_ids:
                AgentTransferInvite.objects.filter(id__in=unsassigned_ids).update(to_user=self.request.user)
                for invite in pending_transfers:
                    if invite.id in unsassigned_ids:
                        invite.to_user = self.request.user
        context['pending_agent_transfer_invites'] = pending_transfers

        return context


class AgentCreateContactView(ConsoleViewMixin, PhoneNumberMixin, TemplateView):
    """Step 2: Contact preferences for agent creation."""
    template_name = "console/agent_create_contact.html"

    @tracer.start_as_current_span("CONSOLE Agent Create Contact View")
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Pre-populate with user's email and SMS if verified
        if 'form' not in kwargs:
            initial_data = {'contact_endpoint_email': self.request.user.email}

            template_code = self.request.session.get(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY)
            template = PretrainedWorkerTemplateService.get_template_by_code(template_code) if template_code else None

            if template:
                template.schedule_description = PretrainedWorkerTemplateService.describe_schedule(template.base_schedule)
                template.display_default_tools = PretrainedWorkerTemplateService.get_tool_display_list(
                    template.default_tools or []
                )
                template.contact_method_label = PretrainedWorkerTemplateService.describe_contact_channel(
                    template.recommended_contact_channel
                )
                context['selected_pretrained_worker'] = template
                preferred = (template.recommended_contact_channel or '').lower()
                valid_choices = {choice for choice, _ in PersistentAgentContactForm.CONTACT_METHOD_CHOICES}
                if preferred in valid_choices:
                    initial_data['preferred_contact_method'] = preferred

            context['form'] = PersistentAgentContactForm(initial=initial_data)
        else:
            template_code = self.request.session.get(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY)
            template = PretrainedWorkerTemplateService.get_template_by_code(template_code) if template_code else None
            if template:
                template.schedule_description = PretrainedWorkerTemplateService.describe_schedule(template.base_schedule)
                template.display_default_tools = PretrainedWorkerTemplateService.get_tool_display_list(
                    template.default_tools or []
                )
                template.contact_method_label = PretrainedWorkerTemplateService.describe_contact_channel(
                    template.recommended_contact_channel
                )
                context['selected_pretrained_worker'] = template

        current_context = context.get('current_context', {
            'type': 'personal',
            'name': self.request.user.get_full_name() or self.request.user.username,
        })

        if current_context.get('type') == 'organization':
            context['agent_owner_label'] = current_context.get('name')
        else:
            context['agent_owner_label'] = self.request.user.get_full_name() or self.request.user.username

        context.setdefault('can_manage_org_agents', True)
        context['show_org_permission_warning'] = (
            current_context.get('type') == 'organization' and not context['can_manage_org_agents']
        )

        return context

    def get(self, request, *args, **kwargs):
        """Render the contact preferences form."""
        resolved_context = build_console_context(request)
        organization = None
        if resolved_context.current_context.type == "organization" and resolved_context.current_membership:
            organization = resolved_context.current_membership.org

        availability_checks: list[bool] = []
        if organization is not None:
            availability_checks.append(AgentService.has_agents_available(organization))
        availability_checks.append(AgentService.has_agents_available(request.user))

        if not any(availability_checks):
            messages.error(request, "You do not have any persistent agents available. Please upgrade to spawn more.")
            return redirect('pages:home')

        # Check if we have charter data from step 1
        if 'agent_charter' not in self.request.session:
            messages.error(self.request, "Please start by describing what your agent should do.")
            return redirect('agents')

        return self.render_to_response(self.get_context_data())

    @tracer.start_as_current_span("CONSOLE Agent Create Contact - Create Agent")
    def post(self, request, *args, **kwargs):
        """Handle step 2: create the agent with contact preferences."""

        resp = self._handle_phone_post()
        if resp:  # phone add/verify/delete handled
            return resp

        form = PersistentAgentContactForm(request.POST)
        phone = self._current_phone()  # helper from PhoneNumberMixin

        form_is_valid = form.is_valid()
        if form_is_valid and form.cleaned_data['preferred_contact_method'] == 'sms':
            if not phone or not phone.is_verified:
                form.add_error(None, "Please verify a phone number before selecting SMS.")

        if form.errors:
            return self.render_to_response(self.get_context_data(form=form))

        # Check if we have charter data from step 1
        if 'agent_charter' not in request.session:
            messages.error(request, "Please start by describing what your agent should do.")
            return redirect('agents')

        initial_user_message = request.session.get('agent_charter')
        user_contact_email = form.cleaned_data.get('contact_endpoint_email') or ''
        sms_enabled = form.cleaned_data.get('sms_enabled', False)
        email_enabled = form.cleaned_data.get('email_enabled', False)
        preferred_contact_method = form.cleaned_data['preferred_contact_method']

        try:
            result = create_persistent_agent_from_charter(
                request,
                initial_message=initial_user_message,
                contact_email=user_contact_email,
                email_enabled=email_enabled,
                sms_enabled=sms_enabled,
                preferred_contact_method=preferred_contact_method,
            )
            return redirect('agent_welcome', pk=result.agent.id)
        except ValidationError as exc:
            error_messages = []
            if hasattr(exc, 'message_dict'):
                for field_errors in exc.message_dict.values():
                    error_messages.extend(field_errors)
            error_messages.extend(getattr(exc, 'messages', []))
            if not error_messages:
                error_messages.append(
                    "We couldn't create that agent. Please check your organization settings and try again."
                )
            for message_text in error_messages:
                form.add_error(None, message_text)
                messages.error(request, message_text)
        except Exception:
            logger.exception("Error creating persistent agent")
            messages.error(
                request,
                "We ran into a problem creating your agent. Please try again.",
            )

        # If form is invalid or has errors, re-render with them
        context = self.get_context_data(form=form)
        context['form'] = form
        return self.render_to_response(context)


class AgentQuickSpawnView(LoginRequiredMixin, View):
    """Create an agent from the saved charter and jump straight into chat."""

    @tracer.start_as_current_span("CONSOLE Agent Quick Spawn")
    def get(self, request, *args, **kwargs):
        return self._handle(request)

    def post(self, request, *args, **kwargs):
        return self._handle(request)

    def _handle(self, request):
        # Restore charter from OAuth cookie if missing from session
        if 'agent_charter' not in request.session:
            cookie_value = request.COOKIES.get(OAUTH_CHARTER_COOKIE)
            if cookie_value:
                try:
                    stashed = signing.loads(cookie_value, max_age=3600)
                    for key in ("agent_charter", PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY, "agent_charter_source"):
                        if key in stashed:
                            request.session[key] = stashed[key]
                    request.session.modified = True
                except (signing.BadSignature, signing.SignatureExpired):
                    logger.debug("Invalid or expired OAuth charter cookie")

        if 'agent_charter' not in request.session:
            messages.error(request, "Please start by describing what your agent should do.")
            return redirect('agents')

        contact_email = (request.user.email or "").strip()
        if not contact_email:
            messages.error(request, "Please add an email address to continue.")
            return redirect('agents')

        try:
            result = create_persistent_agent_from_charter(
                request,
                initial_message=request.session.get('agent_charter'),
                contact_email=contact_email,
                email_enabled=True,
                sms_enabled=False,
                preferred_contact_method='email',
            )
        except ValidationError as exc:
            error_messages = []
            if hasattr(exc, 'message_dict'):
                for field_errors in exc.message_dict.values():
                    error_messages.extend(field_errors)
            error_messages.extend(getattr(exc, 'messages', []))
            if not error_messages:
                error_messages.append("We couldn't create that agent. Please try again.")
            for message_text in error_messages:
                messages.error(request, message_text)
            return redirect('agents')
        except Exception:
            logger.exception("Error creating persistent agent")
            messages.error(request, "We ran into a problem creating your agent. Please try again.")
            return redirect('agents')

        session_return_to = request.session.pop(IMMERSIVE_RETURN_TO_SESSION_KEY, None)
        if session_return_to is not None:
            request.session.modified = True
        embed = (request.GET.get("embed") or "").lower() in {"1", "true", "yes", "on"}
        # Default return_to to agents list so closing the chat doesn't redirect back
        # to this view (which would fail since agent_charter was consumed)
        return_to = request.GET.get("return_to") or session_return_to or reverse("agents")
        app_url = build_immersive_chat_url(
            request,
            result.agent.id,
            return_to=return_to,
            embed=embed,
        )
        response = redirect(app_url)

        # Clear the OAuth charter cookie if present (no longer needed)
        if OAUTH_CHARTER_COOKIE in request.COOKIES:
            response.delete_cookie(OAUTH_CHARTER_COOKIE)

        return response


class AgentEnableSmsView(LoginRequiredMixin, PhoneNumberMixin, TemplateView):
    """Enable SMS communication for an existing agent."""

    template_name = "console/agent_enable_sms.html"

    def dispatch(self, request, *args, **kwargs):
        self.agent = get_object_or_404(
            PersistentAgent.objects.non_eval(),
            pk=kwargs["pk"],
            user=request.user,
        )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        phone = self._current_phone()
        if SKIP_VERIFIED_SMS_SCREEN and phone and phone.is_verified:
            return self._enable_sms_and_redirect(phone)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        resp = self._handle_phone_post()
        if resp:
            return resp

        if "enable_sms" in request.POST:
            phone = self._current_phone()
            if not phone or not phone.is_verified:
                messages.error(request, "Please verify a phone number before enabling SMS.")
                return redirect(request.path)
            return self._enable_sms_and_redirect(phone)

        return super().get(request, *args, **kwargs)

    def _enable_sms_and_redirect(self, phone: UserPhoneNumber):
        try:
            enable_agent_sms_contact(self.agent, phone)
        except ValidationError as exc:
            message_text = exc.messages[0] if getattr(exc, "messages", None) else "Error enabling SMS."
            messages.error(self.request, message_text)
            return redirect("agent_detail", pk=self.agent.pk)
        except Exception as exc:
            logger.exception("Error enabling SMS", exc_info=True)
            messages.error(
                self.request,
                f"Error enabling SMS: {str(exc)}",
            )
            return redirect("agent_detail", pk=self.agent.pk)

        messages.success(self.request, "SMS has been enabled for this agent.")
        return redirect("agent_detail", pk=self.agent.pk)

class AgentDetailView(ConsoleViewMixin, DetailView):
    """Configuration page for a single agent.

    Uses ConsoleViewMixin to respect the current console context. When in
    organization context, only agents belonging to that organization are
    visible. In personal context, only the user's personal agents (no org)
    are visible.
    """
    model = PersistentAgent
    template_name = "console/agent_detail.html"
    context_object_name = "agent"
    pk_url_kwarg = "pk"

    @tracer.start_as_current_span("CONSOLE Agent Detail View - get_object")
    def get_queryset(self):
        """Scope agents to the active console context.

        - Organization context: agents owned by the org, and only if the user
          is an active member of that organization.
        - Personal context: user-owned agents without an organization.
        """
        qs = super().get_queryset().select_related('user__billing')

        context_type = self.request.session.get('context_type', 'personal')
        if context_type == 'organization':
            org_id = self.request.session.get('context_id')
            # Verify membership; if not a member, return no rows to force 404
            if not OrganizationMembership.objects.filter(
                user=self.request.user,
                org_id=org_id,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).exists():
                return qs.none()

            return qs.filter(organization_id=org_id)

        # Personal context
        return qs.filter(user=self.request.user, organization__isnull=True)

    @tracer.start_as_current_span("CONSOLE Agent Detail View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add the primary email to the context."""
        context = super().get_context_data(**kwargs)
        agent = self.get_object()
        
        # Find the primary email endpoint for this agent
        primary_email = agent.comms_endpoints.filter(
            channel=CommsChannel.EMAIL, is_primary=True
        ).first()

        primary_sms = agent.comms_endpoints.filter(
            channel=CommsChannel.SMS, is_primary=True
        ).first()

        context['primary_email'] = primary_email
        context['primary_sms'] = primary_sms

        owner = agent.organization or agent.user
        browser_agent = getattr(agent, "browser_use_agent", None)
        preferred_proxy = browser_agent.preferred_proxy if browser_agent else None
        multi_assign = is_multi_assign_enabled()

        dedicated_total = 0
        dedicated_available = 0
        dedicated_options: list[dict[str, object]] = []

        if owner:
            allocated_qs = (
                DedicatedProxyService.allocated_proxies(owner)
                .select_related("dedicated_allocation")
                .prefetch_related("browser_agents__persistent_agent")
                .order_by("static_ip", "host", "port")
            )
            dedicated_total = allocated_qs.count()

            for proxy in allocated_qs:
                browser_agents = list(getattr(proxy, "browser_agents").all())
                assigned_agents = [
                    ba.persistent_agent
                    for ba in browser_agents
                    if getattr(ba, "persistent_agent", None) is not None
                ]
                selected = preferred_proxy is not None and proxy.id == preferred_proxy.id
                in_use_elsewhere = any(
                    pa.id != agent.id for pa in assigned_agents if pa is not None
                )
                label = proxy.static_ip or proxy.host
                assigned_names = [pa.name for pa in assigned_agents if pa is not None]

                dedicated_options.append(
                    {
                        "id": str(proxy.id),
                        "label": label,
                        "selected": selected,
                        "in_use_elsewhere": in_use_elsewhere,
                        "assigned_names": assigned_names,
                        "disabled": (not multi_assign and in_use_elsewhere and not selected),
                    }
                )

            if multi_assign:
                dedicated_available = dedicated_total
            else:
                dedicated_available = sum(
                    1
                    for option in dedicated_options
                    if not option["in_use_elsewhere"] or option["selected"]
                )

        context['dedicated_proxy_options'] = dedicated_options
        context['selected_dedicated_proxy_id'] = (
            str(preferred_proxy.id) if preferred_proxy else ""
        )
        context['dedicated_ip_total'] = dedicated_total
        context['dedicated_ip_available'] = dedicated_available
        context['dedicated_ip_multi_assign'] = multi_assign
        context['dedicated_ip_owner_type'] = (
            'organization' if agent.organization_id else 'user'
        )

        # Always include allowlist configuration (flag removed)
        from api.models import CommsAllowlistEntry
        context['show_allowlist'] = True
        context['whitelist_policy'] = agent.whitelist_policy
        context['allowlist_entries'] = CommsAllowlistEntry.objects.filter(
            agent=agent
        ).order_by('channel', 'address')
        context['pending_invites'] = AgentAllowlistInvite.objects.filter(
            agent=agent,
            status=AgentAllowlistInvite.InviteStatus.PENDING
        ).order_by('channel', 'address')

        # Count active allowlist entries AND pending invitations for display
        allowlist_active_count = CommsAllowlistEntry.objects.filter(
            agent=agent,
            is_active=True
        ).count()
        allowlist_pending_count = AgentAllowlistInvite.objects.filter(
            agent=agent,
            status=AgentAllowlistInvite.InviteStatus.PENDING
        ).count()
        total_contacts = allowlist_active_count + allowlist_pending_count
        contact_counts = get_agent_contact_counts(agent)
        if contact_counts is not None:
            total_contacts = contact_counts["total"]
        context['contact_counts'] = contact_counts
        context['active_allowlist_count'] = total_contacts
        max_contacts_limit = get_user_max_contacts_per_agent(
            agent.user,
            organization=agent.organization,
        )
        context['max_contacts_per_agent'] = max_contacts_limit

        max_contacts_override = None
        if agent.organization_id is None:
            try:
                billing = agent.user.billing
            except UserBilling.DoesNotExist:
                billing = None

            if (
                billing is not None
                and billing.max_contacts_per_agent is not None
                and billing.max_contacts_per_agent > 0
            ):
                max_contacts_override = int(billing.max_contacts_per_agent)
        context['max_contacts_per_agent_override'] = max_contacts_override
        context['allowlist_limit_reached'] = (
            max_contacts_limit > 0 and total_contacts >= max_contacts_limit
        )

        context['collaborators'] = AgentCollaborator.objects.filter(
            agent=agent,
        ).select_related('user').order_by('user__email')
        context['collaborator_invites'] = AgentCollaboratorInvite.objects.filter(
            agent=agent,
            status=AgentCollaboratorInvite.InviteStatus.PENDING,
            expires_at__gt=timezone.now(),
        ).order_by('email')

        context['can_manage_collaborators'] = self._can_manage_collaborators(self.request.user, agent)

        # Add pending contact requests count
        from api.models import CommsAllowlistRequest
        pending_contact_requests = CommsAllowlistRequest.objects.filter(
            agent=agent,
            status=CommsAllowlistRequest.RequestStatus.PENDING
        ).count()
        context['pending_contact_requests'] = pending_contact_requests

        context['agent_webhooks'] = agent.webhooks.order_by('name')

        # Add owner information for display
        context['owner_email'] = agent.user.email

        # Check if owner has verified phone for SMS display
        try:
            from api.models import UserPhoneNumber
            owner_phone = UserPhoneNumber.objects.filter(
                user=agent.user, 
                is_verified=True
            ).first()
            context['owner_phone'] = owner_phone.phone_number if owner_phone else None
        except:
            context['owner_phone'] = None

        # Provide organizations current user can reassign this agent into (owner/admin only)
        try:
            reassignable_orgs = Organization.objects.filter(
                organizationmembership__user=self.request.user,
                organizationmembership__status=OrganizationMembership.OrgStatus.ACTIVE,
                organizationmembership__role__in=[
                    OrganizationMembership.OrgRole.OWNER,
                    OrganizationMembership.OrgRole.ADMIN,
                ],
            ).order_by('name')
        except ImportError:
            reassignable_orgs = []

        context['reassignable_orgs'] = reassignable_orgs
        context['can_reassign'] = True

        peer_links_qs = (
            AgentPeerLink.objects.filter(Q(agent_a=agent) | Q(agent_b=agent))
            .select_related("agent_a", "agent_b")
            .prefetch_related("communication_states")
            .order_by("created_at")
        )

        peer_links: list[dict] = []
        linked_agent_ids: set = set()
        for link in peer_links_qs:
            counterpart = link.get_other_agent(agent)
            linked_agent_ids.add(link.agent_a_id)
            linked_agent_ids.add(link.agent_b_id)

            state = next(
                (s for s in link.communication_states.all() if s.channel == CommsChannel.OTHER),
                None,
            )

            peer_links.append(
                {
                    "link": link,
                    "counterpart": counterpart,
                    "state": state,
                }
            )

        context['peer_links'] = peer_links

        linked_agent_ids.discard(agent.id)
        if agent.organization_id:
            candidate_qs = PersistentAgent.objects.non_eval().filter(
                organization_id=agent.organization_id
            )
        else:
            candidate_qs = PersistentAgent.objects.non_eval().filter(
                user=agent.user,
                organization__isnull=True,
            )

        candidate_qs = candidate_qs.exclude(id=agent.id)
        if linked_agent_ids:
            candidate_qs = candidate_qs.exclude(id__in=linked_agent_ids)
        context['peer_link_candidates'] = candidate_qs.order_by('name')
        context['peer_link_defaults'] = {
            'messages_per_window': 30,
            'window_hours': 6,
        }

        server_overview = mcp_server_service.agent_server_overview(agent)
        context['inherited_mcp_servers'] = [s for s in server_overview if s.get('inherited')]
        context['organization_mcp_servers'] = [
            s for s in server_overview if s.get('scope') == MCPServerConfig.Scope.ORGANIZATION
        ]
        personal_servers = [s for s in server_overview if s.get('scope') == MCPServerConfig.Scope.USER]
        context['personal_mcp_servers'] = personal_servers
        context['show_personal_mcp_form'] = agent.organization_id is None and bool(personal_servers)

        context.update(build_agent_daily_credit_context(agent, owner))

        pending_transfer = AgentTransferInvite.objects.filter(
            agent=agent,
            status=AgentTransferInvite.Status.PENDING,
        ).first()
        context['pending_transfer_invite'] = pending_transfer

        context['agent_detail_props'] = self._build_agent_detail_props(context)

        return context

    def _serialize_allowlist_state(
        self,
        agent: PersistentAgent,
        *,
        entries=None,
        pending_invites=None,
        owner_email: str | None = None,
        owner_phone: str | None = None,
        active_count: int | None = None,
        pending_count: int | None = None,
        total_count: int | None = None,
        max_contacts: int | None = None,
        pending_contact_requests: int | None = None,
        email_verified: bool | None = None,
    ) -> dict[str, object]:
        from api.models import CommsAllowlistEntry, AgentAllowlistInvite
        from api.services.email_verification import has_verified_email

        entries_qs = entries
        if entries_qs is None:
            entries_qs = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
        entries_list = list(entries_qs)

        pending_qs = pending_invites
        if pending_qs is None:
            pending_qs = AgentAllowlistInvite.objects.filter(
                agent=agent,
                status=AgentAllowlistInvite.InviteStatus.PENDING,
            ).order_by('channel', 'address')
        pending_list = list(pending_qs)

        if owner_email is None:
            owner_email = agent.user.email

        if owner_phone is None:
            phone_obj = UserPhoneNumber.objects.filter(user=agent.user, is_verified=True).first()
            owner_phone = phone_obj.phone_number if phone_obj else None

        if active_count is None:
            active_count = CommsAllowlistEntry.objects.filter(agent=agent, is_active=True).count()
        if pending_count is None:
            pending_count = AgentAllowlistInvite.objects.filter(
                agent=agent,
                status=AgentAllowlistInvite.InviteStatus.PENDING,
            ).count()
        if email_verified is None:
            email_verified = has_verified_email(agent.user)

        display_total = total_count
        if display_total is None:
            display_total = (active_count or 0) + (pending_count or 0)

        return {
            'show': True,
            'ownerEmail': owner_email,
            'ownerPhone': owner_phone,
            'entries': [
                {
                    'id': str(entry.id),
                    'channel': entry.channel,
                    'address': entry.address,
                    'allowInbound': entry.allow_inbound,
                    'allowOutbound': entry.allow_outbound,
                }
                for entry in entries_list
            ],
            'pendingInvites': [
                {
                    'id': str(invite.id),
                    'channel': invite.channel,
                    'address': invite.address,
                    'allowInbound': invite.allow_inbound,
                    'allowOutbound': invite.allow_outbound,
                }
                for invite in pending_list
            ],
            'activeCount': display_total,
            'maxContacts': max_contacts,
            'pendingContactRequests': pending_contact_requests,
            'emailVerified': email_verified,
        }

    def _serialize_collaborator_state(
        self,
        agent: PersistentAgent,
        *,
        collaborators=None,
        pending_invites=None,
        counts: dict[str, int] | None = None,
        max_contacts: int | None = None,
        can_manage: bool | None = None,
    ) -> dict[str, object]:
        if collaborators is None:
            collaborators = (
                AgentCollaborator.objects
                .filter(agent=agent)
                .select_related("user")
                .order_by("user__email")
            )
        if pending_invites is None:
            pending_invites = AgentCollaboratorInvite.objects.filter(
                agent=agent,
                status=AgentCollaboratorInvite.InviteStatus.PENDING,
                expires_at__gt=timezone.now(),
            ).order_by("email")

        collab_list = [
            {
                "id": str(collab.id),
                "userId": str(collab.user_id),
                "email": collab.user.email if collab.user else "",
                "name": (
                    collab.user.get_full_name()
                    or collab.user.username
                    or collab.user.email
                )
                if collab.user
                else "",
            }
            for collab in list(collaborators)
        ]

        pending_list = [
            {
                "id": str(invite.id),
                "email": invite.email,
                "invitedAtIso": invite.created_at.isoformat() if invite.created_at else None,
                "expiresAtIso": invite.expires_at.isoformat() if invite.expires_at else None,
            }
            for invite in list(pending_invites)
        ]

        if counts is None:
            counts = get_agent_contact_counts(agent) or {}

        return {
            "entries": collab_list,
            "pendingInvites": pending_list,
            "activeCount": int(counts.get("collaborators_active", 0) or 0),
            "pendingCount": int(counts.get("collaborators_pending", 0) or 0),
            "totalCount": int(counts.get("total", 0) or 0),
            "maxContacts": max_contacts,
            "canManage": bool(can_manage),
        }

    def _can_manage_collaborators(self, user, agent: PersistentAgent) -> bool:
        if agent.user_id == user.id:
            return True
        if agent.organization_id:
            return OrganizationMembership.objects.filter(
                org=agent.organization,
                user=user,
                status=OrganizationMembership.OrgStatus.ACTIVE,
                role__in=[
                    OrganizationMembership.OrgRole.OWNER,
                    OrganizationMembership.OrgRole.ADMIN,
                ],
            ).exists()
        return False

    def _build_mcp_servers_payload(
        self,
        request: HttpRequest,
        agent: PersistentAgent,
        *,
        server_overview: list[dict[str, object]] | None = None,
        current_context: dict[str, object] | None = None,
        can_manage_org_agents: bool | None = None,
    ) -> dict[str, object]:
        if server_overview is None:
            server_overview = mcp_server_service.agent_server_overview(agent)

        inherited_servers = [
            {
                'id': str(server.get('id')),
                'displayName': server.get('display_name'),
                'description': server.get('description'),
                'scope': server.get('scope'),
                'inherited': bool(server.get('inherited')),
                'assigned': bool(server.get('assigned')),
            }
            for server in server_overview
            if server.get('inherited')
        ]

        organization_servers = [
            {
                'id': str(server.get('id')),
                'displayName': server.get('display_name'),
                'description': server.get('description'),
                'scope': server.get('scope'),
                'inherited': bool(server.get('inherited')),
                'assigned': bool(server.get('assigned')),
            }
            for server in server_overview
            if server.get('scope') == MCPServerConfig.Scope.ORGANIZATION
        ]

        personal_servers = [
            {
                'id': str(server.get('id')),
                'displayName': server.get('display_name'),
                'description': server.get('description'),
                'assigned': bool(server.get('assigned')),
            }
            for server in server_overview
            if server.get('scope') == MCPServerConfig.Scope.USER
        ]

        if current_context is None or can_manage_org_agents is None:
            resolved = build_console_context(request)
            current_context = {
                'type': resolved.current_context.type,
            }
            can_manage_org_agents = resolved.can_manage_org_agents

        can_manage = False
        if current_context.get('type') == 'personal' or can_manage_org_agents:
            can_manage = True

        return {
            'inherited': inherited_servers,
            'organization': organization_servers,
            'personal': personal_servers,
            'showPersonalForm': agent.organization_id is None and bool(personal_servers),
            'canManage': can_manage,
            'manageUrl': reverse('console-mcp-servers'),
        }

    def _build_webhooks_payload(self, agent: PersistentAgent) -> list[dict[str, str]]:
        return [
            {
                'id': str(webhook.id),
                'name': webhook.name,
                'url': webhook.url,
            }
            for webhook in agent.webhooks.order_by('name')
        ]

    def _build_peer_links_payload(self, agent: PersistentAgent) -> dict[str, object]:
        peer_links_qs = (
            AgentPeerLink.objects.filter(Q(agent_a=agent) | Q(agent_b=agent))
            .select_related("agent_a", "agent_b")
            .prefetch_related("communication_states")
            .order_by("created_at")
        )

        entries: list[dict[str, object]] = []
        linked_agent_ids: set[str] = set()

        for link in peer_links_qs:
            counterpart = link.get_other_agent(agent)
            if counterpart:
                linked_agent_ids.add(str(counterpart.id))
            linked_agent_ids.add(str(link.agent_a_id))
            linked_agent_ids.add(str(link.agent_b_id))

            state = next(
                (s for s in link.communication_states.all() if s.channel == CommsChannel.OTHER),
                None,
            )

            entries.append(
                {
                    'id': str(link.id),
                    'counterpartId': str(counterpart.id) if counterpart else None,
                    'counterpartName': counterpart.name if counterpart else None,
                    'isEnabled': link.is_enabled,
                    'messagesPerWindow': link.messages_per_window,
                    'windowHours': link.window_hours,
                    'featureFlag': link.feature_flag,
                    'createdOnLabel': date_format(timezone.localtime(link.created_at), "M j, Y"),
                    'state': (
                        {
                            'creditsRemaining': state.credits_remaining,
                            'windowResetLabel': date_format(timezone.localtime(state.window_reset_at), "M j, Y H:i"),
                        }
                        if state
                        else None
                    ),
                }
            )

        linked_agent_ids.discard(str(agent.id))

        if agent.organization_id:
            candidate_qs = PersistentAgent.objects.non_eval().filter(organization_id=agent.organization_id)
        else:
            candidate_qs = PersistentAgent.objects.non_eval().filter(user=agent.user, organization__isnull=True)

        if linked_agent_ids:
            candidate_qs = candidate_qs.exclude(id__in=linked_agent_ids)

        candidates = [
            {
                'id': str(candidate.id),
                'name': candidate.name,
            }
            for candidate in candidate_qs.exclude(id=agent.id).order_by('name')
        ]

        return {
            'entries': entries,
            'candidates': candidates,
            'defaults': {
                'messagesPerWindow': 30,
                'windowHours': 6,
            },
        }

    def _build_agent_detail_props(self, context: dict[str, Any]) -> dict[str, Any]:
        agent: PersistentAgent = context['agent']
        request = self.request
        upgrade_url = None
        if settings.GOBII_PROPRIETARY_MODE:
            try:
                upgrade_url = reverse('proprietary:pricing')
            except NoReverseMatch:
                upgrade_url = None

        if agent.organization_id:
            owner = agent.organization
            owner_type = 'organization'
            organization = agent.organization
        else:
            owner = agent.user
            owner_type = 'user'
            organization = None

        llm_intelligence = build_llm_intelligence_props(owner, owner_type, organization, upgrade_url)

        def _datetime_iso(value):
            if not value:
                return None
            localized = timezone.localtime(value)
            return localized.isoformat()

        def _datetime_display(value, fmt: str):
            if not value:
                return None
            localized = timezone.localtime(value)
            return date_format(localized, fmt)

        daily_credits = serialize_daily_credit_payload(context)

        dedicated_options = [
            {
                'id': str(option.get('id')),
                'label': option.get('label'),
                'inUseElsewhere': bool(option.get('in_use_elsewhere')),
                'disabled': bool(option.get('disabled')),
                'assignedNames': option.get('assigned_names', []),
            }
            for option in context.get('dedicated_proxy_options', [])
        ]

        account_usage = (context.get('account') or {}).get('usage') or {}
        max_contacts = context.get('max_contacts_per_agent') or account_usage.get('max_contacts_per_agent')

        allowlist = self._serialize_allowlist_state(
            agent,
            entries=context.get('allowlist_entries'),
            pending_invites=context.get('pending_invites'),
            owner_email=context.get('owner_email'),
            owner_phone=context.get('owner_phone'),
            total_count=context.get('active_allowlist_count'),
            max_contacts=max_contacts,
            pending_contact_requests=context.get('pending_contact_requests'),
        )
        allowlist['show'] = bool(context.get('show_allowlist'))

        collaborators = self._serialize_collaborator_state(
            agent,
            collaborators=context.get('collaborators'),
            pending_invites=context.get('collaborator_invites'),
            counts=context.get('contact_counts'),
            max_contacts=max_contacts,
            can_manage=context.get('can_manage_collaborators'),
        )

        inherited_servers = [
            {
                'id': str(server.get('id')),
                'displayName': server.get('display_name'),
                'description': server.get('description'),
                'scope': server.get('scope'),
                'inherited': bool(server.get('inherited')),
                'assigned': bool(server.get('assigned')),
            }
            for server in context.get('inherited_mcp_servers', [])
        ]

        organization_servers = [
            {
                'id': str(server.get('id')),
                'displayName': server.get('display_name'),
                'description': server.get('description'),
                'scope': server.get('scope'),
                'inherited': bool(server.get('inherited')),
                'assigned': bool(server.get('assigned')),
            }
            for server in context.get('organization_mcp_servers', [])
        ]

        personal_servers = [
            {
                'id': str(server.get('id')),
                'displayName': server.get('display_name'),
                'description': server.get('description'),
                'assigned': bool(server.get('assigned')),
            }
            for server in context.get('personal_mcp_servers', [])
        ]

        peer_link_entries = []
        for entry in context.get('peer_links', []):
            link = entry['link']
            counterpart = entry.get('counterpart')
            state = entry.get('state')
            peer_link_entries.append(
                {
                    'id': str(link.id),
                    'counterpartId': str(counterpart.id) if counterpart else None,
                    'counterpartName': counterpart.name if counterpart else None,
                    'isEnabled': link.is_enabled,
                    'messagesPerWindow': link.messages_per_window,
                    'windowHours': link.window_hours,
                    'featureFlag': link.feature_flag,
                    'createdOnLabel': _datetime_display(link.created_at, "M j, Y"),
                    'state': (
                        {
                            'creditsRemaining': state.credits_remaining,
                            'windowResetLabel': _datetime_display(state.window_reset_at, "M j, Y H:i"),
                        }
                        if state
                        else None
                    ),
                }
            )

        peer_link_candidates = [
            {
                'id': str(candidate.id),
                'name': candidate.name,
            }
            for candidate in context.get('peer_link_candidates', [])
        ]

        peer_link_defaults = context.get('peer_link_defaults', {})

        pending_transfer = context.get('pending_transfer_invite')
        pending_transfer_payload = None
        if pending_transfer:
            pending_transfer_payload = {
                'toEmail': pending_transfer.to_email,
                'createdAtIso': _datetime_iso(pending_transfer.created_at),
                'createdAtDisplay': _datetime_display(pending_transfer.created_at, "M j, Y g:i A"),
            }

        primary_email = context.get('primary_email')
        primary_sms = context.get('primary_sms')

        palette = AgentColor.get_active_palette()
        agent_colors = [
            {
                'id': str(color.id),
                'name': color.name,
                'hex': color.hex_value.upper(),
            }
            for color in palette
        ]

        features = {
            'organizations': flag_is_active(request, 'organizations'),
        }

        can_reassign = bool(context.get('can_reassign'))
        reassignment = {
            'enabled': can_reassign,
            'canReassign': can_reassign,
            'organizations': [
                {
                    'id': str(org.id),
                    'name': org.name,
                }
                for org in context.get('reassignable_orgs', [])
            ],
            'assignedOrg': (
                {
                    'id': str(agent.organization_id),
                    'name': agent.organization.name,
                }
                if agent.organization_id
                else None
            ),
        }

        mcp_can_manage = False
        current_context = context.get('current_context', {}) or {}
        if current_context.get('type') == 'personal' or context.get('can_manage_org_agents'):
            mcp_can_manage = True

        webhooks = [
            {
                'id': str(webhook.id),
                'name': webhook.name,
                'url': webhook.url,
            }
            for webhook in context.get('agent_webhooks', [])
        ]

        mcp_manage_url = reverse('console-mcp-servers')

        return {
            'csrfToken': get_token(request),
            'urls': {
                'detail': request.path,
                'list': reverse('agents'),
                'chat': build_immersive_chat_url(request, agent.id, return_to=request.get_full_path()),
                'secrets': reverse('agent_secrets', args=[agent.id]),
                'emailSettings': reverse('agent_email_settings', args=[agent.id]),
                'manageFiles': reverse('agent_files', args=[agent.id]),
                'smsEnable': reverse('agent_enable_sms', args=[agent.id]),
                'contactRequests': reverse('agent_contact_requests', args=[agent.id]),
                'delete': reverse('agent_delete', args=[agent.id]),
                'mcpServersManage': mcp_manage_url,
            },
            'agent': {
                'id': str(agent.id),
                'name': agent.name,
                'charter': agent.charter,
                'avatarUrl': agent.get_avatar_url(),
                'isActive': agent.is_active,
                'createdAtDisplay': _datetime_display(agent.created_at, "F j, Y \a\t g:i A"),
                'pendingTransfer': pending_transfer_payload,
                'whitelistPolicy': agent.whitelist_policy,
                'preferredLlmTier': getattr(getattr(agent, 'preferred_llm_tier', None), 'key', AgentLLMTier.STANDARD.value),
                'agentColorHex': agent.get_display_color().upper(),
                'organization': (
                    {
                        'id': str(agent.organization_id),
                        'name': agent.organization.name,
                    }
                    if agent.organization_id
                    else None
                ),
            },
            'agentColors': agent_colors,
            'primaryEmail': {'address': primary_email.address} if primary_email else None,
            'primarySms': {'address': primary_sms.address} if primary_sms else None,
            'dailyCredits': daily_credits,
            'dedicatedIps': {
                'total': context.get('dedicated_ip_total', 0),
                'available': context.get('dedicated_ip_available', 0),
                'multiAssign': bool(context.get('dedicated_ip_multi_assign')),
                'ownerType': context.get('dedicated_ip_owner_type') or 'user',
                'selectedId': context.get('selected_dedicated_proxy_id') or None,
                'options': dedicated_options,
                'organizationName': agent.organization.name if agent.organization_id else None,
            },
            'allowlist': allowlist,
            'collaborators': collaborators,
            'mcpServers': {
                'inherited': inherited_servers,
                'organization': organization_servers,
                'personal': personal_servers,
                'showPersonalForm': bool(context.get('show_personal_mcp_form')),
                'canManage': mcp_can_manage,
                'manageUrl': mcp_manage_url,
            },
            'peerLinks': {
                'entries': peer_link_entries,
                'candidates': peer_link_candidates,
                'defaults': {
                    'messagesPerWindow': peer_link_defaults.get('messages_per_window', 30),
                    'windowHours': peer_link_defaults.get('window_hours', 6),
                },
            },
            'webhooks': webhooks,
            'features': features,
            'reassignment': reassignment,
            'llmIntelligence': llm_intelligence,
        }

    @tracer.start_as_current_span("CONSOLE Agent Detail View - Post")
    def post(self, request, *args, **kwargs):
        """Handle agent configuration updates and allowlist management."""
        agent = self.get_object()
        self.object = agent
        owner = agent.organization or agent.user
        credit_settings = get_daily_credit_settings_for_owner(owner)
        max_contacts_per_agent = get_user_max_contacts_per_agent(
            agent.user,
            organization=agent.organization,
        )

        # Handle AJAX detection early so we can reuse for multiple branches
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest'

        peer_action = request.POST.get('peer_link_action')
        if peer_action:
            return self._handle_peer_link_action(request, agent, peer_action, ajax=is_ajax)

        webhook_action = request.POST.get('webhook_action')
        if webhook_action:
            return self._handle_webhook_action(request, agent, webhook_action, ajax=is_ajax)

        if request.POST.get('mcp_server_action'):
            return self._handle_mcp_server_update(request, agent, ajax=is_ajax)

        # Handle AJAX allowlist / reassignment operations
        # Check both modern header and legacy header for AJAX detection
        action = request.POST.get('action')
        ajax_actions = {
            'add_allowlist',
            'remove_allowlist',
            'cancel_invite',
            'add_collaborator',
            'remove_collaborator',
            'cancel_collaborator_invite',
            'reassign_org',
        }
        if is_ajax and action in ajax_actions:
            from api.models import CommsAllowlistEntry
            from django.db import IntegrityError

            if action == 'add_allowlist':
                channel = request.POST.get('channel', 'email')
                address = request.POST.get('address', '').strip()
                
                if not address:
                    return JsonResponse({'success': False, 'error': 'Address is required'})
                
                try:
                    # Check if they're already in the allowlist
                    existing_entry = CommsAllowlistEntry.objects.filter(
                        agent=agent,
                        channel=channel,
                        address=address
                    ).first()
                    
                    if existing_entry:
                        if existing_entry.is_active:
                            return JsonResponse({'success': False, 'error': 'This address is already in the allowlist'})
                        else:
                            # Reactivate the existing entry and update inbound/outbound settings
                            existing_entry.is_active = True
                            # Update inbound/outbound settings from POST or keep existing
                            allow_inbound = request.POST.get('allow_inbound')
                            allow_outbound = request.POST.get('allow_outbound')
                            if allow_inbound is not None:
                                existing_entry.allow_inbound = allow_inbound.lower() == 'true'
                            if allow_outbound is not None:
                                existing_entry.allow_outbound = allow_outbound.lower() == 'true'
                            existing_entry.save(update_fields=['is_active', 'allow_inbound', 'allow_outbound'])
                            entry = existing_entry
                    else:
                        # Directly create the allowlist entry (skip invitation process)
                        # Get inbound/outbound settings from POST or default to both
                        allow_inbound = request.POST.get('allow_inbound', 'true').lower() == 'true'
                        allow_outbound = request.POST.get('allow_outbound', 'true').lower() == 'true'
                        
                        entry = CommsAllowlistEntry.objects.create(
                            agent=agent,
                            channel=channel,
                            address=address,
                            is_active=True,
                            allow_inbound=allow_inbound,
                            allow_outbound=allow_outbound
                        )

                        contact_props = Analytics.with_org_properties(
                            {
                                'agent_id': str(agent.id),
                                'channel': channel,
                                'address': address,
                            },
                            organization=getattr(agent, "organization", None),
                        )
                        Analytics.track_event(
                            user_id=request.user.id,
                            event=AnalyticsEvent.AGENT_CONTACTS_APPROVED,
                            source=AnalyticsSource.WEB,
                            properties=contact_props.copy(),
                        )

                    from api.agent.tasks.process_events import process_agent_events_task
                    process_agent_events_task.delay(str(agent.id))
                    
                    # Switch agent to manual allowlist mode if not already
                    # (though it should already be manual with our new changes)
                    if agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
                        agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
                        agent.save(update_fields=['whitelist_policy'])
                    
                    # Render updated list
                    entries = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
                    
                    # We no longer create pending invites from the agent config page
                    # but there might be some from other flows, so we still check
                    pending_invites = AgentAllowlistInvite.objects.filter(
                        agent=agent, 
                        status=AgentAllowlistInvite.InviteStatus.PENDING
                    ).order_by('channel', 'address')
                    
                    # Add owner information for display
                    owner_email = agent.user.email
                    owner_phone = None
                    try:
                        from api.models import UserPhoneNumber
                        phone_obj = UserPhoneNumber.objects.filter(
                            user=agent.user, 
                            is_verified=True
                        ).first()
                        owner_phone = phone_obj.phone_number if phone_obj else None
                    except:
                        pass
                    
                    html = render_to_string('console/partials/_allowlist_entries_inline.html', {
                        'allowlist_entries': entries,
                        'pending_invites': pending_invites,
                        'owner_email': owner_email,
                        'owner_phone': owner_phone,
                    })
                    
                    # Count active entries for the counter
                    active_count = CommsAllowlistEntry.objects.filter(
                        agent=agent,
                        is_active=True
                    ).count()
                    
                    # Also count any remaining pending invitations from other flows
                    pending_count = AgentAllowlistInvite.objects.filter(
                        agent=agent,
                        status=AgentAllowlistInvite.InviteStatus.PENDING
                    ).count()
                    total_count = active_count + pending_count
                    contact_counts = get_agent_contact_counts(agent)
                    if contact_counts is not None:
                        total_count = contact_counts["total"]
                    allowlist_payload = self._serialize_allowlist_state(
                        agent,
                        entries=entries,
                        pending_invites=pending_invites,
                        owner_email=owner_email,
                        owner_phone=owner_phone,
                        active_count=active_count,
                        pending_count=pending_count,
                        total_count=total_count,
                        max_contacts=max_contacts_per_agent,
                    )
                    collaborators_payload = self._serialize_collaborator_state(
                        agent,
                        counts=contact_counts,
                        max_contacts=max_contacts_per_agent,
                        can_manage=self._can_manage_collaborators(request.user, agent),
                    )

                    return JsonResponse({
                        'success': True,
                        'html': html,
                        'active_count': total_count,
                        'allowlist': allowlist_payload,
                        'collaborators': collaborators_payload,
                    })
                    
                except ValidationError as e:
                    existing_entry = locals().get('existing_entry')
                    if existing_entry and existing_entry.pk:
                        existing_entry.refresh_from_db()
                    return JsonResponse({'success': False, 'error': _format_validation_error(e)})
                except IntegrityError:
                    return JsonResponse({'success': False, 'error': 'This address is already in the allowlist'})
                except Exception as e:
                    return JsonResponse({'success': False, 'error': str(e)})
            
            elif action == 'remove_allowlist':
                entry_id = request.POST.get('entry_id')
                
                try:
                    CommsAllowlistEntry.objects.filter(agent=agent, id=entry_id).delete()
                    
                    # Render updated list
                    entries = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
                    pending_invites = AgentAllowlistInvite.objects.filter(
                        agent=agent, 
                        status=AgentAllowlistInvite.InviteStatus.PENDING
                    ).order_by('channel', 'address')
                    
                    # Add owner information for display
                    owner_email = agent.user.email
                    owner_phone = None
                    try:
                        from api.models import UserPhoneNumber
                        phone_obj = UserPhoneNumber.objects.filter(
                            user=agent.user, 
                            is_verified=True
                        ).first()
                        owner_phone = phone_obj.phone_number if phone_obj else None
                    except:
                        pass
                    
                    html = render_to_string('console/partials/_allowlist_entries_inline.html', {
                        'allowlist_entries': entries,
                        'pending_invites': pending_invites,
                        'owner_email': owner_email,
                        'owner_phone': owner_phone,
                    })

                    # Count active entries AND pending invitations for the counter
                    active_count = CommsAllowlistEntry.objects.filter(
                        agent=agent,
                        is_active=True
                    ).count()
                    pending_count = AgentAllowlistInvite.objects.filter(
                        agent=agent,
                        status=AgentAllowlistInvite.InviteStatus.PENDING
                    ).count()
                    total_count = active_count + pending_count
                    contact_counts = get_agent_contact_counts(agent)
                    if contact_counts is not None:
                        total_count = contact_counts["total"]
                    allowlist_payload = self._serialize_allowlist_state(
                        agent,
                        entries=entries,
                        pending_invites=pending_invites,
                        owner_email=owner_email,
                        owner_phone=owner_phone,
                        active_count=active_count,
                        pending_count=pending_count,
                        total_count=total_count,
                        max_contacts=max_contacts_per_agent,
                    )

                    collaborators_payload = self._serialize_collaborator_state(
                        agent,
                        counts=contact_counts,
                        max_contacts=max_contacts_per_agent,
                        can_manage=self._can_manage_collaborators(request.user, agent),
                    )

                    return JsonResponse({
                        'success': True,
                        'html': html,
                        'active_count': total_count,
                        'allowlist': allowlist_payload,
                        'collaborators': collaborators_payload,
                    })
                    
                except Exception as e:
                    return JsonResponse({'success': False, 'error': str(e)})
            
            elif action == 'cancel_invite':
                invite_id = request.POST.get('invite_id')
                
                try:
                    # Find and delete the invitation
                    AgentAllowlistInvite.objects.filter(agent=agent, id=invite_id).delete()
                    
                    # Render updated list
                    entries = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
                    pending_invites = AgentAllowlistInvite.objects.filter(
                        agent=agent, 
                        status=AgentAllowlistInvite.InviteStatus.PENDING
                    ).order_by('channel', 'address')
                    
                    # Add owner information for display
                    owner_email = agent.user.email
                    owner_phone = None
                    try:
                        from api.models import UserPhoneNumber
                        phone_obj = UserPhoneNumber.objects.filter(
                            user=agent.user, 
                            is_verified=True
                        ).first()
                        owner_phone = phone_obj.phone_number if phone_obj else None
                    except:
                        pass
                    
                    html = render_to_string('console/partials/_allowlist_entries_inline.html', {
                        'allowlist_entries': entries,
                        'pending_invites': pending_invites,
                        'owner_email': owner_email,
                        'owner_phone': owner_phone,
                    })
                    
                    # Count active entries AND pending invitations for the counter
                    active_count = CommsAllowlistEntry.objects.filter(
                        agent=agent,
                        is_active=True
                    ).count()
                    pending_count = AgentAllowlistInvite.objects.filter(
                        agent=agent,
                        status=AgentAllowlistInvite.InviteStatus.PENDING
                    ).count()
                    total_count = active_count + pending_count
                    contact_counts = get_agent_contact_counts(agent)
                    if contact_counts is not None:
                        total_count = contact_counts["total"]
                    allowlist_payload = self._serialize_allowlist_state(
                        agent,
                        entries=entries,
                        pending_invites=pending_invites,
                        owner_email=owner_email,
                        owner_phone=owner_phone,
                        active_count=active_count,
                        pending_count=pending_count,
                        total_count=total_count,
                        max_contacts=max_contacts_per_agent,
                    )

                    collaborators_payload = self._serialize_collaborator_state(
                        agent,
                        counts=contact_counts,
                        max_contacts=max_contacts_per_agent,
                        can_manage=self._can_manage_collaborators(request.user, agent),
                    )

                    return JsonResponse({
                        'success': True,
                        'html': html,
                        'active_count': total_count,
                        'allowlist': allowlist_payload,
                        'collaborators': collaborators_payload,
                    })
                    
                except Exception as e:
                    return JsonResponse({'success': False, 'error': str(e)})

            elif action == 'add_collaborator':
                if not self._can_manage_collaborators(request.user, agent):
                    return JsonResponse({'success': False, 'error': 'Not authorized to invite collaborators.'}, status=403)

                email = (request.POST.get('email') or '').strip().lower()
                if not email:
                    return JsonResponse({'success': False, 'error': 'Email is required'})

                try:
                    from django.core.validators import validate_email
                    validate_email(email)
                except ValidationError:
                    return JsonResponse({'success': False, 'error': 'Enter a valid email address'})

                owner_email = (agent.user.email or '').strip().lower()
                if owner_email and email == owner_email:
                    return JsonResponse({'success': False, 'error': 'Owner already has access to this agent'})

                if agent.organization_id:
                    if OrganizationMembership.objects.filter(
                        org=agent.organization,
                        status=OrganizationMembership.OrgStatus.ACTIVE,
                        user__email__iexact=email,
                    ).exists():
                        return JsonResponse({'success': False, 'error': 'Organization members already have access'})

                if AgentCollaborator.objects.filter(agent=agent, user__email__iexact=email).exists():
                    return JsonResponse({'success': False, 'error': 'This collaborator already has access'})

                now = timezone.now()
                if AgentCollaboratorInvite.objects.filter(
                    agent=agent,
                    email__iexact=email,
                    status=AgentCollaboratorInvite.InviteStatus.PENDING,
                    expires_at__gt=now,
                ).exists():
                    return JsonResponse({'success': False, 'error': 'An invitation is already pending for this email'})

                AgentCollaboratorInvite.objects.filter(
                    agent=agent,
                    email__iexact=email,
                    status=AgentCollaboratorInvite.InviteStatus.PENDING,
                    expires_at__lte=now,
                ).update(status=AgentCollaboratorInvite.InviteStatus.EXPIRED)
                AgentCollaboratorInvite.objects.filter(
                    agent=agent,
                    email__iexact=email,
                    status=AgentCollaboratorInvite.InviteStatus.ACCEPTED,
                ).update(status=AgentCollaboratorInvite.InviteStatus.EXPIRED)

                try:
                    invite = AgentCollaboratorInvite.objects.create(
                        agent=agent,
                        email=email,
                        invited_by=request.user,
                        expires_at=timezone.now() + timedelta(days=7),
                    )
                except ValidationError as exc:
                    return JsonResponse({'success': False, 'error': _format_validation_error(exc)})

                accept_url = request.build_absolute_uri(
                    reverse('agent_collaborator_invite_accept', kwargs={'token': invite.token})
                )
                reject_url = request.build_absolute_uri(
                    reverse('agent_collaborator_invite_reject', kwargs={'token': invite.token})
                )
                context = {
                    'agent': agent,
                    'agent_owner': agent.user,
                    'collaborator_email': email,
                    'accept_url': accept_url,
                    'reject_url': reject_url,
                    'invite': invite,
                }
                subject = f"You've been invited to collaborate on {agent.name} on Gobii"
                text_body = render_to_string('emails/agent_collaborator_invite.txt', context)
                html_body = render_to_string('emails/agent_collaborator_invite.html', context)
                try:
                    send_mail(
                        subject,
                        text_body,
                        None,
                        [email],
                        html_message=html_body,
                        fail_silently=True,
                    )
                except Exception:
                    logger.warning("Failed to send collaborator invitation email to %s", email, exc_info=True)

                contact_counts = get_agent_contact_counts(agent)
                total_count = contact_counts["total"] if contact_counts is not None else None
                allowlist_payload = self._serialize_allowlist_state(
                    agent,
                    total_count=total_count,
                    max_contacts=max_contacts_per_agent,
                )
                collaborators_payload = self._serialize_collaborator_state(
                    agent,
                    counts=contact_counts,
                    max_contacts=max_contacts_per_agent,
                    can_manage=True,
                )

                return JsonResponse({'success': True, 'allowlist': allowlist_payload, 'collaborators': collaborators_payload})

            elif action == 'remove_collaborator':
                if not self._can_manage_collaborators(request.user, agent):
                    return JsonResponse({'success': False, 'error': 'Not authorized to remove collaborators.'}, status=403)

                collaborator_id = request.POST.get('collaborator_id')
                if not collaborator_id:
                    return JsonResponse({'success': False, 'error': 'Collaborator id is required'})

                AgentCollaborator.objects.filter(agent=agent, id=collaborator_id).delete()

                contact_counts = get_agent_contact_counts(agent)
                total_count = contact_counts["total"] if contact_counts is not None else None
                allowlist_payload = self._serialize_allowlist_state(
                    agent,
                    total_count=total_count,
                    max_contacts=max_contacts_per_agent,
                )
                collaborators_payload = self._serialize_collaborator_state(
                    agent,
                    counts=contact_counts,
                    max_contacts=max_contacts_per_agent,
                    can_manage=self._can_manage_collaborators(request.user, agent),
                )

                return JsonResponse({'success': True, 'allowlist': allowlist_payload, 'collaborators': collaborators_payload})

            elif action == 'cancel_collaborator_invite':
                if not self._can_manage_collaborators(request.user, agent):
                    return JsonResponse({'success': False, 'error': 'Not authorized to cancel invites.'}, status=403)

                invite_id = request.POST.get('invite_id')
                if not invite_id:
                    return JsonResponse({'success': False, 'error': 'Invite id is required'})

                AgentCollaboratorInvite.objects.filter(agent=agent, id=invite_id).delete()

                contact_counts = get_agent_contact_counts(agent)
                total_count = contact_counts["total"] if contact_counts is not None else None
                allowlist_payload = self._serialize_allowlist_state(
                    agent,
                    total_count=total_count,
                    max_contacts=max_contacts_per_agent,
                )
                collaborators_payload = self._serialize_collaborator_state(
                    agent,
                    counts=contact_counts,
                    max_contacts=max_contacts_per_agent,
                    can_manage=self._can_manage_collaborators(request.user, agent),
                )

                return JsonResponse({'success': True, 'allowlist': allowlist_payload, 'collaborators': collaborators_payload})

            elif action == 'reassign_org':
                # Reassign a user-owned agent to an organization, or move org-owned back to personal
                target_org_id = (request.POST.get('target_org_id') or '').strip() or None
                try:
                    result = reassign_agent_organization(request, agent, target_org_id)
                    if target_org_id:
                        messages.success(request, 'Agent assigned to organization.')
                    else:
                        messages.success(request, 'Agent moved to personal ownership.')
                    return JsonResponse({'success': True, **result})
                except PermissionDenied as exc:
                    return JsonResponse({'success': False, 'error': str(exc)}, status=403)
                except ValidationError as e:
                    err = e.messages[0] if hasattr(e, 'messages') and e.messages else str(e)
                    return JsonResponse({'success': False, 'error': err}, status=400)
                except Exception as e:
                    logger.exception("An error occurred during agent reassignment for agent %s", agent.id, e)
                    return JsonResponse({'success': False, 'error': 'An unexpected error occurred. Please try again.'}, status=500)
            
            return JsonResponse({'success': False, 'error': 'Invalid action'})

        # Handle regular form submission
        # Check if this is an allowlist action that shouldn't have gotten here
        action = action or ''
        if action in ['add_allowlist', 'remove_allowlist', 'cancel_invite', 'add_collaborator', 'remove_collaborator', 'cancel_collaborator_invite']:
            # This shouldn't happen, but if JavaScript failed, redirect back
            # Import messages here if needed
            from django.contrib import messages as django_messages
            django_messages.error(request, "Please enable JavaScript to manage contacts.")
            return redirect('agent_detail', pk=agent.pk)

        if action == 'transfer_agent':
            transfer_email = (request.POST.get('transfer_email') or '').strip()
            transfer_message = (request.POST.get('transfer_message') or '').strip()

            try:
                invite = AgentTransferService.initiate_transfer(
                    agent,
                    transfer_email,
                    request.user,
                    message=transfer_message,
                )
                try:
                    dashboard_url = request.build_absolute_uri(reverse('console-home'))
                    initiator_name = request.user.get_full_name() or request.user.email or "A Gobii user"
                    context = {
                        'agent': agent,
                        'invite': invite,
                        'recipient_email': invite.to_email,
                        'initiator_name': initiator_name,
                        'dashboard_url': dashboard_url,
                    }
                    text_body = render_to_string('emails/agent_transfer_invite.txt', context)
                    html_body = render_to_string('emails/agent_transfer_invite.html', context)
                    subject = f"{initiator_name} wants to transfer {agent.name} to you"
                    send_mail(
                        subject,
                        text_body,
                        None,
                        [invite.to_email],
                        html_message=html_body,
                        fail_silently=True,
                    )
                except Exception as email_exc:  # pragma: no cover - best effort
                    logger.warning(
                        "Failed to send transfer invite email to %s: %s",
                        invite.to_email,
                        email_exc,
                    )
            except ValidationError as exc:
                messages.error(request, '; '.join(exc.messages if hasattr(exc, 'messages') else exc.args))
                return redirect('agent_detail', pk=agent.pk)
            except AgentTransferError as exc:
                messages.error(request, str(exc))
                return redirect('agent_detail', pk=agent.pk)

            messages.success(
                request,
                f"Transfer invitation sent to {invite.to_email}. They'll need to sign in to accept it.",
            )
            return redirect('agent_detail', pk=agent.pk)

        if action == 'cancel_transfer_invite':
            updated = AgentTransferInvite.objects.filter(
                agent=agent,
                status=AgentTransferInvite.Status.PENDING,
            ).update(
                status=AgentTransferInvite.Status.CANCELLED,
                responded_at=timezone.now(),
            )
            if updated:
                messages.success(request, "Transfer invitation cancelled.")
            else:
                messages.info(request, "There is no pending transfer invitation to cancel.")
            return redirect('agent_detail', pk=agent.pk)

        def _general_error(message: str, status: int = 400):
            if is_ajax:
                return JsonResponse({'success': False, 'error': message}, status=status)
            messages.error(request, message)
            return redirect('agent_detail', pk=agent.pk)

        def _validate_avatar_file(file_obj: UploadedFile) -> str | None:
            max_bytes = 5 * 1024 * 1024  # 5 MB limit
            if file_obj.size and file_obj.size > max_bytes:
                return "Avatar must be smaller than 5 MB."

            allowed_content_types = {
                'image/png',
                'image/jpeg',
                'image/jpg',
                'image/webp',
                'image/gif',
            }
            content_type = (file_obj.content_type or "").lower()
            allowed_by_content_type = bool(content_type and content_type in allowed_content_types)
            if content_type and not allowed_by_content_type:
                return "Avatar must be a PNG, JPG, WebP, or GIF image."

            # Lightweight signature check to weed out non-image uploads
            try:
                head = file_obj.read(16)
                file_obj.seek(0)
            except (OSError, ValueError):
                head = b""

            is_image_signature = (
                head.startswith(b"\x89PNG")
                or head.startswith(b"\xff\xd8")
                or head.startswith(b"GIF8")
                or (len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP")
            )

            if not is_image_signature:
                return "Avatar must be a valid image file."

            return None

        new_name = request.POST.get('name', '').strip()
        new_charter = request.POST.get('charter', '').strip()
        # Checkbox inputs are only present in POST data when checked. Determine the desired
        # active state based on whether the "is_active" field was submitted.
        new_is_active = 'is_active' in request.POST
        raw_agent_color_hex = (request.POST.get('agent_color_hex') or '').strip()
        selected_agent_color = None
        if raw_agent_color_hex:
            normalized_agent_color_hex = _normalize_agent_color_hex(raw_agent_color_hex)
            if not normalized_agent_color_hex:
                return _general_error("Select a valid theme color.")
            selected_agent_color = AgentColor.objects.filter(
                hex_value__iexact=normalized_agent_color_hex,
                is_active=True,
            ).first()
            if selected_agent_color is None:
                return _general_error("Select a valid theme color.")

        # Handle whitelist policy update (flag removed)
        new_whitelist_policy = request.POST.get('whitelist_policy', '').strip()

        avatar_file = request.FILES.get('avatar')
        clear_avatar_flag = (request.POST.get('clear_avatar') or '').strip().lower() in {'1', 'true', 'yes', 'on'}

        if avatar_file:
            avatar_error = _validate_avatar_file(avatar_file)
            if avatar_error:
                return _general_error(avatar_error)
            # If an upload is present, ignore any clear flag
            clear_avatar_flag = False
        elif clear_avatar_flag and not agent.avatar:
            # No avatar to clear; ignore the flag
            clear_avatar_flag = False

        raw_limit = (request.POST.get('daily_credit_limit') or '').strip()
        slider_value = (request.POST.get('daily_credit_limit_slider') or '').strip()
        slider_bounds = get_daily_credit_slider_bounds(credit_settings)
        limit_source = raw_limit

        if not limit_source and slider_value:
            try:
                parsed_slider = Decimal(slider_value)
            except InvalidOperation:
                return _general_error("Enter a whole number for the daily credit soft target.")
            if parsed_slider >= slider_bounds["slider_unlimited_value"]:
                limit_source = ""
            else:
                limit_source = slider_value

        if not limit_source:
            new_daily_limit = None
        else:
            try:
                parsed_limit = Decimal(limit_source)
            except InvalidOperation:
                return _general_error("Enter a whole number for the daily credit soft target.")

            if parsed_limit != parsed_limit.to_integral_value(rounding=ROUND_DOWN):
                return _general_error("Enter a whole number for the daily credit soft target.")

            parsed_limit = parsed_limit.to_integral_value(rounding=ROUND_HALF_UP)
            if parsed_limit <= Decimal("0"):
                new_daily_limit = None
            else:
                slider_min = slider_bounds["slider_min"]
                slider_max = slider_bounds["slider_limit_max"]
                if parsed_limit < slider_min:
                    parsed_limit = slider_min
                if parsed_limit > slider_max:
                    parsed_limit = slider_max
                new_daily_limit = int(parsed_limit)

        if not new_name:
            return _general_error("Agent name cannot be empty.")

        if not new_charter:
            return _general_error("Agent assignment cannot be empty.")

        # Fetch the browser agent defensively; it may be missing due to historical corruption.
        browser_agent: BrowserUseAgent | None = None
        if agent.browser_use_agent_id:
            browser_agent = BrowserUseAgent.objects.filter(pk=agent.browser_use_agent_id).first()
            if browser_agent is None:
                logger.warning(
                    "BrowserUseAgent %s not found while updating PersistentAgent %s",
                    agent.browser_use_agent_id,
                    agent.id,
                )

        owner = agent.organization or agent.user
        organization = agent.organization if agent.organization_id else None
        owner_type = 'organization' if agent.organization_id else 'user'
        multi_assign = is_multi_assign_enabled()
        dedicated_proxy_id = (request.POST.get('dedicated_proxy_id') or '').strip()
        selected_proxy: ProxyServer | None = None

        # Capture previous values for analytics
        prev_name = agent.name
        prev_is_active = agent.is_active
        prev_daily_limit = agent.daily_credit_limit
        prev_hard_limit = agent.get_daily_credit_hard_limit()
        prev_preferred_tier = getattr(getattr(agent, "preferred_llm_tier", None), "key", AgentLLMTier.STANDARD.value)
        prev_agent_color_hex = agent.get_display_color().upper()
        prev_whitelist_policy = agent.whitelist_policy

        plan = None
        if owner is not None:
            try:
                if owner_type == 'organization' and organization is not None:
                    plan = get_organization_plan(organization)
                else:
                    plan = get_user_plan(owner)
            except Exception:
                plan = None

        allowed_llm_tier = max_allowed_tier_for_plan(plan, is_organization=(owner_type == 'organization'))
        can_edit_intelligence = bool(
            settings.GOBII_PROPRIETARY_MODE
            and owner is not None
            and (owner_type == 'organization' or allowed_llm_tier != AgentLLMTier.STANDARD)
        )
        current_preferred_tier_value = getattr(getattr(agent, "preferred_llm_tier", None), "key", AgentLLMTier.STANDARD.value)
        try:
            AgentLLMTier(current_preferred_tier_value)
        except ValueError:
            current_preferred_tier_value = AgentLLMTier.STANDARD.value

        preferred_tier_input = (request.POST.get('preferred_llm_tier') or '').strip()
        if not preferred_tier_input:
            preferred_tier_input = current_preferred_tier_value
        try:
            requested_preferred_tier = AgentLLMTier(preferred_tier_input)
        except ValueError:
            return _general_error("Select a valid intelligence level.")

        preferred_tier_changed = requested_preferred_tier.value != current_preferred_tier_value
        if preferred_tier_changed:
            if not can_edit_intelligence:
                if settings.GOBII_PROPRIETARY_MODE:
                    return _general_error("Upgrade your plan to adjust intelligence levels.")
                return _general_error("Intelligence levels are locked by deployment settings.")
            if TIER_ORDER[requested_preferred_tier] > TIER_ORDER[allowed_llm_tier]:
                return _general_error("That intelligence level isn't available for this plan.")

        resolved_preferred_tier = IntelligenceTier.objects.filter(key=requested_preferred_tier.value).first()
        if resolved_preferred_tier is None:
            return _general_error("Select a valid intelligence level.")

        if dedicated_proxy_id:
            if owner is None:
                return _general_error("Dedicated IPs require an account or organization owner.")
            try:
                selected_proxy = (
                    DedicatedProxyService.allocated_proxies(owner)
                    .select_related("dedicated_allocation")
                    .get(id=dedicated_proxy_id)
                )
            except ProxyServer.DoesNotExist:
                return _general_error("Invalid dedicated IP selection.")
            if browser_agent is None:
                return _general_error(
                    "Unable to assign a dedicated IP because the agent is missing its browser component."
                )
            if (
                not multi_assign
                and selected_proxy.browser_agents.exclude(persistent_agent=agent).exists()
            ):
                return _general_error("That dedicated IP is already assigned to another agent.")

        # Check for uniqueness, excluding the current agent's BrowserUseAgent (if present)
        exclude_pk = browser_agent.id if browser_agent else agent.browser_use_agent_id
        browser_name_conflict = BrowserUseAgent.objects.filter(
            user=request.user,
            name=new_name
        )
        if exclude_pk:
            browser_name_conflict = browser_name_conflict.exclude(pk=exclude_pk)
        if browser_name_conflict.exists():
            return _general_error(f"You already have an agent named '{new_name}'.")

        try:
            with transaction.atomic():
                old_avatar_name = agent.avatar.name if getattr(agent, "avatar", None) else None
                avatar_changed = False

                # Track which fields changed
                agent_fields_to_update = []
                browser_agent_fields_to_update = []

                # Update names if they changed
                if agent.name != new_name:
                    agent.name = new_name
                    if browser_agent is not None:
                        browser_agent.name = new_name
                    agent_fields_to_update.append('name')
                    if browser_agent is not None:
                        browser_agent_fields_to_update.append('name')

                # Update charter if it changed
                if agent.charter != new_charter:
                    agent.charter = new_charter
                    agent_fields_to_update.append('charter')

                # Update active status if it changed
                if agent.is_active != new_is_active:
                    agent.is_active = new_is_active
                    agent_fields_to_update.append('is_active')

                if selected_agent_color and agent.agent_color_id != selected_agent_color.id:
                    agent.agent_color = selected_agent_color
                    agent_fields_to_update.append('agent_color')

                # Update whitelist policy if provided and changed
                if new_whitelist_policy and agent.whitelist_policy != new_whitelist_policy:
                    if new_whitelist_policy in [choice[0] for choice in PersistentAgent.WhitelistPolicy.choices]:
                        agent.whitelist_policy = new_whitelist_policy
                        agent_fields_to_update.append('whitelist_policy')

                # Update daily credit limit if changed
                if agent.daily_credit_limit != new_daily_limit:
                    agent.daily_credit_limit = new_daily_limit
                    agent_fields_to_update.append('daily_credit_limit')

                if agent.preferred_llm_tier_id != resolved_preferred_tier.id:
                    agent.preferred_llm_tier = resolved_preferred_tier
                    agent_fields_to_update.append('preferred_llm_tier')

                if avatar_file:
                    agent.avatar = avatar_file
                    agent_fields_to_update.append('avatar')
                    avatar_changed = True
                elif clear_avatar_flag and agent.avatar:
                    agent.avatar = None
                    agent_fields_to_update.append('avatar')
                    avatar_changed = True

                if browser_agent is not None:
                    current_proxy_id = browser_agent.preferred_proxy_id
                    new_proxy_id = selected_proxy.id if selected_proxy else None
                    if current_proxy_id != new_proxy_id:
                        browser_agent.preferred_proxy = selected_proxy
                        if 'preferred_proxy' not in browser_agent_fields_to_update:
                            browser_agent_fields_to_update.append('preferred_proxy')

                # Mark interaction time and reactivate if previously expired
                agent.last_interaction_at = timezone.now()
                agent_fields_to_update.append('last_interaction_at')

                # Persist changes if needed
                if agent_fields_to_update:
                    if 'updated_at' not in agent_fields_to_update:
                        agent_fields_to_update.append('updated_at')
                    agent.save(update_fields=agent_fields_to_update)
                    logger.info("Updated agent %s fields: %s", agent.id, ", ".join(agent_fields_to_update))
                if browser_agent is not None and browser_agent_fields_to_update:
                    browser_agent.save(update_fields=browser_agent_fields_to_update)

                if 'charter' in agent_fields_to_update:
                    def _schedule_charter_artifacts() -> None:
                        try:
                            maybe_schedule_short_description(agent)
                        except Exception:
                            logger.exception(
                                "Failed to schedule short description generation after charter update for agent %s",
                                agent.id,
                            )
                        try:
                            maybe_schedule_agent_tags(agent)
                        except Exception:
                            logger.exception(
                                "Failed to schedule tag generation after charter update for agent %s",
                                agent.id,
                            )

                    transaction.on_commit(_schedule_charter_artifacts)

                # If agent was soft-expired, restore schedule (from snapshot if missing) and mark active
                if agent.life_state == PersistentAgent.LifeState.EXPIRED and agent.is_active:
                    fields = []
                    if agent.schedule_snapshot:
                        agent.schedule = agent.schedule_snapshot
                        fields.append('schedule')
                    agent.life_state = PersistentAgent.LifeState.ACTIVE
                    fields.append('life_state')
                    agent.save(update_fields=fields)

                if not is_ajax:
                    messages.success(request, "Agent updated successfully.")

                soft_value = float(new_daily_limit) if new_daily_limit is not None else None
                hard_limit_value = agent.get_daily_credit_hard_limit()
                changed_fields_for_analytics = [
                    field for field in agent_fields_to_update if field not in {'updated_at', 'last_interaction_at'}
                ]
                update_props = Analytics.with_org_properties(
                    {
                        'agent_id': str(agent.pk),
                        'agent_name': new_name,
                        'is_active': new_is_active,
                        'charter': new_charter,
                        'daily_credit_limit': soft_value,
                        'daily_credit_soft_target': soft_value,
                        'daily_credit_hard_limit': float(hard_limit_value) if hard_limit_value is not None else None,
                        'preferred_llm_tier': resolved_preferred_tier.key,
                        'agent_color_hex': agent.get_display_color().upper(),
                        'updated_fields': changed_fields_for_analytics,
                    },
                    organization=agent.organization,
                )
                if 'daily_credit_limit' in changed_fields_for_analytics:
                    update_props['previous_daily_credit_limit'] = (
                        float(prev_daily_limit) if prev_daily_limit is not None else None
                    )
                    update_props['previous_daily_credit_soft_target'] = (
                        float(prev_daily_limit) if prev_daily_limit is not None else None
                    )
                    update_props['previous_daily_credit_hard_limit'] = (
                        float(prev_hard_limit) if prev_hard_limit is not None else None
                    )
                if 'is_active' in changed_fields_for_analytics:
                    update_props['previous_is_active'] = prev_is_active
                if 'name' in changed_fields_for_analytics:
                    update_props['previous_name'] = prev_name
                if 'preferred_llm_tier' in changed_fields_for_analytics:
                    update_props['previous_preferred_llm_tier'] = prev_preferred_tier
                if 'agent_color' in changed_fields_for_analytics:
                    update_props['previous_agent_color_hex'] = prev_agent_color_hex
                if 'whitelist_policy' in changed_fields_for_analytics:
                    update_props['previous_whitelist_policy'] = prev_whitelist_policy
                Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.PERSISTENT_AGENT_UPDATED,
                    source=AnalyticsSource.WEB,
                    properties=update_props.copy(),
                )

                if avatar_changed:
                    new_avatar_name = agent.avatar.name if getattr(agent, "avatar", None) else None
                    if old_avatar_name and old_avatar_name != new_avatar_name:
                        transaction.on_commit(lambda name=old_avatar_name: default_storage.delete(name))
        except Exception as e:
            if is_ajax:
                return JsonResponse({'success': False, 'error': f"Error updating agent: {e}"}, status=500)
            messages.error(request, f"Error updating agent: {e}")
            return redirect('agent_detail', pk=agent.pk)

        if is_ajax:
            return JsonResponse({
                'success': True,
                'message': "Agent updated successfully.",
                'avatarUrl': agent.get_avatar_url(),
            })

        return redirect('agent_detail', pk=agent.pk)

    def _handle_webhook_action(self, request, agent: PersistentAgent, action: str, *, ajax: bool = False):
        redirect_response = redirect('agent_detail', pk=agent.pk)
        normalized_action = (action or "").lower()

        def _error_response(message: str, status: int = 400):
            if ajax:
                return JsonResponse({'success': False, 'error': message}, status=status)
            messages.error(request, message)
            return redirect_response

        def _success_response(message: str):
            if ajax:
                return JsonResponse(
                    {
                        'success': True,
                        'message': message,
                        'webhooks': self._build_webhooks_payload(agent),
                    }
                )
            messages.success(request, message)
            return redirect_response

        if normalized_action not in {"create", "update", "delete"}:
            return _error_response("Unsupported webhook action.")

        def _track_webhook_event(event_type: AnalyticsEvent, webhook_obj: PersistentAgentWebhook) -> None:
            props = Analytics.with_org_properties(
                {
                    'agent_id': str(agent.pk),
                    'agent_name': agent.name,
                    'webhook_id': str(webhook_obj.id),
                    'webhook_name': webhook_obj.name,
                },
                organization=agent.organization,
            )
            transaction.on_commit(
                lambda evt=event_type, properties=props: Analytics.track_event(
                    user_id=request.user.id,
                    event=evt,
                    source=AnalyticsSource.WEB,
                    properties=properties.copy(),
                )
            )

        if normalized_action == "delete":
            webhook_id = request.POST.get("webhook_id")
            if not webhook_id:
                return _error_response("Missing webhook identifier.")
            try:
                webhook = agent.webhooks.get(id=webhook_id)
            except PersistentAgentWebhook.DoesNotExist:
                return _error_response("Webhook not found or no longer exists.")

            _track_webhook_event(AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_DELETED, webhook)
            webhook.delete()
            return _success_response("Webhook removed.")

        name = (request.POST.get("webhook_name") or "").strip()
        url = (request.POST.get("webhook_url") or "").strip()
        if not name or not url:
            return _error_response("Webhook name and URL are required.")

        if normalized_action == "create":
            webhook = PersistentAgentWebhook(agent=agent, name=name, url=url)
        else:
            webhook_id = request.POST.get("webhook_id")
            if not webhook_id:
                return _error_response("Missing webhook identifier.")
            try:
                webhook = agent.webhooks.get(id=webhook_id)
            except PersistentAgentWebhook.DoesNotExist:
                return _error_response("Webhook not found or no longer exists.")
            webhook.name = name
            webhook.url = url

        try:
            webhook.full_clean()
            webhook.save()
        except ValidationError as exc:
            error_messages = []
            if hasattr(exc, "message_dict"):
                for values in exc.message_dict.values():
                    error_messages.extend(values)
            elif hasattr(exc, "messages"):
                error_messages.extend(exc.messages)
            else:
                error_messages.append(str(exc))

            message_text = "; ".join(error_messages) if error_messages else "Invalid data."
            return _error_response(f"Unable to save webhook: {message_text}")
        except IntegrityError:
            return _error_response("A webhook with that name already exists for this agent.")

        if normalized_action == "create":
            _track_webhook_event(AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_ADDED, webhook)
            return _success_response("Webhook created.")
        else:
            _track_webhook_event(AnalyticsEvent.PERSISTENT_AGENT_WEBHOOK_UPDATED, webhook)
            return _success_response("Webhook updated.")

    def _handle_mcp_server_update(self, request, agent: PersistentAgent, *, ajax: bool = False):
        redirect_response = redirect('agent_detail', pk=agent.pk)

        def _error_response(message: str, status: int = 400):
            if ajax:
                return JsonResponse({'success': False, 'error': message}, status=status)
            messages.error(request, message)
            return redirect_response

        def _success_response(message: str):
            if ajax:
                return JsonResponse(
                    {
                        'success': True,
                        'message': message,
                        'mcpServers': self._build_mcp_servers_payload(
                            request,
                            agent,
                        ),
                    }
                )
            messages.success(request, message)
            return redirect_response

        action = request.POST.get('mcp_server_action')
        if action == 'update_personal':
            if agent.organization_id:
                return _error_response("Personal MCP servers can only be configured for your own agents.")

            server_ids = request.POST.getlist('personal_servers')
            try:
                mcp_server_service.update_agent_personal_servers(
                    agent,
                    server_ids,
                    actor_user_id=request.user.id,
                    source=AnalyticsSource.WEB,
                )
            except ValueError as exc:
                return _error_response(str(exc))

            return _success_response("Personal MCP server access updated.")
        if action == 'update_org':
            if not agent.organization_id:
                return _error_response("Organization MCP servers can only be configured for organization agents.")
            server_ids = request.POST.getlist('org_servers')
            try:
                mcp_server_service.update_agent_org_servers(
                    agent,
                    server_ids,
                    actor_user_id=request.user.id,
                    source=AnalyticsSource.WEB,
                )
            except ValueError as exc:
                return _error_response(str(exc))

            return _success_response("Organization MCP server access updated.")

        return _error_response("Unsupported MCP server action.")

    def _handle_peer_link_action(self, request, agent: PersistentAgent, action: str, *, ajax: bool = False):
        redirect_response = redirect('agent_detail', pk=agent.pk)

        def _error_response(message: str, status: int = 400):
            if ajax:
                return JsonResponse({'success': False, 'error': message}, status=status)
            messages.error(request, message)
            return redirect_response

        def _success_response(message: str):
            if ajax:
                return JsonResponse(
                    {
                        'success': True,
                        'message': message,
                        'peerLinks': self._build_peer_links_payload(agent),
                    }
                )
            messages.success(request, message)
            return redirect_response

        def _track_peer_link_event(
            event_type: AnalyticsEvent,
            *,
            peer_agent: PersistentAgent | None,
            link_id: str,
            messages_per_window: int,
            window_hours: int,
            feature_flag: str | None,
            is_enabled: bool,
        ) -> None:
            props = {
                'agent_id': str(agent.pk),
                'agent_name': agent.name,
                'peer_link_id': link_id,
                'messages_per_window': messages_per_window,
                'window_hours': window_hours,
                'feature_flag': feature_flag or '',
                'is_enabled': is_enabled,
            }
            if peer_agent is not None:
                props['peer_agent_id'] = str(peer_agent.pk)
                props['peer_agent_name'] = peer_agent.name

            props = Analytics.with_org_properties(
                props,
                organization=agent.organization,
            )
            transaction.on_commit(
                lambda evt=event_type, properties=props: Analytics.track_event(
                    user_id=request.user.id,
                    event=evt,
                    source=AnalyticsSource.WEB,
                    properties=properties.copy(),
                )
            )

        try:
            if action == 'create':
                peer_agent_id = request.POST.get('peer_agent_id')
                if not peer_agent_id:
                    return _error_response('Select an agent to link.')

                try:
                    messages_per_window = int(request.POST.get('messages_per_window', 30))
                    window_hours = int(request.POST.get('window_hours', 6))
                except ValueError:
                    return _error_response('Quotas must be positive integers.')

                try:
                    peer_agent = PersistentAgent.objects.non_eval().get(id=peer_agent_id)
                except PersistentAgent.DoesNotExist:
                    return _error_response('Selected agent no longer exists.')

                new_link = AgentPeerLink(
                    agent_a=agent,
                    agent_b=peer_agent,
                    messages_per_window=messages_per_window,
                    window_hours=window_hours,
                    created_by=request.user,
                )

                try:
                    with transaction.atomic():
                        new_link.save()
                except IntegrityError:
                    return _error_response('A peer link already exists for these agents.')

                _track_peer_link_event(
                    AnalyticsEvent.PERSISTENT_AGENT_PEER_LINKED,
                    peer_agent=peer_agent,
                    link_id=str(new_link.id),
                    messages_per_window=new_link.messages_per_window,
                    window_hours=new_link.window_hours,
                    feature_flag=new_link.feature_flag,
                    is_enabled=new_link.is_enabled,
                )
                return _success_response('Peer agent link created.')

            if action == 'update':
                link_id = request.POST.get('link_id')
                if not link_id:
                    return _error_response('Missing peer link identifier.')

                try:
                    with transaction.atomic():
                        link = AgentPeerLink.objects.select_for_update().prefetch_related('communication_states').get(id=link_id)
                        if agent.id not in {link.agent_a_id, link.agent_b_id}:
                            return _error_response('You do not have permission to update this link.')

                        if 'messages_per_window' in request.POST:
                            link.messages_per_window = int(request.POST.get('messages_per_window', link.messages_per_window))
                        if 'window_hours' in request.POST:
                            link.window_hours = int(request.POST.get('window_hours', link.window_hours))
                        if link.messages_per_window < 1 or link.window_hours < 1:
                            raise ValueError
                        if 'feature_flag' in request.POST:
                            link.feature_flag = (request.POST.get('feature_flag') or '').strip()
                        link.is_enabled = 'is_enabled' in request.POST
                        link.save()

                        for state in link.communication_states.all():
                            updates = []
                            if state.messages_per_window != link.messages_per_window:
                                state.messages_per_window = link.messages_per_window
                                updates.append('messages_per_window')
                            if state.window_hours != link.window_hours:
                                state.window_hours = link.window_hours
                                updates.append('window_hours')
                            if state.credits_remaining > link.messages_per_window:
                                state.credits_remaining = link.messages_per_window
                                updates.append('credits_remaining')
                            if updates:
                                updates.append('updated_at')
                                state.save(update_fields=updates)

                except AgentPeerLink.DoesNotExist:
                    return _error_response('Peer link not found.')

                return _success_response('Peer link updated.')

            if action == 'delete':
                link_id = request.POST.get('link_id')
                if not link_id:
                    return _error_response('Missing peer link identifier.')

                with transaction.atomic():
                    link = AgentPeerLink.objects.select_related('conversation').get(id=link_id)
                    if agent.id not in {link.agent_a_id, link.agent_b_id}:
                        return _error_response('You do not have permission to remove this link.')
                    peer_agent = link.agent_a if link.agent_a_id != agent.id else link.agent_b
                    link_snapshot = {
                        'link_id': str(link.id),
                        'messages_per_window': link.messages_per_window,
                        'window_hours': link.window_hours,
                        'feature_flag': link.feature_flag,
                        'is_enabled': link.is_enabled,
                    }

                    AgentCommPeerState.objects.filter(link=link).delete()
                    try:
                        conversation = link.conversation
                    except PersistentAgentConversation.DoesNotExist:
                        conversation = None
                    if conversation:
                        conversation.peer_link = None
                        conversation.is_peer_dm = False
                        conversation.save(update_fields=['peer_link', 'is_peer_dm'])

                    link.delete()

                _track_peer_link_event(
                    AnalyticsEvent.PERSISTENT_AGENT_PEER_UNLINKED,
                    peer_agent=peer_agent,
                    **link_snapshot,
                )
                return _success_response('Peer link removed.')

            return _error_response('Unsupported peer link action.')

        except AgentPeerLink.DoesNotExist:
            return _error_response('Peer link not found.')
        except ValueError:
            return _error_response('Invalid values supplied for peer link settings.')
        except ValidationError as exc:
            return _error_response('; '.join(exc.messages))
        except Exception as exc:
            logger.exception('Peer link operation failed for agent %s', agent.id, exc_info=True)
            return _error_response(f'Peer link operation failed: {exc}', status=500)


class ConsoleDiagnosticsView(ConsoleViewMixin, TemplateView):
    template_name = "console/diagnostics.html"

    def post(self, request, *args, **kwargs):  # pragma: no cover - view is read-only
        return HttpResponseNotAllowed(['GET'])


class ConsoleUsageView(ConsoleViewMixin, TemplateView):
    template_name = "console/usage.html"

    def get(self, request, *args, **kwargs):
        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.CONSOLE_USAGE_VIEWED,
            source=AnalyticsSource.WEB,
        )
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):  # pragma: no cover - view is read-only
        return HttpResponseNotAllowed(['GET'])


class ConsoleLLMConfigView(SystemAdminRequiredMixin, TemplateView):
    template_name = "console/llm_config.html"

    def post(self, request, *args, **kwargs):  # pragma: no cover - read-only shell
        return HttpResponseNotAllowed(['GET'])


class ConsoleEvalsView(SystemAdminRequiredMixin, TemplateView):
    template_name = "console/evals.html"

    def post(self, request, *args, **kwargs):  # pragma: no cover - read-only shell
        return HttpResponseNotAllowed(['GET'])


class ConsoleEvalsDetailView(SystemAdminRequiredMixin, TemplateView):
    template_name = "console/evals_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["suite_run_id"] = kwargs.get("suite_run_id")
        return context

    def post(self, request, *args, **kwargs):  # pragma: no cover - read-only shell
        return HttpResponseNotAllowed(['GET'])


class StaffAgentAuditView(SystemAdminRequiredMixin, TemplateView):
    template_name = "console/staff_agent_audit.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agent_id = kwargs.get("agent_id")
        agent = get_object_or_404(PersistentAgent, pk=agent_id)
        context["agent"] = agent
        return context


class MCPServerOwnerMixin:
    """Shared owner resolution logic for MCP server management views."""

    owner_scope: str | None = None
    owner_user = None
    owner_org = None

    def dispatch(self, request, *args, **kwargs):
        self.owner_scope, self.owner_user, self.owner_org = self._resolve_owner()
        return super().dispatch(request, *args, **kwargs)

    def _resolve_owner(self):
        context = build_console_context(self.request)
        if context.current_context.type == 'organization':
            membership = context.current_membership
            if membership is None or not context.can_manage_org_agents:
                raise PermissionDenied("You do not have permission to manage organization MCP servers.")
            return ('organization', None, membership.org)
        return ('user', self.request.user, None)

    def get_mcp_servers_queryset(self):
        if self.owner_scope == 'organization':
            return MCPServerConfig.objects.filter(
                scope=MCPServerConfig.Scope.ORGANIZATION,
                organization=self.owner_org,
            ).order_by('display_name')
        return MCPServerConfig.objects.filter(
            scope=MCPServerConfig.Scope.USER,
            user=self.owner_user,
        ).order_by('display_name')

    def get_owner_label(self):
        if self.owner_scope == 'organization' and self.owner_org:
            return self.owner_org.name
        return self.request.user.get_full_name() or self.request.user.username


class MCPServerManagementView(MCPServerOwnerMixin, ConsoleViewMixin, TemplateView):
    template_name = "console/mcp_servers.html"

    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'owner_scope': self.owner_scope,
                'owner_label': self.get_owner_label(),
            }
        )
        return context


class MCPOAuthCallbackPageView(ConsoleViewMixin, TemplateView):
    """Landing page shown after external OAuth redirects back to Gobii."""

    template_name = "console/mcp_oauth_callback.html"


class AgentEmailOAuthCallbackPageView(ConsoleViewMixin, TemplateView):
    """Landing page shown after email OAuth redirects back to Gobii."""

    template_name = "console/agent_email_oauth_callback.html"


class SharedAgentAccessMixin:
    def get_object(self, queryset=None):
        agent_id = self.kwargs.get(self.pk_url_kwarg)
        if not agent_id:
            raise Http404
        agent = resolve_agent_for_request(self.request, agent_id, allow_shared=True)
        self._can_manage_agent = user_can_manage_agent(self.request.user, agent)
        self._is_collaborator = user_is_collaborator(self.request.user, agent)
        return agent

    @property
    def can_manage_agent(self) -> bool:
        return bool(getattr(self, "_can_manage_agent", False))

    @property
    def is_collaborator(self) -> bool:
        return bool(getattr(self, "_is_collaborator", False))


class PersistentAgentChatShellView(SharedAgentAccessMixin, ConsoleViewMixin, DetailView):
    model = PersistentAgent
    context_object_name = "agent"
    pk_url_kwarg = "pk"
    template_name = "console/persistent_agent_chat_shell.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        immersive = (self.request.GET.get("immersive") or "").lower() in {"1", "true", "yes"}
        context["immersive"] = immersive
        if immersive:
            context["body_class"] = "min-h-screen bg-white"
        return context

    def post(self, request, *args, **kwargs):  # pragma: no cover - view is read-only
        return HttpResponseNotAllowed(['GET'])


class AgentAvatarProxyView(SharedAgentAccessMixin, ConsoleViewMixin, DetailView):
    model = PersistentAgent
    context_object_name = "agent"
    pk_url_kwarg = "pk"
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        agent = self.get_object()
        file_field = getattr(agent, "avatar", None)
        if not file_field or not getattr(file_field, "name", None):
            raise Http404("Avatar not found.")

        storage = file_field.storage
        name = file_field.name
        if hasattr(storage, "exists") and not storage.exists(name):
            raise Http404("Avatar not found.")

        try:
            file_handle = storage.open(name, "rb")
        except (FileNotFoundError, OSError):
            raise Http404("Avatar not found.")

        content_type, encoding = mimetypes.guess_type(name)
        response = FileResponse(file_handle, content_type=content_type or "application/octet-stream")
        response["Cache-Control"] = "private, max-age=300"
        if encoding:
            response["Content-Encoding"] = encoding
        return response


class AgentAllowlistView(LoginRequiredMixin, TemplateView):
    """Manage manual allowlist and policy for an agent."""
    template_name = "console/agent_allowlist.html"

    def _get_agent(self):
        pk = self.kwargs.get('pk')
        agent = (
            PersistentAgent.objects.non_eval()
            .filter(pk=pk)
            .select_related('organization')
            .first()
        )
        if not agent:
            raise Http404
        if not self._can_manage(self.request.user, agent):
            raise PermissionDenied
        return agent

    def _can_manage(self, user, agent: PersistentAgent) -> bool:
        if agent.user_id == user.id:
            return True
        if agent.organization_id:
            return OrganizationMembership.objects.filter(
                org=agent.organization,
                user=user,
                status=OrganizationMembership.OrgStatus.ACTIVE,
                role__in=[OrganizationMembership.OrgRole.OWNER, OrganizationMembership.OrgRole.ADMIN],
            ).exists()
        return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agent = self._get_agent()
        context['agent'] = agent
        context['entries'] = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
        context['form'] = kwargs.get('form') or AllowlistEntryForm()
        context['policy'] = agent.whitelist_policy
        return context

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self.get_context_data())

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        agent = self._get_agent()
        action = request.POST.get('action')

        if action == 'add':
            form = AllowlistEntryForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Please correct the errors below.")
                if request.headers.get('HX-Request'):
                    # Return entries list unchanged
                    ctx = self.get_context_data(form=form)
                    return render(request, 'console/partials/_allowlist_entries.html', { 'entries': ctx['entries'] })
                return render(request, self.template_name, self.get_context_data(form=form))
            try:
                from django.db import IntegrityError

                entry = CommsAllowlistEntry(
                    agent=agent,
                    channel=form.cleaned_data['channel'],
                    address=form.cleaned_data['address'],
                    allow_inbound=form.cleaned_data.get('allow_inbound', True),
                    allow_outbound=form.cleaned_data.get('allow_outbound', True),
                )
                entry.full_clean()  # This will run model validation
                entry.save()

                messages.success(request, "Allowlist entry added.")
            except (ValidationError, IntegrityError) as e:
                messages.error(request, f"Could not add entry: {e}")
            if request.headers.get('HX-Request'):
                entries = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
                return render(request, 'console/partials/_allowlist_entries.html', { 'entries': entries })

        elif action == 'delete':
            entry_id = request.POST.get('entry_id')
            deleted = CommsAllowlistEntry.objects.filter(agent=agent, id=entry_id).delete()[0]
            if deleted:
                messages.success(request, "Allowlist entry deleted.")
            else:
                messages.error(request, "Entry not found.")
            if request.headers.get('HX-Request'):
                entries = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
                return render(request, 'console/partials/_allowlist_entries.html', { 'entries': entries })

        elif action == 'policy':
            policy = request.POST.get('whitelist_policy')
            if policy in dict(PersistentAgent.WhitelistPolicy.choices):
                agent.whitelist_policy = policy
                agent.save(update_fields=['whitelist_policy'])
                messages.success(request, "Whitelist policy updated.")
            else:
                messages.error(request, "Invalid policy value.")

        return redirect('agent_allowlist', pk=agent.pk)


class AgentFilesView(SharedAgentAccessMixin, ConsoleViewMixin, DetailView):
    """File browser page for a single agent."""
    model = PersistentAgent
    template_name = "console/agent_files.html"
    context_object_name = "agent"
    pk_url_kwarg = "pk"

    def get_queryset(self):
        qs = super().get_queryset().select_related('organization')

        context_type = self.request.session.get('context_type', 'personal')
        if context_type == 'organization':
            org_id = self.request.session.get('context_id')
            if not OrganizationMembership.objects.filter(
                user=self.request.user,
                org_id=org_id,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).exists():
                return qs.none()
            return qs.filter(organization_id=org_id)

        return qs.filter(user=self.request.user, organization__isnull=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agent = self.get_object()
        can_manage = self.can_manage_agent
        context["agent"] = agent
        context["agent_files_props"] = {
            "csrfToken": get_token(self.request),
            "agent": {
                "id": str(agent.id),
                "name": agent.name,
            },
            "backLink": {
                "url": reverse("agent_detail", args=[agent.id]) if can_manage else reverse("agents"),
                "label": "Back to Agent Settings" if can_manage else "Back to Agents",
            },
            "permissions": {
                "canManage": can_manage,
            },
            "urls": {
                "files": reverse("console_agent_fs_list", kwargs={"agent_id": agent.id}),
                "upload": reverse("console_agent_fs_upload", kwargs={"agent_id": agent.id}),
                "delete": reverse("console_agent_fs_delete", kwargs={"agent_id": agent.id}),
                "download": reverse("console_agent_fs_download", kwargs={"agent_id": agent.id}),
                "createFolder": reverse("console_agent_fs_create_folder", kwargs={"agent_id": agent.id}),
                "move": reverse("console_agent_fs_move", kwargs={"agent_id": agent.id}),
            },
        }
        return context

class AgentDeleteView(LoginRequiredMixin, View):
    """Handle agent deletion."""

    @transaction.atomic
    @tracer.start_as_current_span("CONSOLE Agent Delete View - delete")
    def delete(self, request, *args, **kwargs):
        try:
            agent = PersistentAgent.objects.non_eval().get(
                pk=self.kwargs['pk'],
                user=request.user
            )

            agent_name = agent.name
            agent_id = str(agent.pk)
            agent_org = agent.organization

            # Persist the referenced BrowserUseAgent ID before deleting the PersistentAgent.
            browser_agent_id = agent.browser_use_agent_id
            if browser_agent_id and not BrowserUseAgent.objects.filter(pk=browser_agent_id).exists():
                logger.warning(
                    "BrowserUseAgent %s not found while deleting PersistentAgent %s",
                    browser_agent_id,
                    agent_id,
                )
                browser_agent_id = None

            # Delete the persistent agent using a queryset delete to avoid triggering
            # BrowserUseAgent lookups that can explode when historical data is missing.
            deleted_count, _ = PersistentAgent.objects.non_eval().filter(
                pk=agent.pk,
                user=request.user,
            ).delete()

            if deleted_count == 0:
                logger.warning(
                    "PersistentAgent %s not deleted via queryset path; returning 404",
                    agent_id,
                )
                return HttpResponse("Agent not found or you don't have permission.", status=404)

            # Now delete the browser use agent if it still exists
            if browser_agent_id:
                BrowserUseAgent.objects.filter(pk=browser_agent_id).delete()
            
            messages.success(request, f"Agent '{agent_name}' has been deleted.")

            base_props = {
                'agent_id': agent_id,
                'agent_name': agent_name,
            }
            props = Analytics.with_org_properties(base_props, organization=agent_org)
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.PERSISTENT_AGENT_DELETED,
                source=AnalyticsSource.WEB,
                properties=props.copy(),
            ))
            if props.get('organization'):
                transaction.on_commit(lambda: Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.ORGANIZATION_PERSISTENT_AGENT_DELETED,
                    source=AnalyticsSource.WEB,
                    properties=props.copy(),
                ))
                transaction.on_commit(lambda: Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.ORGANIZATION_AGENT_DELETED,
                    source=AnalyticsSource.WEB,
                    properties=props.copy(),
                ))

            response = HttpResponse(status=200)
            response['HX-Redirect'] = reverse('agents')
            return response
            
        except PersistentAgent.DoesNotExist:
            return HttpResponse("Agent not found or you don't have permission.", status=404)
        except Exception as e:
            return HttpResponse(f"An error occurred: {e}", status=500)

class AgentSecretsView(LoginRequiredMixin, TemplateView):
    """Secrets management page for a single agent."""
    template_name = "console/agent_secrets.html"

    @tracer.start_as_current_span("CONSOLE Agent Secrets View - get_object")
    def get_object(self):
        """Get the agent or raise 404."""
        cached = getattr(self, "_agent_cache", None)
        if cached is not None:
            return cached
        agent = get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user
        )
        self._agent_cache = agent
        return agent

    def get(self, request, *args, **kwargs):
        agent = self.get_object()
        props = Analytics.with_org_properties(
            {
                'agent_id': str(agent.pk),
                'agent_name': agent.name,
            },
            organization=agent.organization,
        )
        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.PERSISTENT_AGENT_SECRETS_VIEWED,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        )
        return super().get(request, *args, **kwargs)

    @tracer.start_as_current_span("CONSOLE Agent Secrets View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent and secrets to context."""
        context = super().get_context_data(**kwargs)
        agent = self.get_object()
        context['agent'] = agent
        
        # Get secrets from the new model, split by requested/fulfilled
        from api.models import PersistentAgentSecret
        fulfilled_qs = PersistentAgentSecret.objects.filter(agent=agent, requested=False).order_by('domain_pattern', 'name')
        requested_qs = PersistentAgentSecret.objects.filter(agent=agent, requested=True).order_by('domain_pattern', 'name')

        # Group fulfilled secrets by domain for display
        secrets = {}
        for secret in fulfilled_qs:
            if secret.domain_pattern not in secrets:
                secrets[secret.domain_pattern] = {}
            secrets[secret.domain_pattern][secret.name] = {
                'id': secret.id,
                'name': secret.name,
                'description': secret.description,
                'key': secret.key,
                'created_at': secret.created_at,
                'updated_at': secret.updated_at
            }
        context['secrets'] = secrets
        context['has_secrets'] = bool(secrets)
        context['requested_secrets'] = requested_qs
        context['has_requested_secrets'] = requested_qs.exists()

        return context


class AgentSecretsAddView(LoginRequiredMixin, View):
    """Add a new secret to an agent."""

    @tracer.start_as_current_span("CONSOLE Agent Secrets Add")
    def get_object(self):
        """Get the agent or raise 404."""
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user
        )

    def post(self, request, *args, **kwargs):
        """Handle adding a new secret."""
        agent = self.get_object()
        form = PersistentAgentAddSecretForm(request.POST, agent=agent)
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    from api.models import PersistentAgentSecret
                    
                    # Create the new secret
                    domain = form.cleaned_data['domain']
                    name = form.cleaned_data['name']
                    description = form.cleaned_data.get('description', '')
                    value = form.cleaned_data['value']
                    
                    # Create and save the secret
                    secret = PersistentAgentSecret(
                        agent=agent,
                        domain_pattern=domain,
                        name=name,
                        description=description
                    )
                    # The key will be auto-generated in the clean() method
                    secret.full_clean()  # This generates the key from name
                    secret.set_value(value)  # This validates and encrypts the value
                    secret.save()
                    
                    messages.success(request, f"Secret '{name}' added successfully for domain '{domain}'.")

                    # Count total secrets for analytics
                    total_secrets = PersistentAgentSecret.objects.filter(agent=agent).count()

                    transaction.on_commit(lambda: Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.PERSISTENT_AGENT_SECRET_ADDED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'agent_id': str(agent.pk),
                            'agent_name': agent.name,
                            'secret_name': name,
                            'secret_key': secret.key,  # Generated key
                            'domain': domain,
                            'total_secrets': total_secrets,
                        }
                    ))

            except Exception as e:
                logger.error(f"Failed to add secret to agent {agent.id}: {str(e)}")
                messages.error(request, "Failed to add secret. Please try again.")
        
        # Handle form errors by showing them as messages
        if not form.is_valid():
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
        
        return redirect('agent_secrets', pk=agent.pk)


class AgentSecretsEditView(LoginRequiredMixin, TemplateView):
    """Edit view for existing secret value (GET render + POST update)."""
    template_name = "console/agent_secret_edit.html"

    @tracer.start_as_current_span("CONSOLE Agent Secrets Edit - get_object")
    def get_object(self):
        """Get the agent or raise 404."""
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user
        )

    def get(self, request, *args, **kwargs):
        """Load secret by ID for edit form."""
        agent = self.get_object()
        secret_id = kwargs.get('secret_id') or self.kwargs.get('secret_id')

        from api.models import PersistentAgentSecret

        if not secret_id:
            messages.error(request, "Secret ID is required.")
            return redirect('agent_secrets', pk=agent.pk)

        try:
            secret = PersistentAgentSecret.objects.get(agent=agent, pk=secret_id)
        except PersistentAgentSecret.DoesNotExist:
            messages.error(request, "Secret not found.")
            return redirect('agent_secrets', pk=agent.pk)

        # Store the secret in kwargs for other methods
        kwargs['secret_obj'] = secret
        return super().get(request, *args, **kwargs)

    @tracer.start_as_current_span("CONSOLE Agent Secrets Edit - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent, secret info, and form to context."""
        context = super().get_context_data(**kwargs)
        agent = self.get_object()
        secret_obj = kwargs.get('secret_obj')
        
        context['agent'] = agent
        context['secret_key'] = secret_obj.key if secret_obj else None
        context['secret_name'] = secret_obj.name if secret_obj else None
        context['domain'] = secret_obj.domain_pattern if secret_obj else None
        context['form'] = PersistentAgentEditSecretForm(agent=agent, secret=secret_obj)
        return context

    def post(self, request, *args, **kwargs):
        """Handle form submission for editing a secret value."""
        agent = self.get_object()
        secret_id = kwargs.get('secret_id') or self.kwargs.get('secret_id')

        if not secret_id:
            messages.error(request, "Secret ID is required.")
            return redirect('agent_secrets', pk=agent.pk)

        # Find the secret by ID
        from api.models import PersistentAgentSecret
        try:
            secret = PersistentAgentSecret.objects.get(agent=agent, pk=secret_id)
        except PersistentAgentSecret.DoesNotExist:
            messages.error(request, "Secret not found.")
            return redirect('agent_secrets', pk=agent.pk)

        form = PersistentAgentEditSecretForm(request.POST, agent=agent, secret=secret)

        if form.is_valid():
            try:
                with transaction.atomic():
                    # Update the secret fields
                    new_name = form.cleaned_data['name']
                    new_description = form.cleaned_data.get('description', '')
                    new_value = form.cleaned_data['value']
                    
                    # Update name and description
                    secret.name = new_name
                    secret.description = new_description
                    secret.full_clean()  # This will regenerate the key if name changed
                    secret.set_value(new_value)  # This validates and encrypts the value
                    secret.save()
                    
                    messages.success(request, f"Secret '{secret.name}' updated successfully.")

                    transaction.on_commit(lambda: Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.PERSISTENT_AGENT_SECRET_UPDATED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'agent_id': str(agent.pk),
                            'agent_name': agent.name,
                            'secret_name': secret.name,
                            'secret_key': secret.key,
                            'domain': secret.domain_pattern,
                        }
                    ))

                    return redirect('agent_secrets', pk=agent.pk)

            except Exception as e:
                logger.error(f"Failed to edit secret for agent {agent.id}: {str(e)}")
                messages.error(request, "Failed to update secret. Please try again.")
        
        # If form is invalid or exception occurred, re-render with errors
        context = self.get_context_data(**kwargs)
        context['form'] = form
        return self.render_to_response(context)


class AgentSecretsDeleteView(LoginRequiredMixin, View):
    """Delete a secret from an agent."""

    @tracer.start_as_current_span("CONSOLE Agent Secrets Delete")
    def get_object(self):
        """Get the agent or raise 404."""
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user
        )

    def post(self, request, *args, **kwargs):
        """Handle deleting a secret by secret ID."""
        agent = self.get_object()
        secret_id = kwargs.get('secret_id')

        if not secret_id:
            messages.error(request, "Secret ID is required.")
            return redirect('agent_secrets', pk=agent.pk)

        # Get the specific secret by ID
        try:
            from api.models import PersistentAgentSecret
            secret = PersistentAgentSecret.objects.get(
                pk=secret_id,
                agent=agent
            )
        except PersistentAgentSecret.DoesNotExist:
            messages.error(request, "Secret not found.")
            return redirect('agent_secrets', pk=agent.pk)

        try:
            with transaction.atomic():
                secret_key = secret.key
                secret_domain = secret.domain_pattern

                # Delete the secret
                secret.delete()
                
                messages.success(request, f"Secret '{secret_key}' deleted successfully.")

                # Count remaining secrets for analytics
                remaining_secrets = PersistentAgentSecret.objects.filter(agent=agent).count()

                transaction.on_commit(lambda: Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.PERSISTENT_AGENT_SECRET_DELETED,
                    source=AnalyticsSource.WEB,
                    properties={
                        'agent_id': str(agent.pk),
                        'agent_name': agent.name,
                        'secret_key': secret_key,
                        'domain': secret_domain,
                        'remaining_secrets': remaining_secrets,
                    }
                ))

        except Exception as e:
            logger.error(f"Failed to delete secret {secret_id} for agent {agent.id}: {str(e)}")
            messages.error(request, "Failed to delete secret. Please try again.")
        
        return redirect('agent_secrets', pk=agent.pk)


class AgentEmailSettingsView(LoginRequiredMixin, TemplateView):
    """Simple console page to edit an agent-owned email account settings."""
    template_name = "console/agent_email_settings.html"

    OAUTH_PROVIDER_DEFAULTS = {
        "gmail": {
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_security": "starttls",
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
            "imap_security": "ssl",
        },
    }

    def _validate_smtp_connection(self, account: AgentEmailAccount) -> tuple[bool, str]:
        try:
            import smtplib
            if account.smtp_security == AgentEmailAccount.SmtpSecurity.SSL:
                client = smtplib.SMTP_SSL(account.smtp_host, int(account.smtp_port or 465), timeout=30)
            else:
                client = smtplib.SMTP(account.smtp_host, int(account.smtp_port or 587), timeout=30)
            try:
                client.ehlo()
                if account.smtp_security == AgentEmailAccount.SmtpSecurity.STARTTLS:
                    client.starttls()
                    client.ehlo()
                if account.smtp_auth == AgentEmailAccount.AuthMode.OAUTH2:
                    from api.agent.comms.email_oauth import build_xoauth2_string, resolve_oauth_identity_and_token
                    identity, access_token, _credential = resolve_oauth_identity_and_token(account, "smtp")
                    auth_string = build_xoauth2_string(identity, access_token)
                    client.auth("XOAUTH2", lambda _=None: auth_string)
                elif account.smtp_auth != AgentEmailAccount.AuthMode.NONE:
                    client.login(account.smtp_username or '', account.get_smtp_password() or '')
                try:
                    client.noop()
                except Exception:
                    pass
            finally:
                try:
                    client.quit()
                except Exception:
                    try:
                        client.close()
                    except Exception:
                        pass
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _validate_imap_connection(self, account: AgentEmailAccount) -> tuple[bool, str]:
        try:
            import imaplib
            if account.imap_security == AgentEmailAccount.ImapSecurity.SSL:
                client = imaplib.IMAP4_SSL(account.imap_host, int(account.imap_port or 993), timeout=30)
            else:
                client = imaplib.IMAP4(account.imap_host, int(account.imap_port or 143), timeout=30)
                if account.imap_security == AgentEmailAccount.ImapSecurity.STARTTLS:
                    client.starttls()
            try:
                if account.imap_auth == AgentEmailAccount.ImapAuthMode.OAUTH2:
                    from api.agent.comms.email_oauth import build_xoauth2_string, resolve_oauth_identity_and_token
                    identity, access_token, _credential = resolve_oauth_identity_and_token(account, "imap")
                    auth_string = build_xoauth2_string(identity, access_token)
                    client.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
                elif account.imap_auth != AgentEmailAccount.ImapAuthMode.NONE:
                    client.login(account.imap_username or '', account.get_imap_password() or '')
                client.select(account.imap_folder or 'INBOX', readonly=True)
                try:
                    client.noop()
                except Exception:
                    pass
            finally:
                try:
                    client.logout()
                except Exception:
                    try:
                        client.shutdown()
                    except Exception:
                        pass
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def get_agent(self):
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user,
        )

    def _get_email_endpoint(self, agent: PersistentAgent):
        ep = agent.comms_endpoints.filter(channel=CommsChannel.EMAIL, owner_agent=agent, is_primary=True).first()
        if not ep:
            ep = agent.comms_endpoints.filter(channel=CommsChannel.EMAIL, owner_agent=agent).first()
        return ep

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agent = self.get_agent()
        endpoint = self._get_email_endpoint(agent)
        account = getattr(endpoint, 'agentemailaccount', None) if endpoint else None
        oauth_credential = getattr(account, "oauth_credential", None) if account else None
        from django.conf import settings as dj_settings
        default_domain = getattr(dj_settings, 'DEFAULT_AGENT_EMAIL_DOMAIN', 'agents.localhost')
        is_default_endpoint = False
        if endpoint and endpoint.address and default_domain:
            try:
                is_default_endpoint = endpoint.address.lower().endswith('@' + default_domain.lower())
            except Exception:
                is_default_endpoint = False

        if endpoint and account is None:
            from api.models import AgentEmailAccount
            account, _ = AgentEmailAccount.objects.get_or_create(endpoint=endpoint)

        initial = {}
        if account:
            initial = {
                'smtp_host': account.smtp_host,
                'smtp_port': account.smtp_port,
                'smtp_security': account.smtp_security,
                'smtp_auth': account.smtp_auth,
                'smtp_username': account.smtp_username,
                'is_outbound_enabled': account.is_outbound_enabled,
                'imap_host': account.imap_host,
                'imap_port': account.imap_port,
                'imap_security': account.imap_security,
                'imap_username': account.imap_username,
                'imap_auth': account.imap_auth,
                'imap_folder': account.imap_folder,
                'is_inbound_enabled': account.is_inbound_enabled,
                'imap_idle_enabled': account.imap_idle_enabled,
                'poll_interval_sec': account.poll_interval_sec,
                'connection_mode': account.connection_mode,
            }

        context['agent'] = agent
        context['endpoint'] = endpoint
        context['account'] = account
        context['oauth_credential'] = oauth_credential
        context['oauth_connected'] = oauth_credential is not None
        context['oauth_scope'] = getattr(oauth_credential, "scope", "")
        context['oauth_expires_at'] = getattr(oauth_credential, "expires_at", None)
        context['oauth_provider'] = getattr(oauth_credential, "provider", "")
        try:
            context['oauth_return_url'] = reverse('agent_email_settings', args=[agent.pk])
        except Exception:
            context['oauth_return_url'] = ""
        context['is_default_endpoint'] = is_default_endpoint
        context['default_domain'] = default_domain
        context['form'] = AgentEmailAccountConsoleForm(initial=initial)
        return context

    def post(self, request, *args, **kwargs):
        agent = self.get_agent()
        endpoint = self._get_email_endpoint(agent)
        if not endpoint:
            # Allow creating endpoint directly from this page
            action = request.POST.get('action')
            if action == 'create_endpoint':
                address = (request.POST.get('address') or '').strip()
                if not address or '@' not in address:
                    messages.error(request, "Please provide a valid email address (e.g., agent@example.com).")
                    return redirect('agent_email_settings', pk=agent.pk)
                try:
                    ep = PersistentAgentCommsEndpoint.objects.create(
                        owner_agent=agent,
                        channel=CommsChannel.EMAIL,
                        address=address,
                        is_primary=True,
                    )
                    messages.success(request, "Agent email endpoint created.")
                    return redirect('agent_email_settings', pk=agent.pk)
                except Exception as e:
                    messages.error(request, f"Failed to create email endpoint: {e}")
                    return redirect('agent_email_settings', pk=agent.pk)
            else:
                messages.error(request, "This agent has no email endpoint yet. Provide an email address to create one.")
                return redirect('agent_email_settings', pk=agent.pk)

        form = AgentEmailAccountConsoleForm(request.POST)
        action = request.POST.get('action', 'save')
        if not form.is_valid() and action == 'save':
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
            return redirect('agent_email_settings', pk=agent.pk)

        # Load or create account for save/test operations
        from api.models import AgentEmailAccount
        account = getattr(endpoint, 'agentemailaccount', None)

        # Handle save
        if action == 'save':
            data = form.cleaned_data
            created = False
            if not account:
                account = AgentEmailAccount(endpoint=endpoint)
                created = True
            # Update endpoint address to match user-entered value, if provided
            new_address = (request.POST.get('endpoint_address') or '').strip()
            if new_address:
                normalized_address = PersistentAgentCommsEndpoint.normalize_address(CommsChannel.EMAIL, new_address)
                if normalized_address and normalized_address != endpoint.address:
                    existing_endpoint = PersistentAgentCommsEndpoint.objects.filter(
                        channel=CommsChannel.EMAIL,
                        address__iexact=normalized_address,
                    ).first()
                    if existing_endpoint and existing_endpoint.id != endpoint.id:
                        if existing_endpoint.owner_agent_id and existing_endpoint.owner_agent_id != agent.id:
                            messages.error(
                                request,
                                "That email address is already assigned to another agent.",
                            )
                            return redirect('agent_email_settings', pk=agent.pk)
                        try:
                            from api.models import AgentEmailOAuthCredential
                            with transaction.atomic():
                                existing_endpoint.owner_agent = agent
                                existing_endpoint.is_primary = True
                                existing_endpoint.save(update_fields=["owner_agent", "is_primary"])
                                if endpoint.is_primary:
                                    endpoint.is_primary = False
                                    endpoint.save(update_fields=["is_primary"])
                                if account:
                                    new_account, _ = AgentEmailAccount.objects.get_or_create(endpoint=existing_endpoint)
                                    if new_account.pk != account.pk:
                                        for field in (
                                            "smtp_host",
                                            "smtp_port",
                                            "smtp_security",
                                            "smtp_auth",
                                            "smtp_username",
                                            "is_outbound_enabled",
                                            "imap_host",
                                            "imap_port",
                                            "imap_security",
                                            "imap_username",
                                            "imap_auth",
                                            "imap_folder",
                                            "is_inbound_enabled",
                                            "imap_idle_enabled",
                                            "poll_interval_sec",
                                            "last_polled_at",
                                            "last_seen_uid",
                                            "backoff_until",
                                            "connection_mode",
                                            "connection_last_ok_at",
                                            "connection_error",
                                        ):
                                            setattr(new_account, field, getattr(account, field))
                                        new_account.smtp_password_encrypted = account.smtp_password_encrypted
                                        new_account.imap_password_encrypted = account.imap_password_encrypted
                                        new_account.save()
                                        try:
                                            credential = account.oauth_credential
                                        except AgentEmailOAuthCredential.DoesNotExist:
                                            credential = None
                                        if credential:
                                            credential.account = new_account
                                            credential.save(update_fields=["account"])
                                        if account.pk:
                                            account.delete()
                                    account = new_account
                                endpoint = existing_endpoint
                        except Exception as e:
                            messages.error(request, f"Failed to update agent email address: {e}")
                            return redirect('agent_email_settings', pk=agent.pk)
                    else:
                        try:
                            endpoint.address = normalized_address
                            endpoint.save(update_fields=['address'])
                        except Exception as e:
                            messages.error(request, f"Failed to update agent email address: {e}")
                            return redirect('agent_email_settings', pk=agent.pk)
            # Assign simple fields
            for f in ('smtp_host', 'smtp_port', 'smtp_security', 'smtp_auth', 'smtp_username', 'is_outbound_enabled',
                      'imap_host', 'imap_port', 'imap_security', 'imap_username', 'imap_auth', 'imap_folder', 'is_inbound_enabled', 'imap_idle_enabled',
                      'poll_interval_sec', 'connection_mode'):
                setattr(account, f, data.get(f))
            # Passwords
            from api.encryption import SecretsEncryption
            if data.get('smtp_password'):
                account.smtp_password_encrypted = SecretsEncryption.encrypt_value(data.get('smtp_password'))
            if data.get('imap_password'):
                account.imap_password_encrypted = SecretsEncryption.encrypt_value(data.get('imap_password'))
            if account.connection_mode == AgentEmailAccount.ConnectionMode.OAUTH2:
                account.smtp_auth = AgentEmailAccount.AuthMode.OAUTH2
                account.imap_auth = AgentEmailAccount.ImapAuthMode.OAUTH2
                if not account.smtp_username:
                    account.smtp_username = endpoint.address
                if not account.imap_username:
                    account.imap_username = endpoint.address
                provider = ""
                credential = getattr(account, "oauth_credential", None)
                if credential:
                    provider = (credential.provider or "").lower()
                if not provider:
                    provider = (request.POST.get("oauth_provider") or "").lower()
                defaults = self.OAUTH_PROVIDER_DEFAULTS.get(provider)
                if defaults:
                    for key, value in defaults.items():
                        if not getattr(account, key):
                            setattr(account, key, value)
                if credential:
                    smtp_ok, smtp_error = self._validate_smtp_connection(account)
                    imap_ok, imap_error = self._validate_imap_connection(account)
                    errors = []
                    if smtp_ok:
                        account.is_outbound_enabled = True
                    else:
                        account.is_outbound_enabled = False
                        errors.append(f"SMTP validation failed: {smtp_error}")
                    if imap_ok:
                        account.is_inbound_enabled = True
                    else:
                        account.is_inbound_enabled = False
                        errors.append(f"IMAP validation failed: {imap_error}")
                    if smtp_ok or imap_ok:
                        account.connection_last_ok_at = timezone.now()
                    if errors:
                        account.connection_error = "; ".join(errors)
                        for message_text in errors:
                            messages.error(request, message_text)
                    else:
                        account.connection_error = ""
            try:
                account.full_clean()
                account.save()
                messages.success(request, "Email settings saved.")
                # Analytics for create/update
                try:
                    Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.EMAIL_ACCOUNT_CREATED if created else AnalyticsEvent.EMAIL_ACCOUNT_UPDATED,
                        source=AnalyticsSource.WEB,
                        properties={'agent_id': str(agent.pk), 'endpoint': endpoint.address},
                    )
                except Exception:
                    pass
            except ValidationError as e:
                for field, errs in e.message_dict.items():
                    for err in errs:
                        messages.error(request, f"{field}: {err}")
            return redirect('agent_email_settings', pk=agent.pk)

        # Ensure account exists before tests / poll
        if not account:
            messages.error(request, "Please save email settings before testing or polling.")
            return redirect('agent_email_settings', pk=agent.pk)

        # Test SMTP
        if action == 'test_smtp':
            ok, error = self._validate_smtp_connection(account)
            if ok:
                account.connection_last_ok_at = timezone.now()
                account.connection_error = ""
                account.save(update_fields=['connection_last_ok_at', 'connection_error'])
                messages.success(request, "SMTP test succeeded.")
                try:
                    Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.SMTP_TEST_PASSED,
                        source=AnalyticsSource.WEB,
                        properties={'agent_id': str(agent.pk), 'endpoint': endpoint.address},
                    )
                except Exception:
                    pass
            else:
                account.connection_error = error
                account.save(update_fields=['connection_error'])
                messages.error(request, f"SMTP test failed: {error}")
                try:
                    Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.SMTP_TEST_FAILED,
                        source=AnalyticsSource.WEB,
                        properties={'agent_id': str(agent.pk), 'endpoint': endpoint.address, 'error': error[:500]},
                    )
                except Exception:
                    pass
            return redirect('agent_email_settings', pk=agent.pk)

        # Test IMAP
        if action == 'test_imap':
            ok, error = self._validate_imap_connection(account)
            if ok:
                account.connection_last_ok_at = timezone.now()
                account.connection_error = ""
                account.save(update_fields=['connection_last_ok_at', 'connection_error'])
                messages.success(request, "IMAP test succeeded.")
                try:
                    Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.IMAP_TEST_PASSED,
                        source=AnalyticsSource.WEB,
                        properties={'agent_id': str(agent.pk), 'endpoint': endpoint.address},
                    )
                except Exception:
                    pass
            else:
                account.connection_error = error
                account.save(update_fields=['connection_error'])
                messages.error(request, f"IMAP test failed: {error}")
                try:
                    Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.IMAP_TEST_FAILED,
                        source=AnalyticsSource.WEB,
                        properties={'agent_id': str(agent.pk), 'endpoint': endpoint.address, 'error': error[:500]},
                    )
                except Exception:
                    pass
            return redirect('agent_email_settings', pk=agent.pk)

        # Poll now
        if action == 'poll_now':
            try:
                from api.agent.tasks import poll_imap_inbox
                poll_imap_inbox.delay(str(account.pk))
                messages.success(request, "IMAP poll enqueued.")
            except Exception as e:
                messages.error(request, f"Failed to enqueue IMAP poll: {e}")
            return redirect('agent_email_settings', pk=agent.pk)

        # Default: redirect back
        return redirect('agent_email_settings', pk=agent.pk)


class AgentSecretsAddFormView(LoginRequiredMixin, TemplateView):
    """Form view for adding a new secret to an agent."""
    template_name = "console/agent_secret_add.html"

    @tracer.start_as_current_span("CONSOLE Agent Secrets Add Form View - get_object")
    def get_object(self):
        """Get the agent or raise 404."""
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user
        )

    @tracer.start_as_current_span("CONSOLE Agent Secrets Add Form View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent and form to context."""
        context = super().get_context_data(**kwargs)
        agent = self.get_object()
        context['agent'] = agent
        context['form'] = PersistentAgentAddSecretForm(agent=agent)
        return context

    def post(self, request, *args, **kwargs):
        """Handle form submission."""
        agent = self.get_object()
        form = PersistentAgentAddSecretForm(request.POST, agent=agent)
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    from api.models import PersistentAgentSecret
                    
                    # Create the new secret
                    domain = form.cleaned_data['domain']
                    name = form.cleaned_data['name']
                    description = form.cleaned_data.get('description', '')
                    value = form.cleaned_data['value']

                    # Create and save the secret
                    secret = PersistentAgentSecret(
                        agent=agent,
                        domain_pattern=domain,
                        name=name,
                        description=description
                    )
                    # The key will be auto-generated in the clean() method
                    secret.full_clean()  # This generates the key from name
                    secret.set_value(value)  # This validates and encrypts the value
                    secret.save()

                    messages.success(request, f"Secret '{name}' added successfully for domain '{domain}'.")

                    # Count total secrets for analytics
                    total_secrets = PersistentAgentSecret.objects.filter(agent=agent).count()

                    transaction.on_commit(lambda: Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.PERSISTENT_AGENT_SECRET_ADDED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'agent_id': str(agent.pk),
                            'agent_name': agent.name,
                            'secret_name': name,
                            'secret_key': secret.key,  # Generated key
                            'domain': domain,
                            'total_secrets': total_secrets,
                        }
                    ))

                    return redirect('agent_secrets', pk=agent.pk)

            except Exception as e:
                logger.error(f"Failed to add secret to agent {agent.id}: {str(e)}")
                messages.error(request, "Failed to add secret. Please try again.")
        
        # If form is invalid or exception occurred, re-render with errors
        context = self.get_context_data(**kwargs)
        context['form'] = form
        return self.render_to_response(context)


# (Consolidated) AgentSecretsEditFormView removed; logic merged into AgentSecretsEditView

@login_required
@require_POST
@tracer.start_as_current_span("GRANT_CREDITS")
def grant_credits(request):
    """Endpoint to grant 100 task credits to a user. Admin only."""

    # Check if user is staff/admin
    if not request.user.is_staff:
        return JsonResponse({'success': False, 'error': 'Unauthorized. Admin access required.'}, status=403)

    user_id = request.POST.get('user_id')
    if not user_id:
        return JsonResponse({'success': False, 'error': 'User ID is required.'}, status=400)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found.'}, status=404)

    try:
        with transaction.atomic():
            # Create a new TaskCredit record for compensation
            grant_date = timezone.now()
            expiration_date = grant_date + timedelta(days=365)  # 1 year expiration for admin grants

            task_credit = TaskCredit.objects.create(
                user=user,
                credits=100,
                credits_used=0,
                granted_date=grant_date,
                expiration_date=expiration_date,
                plan=PlanNamesChoices.FREE,  # Use FREE plan for admin grants
                grant_type=GrantTypeChoices.COMPENSATION,
                additional_task=False,
                voided=False
            )

            logger.info(f"Admin {request.user.id} granted 100 task credits to user {user.id}")

            return JsonResponse({
                'success': True,
                'message': f"100 task credits granted to {user.email or user.username}.",
                'credits_granted': 100
            })

    except Exception as e:
        logger.error(f"Failed to grant credits to user {user_id}: {str(e)}")
        return JsonResponse({'success': False, 'error': f"Failed to grant credits: {str(e)}"}, status=500)


class AgentSecretsRequestView(LoginRequiredMixin, TemplateView):
    """View for displaying requested secrets that need values."""
    template_name = "console/agent_secrets_request.html"

    @tracer.start_as_current_span("CONSOLE Agent Secrets Request View - get_object")
    def get_object(self):
        """Get the agent or raise 404."""
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user
        )

    @tracer.start_as_current_span("CONSOLE Agent Secrets Request View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent and requested secrets to context."""
        context = super().get_context_data(**kwargs)
        agent = self.get_object()
        context['agent'] = agent

        # Get requested secrets (those that have requested=True)
        from api.models import PersistentAgentSecret
        requested_secrets = PersistentAgentSecret.objects.filter(
            agent=agent,
            requested=True
        ).order_by('domain_pattern', 'name')

        context['requested_secrets'] = requested_secrets
        context['has_requested_secrets'] = requested_secrets.exists()
        context['form'] = PersistentAgentSecretsRequestForm(requested_secrets=requested_secrets)

        return context

    def post(self, request, *args, **kwargs):
        """Handle saving values or removing requested secrets."""
        agent = self.get_object()
        action = (request.POST.get('action') or '').strip().lower()

        from api.models import PersistentAgentSecret

        # Bulk remove requested secrets
        if request.resolver_match.url_name == 'agent_requested_secrets_remove' or action == 'remove_selected':
            try:
                ids = request.POST.getlist('secret_ids')
                if not ids:
                    messages.info(request, "No requests selected for removal.")
                    return redirect('agent_secrets_request', pk=agent.pk)
                with transaction.atomic():
                    qs = PersistentAgentSecret.objects.filter(agent=agent, requested=True, id__in=ids)
                    deleted_count = qs.count()
                    qs.delete()
                messages.success(request, f"Removed {deleted_count} requested credential(s).")
            except Exception as e:
                logger.error(f"Failed to bulk remove requested secrets for agent {agent.id}: {e}")
                messages.error(request, "Failed to remove selected requests.")
            return redirect('agent_secrets_request', pk=agent.pk)

        # Single remove via per-row action
        if request.resolver_match.url_name == 'agent_requested_secret_remove':
            secret_id = self.kwargs.get('secret_id')
            try:
                with transaction.atomic():
                    secret = PersistentAgentSecret.objects.get(agent=agent, id=secret_id, requested=True)
                    name = secret.name
                    secret.delete()
                messages.success(request, f"Removed request for '{name}'.")
            except PersistentAgentSecret.DoesNotExist:
                messages.error(request, "Requested secret not found.")
            except Exception as e:
                logger.error(f"Failed to remove requested secret {secret_id} for agent {agent.id}: {e}")
                messages.error(request, "Failed to remove request.")
            return redirect('agent_secrets_request', pk=agent.pk)

        # Default: save provided values (partial allowed)
        requested_secrets = PersistentAgentSecret.objects.filter(
            agent=agent,
            requested=True
        ).order_by('domain_pattern', 'name')

        form = PersistentAgentSecretsRequestForm(request.POST, requested_secrets=requested_secrets)

        if form.is_valid():
            try:
                with transaction.atomic():
                    updated_count = 0
                    for secret in requested_secrets:
                        field_name = f'secret_{secret.id}'
                        value = form.cleaned_data.get(field_name)
                        if value:
                            secret.set_value(value)
                            secret.requested = False
                            secret.save()
                            updated_count += 1

                    if updated_count > 0:
                        from api.models import PersistentAgentStep, PersistentAgentSystemStep
                        step = PersistentAgentStep.objects.create(
                            agent=agent,
                            description=f"User provided {updated_count} requested credential(s)"
                        )
                        PersistentAgentSystemStep.objects.create(
                            step=step,
                            code=PersistentAgentSystemStep.Code.CREDENTIALS_PROVIDED,
                            notes=f"Secrets provided: {updated_count}"
                        )
                        from api.agent.tasks.process_events import process_agent_events_task
                        transaction.on_commit(lambda: process_agent_events_task.delay(str(agent.pk)))
                        Analytics.track_event(
                            user_id=self.request.user.id,
                            event=AnalyticsEvent.PERSISTENT_AGENT_SECRETS_PROVIDED,
                            source=AnalyticsSource.WEB,
                            properties={
                                'agent_id': str(agent.pk),
                                'agent_name': agent.name,
                                'secrets_provided': updated_count,
                            },
                        )
                        return redirect('agent_secrets_request_thanks', pk=agent.pk)
                    else:
                        messages.info(request, "No changes detected. Enter values to save or remove requests you no longer need.")
            except Exception as e:
                logger.error(f"Failed to update requested secrets for agent {agent.id}: {str(e)}")
                messages.error(request, "Failed to save secrets. Please try again.")

        context = self.get_context_data(**kwargs)
        context['form'] = form
        return self.render_to_response(context)


class AgentSecretRerequestView(LoginRequiredMixin, View):
    """Mark a fulfilled secret as requested again and clear its stored value."""
    def post(self, request, *args, **kwargs):
        agent = get_object_or_404(
            PersistentAgent.objects.non_eval(),
            pk=self.kwargs['pk'],
            user=request.user,
        )
        secret_id = self.kwargs.get('secret_id')
        from api.models import PersistentAgentSecret
        try:
            with transaction.atomic():
                secret = PersistentAgentSecret.objects.get(agent=agent, pk=secret_id)
                secret.requested = True
                secret.encrypted_value = b''
                secret.save(update_fields=['requested', 'encrypted_value', 'updated_at'])
            messages.success(request, f"Re-requested '{secret.name}'. A new value is now required.")
        except PersistentAgentSecret.DoesNotExist:
            messages.error(request, "Secret not found.")
        except Exception as e:
            logger.error(f"Failed to re-request secret {secret_id} for agent {agent.id}: {e}")
            messages.error(request, "Failed to re-request secret.")
        return redirect('agent_secrets', pk=agent.pk)


class AgentSecretsRequestThanksView(LoginRequiredMixin, TemplateView):
    """Thank you page after providing secret values."""
    template_name = "console/agent_secrets_request_thanks.html"

    @tracer.start_as_current_span("CONSOLE Agent Secrets Request Thanks View - get_object")
    def get_object(self):
        """Get the agent or raise 404."""
        return get_object_or_404(
            PersistentAgent.objects.non_eval(),
            pk=self.kwargs['pk'],
            user=self.request.user
        )

    @tracer.start_as_current_span("CONSOLE Agent Secrets Request Thanks View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent to context."""
        context = super().get_context_data(**kwargs)
        context['agent'] = self.get_object()
        return context

class AgentWelcomeView(LoginRequiredMixin, DetailView):
    """Welcome page shown immediately after creating an agent."""
    model = PersistentAgent
    template_name = "console/agent_welcome.html"
    context_object_name = "agent"
    pk_url_kwarg = "pk"

    @tracer.start_as_current_span("CONSOLE Agent Welcome View - get_queryset")
    def get_queryset(self):
        # Ensure users can only access their own agents
        return (
            super()
            .get_queryset()
            .filter(user=self.request.user)
            .select_related('organization__billing')
        )

    @tracer.start_as_current_span("CONSOLE Agent Welcome View - get_context_data")
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agent = self.get_object()

        # Show agent endpoints for each channel if they exist, regardless of primary flag
        primary_email = agent.comms_endpoints.filter(
            channel=CommsChannel.EMAIL
        ).first()
        primary_sms = agent.comms_endpoints.filter(
            channel=CommsChannel.SMS
        ).first()

        context['primary_email'] = primary_email
        context['primary_sms'] = primary_sms

        # Determine the user's preferred contact channel from the agent's preference
        preferred_channel = None
        try:
            preferred_ep = agent.preferred_contact_endpoint
            if preferred_ep and preferred_ep.channel in (CommsChannel.SMS, CommsChannel.EMAIL):
                preferred_channel = 'sms' if preferred_ep.channel == CommsChannel.SMS else 'email'
        except Exception:
            preferred_channel = None
        # Fallback to detect a likely preference if not set
        if preferred_channel is None:
            if primary_sms and getattr(primary_sms, 'is_primary', False):
                preferred_channel = 'sms'
            elif primary_email and getattr(primary_email, 'is_primary', False):
                preferred_channel = 'email'
        context['preferred_channel'] = preferred_channel

        owner_plan = get_user_plan(self.request.user)
        organization_name = None
        org_has_paid_seats = False

        if agent.organization_id:
            organization = agent.organization
            organization_name = getattr(organization, "name", None)
            owner_plan = get_organization_plan(organization)
            billing = getattr(organization, "billing", None)
            org_has_paid_seats = bool(getattr(billing, "purchased_seats", 0) > 0)

        plan_id = str(owner_plan.get("id", "")).lower() if owner_plan else ""

        show_pro_scale_upsell = plan_id == PlanNamesChoices.FREE.value
        show_scale_upsell = plan_id in (PlanNamesChoices.FREE.value, PlanNamesChoices.STARTUP.value)

        upsell_count = 0
        if show_pro_scale_upsell:
            upsell_count += 1
        if show_scale_upsell:
            upsell_count += 1
        if agent.organization_id and not org_has_paid_seats:
            upsell_count += 1

        context.update({
            'owner_plan': owner_plan,
            'owner_plan_id': plan_id,
            'owner_plan_name': owner_plan.get("name", "") if owner_plan else "",
            'agent_has_org': bool(agent.organization_id),
            'agent_org_name': organization_name,
            'org_has_paid_seats': org_has_paid_seats if agent.organization_id else None,
            'show_pro_scale_upsell': show_pro_scale_upsell,
            'show_scale_upsell': show_scale_upsell,
            'upsell_count': upsell_count,
        })

        return context

class AgentContactRequestsView(LoginRequiredMixin, TemplateView):
    """View for displaying and approving contact requests from agents."""
    template_name = "console/agent_contact_requests.html"
    
    def _resolve_agent_or_issue(self):
        """Return (agent, issue) where issue is one of: None, 'invalid', 'wrong_account'."""
        pk = self.kwargs['pk']
        current_span = trace.get_current_span()
        agent = (
            PersistentAgent.objects.non_eval()
            .filter(pk=pk)
            .select_related('user')
            .first()
        )

        if not agent:
            if current_span:
                current_span.set_attribute("approval.issue", "invalid")
            logger.info("Agent contact-requests invalid agent id", extra={"agent_id": str(pk)})
            return None, 'invalid'

        if agent.user != self.request.user:
            if current_span:
                current_span.set_attribute("approval.issue", "wrong_account")
            logger.info("Agent contact-requests wrong account", extra={"agent_id": str(pk), "user_id": self.request.user.id})
            return None, 'wrong_account'
            
        return agent, None

    @tracer.start_as_current_span("CONSOLE Agent Contact Requests View - get")
    def get(self, request, *args, **kwargs):
        agent, issue = self._resolve_agent_or_issue()
        if issue:
            return self._issue_response(request, action='view', issue=issue)
        return super().get(request, *args, **kwargs)

    @tracer.start_as_current_span("CONSOLE Agent Contact Requests View - get_object")
    def get_object(self):
        agent, issue = self._resolve_agent_or_issue()
        if issue:
            # Should have been handled in get/post, but keep safety net
            raise Http404("Agent not available")
        return agent
    
    @tracer.start_as_current_span("CONSOLE Agent Contact Requests View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent and pending contact requests to context."""
        context = super().get_context_data(**kwargs)
        agent = self.get_object()
        context['agent'] = agent
        
        # Get pending contact requests
        from api.models import CommsAllowlistRequest, CommsAllowlistEntry, AgentAllowlistInvite
        pending_requests = CommsAllowlistRequest.objects.filter(
            agent=agent,
            status=CommsAllowlistRequest.RequestStatus.PENDING
        ).order_by('-requested_at')
        
        context['pending_requests'] = pending_requests
        context['has_pending_requests'] = pending_requests.exists()
        
        # Get current allowlist usage for limit display
        max_contacts = get_user_max_contacts_per_agent(
            agent.user,
            organization=agent.organization,
        )
        active_count = CommsAllowlistEntry.objects.filter(
            agent=agent, is_active=True
        ).count()
        pending_invites = AgentAllowlistInvite.objects.filter(
            agent=agent, status=AgentAllowlistInvite.InviteStatus.PENDING
        ).count()
        total_count = active_count + pending_invites
        contact_counts = get_agent_contact_counts(agent)
        if contact_counts is not None:
            total_count = contact_counts["total"]

        context['max_contacts'] = max_contacts
        context['contact_cap_unlimited'] = max_contacts <= 0
        context['active_count'] = active_count
        context['pending_invites'] = pending_invites
        context['total_count'] = active_count + pending_invites
        context['remaining_slots'] = (
            None if max_contacts <= 0 else max(0, max_contacts - (active_count + pending_invites))
        )
        
        # Create form
        from console.forms import ContactRequestApprovalForm
        context['form'] = ContactRequestApprovalForm(contact_requests=pending_requests)
        
        return context
    
    def post(self, request, *args, **kwargs):
        """Handle approval/rejection of contact requests."""
        agent, issue = self._resolve_agent_or_issue()
        if issue:
            return self._issue_response(request, action='update', issue=issue)

        # Safety: agent is present beyond this point
        # Get pending requests
        from api.models import CommsAllowlistRequest, PersistentAgentStep, PersistentAgentSystemStep
        pending_requests = CommsAllowlistRequest.objects.filter(
            agent=agent,
            status=CommsAllowlistRequest.RequestStatus.PENDING
        ).order_by('-requested_at')
        
        from console.forms import ContactRequestApprovalForm
        form = ContactRequestApprovalForm(request.POST, contact_requests=pending_requests)
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    approved_count = 0
                    rejected_count = 0
                    approved_addresses = []
                    invitations_sent = []
                    
                    for request_obj in pending_requests:
                        field_name = f'approve_{request_obj.id}'
                        should_approve = form.cleaned_data.get(field_name, False)
                        
                        try:
                            if should_approve:
                                # Get the direction and config settings from the form
                                inbound_field = f'inbound_{request_obj.id}'
                                outbound_field = f'outbound_{request_obj.id}'
                                configure_field = f'configure_{request_obj.id}'
                                allow_inbound = form.cleaned_data.get(inbound_field, True)
                                allow_outbound = form.cleaned_data.get(outbound_field, True)
                                can_configure = form.cleaned_data.get(configure_field, False)

                                # Update the request's settings before approving
                                request_obj.request_inbound = allow_inbound
                                request_obj.request_outbound = allow_outbound
                                request_obj.request_configure = can_configure
                                request_obj.save(update_fields=['request_inbound', 'request_outbound', 'request_configure'])
                                
                                # Try to approve (will directly add to allowlist, skipping invitation)
                                result = request_obj.approve(invited_by=request.user, skip_invitation=True)
                                approved_count += 1
                                approved_addresses.append(f"{request_obj.name or request_obj.address}")
                                
                                # Check if we created a new invitation that needs email (won't happen with skip_invitation=True)
                                from api.models import AgentAllowlistInvite
                                if isinstance(result, AgentAllowlistInvite):
                                    invitations_sent.append(request_obj.address)
                            else:
                                request_obj.reject()
                                rejected_count += 1
                        except ValidationError as e:
                            # Hit the limit, show error
                            messages.error(
                                request, 
                                f"Could not approve {request_obj.address}: {e.message if hasattr(e, 'message') else str(e)}"
                            )
                            continue
                    
                    if approved_count > 0:
                        # Switch agent to manual allowlist mode if not already
                        if agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
                            agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
                            agent.save(update_fields=['whitelist_policy'])
                        
                        # Send invitation emails for new invitations
                        if invitations_sent:
                            from django.urls import reverse
                            from api.models import AgentAllowlistInvite, CommsChannel
                            
                            for address in invitations_sent:
                                # Get the invitation we just created
                                invitation = AgentAllowlistInvite.objects.filter(
                                    agent=agent,
                                    address=address,
                                    status=AgentAllowlistInvite.InviteStatus.PENDING
                                ).first()
                                
                                if invitation and invitation.channel == 'email':
                                    try:
                                        # Get the agent's primary email endpoint
                                        primary_email = agent.comms_endpoints.filter(
                                            channel=CommsChannel.EMAIL, is_primary=True
                                        ).first()
                                        
                                        if not primary_email:
                                            primary_email = agent.comms_endpoints.filter(
                                                channel=CommsChannel.EMAIL
                                            ).first()
                                        
                                        if primary_email:
                                            # Build accept/reject URLs
                                            accept_url = request.build_absolute_uri(
                                                reverse('agent_allowlist_invite_accept', kwargs={'token': invitation.token})
                                            )
                                            reject_url = request.build_absolute_uri(
                                                reverse('agent_allowlist_invite_reject', kwargs={'token': invitation.token})
                                            )
                                            
                                            context = {
                                                'agent': agent,
                                                'agent_owner': agent.user,
                                                'contact_email': address,
                                                'agent_email': primary_email.address,
                                                'accept_url': accept_url,
                                                'reject_url': reject_url,
                                                'invite': invitation,
                                            }
                                            
                                            subject = f"You're invited to communicate with {agent.name} on Gobii"
                                            text_body = render_to_string('emails/agent_allowlist_invite.txt', context)
                                            html_body = render_to_string('emails/agent_allowlist_invite.html', context)
                                            
                                            send_mail(
                                                subject,
                                                text_body,
                                                None,  # Use default from email
                                                [address],
                                                html_message=html_body,
                                                fail_silently=True,  # Don't fail the whole process if email fails
                                            )
                                    except Exception as e:
                                        logger.warning("Failed to send allowlist invitation email to %s: %s", address, e)
                        
                        # Create system step to record approvals
                        step = PersistentAgentStep.objects.create(
                            agent=agent,
                            description=f"User approved {approved_count} contact request(s)"
                        )
                        PersistentAgentSystemStep.objects.create(
                            step=step,
                            code=PersistentAgentSystemStep.Code.CONTACTS_APPROVED,
                            notes=f"Approved: {', '.join(approved_addresses)}"
                        )
                        
                        # Trigger agent event processing
                        from api.agent.tasks.process_events import process_agent_events_task
                        transaction.on_commit(lambda: process_agent_events_task.delay(str(agent.pk)))
                        
                        Analytics.track_event(
                            user_id=self.request.user.id,
                            event=AnalyticsEvent.AGENT_CONTACTS_APPROVED,
                            source=AnalyticsSource.WEB,
                            properties={
                                'agent_id': str(agent.pk),
                                'agent_name': agent.name,
                                'approved_count': approved_count,
                                'rejected_count': rejected_count,
                                'invitations_sent': len(invitations_sent),
                            }
                        )
                        
                        # Success message for approved contacts
                        messages.success(
                            request, 
                            f"Successfully approved {approved_count} contact(s) - added to allowlist."
                        )
                    
                    if rejected_count > 0:
                        messages.info(request, f"Rejected {rejected_count} contact(s)")
                    
                    if approved_count > 0 or rejected_count > 0:
                        return redirect('agent_contact_requests_thanks', pk=agent.pk)
                    else:
                        messages.warning(request, "No contacts were selected")
                        
            except Exception as e:
                logger.error(f"Failed to process contact requests for agent {agent.id}: {str(e)}")
                messages.error(request, "Failed to process requests. Please try again.")
        
        # If form invalid or failed, redisplay
        context = self.get_context_data(**kwargs)
        context['form'] = form
        return self.render_to_response(context)

    def _issue_response(self, request, action: str, issue: str, extra: dict | None = None):
        ctx = {
            'issue': issue,
            'context_type': 'agent_allowlist',
            'action': action,
        }
        if extra:
            ctx.update(extra)
        return render(request, "console/approval_link_issue.html", ctx, status=200)


class AgentContactRequestsThanksView(LoginRequiredMixin, TemplateView):
    """Thank you page after approving contact requests."""
    template_name = "console/agent_contact_requests_thanks.html"
    
    def _resolve_agent_or_issue(self):
        pk = self.kwargs['pk']
        current_span = trace.get_current_span()
        exists = PersistentAgent.objects.non_eval().filter(pk=pk).exists()
        if not exists:
            if current_span:
                current_span.set_attribute("approval.issue", "invalid")
            logger.info("Agent contact-requests-thanks invalid agent id", extra={"agent_id": str(pk)})
            return None, 'invalid'
        agent = (
            PersistentAgent.objects.non_eval()
            .filter(pk=pk, user=self.request.user)
            .first()
        )
        if not agent:
            if current_span:
                current_span.set_attribute("approval.issue", "wrong_account")
            logger.info("Agent contact-requests-thanks wrong account", extra={"agent_id": str(pk), "user_id": self.request.user.id})
            return None, 'wrong_account'
        return agent, None

    @tracer.start_as_current_span("CONSOLE Agent Contact Requests Thanks View - get")
    def get(self, request, *args, **kwargs):
        agent, issue = self._resolve_agent_or_issue()
        if issue:
            return self._issue_response(request, action='view', issue=issue)
        return super().get(request, *args, **kwargs)

    @tracer.start_as_current_span("CONSOLE Agent Contact Requests Thanks View - get_object")
    def get_object(self):
        agent, issue = self._resolve_agent_or_issue()
        if issue:
            raise Http404("Agent not available")
        return agent

    def _issue_response(self, request, action: str, issue: str, extra: dict | None = None):
        ctx = {
            'issue': issue,
            'context_type': 'agent_allowlist',
            'action': action,
        }
        if extra:
            ctx.update(extra)
        return render(request, "console/approval_link_issue.html", ctx, status=200)
    
    @tracer.start_as_current_span("CONSOLE Agent Contact Requests Thanks View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent to context."""
        context = super().get_context_data(**kwargs)
        context['agent'] = self.get_object()
        return context

@tracer.start_as_current_span("CONSOLE Profile - handle_send_verification")
def handle_send_verification(request, phone):
    """
    Handle sending verification code

    This function checks if the user has an unverified phone number and attempts to send a verification code. If the
    phone number is already verified or does not exist, it shows an error message.

    """
    if not phone:
        return JsonResponse({
            'success': False,
            'error': "No phone number found to send verification code."
        })

    try:
        # Send verification SMS
        with traced("CONSOLE Profile - Twilio - SMS Verification Send"):
            sms.start_verification(phone)

        logger.info(f"Verification code sent to user {request.user.id}")

        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.SMS_VERIFICATION_CODE_SENT,
            source=AnalyticsSource.WEB,
            properties={
                'phone_number': phone,
                'user_id': request.user.id,
            }
        )

        return JsonResponse({
            'success': True,
            'message': f"Verification code sent to {phone}"
        })

    except Exception as e:
        logger.error(f"Failed to send verification code for user {request.user.id}: {str(e)}")

    # If we're here, something went wrong
    return JsonResponse({
        'success': False,
        'error': "Failed to send verification code. Please try again."
    })

@tracer.start_as_current_span("CONSOLE Profile - handle_resend_verification")
def handle_resend_verification(request, phone_number):
    """
    Handle resending verification code

    This function checks if the user has an unverified phone number and attempts to resend the verification code. If the
    phone number is already verified or does not exist, it shows an error message.

    """
    try:
        # Send verification SMS
        with traced("CONSOLE Profile - Twilio - SMS Verification Send"):
            sms.start_verification(phone_number)

        logger.info(f"Verification code resent to user {request.user.id}")

        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.SMS_RESEND_VERIFICATION_CODE,
            source=AnalyticsSource.WEB,
            properties={
                'phone_number': phone_number,
                'user_id': request.user.id,
            }
        )

        return JsonResponse({
            'success': True,
            'message': f"Verification code resent to {phone_number}"
        })

    except Exception as e:
        logger.error(f"Failed to resend verification code for user {request.user.id}: {str(e)}")
        messages.error(request, "Failed to send verification code. Please try again.")

    # If we're here, something went wrong
    return JsonResponse({
        'success': False,
        'error': "Failed to resend verification code. Please try again."
    })

@tracer.start_as_current_span("CONSOLE Profile - handle_delete_phone")
def handle_delete_phone(request):
    """
    Handle deleting phone number

    This function checks if the user has a phone number and attempts to delete it. If the phone number does not exist,
    it shows an error message. If deletion is successful, it shows a success message.
    """
    try:
        # Get the user's phone number
        phone = UserPhoneNumber.objects.get(user=request.user)

        if not phone:
            logger.warning(f"User {request.user.id} has no phone number but requested to delete it.")
            return JsonResponse({
                'success': False,
                'error': "No phone number found to delete."
            })

        phone.delete()
        logger.info(f"Phone number deleted for user {request.user.id}")

        Analytics.identify(
            user_id=request.user.id,
            traits={
                'has_phone': False,
                'phone_verified': False,
            }
        )

        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.SMS_DELETED,
            source=AnalyticsSource.WEB,
            properties={
                'user_id': request.user.id,
            }
        )

        return JsonResponse({
            'success': True,
            'message': "Phone number deleted successfully."
        })

    except Exception as e:
        logger.error(f"Failed to delete phone number for user {request.user.id}: {str(e)}")
        messages.error(request, "Failed to delete phone number. Please try again.")

    # If we're here, something went wrong
    return JsonResponse({
        'success': False,
        'error': "Failed to delete phone number. Please try again."
    })

@tracer.start_as_current_span("CONSOLE Profile - handle_profile_update")
def handle_profile_update(request, user, phone):
    """Handle normal profile and phone form submission"""
    profile_form = UserProfileForm(request.POST, instance=user)
    phone_form = UserPhoneNumberForm(request.POST, user=user)

    profile_valid = profile_form.is_valid()

    if profile_valid:
        try:
            # Save profile changes
            profile_form.save()

            # Handle phone number changes
            phone_number = phone_form.cleaned_data.get('phone_number')
            verification_code = phone_form.cleaned_data.get('verification_code')

            messages.success(request, "Profile updated successfully!")
            return redirect('console:profile')

        except Exception as e:
            logger.error(f"Error updating profile for user {user.id}: {str(e)}")
            messages.error(request, "An error occurred while updating your profile.")

    # Form validation failed - redisplay with errors
    context = {
        "profile_form": profile_form,
        "phone_form": phone_form,
        "phone": phone,
    }

    return render(request, "console/profile.html", context)

@tracer.start_as_current_span("CONSOLE Profile - handle_confirm_code")
def handle_confirm_code(request, phone_number, verification_code):
    """
    Handle confirming verification code

    This function checks if the user has an unverified phone number and attempts to confirm the verification code.
    If the phone number is already verified or does not exist, it shows an error message.
    """
    if not verification_code:
        return JsonResponse({
            'success': False,
            'error': "Verification code is required."
        })

    try:
        check = False

        with traced("CONSOLE Profile - Twilio - SMS Code Verification"):
            check = sms.check_verification(phone_number, verification_code)

        if check:
            # If the phone number is verified, update the UserPhoneNumber model
            phone, created = UserPhoneNumber.objects.get_or_create(
                user=request.user,
                phone_number=phone_number,
                defaults={
                    'is_verified': True,
                    'is_primary': True,  # Set as primary if it's a new phone, and we only support one phone *for now*
                    'verified_at': timezone.now(),
                    'created_at': timezone.now(),
                    'updated_at': timezone.now(),
                }
            )

            Analytics.identify(
                user_id=request.user.id,
                traits={
                    'has_phone': True,
                    'phone_verified': True,
                }
            )

            Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.SMS_VERIFIED,
                source=AnalyticsSource.WEB,
                properties={
                    'phone_number': phone_number,
                    'user_id': request.user.id,
                }
            )

            return JsonResponse({'success': True, 'message': "Phone number verified successfully!"})
        else:
            return JsonResponse({'success': False, 'error': "Invalid verification code. Please try again."})

    except Exception as e:
        logger.warning(f"Failed to confirm verification code for user {request.user.id}: {str(e)}")

    return JsonResponse({'success': False, 'error': "Failed to confirm verification code. Please try again."})


class OrganizationListView(WaffleFlagMixin, ConsoleViewMixin, TemplateView):
    """List organizations the user belongs to."""

    waffle_flag = ORGANIZATIONS
    template_name = "console/organizations.html"

    @tracer.start_as_current_span("CONSOLE Organization List")
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        memberships = (
            OrganizationMembership.objects.filter(
                user=self.request.user,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            )
            .select_related("org")
            .order_by("org__name")
        )
        context["memberships"] = memberships
        # Pending invitations for the current user's email
        now = timezone.now()
        pending_invites = (
            OrganizationInvite.objects.filter(
                email__iexact=self.request.user.email,
                accepted_at__isnull=True,
                revoked_at__isnull=True,
                expires_at__gte=now,
            )
            .select_related("org", "invited_by")
            .order_by("org__name")
        )
        context["pending_invites"] = pending_invites
        return context


class OrganizationCreateView(WaffleFlagMixin, ConsoleViewMixin, TemplateView):
    """Create a new organization."""

    waffle_flag = ORGANIZATIONS
    template_name = "console/organization_create.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = OrganizationForm()
        return context

    @tracer.start_as_current_span("CONSOLE Organization Create")
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        form = OrganizationForm(request.POST)
        if form.is_valid():
            org = form.save(commit=False)
            org.slug = slugify(org.name)
            org.created_by = request.user
            org.save()
            owner_membership = OrganizationMembership.objects.create(
                org=org,
                user=request.user,
                role=OrganizationMembership.OrgRole.OWNER,
            )

            created_props = Analytics.with_org_properties(
                {
                    'organization_slug': org.slug,
                },
                organization=org,
            )
            member_props = Analytics.with_org_properties(
                {
                    'member_id': str(request.user.id),
                    'member_role': owner_membership.role,
                    'actor_id': str(request.user.id),
                },
                organization=org,
            )

            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_CREATED,
                source=AnalyticsSource.WEB,
                properties=created_props.copy(),
            ))

            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_MEMBER_ADDED,
                source=AnalyticsSource.WEB,
                properties=member_props.copy(),
            ))
            messages.success(request, "Organization created successfully.")
            return redirect("organization_detail", org_id=org.id)
        return render(request, self.template_name, {"form": form})


def get_org_and_active_membership(request, org_id):
    """Return organization and the requesting user's active membership."""
    org = get_object_or_404(Organization, id=org_id)
    membership = (
        OrganizationMembership.objects.filter(
            org=org,
            user=request.user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        .select_related("user")
        .first()
    )
    return org, membership


class OrganizationDetailView(WaffleFlagMixin, ConsoleViewMixin, TemplateView):
    """Display organization details and members."""

    waffle_flag = ORGANIZATIONS
    template_name = "console/organization_detail.html"

    def dispatch(self, request, *args, **kwargs):
        self.org, self.membership = get_org_and_active_membership(
            request,
            kwargs["org_id"],
        )

        if not self.membership:
            return HttpResponseForbidden()

        self.can_manage_members = self.membership.role in MEMBER_MANAGE_ROLES
        self.can_manage_billing = self.membership.role in BILLING_MANAGE_ROLES
        self.is_org_owner = self.membership.role == OrganizationMembership.OrgRole.OWNER
        self.is_org_admin = self.membership.role == OrganizationMembership.OrgRole.ADMIN
        # Set console context to this organization when visiting its page directly
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(self.org.id)
        request.session['context_name'] = self.org.name
        request.session.modified = True
        return super().dispatch(request, *args, **kwargs)

    @tracer.start_as_current_span("CONSOLE Organization Detail")
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        members = OrganizationMembership.objects.filter(
            org=self.org, status=OrganizationMembership.OrgStatus.ACTIVE
        ).select_related("user")
        # Pending invites for this organization
        now = timezone.now()
        org_pending_invites = (
            OrganizationInvite.objects.filter(
                org=self.org,
                accepted_at__isnull=True,
                revoked_at__isnull=True,
                expires_at__gte=now,
            ).select_related("invited_by")
        )
        billing = getattr(self.org, "billing", None)

        all_role_choices = list(OrganizationMembership.OrgRole.choices)
        if self.is_org_owner:
            allowed_role_choices = all_role_choices
        elif self.is_org_admin:
            allowed_role_choices = [c for c in all_role_choices if c[0] != OrganizationMembership.OrgRole.OWNER]
        else:
            allowed_role_choices = []

        invite_form = context.get("invite_form") or OrganizationInviteForm(org=self.org)

        context.update(
            {
                "org": self.org,
                "members": members,
                "invite_form": invite_form,
                "pending_invites": org_pending_invites,
                "can_manage_members": self.can_manage_members,
                "can_manage_billing": self.can_manage_billing,
                "allowed_role_choices": allowed_role_choices,
                "is_org_owner": self.is_org_owner,
                "is_org_admin": self.is_org_admin,
                "org_billing": billing,
            }
        )
        return context

    @tracer.start_as_current_span("CONSOLE Organization Invite")
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        if not self.can_manage_members:
            return HttpResponseForbidden()

        form = OrganizationInviteForm(request.POST, org=self.org)
        # Defensive check: block when no seats available, even if submitted concurrently
        billing = getattr(self.org, "billing", None)
        if billing and billing.seats_available <= 0:
            form.add_error(None, "No seats available. Increase the seat count before inviting new members.")
        if form.is_valid():
            invite = OrganizationInvite.objects.create(
                org=self.org,
                email=form.cleaned_data["email"],
                role=form.cleaned_data["role"],
                token=uuid.uuid4().hex,
                expires_at=timezone.now() + timedelta(days=7),
                invited_by=request.user,
            )
            invite_props = Analytics.with_org_properties(
                {
                    'invite_id': str(invite.id),
                    'invite_token': invite.token,
                    'invite_role': invite.role,
                    'invite_email': invite.email,
                    'actor_id': str(request.user.id),
                },
                organization=self.org,
            )
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_INVITE_SENT,
                source=AnalyticsSource.WEB,
                properties=invite_props.copy(),
            ))
            # Send invitation email
            try:
                accept_url = request.build_absolute_uri(
                    reverse("org_invite_accept", kwargs={"token": invite.token})
                )
                reject_url = request.build_absolute_uri(
                    reverse("org_invite_reject", kwargs={"token": invite.token})
                )
                context = {
                    "org": self.org,
                    "invited_by": request.user,
                    "invite": invite,
                    "accept_url": accept_url,
                    "reject_url": reject_url,
                }
                html_body = render_to_string("emails/organization_invite.html", context)
                text_body = render_to_string("emails/organization_invite.txt", context)
                subject = f"You're invited to join {self.org.name} on Gobii"
                send_mail(
                    subject,
                    text_body,
                    None,
                    [invite.email],
                    html_message=html_body,
                    fail_silently=False,
                )
            except Exception as e:
                logger.warning("Failed sending org invite email: %s", e)
            messages.success(request, "Invite sent.")
            if request.htmx:
                response = HttpResponse(status=204)
                response["HX-Redirect"] = reverse("organization_detail", kwargs={"org_id": self.org.id})
                return response
            return redirect("organization_detail", org_id=self.org.id)

        if request.htmx:
            context = {
                "form": form,
                "org": self.org,
                "org_billing": billing,
                "can_manage_billing": self.can_manage_billing,
            }
            return render(
                request,
                "partials/_org_invite_modal.html",
                context,
                status=400,
            )

        context = self.get_context_data(invite_form=form)
        return self.render_to_response(context)


class OrganizationInviteModalView(WaffleFlagMixin, LoginRequiredMixin, View):
    waffle_flag = ORGANIZATIONS

    def dispatch(self, request, *args, **kwargs):
        self.org, self.membership = get_org_and_active_membership(
            request,
            kwargs["org_id"],
        )

        if not self.membership or self.membership.role not in MEMBER_MANAGE_ROLES:
            return HttpResponseForbidden()

        self.can_manage_billing = self.membership.role in BILLING_MANAGE_ROLES
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        context = {
            "form": OrganizationInviteForm(org=self.org),
            "org": self.org,
            "org_billing": getattr(self.org, "billing", None),
            "can_manage_billing": self.can_manage_billing,
        }
        return render(request, "partials/_org_invite_modal.html", context)


class OrganizationInviteValidationMixin:
    """Shared validation helpers for organization invite accept/reject flows."""

    def _resolve_invite_or_issue(self, request, token: str):
        """
        Returns (invite, issue, extra_ctx).
        - invite: OrganizationInvite or None
        - issue: one of None | 'invalid' | 'expired' | 'wrong_account'
        - extra_ctx: dict with optional org/invited_email/invited_by
        """
        invite = (
            OrganizationInvite.objects.select_related("org", "invited_by")
            .filter(token=token)
            .first()
        )
        current_span = trace.get_current_span()
        if not invite:
            logger.info("Organization invite token not found", extra={"token": token})
            if current_span:
                current_span.set_attribute("invite.issue", "invalid_token")
            return None, "invalid", {}

        # Expired or finalized
        if (
            invite.accepted_at is not None
            or invite.revoked_at is not None
            or invite.expires_at < timezone.now()
        ):
            logger.info(
                "Organization invite expired or not valid",
                extra={"org_id": str(invite.org_id), "token": token},
            )
            if current_span:
                current_span.set_attribute("invite.issue", "expired_or_finalized")
            return invite, "expired", {
                "org": invite.org,
                "invited_email": invite.email,
                "invited_by": invite.invited_by,
            }

        # Wrong account/session
        if not request.user.email or invite.email.lower() != request.user.email.lower():
            logger.info(
                "Organization invite wrong account/session",
                extra={"expected_email": invite.email, "actual_email": request.user.email},
            )
            if current_span:
                current_span.set_attribute("invite.issue", "wrong_account")
            return invite, "wrong_account", {
                "org": invite.org,
                "invited_email": invite.email,
                "invited_by": invite.invited_by,
            }

        return invite, None, {}


class OrganizationInviteAcceptView(OrganizationInviteValidationMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Accept an organization invite by token and join the org."""

    waffle_flag = ORGANIZATIONS

    def _accept(self, request, token: str):
        invite, issue, extra = self._resolve_invite_or_issue(request, token)
        if issue:
            ctx = {"issue": issue, "context_type": "organization_invite", "action": "accept"}
            ctx.update(extra)
            return render(request, "console/approval_link_issue.html", ctx, status=200)

        # Set console context to the invited organization for continuity
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(invite.org.id)
        request.session['context_name'] = invite.org.name
        request.session.modified = True

        # Create or reactivate membership
        membership, created = OrganizationMembership.objects.get_or_create(
            org=invite.org,
            user=request.user,
            defaults={
                "role": invite.role,
                "status": OrganizationMembership.OrgStatus.ACTIVE,
            },
        )
        was_active = membership.status == OrganizationMembership.OrgStatus.ACTIVE
        previous_role = membership.role
        if not created:
            # If membership already exists, reactivate and/or update role if necessary.
            if membership.status != OrganizationMembership.OrgStatus.ACTIVE or membership.role != invite.role:
                membership.status = OrganizationMembership.OrgStatus.ACTIVE
                membership.role = invite.role
                membership.save(update_fields=["status", "role"])

        invite.accepted_at = timezone.now()
        invite.save(update_fields=["accepted_at"])

        invite_props = Analytics.with_org_properties(
            {
                'invite_id': str(invite.id),
                'invite_token': invite.token,
                'actor_id': str(request.user.id),
                'role': invite.role,
            },
            organization=invite.org,
        )
        reactivated = (not created) and (not was_active or previous_role != invite.role)
        membership_props = Analytics.with_org_properties(
            {
                'member_id': str(request.user.id),
                'member_role': membership.role,
                'actor_id': str(request.user.id),
                'reactivated': reactivated,
            },
            organization=invite.org,
        )
        seat_props = Analytics.with_org_properties(
            {
                'member_id': str(request.user.id),
                'actor_id': str(request.user.id),
                'seat_delta': 1,
                'reactivated': reactivated,
            },
            organization=invite.org,
        )

        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_INVITE_ACCEPTED,
            source=AnalyticsSource.WEB,
            properties=invite_props.copy(),
        ))

        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_MEMBER_ADDED,
            source=AnalyticsSource.WEB,
            properties=membership_props.copy(),
        ))

        if created or not was_active:
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_SEAT_ASSIGNED,
                source=AnalyticsSource.WEB,
                properties=seat_props.copy(),
            ))
        messages.success(request, f"Joined {invite.org.name}.")
        return redirect("organization_detail", org_id=invite.org.id)

    @tracer.start_as_current_span("CONSOLE Organization Invite Accept")
    @transaction.atomic
    def post(self, request, token: str):
        return self._accept(request, token)

    @tracer.start_as_current_span("CONSOLE Organization Invite Accept")
    @transaction.atomic
    def get(self, request, token: str):
        return self._accept(request, token)


class OrganizationInviteRejectView(OrganizationInviteValidationMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Reject an organization invite by token."""

    waffle_flag = ORGANIZATIONS

    def _reject(self, request, token: str):
        invite, issue, extra = self._resolve_invite_or_issue(request, token)
        if issue:
            ctx = {"issue": issue, "context_type": "organization_invite", "action": "reject"}
            ctx.update(extra)
            return render(request, "console/approval_link_issue.html", ctx, status=200)

        # Set console context to the invite's organization for continuity
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(invite.org.id)
        request.session['context_name'] = invite.org.name
        request.session.modified = True

        if invite.accepted_at is None and invite.revoked_at is None:
            invite.revoked_at = timezone.now()
            invite.save(update_fields=["revoked_at"])
            decline_props = Analytics.with_org_properties(
                {
                    'invite_id': str(invite.id),
                    'invite_token': invite.token,
                    'actor_id': str(request.user.id),
                    'reason': 'declined',
                },
                organization=invite.org,
            )
            seat_props = Analytics.with_org_properties(
                {
                    'actor_id': str(request.user.id),
                    'seat_delta': -1,
                    'reason': 'invite_declined',
                },
                organization=invite.org,
            )
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_INVITE_DECLINED,
                source=AnalyticsSource.WEB,
                properties=decline_props.copy(),
            ))
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_SEAT_UNASSIGNED,
                source=AnalyticsSource.WEB,
                properties=seat_props.copy(),
            ))
            messages.info(request, "Invitation declined.")
        else:
            # Should not hit due to resolver, but keep safety
            return render(request, "console/approval_link_issue.html", {
                "issue": "expired",
                "context_type": "organization_invite",
                "action": "reject",
                "org": invite.org,
                "invited_email": invite.email,
                "invited_by": invite.invited_by,
            }, status=200)
        return redirect("organizations")

    @tracer.start_as_current_span("CONSOLE Organization Invite Reject")
    @transaction.atomic
    def post(self, request, token: str):
        return self._reject(request, token)

    @tracer.start_as_current_span("CONSOLE Organization Invite Reject")
    @transaction.atomic
    def get(self, request, token: str):
        return self._reject(request, token)


class OrganizationSeatCheckoutView(StripeFeatureRequiredMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Kick off Stripe Checkout to purchase seats for an organization."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Seat Checkout")
    @transaction.atomic
    def post(self, request, org_id: str):
        org = get_object_or_404(Organization.objects.select_related("billing"), id=org_id)

        membership = OrganizationMembership.objects.filter(
            org=org,
            user=request.user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
            role__in=(
                OrganizationMembership.OrgRole.OWNER,
                OrganizationMembership.OrgRole.ADMIN,
                OrganizationMembership.OrgRole.BILLING,
            ),
        ).first()

        if membership is None:
            return HttpResponseForbidden()

        form = OrganizationSeatPurchaseForm(request.POST, org=org)
        if not form.is_valid():
            for error in form.errors.get("seats", []):
                messages.error(request, error)
            return redirect("billing")

        billing = getattr(org, "billing", None)
        seat_count = form.cleaned_data["seats"]
        if seat_count <= 0:
            messages.error(request, "Please select at least one seat to purchase.")
            return redirect("billing")

        stripe_settings = get_stripe_settings()
        seat_price_id = stripe_settings.org_team_price_id

        if billing and getattr(billing, "stripe_subscription_id", None):
            # Organization already has an active subscription; push the user through
            # Stripe Checkout so they explicitly confirm the updated quantity.
            try:
                _assign_stripe_api_key()
                subscription = stripe.Subscription.retrieve(
                    billing.stripe_subscription_id,
                    expand=["items.data.price"],
                )

                subscription_items = subscription.get("items", {}).get("data", []) or []
                licensed_item = None
                for item in subscription_items:
                    price = item.get("price", {}) or {}
                    price_usage_type = price.get("usage_type") or (price.get("recurring", {}) or {}).get("usage_type")
                    price_id = price.get("id")
                    if price_usage_type == "licensed" or (seat_price_id and price_id == seat_price_id):
                        licensed_item = item
                        break

                if not licensed_item:
                    messages.error(
                        request,
                        "We couldn't find a seat item on the active subscription. Please contact support.",
                    )
                    return redirect("billing")

                current_quantity = int(licensed_item.get("quantity") or 0)
                if current_quantity < 0:
                    current_quantity = 0
                new_quantity = current_quantity + seat_count

                request.session["org_seat_portal_target"] = {
                    "org_id": str(org.id),
                    "current": current_quantity,
                    "requested": new_quantity,
                }

                return_url = request.build_absolute_uri(reverse("billing")) + "?seats_success=1"
                cancel_url = request.build_absolute_uri(reverse("billing")) + "?seats_cancelled=1"

                overage_detach_performed = _detach_org_overage_item(
                    subscription,
                    stripe_settings.org_team_additional_task_price_id,
                    str(org.id),
                    request,
                )

                try:
                    session = stripe.billing_portal.Session.create(
                        api_key=stripe.api_key,
                        customer=subscription.get("customer"),
                        flow_data={
                            "type": "subscription_update_confirm",
                            "subscription_update_confirm": {
                                "subscription": subscription.get("id"),
                                "items": [
                                    {
                                        "id": licensed_item.get("id"),
                                        "quantity": new_quantity,
                                    }
                                ],
                            },
                        },
                        return_url=return_url,
                    )

                    _track_org_event_for_console(
                        request,
                        AnalyticsEvent.ORGANIZATION_SEAT_ADDED,
                        {
                            'actor_id': str(request.user.id),
                            'seats_requested': seat_count,
                            'current_quantity': current_quantity,
                            'target_quantity': new_quantity,
                            'method': 'portal',
                        },
                        organization=org,
                    )
                    _track_org_event_for_console(
                        request,
                        AnalyticsEvent.ORGANIZATION_BILLING_UPDATED,
                        {
                            'actor_id': str(request.user.id),
                            'update_type': 'seats_portal_increase',
                            'seats_requested': seat_count,
                        },
                        organization=org,
                    )
                    return redirect(session.url)
                except stripe.error.InvalidRequestError as portal_exc:
                    logger.warning(
                        "Stripe portal seat update unavailable for subscription %s on org %s. Falling back to direct seat update: %s",
                        getattr(billing, "stripe_subscription_id", None),
                        org.id,
                        portal_exc,
                    )

                    request.session.pop("org_seat_portal_target", None)

                    try:
                        stripe.Subscription.modify(
                            subscription.get("id"),
                            items=[
                                {
                                    "id": licensed_item.get("id"),
                                    "quantity": new_quantity,
                                }
                            ],
                            metadata={
                                **(subscription.get("metadata") or {}),
                                "seat_requestor_id": str(request.user.id),
                            },
                            proration_behavior="create_prorations",
                        )

                        if overage_detach_performed:
                            reattached = _reattach_overage_from_session(request, str(org.id))
                            if not reattached:
                                logger.warning(
                                    "Failed to reattach overage SKU after direct seat update for org %s",
                                    org.id,
                                )

                        messages.warning(
                            request,
                            "Stripe portal seat updates are disabled, so we applied the seat change immediately. Additional seats will activate once Stripe processes the change.",
                        )
                        _track_org_event_for_console(
                            request,
                            AnalyticsEvent.ORGANIZATION_SEAT_ADDED,
                            {
                                'actor_id': str(request.user.id),
                                'seats_requested': seat_count,
                                'current_quantity': current_quantity,
                                'target_quantity': new_quantity,
                                'method': 'direct_update',
                            },
                            organization=org,
                        )
                        _track_org_event_for_console(
                            request,
                            AnalyticsEvent.ORGANIZATION_BILLING_UPDATED,
                            {
                                'actor_id': str(request.user.id),
                                'update_type': 'seats_direct_increase',
                                'seats_requested': seat_count,
                            },
                            organization=org,
                        )
                    except Exception as modify_exc:
                        logger.exception(
                            "Failed to update Stripe subscription %s for org %s after portal fallback: %s",
                            getattr(billing, "stripe_subscription_id", None),
                            org.id,
                            modify_exc,
                        )
                        if overage_detach_performed:
                            reattached = _reattach_overage_from_session(request, str(org.id))
                            if not reattached:
                                logger.warning(
                                    "Failed to reattach overage SKU after modify error for org %s",
                                    org.id,
                                )
                        messages.error(
                            request,
                            "We weren't able to update the seat count. Please try again or contact support.",
                        )

                    return redirect("billing")
                except Exception as portal_exc:
                    if overage_detach_performed:
                        reattached = _reattach_overage_from_session(request, str(org.id))
                        if not reattached:
                            logger.warning(
                                "Failed to reattach overage SKU after portal error for org %s",
                                org.id,
                            )
                    raise portal_exc
            except Exception as exc:
                logger.exception(
                    "Failed to start Stripe portal update for subscription %s on org %s: %s",
                    getattr(billing, "stripe_subscription_id", None),
                    org.id,
                    exc,
                )
                request.session.pop("org_seat_portal_target", None)
                messages.error(
                    request,
                    "We weren't able to start the checkout flow. Please try again or contact support.",
                )

            return redirect("billing")

        price_id = seat_price_id
        if not price_id:
            messages.error(request, "Stripe price not configured. Please contact support.")
            return redirect("billing")

        try:
            _assign_stripe_api_key()
            customer = get_or_create_stripe_customer(org)

            success_url = request.build_absolute_uri(
                reverse("billing")
            ) + "?seats_success=1"
            cancel_url = request.build_absolute_uri(
                reverse("billing")
            ) + "?seats_cancelled=1"

            line_items = [
                {
                    "price": price_id,
                    "quantity": seat_count,
                }
            ]

            session = stripe.checkout.Session.create(
                customer=customer.id,
                api_key=stripe.api_key,
                mode="subscription",
                success_url=success_url,
                cancel_url=cancel_url,
                allow_promotion_codes=True,
                line_items=line_items,
                metadata={
                    "org_id": str(org.id),
                    "seat_requestor_id": str(request.user.id),
                },
            )

            _track_org_event_for_console(
                request,
                AnalyticsEvent.ORGANIZATION_SEAT_ADDED,
                {
                    'actor_id': str(request.user.id),
                    'seats_requested': seat_count,
                    'method': 'checkout',
                },
                organization=org,
            )
            _track_org_event_for_console(
                request,
                AnalyticsEvent.ORGANIZATION_BILLING_UPDATED,
                {
                    'actor_id': str(request.user.id),
                    'update_type': 'seats_checkout_initiated',
                    'seats_requested': seat_count,
                },
                organization=org,
            )
            return redirect(session.url)
        except Exception as exc:
            logger.exception("Failed to create Stripe checkout session for org %s: %s", org.id, exc)
            messages.error(
                request,
                "We weren’t able to start the checkout flow. Please try again or contact support.",
            )
            return redirect("billing")


class OrganizationSeatScheduleView(StripeFeatureRequiredMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Schedule a reduction in organization seats effective next billing cycle."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Seat Schedule")
    @transaction.atomic
    def post(self, request, org_id: str):
        org = get_object_or_404(Organization.objects.select_related("billing"), id=org_id)

        membership = OrganizationMembership.objects.filter(
            org=org,
            user=request.user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
            role__in=(
                OrganizationMembership.OrgRole.OWNER,
                OrganizationMembership.OrgRole.ADMIN,
                OrganizationMembership.OrgRole.BILLING,
            ),
        ).first()

        if membership is None:
            return HttpResponseForbidden()

        form = OrganizationSeatReductionForm(request.POST, org=org)
        if not form.is_valid():
            for error in form.errors.get("future_seats", []):
                messages.error(request, error)
            return redirect("billing")

        billing = getattr(org, "billing", None)
        if not billing or not getattr(billing, "stripe_subscription_id", None):
            messages.error(request, "This organization does not have an active Stripe subscription yet.")
            return redirect("billing")

        target_quantity = form.cleaned_data["future_seats"]

        stripe_settings = get_stripe_settings()
        seat_price_id = stripe_settings.org_team_price_id

        if not seat_price_id:
            messages.error(request, "Stripe seat price not configured. Please contact support.")
            return redirect("billing")

        try:
            _assign_stripe_api_key()
            subscription = stripe.Subscription.retrieve(
                billing.stripe_subscription_id,
                expand=["items.data.price"],
            )

            licensed_item = None
            subscription_items = subscription.get("items", {}).get("data", []) or []
            for item in subscription_items:
                price = item.get("price", {}) or {}
                usage_type = price.get("usage_type") or (price.get("recurring", {}) or {}).get("usage_type")
                price_id = price.get("id")
                if usage_type == "licensed" or (price_id and price_id == seat_price_id):
                    licensed_item = item
                    break

            if not licensed_item:
                messages.error(
                    request,
                    "We couldn't find a seat item on the active subscription. Please contact support.",
                )
                return redirect("billing")

            try:
                current_quantity = int(licensed_item.get("quantity") or 0)
            except (TypeError, ValueError):
                current_quantity = 0

            if current_quantity <= 0:
                messages.error(request, "No seats are currently active to reduce.")
                return redirect("billing")

            if target_quantity >= current_quantity:
                messages.error(
                    request,
                    "Enter a number smaller than your current seat total to schedule a reduction.",
                )
                return redirect("billing")

            existing_schedule_id = subscription.get("schedule") or getattr(billing, "pending_seat_schedule_id", "")
            if existing_schedule_id:
                try:
                    stripe.SubscriptionSchedule.release(existing_schedule_id)
                except Exception as exc:  # pragma: no cover - unexpected Stripe error
                    logger.exception(
                        "Failed to release existing Stripe schedule %s for org %s: %s",
                        existing_schedule_id,
                        org.id,
                        exc,
                    )
                    messages.error(
                        request,
                        "We weren't able to update the seat schedule. Please try again or contact support.",
                    )
                    return redirect("billing")

                billing.pending_seat_quantity = None
                billing.pending_seat_effective_at = None
                billing.pending_seat_schedule_id = ""
                billing.save(
                    update_fields=[
                        "pending_seat_quantity",
                        "pending_seat_effective_at",
                        "pending_seat_schedule_id",
                    ]
                )

            current_phase_items: list[dict[str, object]] = []
            next_phase_items: list[dict[str, object]] = []

            for item in subscription_items:
                price = item.get("price", {}) or {}
                price_id = price.get("id")
                if not price_id:
                    continue

                usage_type = price.get("usage_type") or (price.get("recurring", {}) or {}).get("usage_type")
                is_seat_item = (
                    item is licensed_item or usage_type == "licensed" or (price_id and price_id == seat_price_id)
                )

                try:
                    quantity = int(item.get("quantity") or 0)
                except (TypeError, ValueError):
                    quantity = 0

                current_payload: dict[str, object] = {"price": price_id}
                next_payload: dict[str, object] = {"price": price_id}

                if is_seat_item:
                    current_payload["quantity"] = current_quantity
                    next_payload["quantity"] = target_quantity
                elif usage_type != "metered" and quantity > 0:
                    current_payload["quantity"] = quantity
                    next_payload["quantity"] = quantity

                current_phase_items.append(current_payload)
                next_phase_items.append(next_payload)

            current_period_start_ts = subscription.get("current_period_start")
            current_period_end_ts = subscription.get("current_period_end")

            phases: list[dict[str, object]] = [
                {
                    "items": current_phase_items,
                    "proration_behavior": "none",
                },
                {
                    "items": next_phase_items,
                    "proration_behavior": "none",
                },
            ]

            if current_period_start_ts:
                phases[0]["start_date"] = int(current_period_start_ts)
            if current_period_end_ts:
                periods_end_int = int(current_period_end_ts)
                phases[0]["end_date"] = periods_end_int
                phases[1]["start_date"] = periods_end_int

            metadata = {
                "org_id": str(org.id),
                "seat_requestor_id": str(request.user.id),
                "seat_target_quantity": str(target_quantity),
            }

            schedule = stripe.SubscriptionSchedule.create(
                from_subscription=subscription.get("id"),
            )

            stripe.SubscriptionSchedule.modify(
                getattr(schedule, "id", ""),
                phases=phases,
                end_behavior="release",
                metadata=metadata,
            )

            period_end_ts = current_period_end_ts
            effective_at = None
            if period_end_ts:
                try:
                    effective_at = datetime.fromtimestamp(int(period_end_ts), tz=dt_timezone.utc)
                except (TypeError, ValueError, OSError):
                    effective_at = None

            billing.pending_seat_quantity = target_quantity
            billing.pending_seat_effective_at = effective_at
            billing.pending_seat_schedule_id = getattr(schedule, "id", "") or ""
            billing.save(
                update_fields=[
                    "pending_seat_quantity",
                    "pending_seat_effective_at",
                    "pending_seat_schedule_id",
                ]
            )

            messages.success(
                request,
                "Seat reduction scheduled. The new total will apply at the start of the next billing period.",
            )
            _track_org_event_for_console(
                request,
                AnalyticsEvent.ORGANIZATION_SEAT_REMOVED,
                {
                    'actor_id': str(request.user.id),
                    'target_quantity': target_quantity,
                    'current_quantity': current_quantity,
                    'method': 'schedule',
                },
                organization=org,
            )
            _track_org_event_for_console(
                request,
                AnalyticsEvent.ORGANIZATION_BILLING_UPDATED,
                {
                    'actor_id': str(request.user.id),
                    'update_type': 'seats_schedule_reduction',
                    'target_quantity': target_quantity,
                },
                organization=org,
            )
        except Exception as exc:  # pragma: no cover - unexpected Stripe error
            logger.exception(
                "Failed to create Stripe seat schedule for org %s (sub %s): %s",
                org.id,
                getattr(billing, "stripe_subscription_id", None),
                exc,
            )
            messages.error(
                request,
                "We weren't able to schedule the seat reduction. Please try again or contact support.",
            )

        return redirect("billing")


class OrganizationSeatScheduleCancelView(StripeFeatureRequiredMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Cancel any pending seat reductions for an organization."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Seat Schedule Cancel")
    @transaction.atomic
    def post(self, request, org_id: str):
        org = get_object_or_404(Organization.objects.select_related("billing"), id=org_id)

        membership = OrganizationMembership.objects.filter(
            org=org,
            user=request.user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
            role__in=(
                OrganizationMembership.OrgRole.OWNER,
                OrganizationMembership.OrgRole.ADMIN,
                OrganizationMembership.OrgRole.BILLING,
            ),
        ).first()

        if membership is None:
            return HttpResponseForbidden()

        billing = getattr(org, "billing", None)
        schedule_id = getattr(billing, "pending_seat_schedule_id", "") if billing else ""

        if not billing or not schedule_id:
            messages.info(request, "No scheduled seat changes to cancel.")
            return redirect("billing")

        try:
            _assign_stripe_api_key()
            stripe.SubscriptionSchedule.release(schedule_id)
        except Exception as exc:  # pragma: no cover - unexpected Stripe error
            logger.exception(
                "Failed to release Stripe schedule %s for org %s: %s",
                schedule_id,
                org.id,
                exc,
            )
            messages.error(
                request,
                "We weren't able to cancel the scheduled seat change. Please try again or contact support.",
            )
            return redirect("billing")

        billing.pending_seat_quantity = None
        billing.pending_seat_effective_at = None
        billing.pending_seat_schedule_id = ""
        billing.save(
            update_fields=[
                "pending_seat_quantity",
                "pending_seat_effective_at",
                "pending_seat_schedule_id",
            ]
        )

        _track_org_event_for_console(
            request,
            AnalyticsEvent.ORGANIZATION_BILLING_UPDATED,
            {
                'actor_id': str(request.user.id),
                'update_type': 'seats_schedule_cancelled',
            },
            organization=org,
        )
        messages.success(request, "Scheduled seat changes were cancelled.")
        return redirect("billing")


class OrganizationSeatPortalView(StripeFeatureRequiredMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Open the Stripe billing portal to manage existing organization seats."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Seat Portal")
    @transaction.atomic
    def post(self, request, org_id: str):
        org = get_object_or_404(Organization.objects.select_related("billing"), id=org_id)

        membership = OrganizationMembership.objects.filter(
            org=org,
            user=request.user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
            role__in=(
                OrganizationMembership.OrgRole.OWNER,
                OrganizationMembership.OrgRole.ADMIN,
                OrganizationMembership.OrgRole.BILLING,
            ),
        ).first()

        if membership is None:
            return HttpResponseForbidden()

        billing = getattr(org, "billing", None)
        if not billing or not billing.stripe_customer_id:
            messages.error(request, "This organization does not have an active Stripe subscription yet.")
            return redirect("billing")

        try:
            _assign_stripe_api_key()

            return_url = request.build_absolute_uri(reverse("billing"))

            session = stripe.billing_portal.Session.create(
                customer=billing.stripe_customer_id,
                api_key=stripe.api_key,
                return_url=return_url,
            )

            return redirect(session.url)
        except Exception as exc:
            logger.exception("Failed to create Stripe billing portal session for org %s: %s", org.id, exc)
            messages.error(
                request,
                "We weren’t able to open the Stripe billing portal. Please try again or contact support.",
            )
            return redirect("billing")


class _OrgPermissionMixin:
    """Utilities for checking org membership/role permissions."""

    def _require_org_admin(self, request, org: Organization):
        try:
            membership = OrganizationMembership.objects.get(org=org, user=request.user)
        except OrganizationMembership.DoesNotExist:
            return None
        if membership.status != OrganizationMembership.OrgStatus.ACTIVE:
            return None
        # Allow OWNER and ADMIN to manage invites
        if membership.role not in (
            OrganizationMembership.OrgRole.OWNER,
            OrganizationMembership.OrgRole.ADMIN,
        ):
            return None
        return membership


class OrganizationInviteRevokeOrgView(_OrgPermissionMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Revoke a pending invite from the org detail page."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Invite Revoke (Org)")
    @transaction.atomic
    def post(self, request, org_id: str, token: str):
        org = get_object_or_404(Organization, id=org_id)
        # Set context to this organization
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(org.id)
        request.session['context_name'] = org.name
        request.session.modified = True
        if not self._require_org_admin(request, org):
            return HttpResponseForbidden()

        invite = get_object_or_404(OrganizationInvite, org=org, token=token)
        if invite.accepted_at or invite.revoked_at:
            messages.error(request, "Invite is already finalized.")
        else:
            invite.revoked_at = timezone.now()
            invite.save(update_fields=["revoked_at"])
            revoke_props = Analytics.with_org_properties(
                {
                    'invite_id': str(invite.id),
                    'invite_token': invite.token,
                    'actor_id': str(request.user.id),
                    'reason': 'revoked',
                },
                organization=org,
            )
            seat_props = Analytics.with_org_properties(
                {
                    'actor_id': str(request.user.id),
                    'seat_delta': -1,
                    'reason': 'invite_revoked',
                },
                organization=org,
            )
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_INVITE_DECLINED,
                source=AnalyticsSource.WEB,
                properties=revoke_props.copy(),
            ))
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_SEAT_UNASSIGNED,
                source=AnalyticsSource.WEB,
                properties=seat_props.copy(),
            ))
            messages.success(request, "Invitation revoked.")
        return redirect("organization_detail", org_id=org.id)


class OrganizationInviteResendOrgView(_OrgPermissionMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Resend a pending invite email from the org detail page."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Invite Resend (Org)")
    @transaction.atomic
    def post(self, request, org_id: str, token: str):
        org = get_object_or_404(Organization, id=org_id)
        # Set context to this organization
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(org.id)
        request.session['context_name'] = org.name
        request.session.modified = True
        if not self._require_org_admin(request, org):
            return HttpResponseForbidden()

        invite = get_object_or_404(OrganizationInvite, org=org, token=token)
        if invite.accepted_at or invite.revoked_at or invite.expires_at < timezone.now():
            messages.error(request, "Cannot resend: invite is no longer valid.")
            return redirect("organization_detail", org_id=org.id)

        try:
            accept_url = request.build_absolute_uri(
                reverse("org_invite_accept", kwargs={"token": invite.token})
            )
            reject_url = request.build_absolute_uri(
                reverse("org_invite_reject", kwargs={"token": invite.token})
            )
            context = {
                "org": org,
                "invited_by": request.user,
                "invite": invite,
                "accept_url": accept_url,
                "reject_url": reject_url,
            }
            html_body = render_to_string("emails/organization_invite.html", context)
            text_body = render_to_string("emails/organization_invite.txt", context)
            subject = f"You're invited to join {org.name} on Gobii"
            send_mail(
                subject,
                text_body,
                None,
                [invite.email],
                html_message=html_body,
                fail_silently=False,
            )
            resend_props = Analytics.with_org_properties(
                {
                    'invite_id': str(invite.id),
                    'invite_token': invite.token,
                    'actor_id': str(request.user.id),
                    'resend': True,
                },
                organization=org,
            )
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_INVITE_SENT,
                source=AnalyticsSource.WEB,
                properties=resend_props.copy(),
            ))
            messages.success(request, "Invitation email resent.")
        except Exception as e:
            logger.warning("Failed resending org invite email: %s", e)
            messages.error(request, "Failed to resend invitation email.")

        return redirect("organization_detail", org_id=org.id)


class OrganizationMemberRemoveOrgView(_OrgPermissionMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Remove a member from an organization (mark membership removed)."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Member Remove (Org)")
    @transaction.atomic
    def post(self, request, org_id: str, user_id: int):
        org = get_object_or_404(Organization, id=org_id)
        # Set context to this organization
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(org.id)
        request.session['context_name'] = org.name
        request.session.modified = True
        acting_membership = self._require_org_admin(request, org)
        if not acting_membership:
            return HttpResponseForbidden()

        # Prevent removing self via this action
        if request.user.id == user_id:
            messages.error(request, "You cannot remove yourself.")
            return redirect("organization_detail", org_id=org.id)

        target_membership = get_object_or_404(
            OrganizationMembership,
            org=org,
            user_id=user_id,
        )

        if target_membership.status != OrganizationMembership.OrgStatus.ACTIVE:
            messages.info(request, "This member is already removed.")
            return redirect("organization_detail", org_id=org.id)

        # Admins cannot remove owners
        if (
            acting_membership.role == OrganizationMembership.OrgRole.ADMIN
            and target_membership.role == OrganizationMembership.OrgRole.OWNER
        ):
            return HttpResponseForbidden()

        # Do not remove the last owner
        if target_membership.role == OrganizationMembership.OrgRole.OWNER:
            active_owner_count = OrganizationMembership.objects.filter(
                org=org,
                role=OrganizationMembership.OrgRole.OWNER,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).count()
            if active_owner_count <= 1:
                messages.error(request, "You must keep at least one owner in the organization.")
                return redirect("organization_detail", org_id=org.id)

        target_membership.status = OrganizationMembership.OrgStatus.REMOVED
        target_membership.save(update_fields=["status"])
        removal_props = Analytics.with_org_properties(
            {
                'member_id': str(target_membership.user_id),
                'member_role': target_membership.role,
                'actor_id': str(request.user.id),
                'reason': 'removed_by_admin',
            },
            organization=org,
        )
        seat_props = Analytics.with_org_properties(
            {
                'member_id': str(target_membership.user_id),
                'actor_id': str(request.user.id),
                'seat_delta': -1,
                'reason': 'member_removed',
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_MEMBER_REMOVED,
            source=AnalyticsSource.WEB,
            properties=removal_props.copy(),
        ))
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_SEAT_UNASSIGNED,
            source=AnalyticsSource.WEB,
            properties=seat_props.copy(),
        ))
        messages.success(request, "Member removed.")
        return redirect("organization_detail", org_id=org.id)


class OrganizationLeaveOrgView(WaffleFlagMixin, LoginRequiredMixin, View):
    """Allow a user to leave an organization, with safeguards."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Leave (Org)")
    @transaction.atomic
    def post(self, request, org_id: str):
        org = get_object_or_404(Organization, id=org_id)
        # Ensure context is set to this org for the operation
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(org.id)
        request.session['context_name'] = org.name
        request.session.modified = True
        try:
            membership = OrganizationMembership.objects.get(org=org, user=request.user)
        except OrganizationMembership.DoesNotExist:
            return HttpResponseForbidden()

        if membership.status != OrganizationMembership.OrgStatus.ACTIVE:
            messages.info(request, "You are not an active member of this organization.")
            return redirect("organizations")

        # Prevent leaving if this is the last remaining owner
        if membership.role == OrganizationMembership.OrgRole.OWNER:
            active_owner_count = OrganizationMembership.objects.filter(
                org=org,
                role=OrganizationMembership.OrgRole.OWNER,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).count()
            if active_owner_count <= 1:
                messages.error(request, "You are the last owner. Transfer ownership or add another owner before leaving.")
                return redirect("organization_detail", org_id=org.id)

        membership.status = OrganizationMembership.OrgStatus.REMOVED
        membership.save(update_fields=["status"])
        removal_props = Analytics.with_org_properties(
            {
                'member_id': str(request.user.id),
                'member_role': membership.role,
                'actor_id': str(request.user.id),
                'reason': 'left_organization',
            },
            organization=org,
        )
        seat_props = Analytics.with_org_properties(
            {
                'member_id': str(request.user.id),
                'actor_id': str(request.user.id),
                'seat_delta': -1,
                'reason': 'member_left',
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_MEMBER_REMOVED,
            source=AnalyticsSource.WEB,
            properties=removal_props.copy(),
        ))
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_SEAT_UNASSIGNED,
            source=AnalyticsSource.WEB,
            properties=seat_props.copy(),
        ))
        # After leaving, reset context back to personal
        request.session['context_type'] = 'personal'
        request.session['context_id'] = str(request.user.id)
        request.session['context_name'] = request.user.get_full_name() or request.user.username
        request.session.modified = True
        messages.success(request, f"You left {org.name}.")
        return redirect("organizations")


class OrganizationMemberRoleUpdateOrgView(_OrgPermissionMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Change a member's role within an org with basic guardrails."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Member Role Update (Org)")
    @transaction.atomic
    def post(self, request, org_id: str, user_id: int):
        org = get_object_or_404(Organization, id=org_id)
        # Set context to this organization
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(org.id)
        request.session['context_name'] = org.name
        request.session.modified = True
        acting_membership = self._require_org_admin(request, org)
        if not acting_membership:
            return HttpResponseForbidden()

        new_role = request.POST.get("role")
        valid_roles = {choice[0] for choice in OrganizationMembership.OrgRole.choices}
        if new_role not in valid_roles:
            return HttpResponseForbidden()

        target_membership = get_object_or_404(
            OrganizationMembership,
            org=org,
            user_id=user_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        # No-op
        if target_membership.role == new_role:
            messages.info(request, "Role unchanged.")
            return redirect("organization_detail", org_id=org.id)

        # Admins cannot modify Owners, nor assign Owner role
        if acting_membership.role == OrganizationMembership.OrgRole.ADMIN:
            if target_membership.role == OrganizationMembership.OrgRole.OWNER:
                return HttpResponseForbidden()
            if new_role == OrganizationMembership.OrgRole.OWNER:
                return HttpResponseForbidden()

        # Prevent demoting the last Owner
        if target_membership.role == OrganizationMembership.OrgRole.OWNER and new_role != OrganizationMembership.OrgRole.OWNER:
            active_owner_count = OrganizationMembership.objects.filter(
                org=org,
                role=OrganizationMembership.OrgRole.OWNER,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).count()
            if active_owner_count <= 1:
                messages.error(request, "You must keep at least one owner in the organization.")
                return redirect("organization_detail", org_id=org.id)

        previous_role = target_membership.role
        target_membership.role = new_role
        target_membership.save(update_fields=["role"])
        role_props = Analytics.with_org_properties(
            {
                'member_id': str(target_membership.user_id),
                'actor_id': str(request.user.id),
                'old_role': previous_role,
                'new_role': new_role,
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_MEMBER_ROLE_UPDATED,
            source=AnalyticsSource.WEB,
            properties=role_props.copy(),
        ))
        messages.success(request, "Member role updated.")
        return redirect("organization_detail", org_id=org.id)


class AgentTransferInviteRespondView(LoginRequiredMixin, View):
    """Handle accept/decline actions for agent transfer invites."""

    http_method_names = ["post"]

    def post(self, request, invite_id: uuid.UUID, action: str):
        invite = get_object_or_404(
            AgentTransferInvite.objects.select_related("agent", "agent__user"),
            pk=invite_id,
        )

        if invite.status != AgentTransferInvite.Status.PENDING:
            messages.info(request, "This transfer invite has already been handled.")
            return redirect('console-home')

        user_email = (request.user.email or "").lower()
        if not user_email or invite.to_email.lower() != user_email:
            messages.error(request, "This transfer invite is not addressed to your account.")
            return redirect('console-home')

        original_owner = invite.initiated_by
        original_owner_email = getattr(original_owner, "email", "") or ""
        agent_before = invite.agent

        try:
            if action == 'accept':
                invite = AgentTransferService.accept_invite(invite, request.user)
                agent = invite.agent
                agent.refresh_from_db(fields=["name", "is_active"])
                if not agent.is_active:
                    messages.warning(
                        request,
                        f"You now own {agent.name}, but it has been paused because you are at your agent limit.",
                    )
                else:
                    messages.success(request, f"You now own {agent.name}.")

                if original_owner_email:
                    try:
                        agent_url = request.build_absolute_uri(reverse('agent_detail', args=[agent.id]))
                        context = {
                            'owner_name': original_owner.get_full_name() or original_owner_email,
                            'recipient_name': request.user.get_full_name() or request.user.email,
                            'agent': agent,
                            'agent_url': agent_url,
                        }
                        subject = f"{context['recipient_name']} accepted your agent {agent.name}"
                        text_body = render_to_string('emails/agent_transfer_owner_accepted.txt', context)
                        html_body = render_to_string('emails/agent_transfer_owner_accepted.html', context)
                        send_mail(
                            subject,
                            text_body,
                            None,
                            [original_owner_email],
                            html_message=html_body,
                            fail_silently=True,
                        )
                    except Exception as email_exc:  # pragma: no cover - best effort
                        logger.warning(
                            "Failed to send transfer acceptance email to %s: %s",
                            original_owner_email,
                            email_exc,
                        )
            elif action == 'decline':
                invite = AgentTransferService.decline_invite(invite, request.user)
                messages.info(request, "Transfer invitation declined.")

                if original_owner_email:
                    try:
                        agent_url = request.build_absolute_uri(reverse('agent_detail', args=[agent_before.id]))
                        context = {
                            'owner_name': original_owner.get_full_name() or original_owner_email,
                            'recipient_name': request.user.get_full_name() or request.user.email,
                            'agent': agent_before,
                            'agent_url': agent_url,
                        }
                        subject = f"{context['recipient_name']} declined your agent {agent_before.name}"
                        text_body = render_to_string('emails/agent_transfer_owner_declined.txt', context)
                        html_body = render_to_string('emails/agent_transfer_owner_declined.html', context)
                        send_mail(
                            subject,
                            text_body,
                            None,
                            [original_owner_email],
                            html_message=html_body,
                            fail_silently=True,
                        )
                    except Exception as email_exc:  # pragma: no cover - best effort
                        logger.warning(
                            "Failed to send transfer decline email to %s: %s",
                            original_owner_email,
                            email_exc,
                        )
            else:
                messages.error(request, "Unsupported invite action.")
        except AgentTransferDenied as exc:
            messages.error(request, str(exc))
        except AgentTransferError as exc:
            messages.error(request, f"Could not process the transfer invite: {exc}")

        return redirect('console-home')


class AgentAllowlistInviteAcceptView(TemplateView):
    """Handle accepting an agent allowlist invitation."""
    template_name = "console/agent_allowlist_invite_response.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        token = kwargs.get("token")
        
        try:
            # Use select_related and prefetch_related for efficiency
            invite = AgentAllowlistInvite.objects.select_related('agent__user').prefetch_related('agent__comms_endpoints').get(token=token)
            context["invite"] = invite
            context["agent"] = invite.agent
            
            if invite.status != AgentAllowlistInvite.InviteStatus.PENDING:
                context["already_responded"] = True
                context["status"] = invite.get_status_display()
            elif invite.is_expired():
                context["expired"] = True
            else:
                context["can_accept"] = True
                
        except AgentAllowlistInvite.DoesNotExist:
            context["invalid_token"] = True
            
        return context
    
    def post(self, request, *args, **kwargs):
        token = kwargs.get("token")
        
        try:
            invite = AgentAllowlistInvite.objects.get(token=token)
            
            if not invite.can_be_accepted():
                messages.error(request, "This invitation is no longer valid.")
                return redirect("agent_allowlist_invite_accept", token=token)
            
            # Accept the invitation
            invite.accept()
            messages.success(
                request, 
                f"Great! You can now communicate with {invite.agent.name} by email."
            )
            
        except AgentAllowlistInvite.DoesNotExist:
            messages.error(request, "Invalid invitation token.")
        except Exception as e:
            messages.error(request, f"Error accepting invitation: {e}")
            
        return redirect("agent_allowlist_invite_accept", token=token)


class AgentAllowlistInviteRejectView(TemplateView):
    """Handle rejecting an agent allowlist invitation.""" 
    template_name = "console/agent_allowlist_invite_response.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        token = kwargs.get("token")
        
        try:
            # Use select_related and prefetch_related for efficiency
            invite = AgentAllowlistInvite.objects.select_related('agent__user').prefetch_related('agent__comms_endpoints').get(token=token)
            context["invite"] = invite
            context["agent"] = invite.agent
            context["rejecting"] = True
            
            if invite.status != AgentAllowlistInvite.InviteStatus.PENDING:
                context["already_responded"] = True  
                context["status"] = invite.get_status_display()
            elif invite.is_expired():
                context["expired"] = True
            else:
                context["can_reject"] = True
                
        except AgentAllowlistInvite.DoesNotExist:
            context["invalid_token"] = True
            
        return context
    
    def post(self, request, *args, **kwargs):
        token = kwargs.get("token")
        
        try:
            invite = AgentAllowlistInvite.objects.get(token=token)
            
            if invite.status != AgentAllowlistInvite.InviteStatus.PENDING:
                messages.error(request, "This invitation has already been responded to.")
                return redirect("agent_allowlist_invite_reject", token=token)
            
            # Reject the invitation
            invite.reject()
            messages.success(request, "You have declined the invitation.")
            
        except AgentAllowlistInvite.DoesNotExist:
            messages.error(request, "Invalid invitation token.")
        except Exception as e:
            messages.error(request, f"Error rejecting invitation: {e}")
            
        return redirect("agent_allowlist_invite_reject", token=token)


class AgentCollaboratorInviteAcceptView(LoginRequiredMixin, TemplateView):
    """Handle accepting an agent collaborator invitation."""
    template_name = "console/agent_collaborator_invite_response.html"

    def _user_matches_invite(self, user, invite: AgentCollaboratorInvite) -> bool:
        invite_email = (invite.email or "").strip().lower()
        if not invite_email:
            return False
        if (user.email or "").strip().lower() == invite_email:
            return True
        try:
            from allauth.account.models import EmailAddress
            return EmailAddress.objects.filter(user=user, email__iexact=invite_email).exists()
        except Exception:
            return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        token = kwargs.get("token")

        try:
            invite = AgentCollaboratorInvite.objects.select_related('agent__user').get(token=token)
            context["invite"] = invite
            context["agent"] = invite.agent

            if invite.status != AgentCollaboratorInvite.InviteStatus.PENDING:
                context["already_responded"] = True
                context["status"] = invite.get_status_display()
            elif invite.is_expired():
                context["expired"] = True
            elif not self._user_matches_invite(self.request.user, invite):
                context["wrong_account"] = True
            else:
                context["can_accept"] = True
        except AgentCollaboratorInvite.DoesNotExist:
            context["invalid_token"] = True

        return context

    def post(self, request, *args, **kwargs):
        token = kwargs.get("token")
        try:
            invite = AgentCollaboratorInvite.objects.get(token=token)
        except AgentCollaboratorInvite.DoesNotExist:
            messages.error(request, "Invalid invitation token.")
            return redirect("agent_collaborator_invite_accept", token=token)

        if not self._user_matches_invite(request.user, invite):
            messages.error(request, "Please sign in with the invited email address to accept.")
            return redirect("agent_collaborator_invite_accept", token=token)

        if not invite.can_be_accepted():
            messages.error(request, "This invitation is no longer valid.")
            return redirect("agent_collaborator_invite_accept", token=token)

        try:
            invite.accept(request.user)
            messages.success(request, f"You now have access to {invite.agent.name}.")
        except ValidationError as exc:
            message_text = exc.messages[0] if getattr(exc, "messages", None) else "Unable to accept invitation."
            messages.error(request, message_text)
        except Exception as exc:
            messages.error(request, f"Error accepting invitation: {exc}")

        return redirect("agent_collaborator_invite_accept", token=token)


class AgentCollaboratorInviteRejectView(LoginRequiredMixin, TemplateView):
    """Handle rejecting an agent collaborator invitation."""
    template_name = "console/agent_collaborator_invite_response.html"

    def _user_matches_invite(self, user, invite: AgentCollaboratorInvite) -> bool:
        invite_email = (invite.email or "").strip().lower()
        if not invite_email:
            return False
        if (user.email or "").strip().lower() == invite_email:
            return True
        try:
            from allauth.account.models import EmailAddress
            return EmailAddress.objects.filter(user=user, email__iexact=invite_email).exists()
        except Exception:
            return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        token = kwargs.get("token")

        try:
            invite = AgentCollaboratorInvite.objects.select_related('agent__user').get(token=token)
            context["invite"] = invite
            context["agent"] = invite.agent
            context["rejecting"] = True

            if invite.status != AgentCollaboratorInvite.InviteStatus.PENDING:
                context["already_responded"] = True
                context["status"] = invite.get_status_display()
            elif invite.is_expired():
                context["expired"] = True
            elif not self._user_matches_invite(self.request.user, invite):
                context["wrong_account"] = True
            else:
                context["can_reject"] = True
        except AgentCollaboratorInvite.DoesNotExist:
            context["invalid_token"] = True

        return context

    def post(self, request, *args, **kwargs):
        token = kwargs.get("token")
        try:
            invite = AgentCollaboratorInvite.objects.get(token=token)
        except AgentCollaboratorInvite.DoesNotExist:
            messages.error(request, "Invalid invitation token.")
            return redirect("agent_collaborator_invite_reject", token=token)

        if not self._user_matches_invite(request.user, invite):
            messages.error(request, "Please sign in with the invited email address to respond.")
            return redirect("agent_collaborator_invite_reject", token=token)

        if invite.status != AgentCollaboratorInvite.InviteStatus.PENDING:
            messages.error(request, "This invitation has already been responded to.")
            return redirect("agent_collaborator_invite_reject", token=token)

        try:
            invite.reject()
            messages.success(request, "You have declined the invitation.")
        except Exception as exc:
            messages.error(request, f"Error rejecting invitation: {exc}")

        return redirect("agent_collaborator_invite_reject", token=token)


def _resolve_billing_owner(request):
    resolved = build_console_context(request)

    if resolved.current_context.type == "organization":
        membership = resolved.current_membership
        if membership is None:
            messages.error(request, "You no longer have access to manage this organization.")
            return redirect('billing')
        if membership.role not in BILLING_MANAGE_ROLES:
            messages.error(request, "You do not have permission to modify billing settings for this organization.")
            return redirect('billing')
        return membership.org, "organization"

    return request.user, "user"


def with_billing_owner(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        resolved = _resolve_billing_owner(request)
        if isinstance(resolved, HttpResponse):
            return resolved
        owner, owner_type = resolved
        return view_func(request, owner, owner_type, *args, **kwargs)

    return wrapper


def _get_owner_plan_id(owner, owner_type: str) -> str | None:
    if owner_type == "organization":
        plan = get_organization_plan(owner)
    else:
        plan = get_user_plan(owner)
    return (plan or {}).get("id")


def _update_subscription_item_quantity_generic(subscription_id: str, price_id: str, quantity: int) -> None:
    """Create, update, or remove a subscription item for the given price."""
    subscription_data = stripe.Subscription.retrieve(
        subscription_id,
        expand=["items.data.price"],
    )

    existing_item = None
    for item in subscription_data.get("items", {}).get("data", []) or []:
        price = item.get("price") or {}
        if price.get("id") == price_id:
            existing_item = item
            break

    if quantity > 0:
        if existing_item is None:
            stripe.SubscriptionItem.create(
                subscription=subscription_id,
                price=price_id,
                quantity=quantity,
            )
        else:
            stripe.SubscriptionItem.modify(
                existing_item.get("id"),
                quantity=quantity,
            )
    elif existing_item is not None:
        stripe.SubscriptionItem.delete(existing_item.get("id"))


from typing import Mapping


def _get_subscription_item_for_price(subscription_data: Mapping[str, Any], price_id: str) -> Mapping[str, Any] | None:
    items = (subscription_data.get("items") or {}).get("data", []) if isinstance(subscription_data, Mapping) else []
    for item in items or []:
        price = item.get("price") or {}
        if price.get("id") == price_id:
            return item
    return None


def _start_addon_portal_session(subscription_id: str, customer_id: str, price_id: str, quantity: int, return_url: str, item_id: str | None = None) -> str:
    """Create a billing portal session to confirm add-on quantity changes."""
    flow_data = {
        "type": "subscription_update_confirm",
        "subscription_update_confirm": {
            "subscription": subscription_id,
            "items": [
                {
                    "id": item_id,
                    "price": price_id,
                    "quantity": quantity,
                }
            ],
        },
    }

    session = stripe.billing_portal.Session.create(
        api_key=stripe.api_key,
        customer=customer_id,
        flow_data=flow_data,
        return_url=return_url,
    )
    return session.url


def _start_addon_checkout_session(customer_id: str, price_id: str, quantity: int, success_url: str, cancel_url: str) -> str:
    """Fallback to Checkout when the subscription lacks the add-on item."""
    session = stripe.checkout.Session.create(
        api_key=stripe.api_key,
        customer=customer_id,
        success_url=success_url,
        cancel_url=cancel_url,
        mode="subscription",
        allow_promotion_codes=True,
        line_items=[
            {
                "price": price_id,
                "quantity": quantity,
            }
        ],
    )
    return session.url


def _update_addon_quantity(
    request,
    owner,
    owner_type: str,
    addon_kind: str,
    form_label: str,
    success_message: str,
    failure_noun: str,
):
    if not stripe_status().enabled:
        messages.error(request, "Stripe billing is not available in this deployment.")
        return redirect("billing")

    form = AddonQuantityForm(request.POST, label=form_label)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return redirect(_billing_redirect(owner, owner_type))

    plan_id = _get_owner_plan_id(owner, owner_type)
    price_options = AddonEntitlementService.get_price_options(owner_type, plan_id, addon_kind)
    if not price_options:
        messages.error(request, f"{form_label} price is not configured for your plan.")
        return redirect(_billing_redirect(owner, owner_type))

    valid_price_ids = {cfg.price_id for cfg in price_options}
    selected_price_id = (form.cleaned_data.get("price_id") or "").strip()
    if selected_price_id and selected_price_id not in valid_price_ids:
        messages.error(request, f"That {form_label.lower()} tier is not available for your plan.")
        return redirect(_billing_redirect(owner, owner_type))

    if not selected_price_id:
        if len(price_options) == 1:
            selected_price_id = price_options[0].price_id
        else:
            messages.error(request, f"Choose a {form_label.lower()} tier to update.")
            return redirect(_billing_redirect(owner, owner_type))

    price_id = selected_price_id
    if not price_id:
        return redirect(_billing_redirect(owner, owner_type))

    subscription = get_active_subscription(owner, preferred_plan_id=_get_owner_plan_id(owner, owner_type))
    if not subscription:
        messages.error(request, "No active subscription found.")
        return redirect(_billing_redirect(owner, owner_type))

    try:
        _assign_stripe_api_key()
        desired_qty = int(form.cleaned_data["quantity"])
        stripe_subscription = stripe.Subscription.retrieve(subscription.id, expand=["customer", "items.data.price"])
        customer_id = (stripe_subscription.get("customer") or "")
        if not customer_id:
            messages.error(request, "Stripe customer not found for this subscription.")
            return redirect(_billing_redirect(owner, owner_type))

        item = _get_subscription_item_for_price(stripe_subscription, price_id)
        items_data = (stripe_subscription.get("items") or {}).get("data", []) if isinstance(stripe_subscription, Mapping) else []
        updated_items = list(items_data) if isinstance(items_data, list) else []
        current_qty = 0
        if item:
            try:
                current_qty = int(item.get("quantity") or 0)
            except (TypeError, ValueError):
                current_qty = 0

        if item and desired_qty == current_qty:
            messages.success(request, success_message)
            return redirect(_billing_redirect(owner, owner_type))

        if desired_qty <= 0 and not item:
            messages.success(request, success_message)
        else:
            if desired_qty <= 0:
                items_payload = [{"id": item.get("id"), "deleted": True}]
            elif item:
                items_payload = [{"id": item.get("id"), "quantity": desired_qty}]
            else:
                items_payload = [{"price": price_id, "quantity": desired_qty}]

            modify_kwargs = {
                "items": items_payload,
                "proration_behavior": "always_invoice",
                "expand": ["items.data.price"],
            }
            if not any(item.get("deleted") for item in items_payload):
                modify_kwargs["payment_behavior"] = "pending_if_incomplete"
            updated_subscription = stripe.Subscription.modify(subscription.id, **modify_kwargs)
            updated_items = (updated_subscription.get("items") or {}).get("data", []) if isinstance(updated_subscription, Mapping) else []
            if not isinstance(updated_items, list):
                updated_items = []
            messages.success(request, success_message)

        try:
            period_start, period_end = BillingService.get_current_billing_period_for_owner(owner)
            tz = timezone.get_current_timezone()
            period_start_dt = timezone.make_aware(datetime.combine(period_start, datetime.min.time()), tz)
            period_end_dt = timezone.make_aware(
                datetime.combine(period_end + timedelta(days=1), datetime.min.time()),
                tz,
            )
            AddonEntitlementService.sync_subscription_entitlements(
                owner=owner,
                owner_type=owner_type,
                plan_id=plan_id,
                subscription_items=updated_items,
                period_start=period_start_dt,
                period_end=period_end_dt,
                created_via="console_direct_update",
            )
        except Exception:
            logger.exception(
                "Failed to sync %s add-on entitlements after update for %s",
                addon_kind,
                getattr(owner, "id", None) or owner,
            )
        return redirect(_billing_redirect(owner, owner_type))
    except stripe.error.StripeError as exc:
        logger.warning("Stripe API error while updating addon quantity: %s", exc)
        messages.error(request, f"A billing error occurred: {exc}")
    except Exception as exc:
        logger.exception("Failed to update %s quantity for %s", addon_kind, getattr(owner, "id", None) or owner)
        messages.error(request, f"An unexpected error occurred while updating {failure_noun}.")

    return redirect(_billing_redirect(owner, owner_type))


def _update_stripe_dedicated_ip_quantity(owner, owner_type: str, desired_qty: int) -> None:
    """Ensure the Stripe subscription item reflects the desired dedicated IP quantity."""
    desired_qty = int(desired_qty)
    subscription = get_active_subscription(owner)
    if not subscription:
        raise ValueError("Active subscription not found")

    stripe_settings = get_stripe_settings()
    dedicated_price_id = (
        stripe_settings.startup_dedicated_ip_price_id
        if owner_type == "user"
        else stripe_settings.org_team_dedicated_ip_price_id
    )
    if not dedicated_price_id:
        raise ValueError("Dedicated IP price not configured")

    subscription_data = stripe.Subscription.retrieve(
        subscription.id,
        expand=["items.data.price"],
    )

    dedicated_item = None
    for item in subscription_data.get("items", {}).get("data", []) or []:
        price = item.get("price") or {}
        if price.get("id") == dedicated_price_id:
            dedicated_item = item
            break

    if desired_qty > 0:
        if dedicated_item is None:
            items_payload = [{"price": dedicated_price_id, "quantity": desired_qty}]
        else:
            items_payload = [{"id": dedicated_item.get("id"), "quantity": desired_qty}]
        modify_kwargs = {
            "items": items_payload,
            "proration_behavior": "always_invoice",
            "expand": ["items.data.price"],
            "payment_behavior": "pending_if_incomplete",
        }
        stripe.Subscription.modify(subscription.id, **modify_kwargs)
    elif dedicated_item is not None:
        stripe.Subscription.modify(
            subscription.id,
            items=[{"id": dedicated_item.get("id"), "deleted": True}],
            proration_behavior="always_invoice",
        )


@login_required
@require_POST
@transaction.atomic
@with_billing_owner
@tracer.start_as_current_span("BILLING Update Task Pack Quantity")
def update_task_pack_quantity(request, owner, owner_type):
    return _update_addon_quantity(
        request,
        owner,
        owner_type,
        addon_kind="task_pack",
        form_label="Task packs",
        success_message="Task pack quantity updated.",
        failure_noun="task packs",
    )


@login_required
@require_POST
@transaction.atomic
@with_billing_owner
@tracer.start_as_current_span("BILLING Update Contact Pack Quantity")
def update_contact_pack_quantity(request, owner, owner_type):
    return _update_addon_quantity(
        request,
        owner,
        owner_type,
        addon_kind="contact_pack",
        form_label="Contact packs",
        success_message="Contact pack quantity updated.",
        failure_noun="contact packs",
    )


@login_required
@require_POST
@transaction.atomic
@with_billing_owner
@tracer.start_as_current_span("BILLING Update Add-ons Batch")
def update_addons(request, owner, owner_type):
    if not stripe_status().enabled:
        messages.error(request, "Stripe billing is not available in this deployment.")
        return redirect("billing")

    plan_id = _get_owner_plan_id(owner, owner_type)
    task_options = AddonEntitlementService.get_price_options(owner_type, plan_id, "task_pack")
    contact_options = AddonEntitlementService.get_price_options(owner_type, plan_id, "contact_pack")
    browser_task_options = AddonEntitlementService.get_price_options(owner_type, plan_id, "browser_task_limit")
    advanced_captcha_options = AddonEntitlementService.get_price_options(
        owner_type,
        plan_id,
        "advanced_captcha_resolution",
    )
    all_options = (task_options or []) + (contact_options or []) + (browser_task_options or []) + (advanced_captcha_options or [])
    if not all_options:
        messages.error(request, "No add-ons are configured for your plan.")
        return redirect(_billing_redirect(owner, owner_type))

    price_to_kind: dict[str, str] = {}
    for opt in task_options or []:
        price_to_kind[opt.price_id] = "task_pack"
    for opt in contact_options or []:
        price_to_kind[opt.price_id] = "contact_pack"
    for opt in browser_task_options or []:
        price_to_kind[opt.price_id] = "browser_task_limit"
    for opt in advanced_captcha_options or []:
        price_to_kind[opt.price_id] = "advanced_captcha_resolution"

    desired_quantities: dict[str, int] = {}
    for key, value in request.POST.items():
        if not key.startswith("quantity__"):
            continue
        price_id = key.replace("quantity__", "", 1)
        if price_id not in price_to_kind:
            continue
        try:
            qty = int(value)
        except (TypeError, ValueError):
            messages.error(request, "Quantities must be whole numbers.")
            return redirect(_billing_redirect(owner, owner_type))
        if qty < 0 or qty > 999:
            messages.error(request, "Quantities must be between 0 and 999.")
            return redirect(_billing_redirect(owner, owner_type))
        desired_quantities[price_id] = qty

    if not desired_quantities:
        messages.error(request, "No add-on quantities provided.")
        return redirect(_billing_redirect(owner, owner_type))

    subscription = get_active_subscription(owner, preferred_plan_id=plan_id)
    if not subscription:
        messages.error(request, "No active subscription found.")
        return redirect(_billing_redirect(owner, owner_type))

    try:
        _assign_stripe_api_key()
        stripe_subscription = stripe.Subscription.retrieve(subscription.id, expand=["customer", "items.data.price"])
        customer_id = (stripe_subscription.get("customer") or "")
        if not customer_id:
            messages.error(request, "Stripe customer not found for this subscription.")
            return redirect(_billing_redirect(owner, owner_type))

        items_data = (stripe_subscription.get("items") or {}).get("data", []) if isinstance(stripe_subscription, Mapping) else []

        # Build existing quantities map
        existing_qty: dict[str, int] = {}
        item_id_by_price: dict[str, str] = {}
        for item in items_data or []:
            price = item.get("price") or {}
            pid = price.get("id")
            if not pid:
                continue
            item_id_by_price[pid] = item.get("id")
            try:
                existing_qty[pid] = int(item.get("quantity") or 0)
            except (TypeError, ValueError):
                existing_qty[pid] = 0

        changes_made = False
        items_payload: list[dict[str, Any]] = []
        for price_id, desired_qty in desired_quantities.items():
            current_qty = existing_qty.get(price_id, 0)
            if desired_qty == current_qty:
                continue

            if desired_qty > 0:
                if price_id in item_id_by_price:
                    items_payload.append({"id": item_id_by_price[price_id], "quantity": desired_qty})
                else:
                    items_payload.append({"price": price_id, "quantity": desired_qty})
            else:
                if price_id in item_id_by_price:
                    items_payload.append({"id": item_id_by_price[price_id], "deleted": True})
            changes_made = True

        if changes_made:
            modify_kwargs = {
                "items": items_payload,
                "proration_behavior": "always_invoice",
                "expand": ["items.data.price"],
            }
            if not any(item.get("deleted") for item in items_payload):
                modify_kwargs["payment_behavior"] = "pending_if_incomplete"
            updated_subscription = stripe.Subscription.modify(subscription.id, **modify_kwargs)
            updated_items = (updated_subscription.get("items") or {}).get("data", []) if isinstance(updated_subscription, Mapping) else []
            if not isinstance(updated_items, list):
                updated_items = []

            try:
                period_start, period_end = BillingService.get_current_billing_period_for_owner(owner)
                tz = timezone.get_current_timezone()
                period_start_dt = timezone.make_aware(datetime.combine(period_start, datetime.min.time()), tz)
                period_end_dt = timezone.make_aware(
                    datetime.combine(period_end + timedelta(days=1), datetime.min.time()),
                    tz,
                )
                AddonEntitlementService.sync_subscription_entitlements(
                    owner=owner,
                    owner_type=owner_type,
                    plan_id=plan_id,
                    subscription_items=updated_items,
                    period_start=period_start_dt,
                    period_end=period_end_dt,
                    created_via="console_batch_update",
                )
            except Exception:
                logger.exception(
                    "Failed to sync add-on entitlements after batch update for %s",
                    getattr(owner, "id", None) or owner,
                )

        messages.success(request, "Add-ons updated.")
    except stripe.error.StripeError as exc:
        logger.warning("Stripe API error while updating addons: %s", exc)
        messages.error(request, f"A billing error occurred: {exc}")
    except Exception as exc:
        logger.exception("Failed to update add-ons for %s", getattr(owner, "id", None) or owner)
        messages.error(request, "An unexpected error occurred while updating add-ons.")

    return redirect(_billing_redirect(owner, owner_type))


@login_required
@require_POST
@transaction.atomic
@with_billing_owner
@tracer.start_as_current_span("BILLING Add Dedicated IP Quantity")
def add_dedicated_ip_quantity(request, owner, owner_type):
    if not stripe_status().enabled:
        messages.error(request, "Stripe billing is not available in this deployment.")
        return redirect('billing')

    owner_plan_id = None
    if owner_type == "user":
        plan = get_user_plan(owner)
        owner_plan_id = (plan or {}).get("id")
    else:
        billing = getattr(owner, "billing", None)
        owner_plan_id = getattr(billing, "subscription", PlanNamesChoices.FREE.value) if billing else PlanNamesChoices.FREE.value

    if owner_plan_id in (PlanNamesChoices.FREE.value, PlanNamesChoices.FREE):
        if settings.GOBII_PROPRIETARY_MODE:
            messages.error(request, "Upgrade to a paid plan to add dedicated IPs.")
        else:
            messages.error(request, "Dedicated IPs are not available in this deployment.")
        return redirect(_billing_redirect(owner, owner_type))

    form = DedicatedIpAddForm(request.POST)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return redirect(_billing_redirect(owner, owner_type))

    add_quantity = form.cleaned_data["quantity"]

    try:
        _assign_stripe_api_key()

        customer = get_or_create_stripe_customer(owner)
        if not customer:
            raise ValueError("Stripe customer not found for owner")

        current_qty = DedicatedProxyService.allocated_count(owner)
        desired_qty = current_qty + int(add_quantity)

        _update_stripe_dedicated_ip_quantity(owner, owner_type, desired_qty)

        missing = desired_qty - current_qty
        allocated = 0
        for _ in range(missing):
            try:
                DedicatedProxyService.allocate_proxy(owner)
                allocated += 1
            except DedicatedProxyUnavailableError:
                messages.warning(
                    request,
                    "Not enough dedicated IP inventory was available. We've allocated as many as possible.",
                )
                break

        messages.success(request, "Dedicated IP quantity updated.")
    except Exception as exc:
        logger.exception("Failed to update dedicated IP quantity", exc_info=True)
        messages.error(request, f"Failed to update dedicated IPs: {exc}")

    return redirect(_billing_redirect(owner, owner_type))


@login_required
@require_POST
@transaction.atomic
@with_billing_owner
@tracer.start_as_current_span("BILLING Remove Dedicated IP")
def remove_dedicated_ip(request, owner, owner_type):
    if not stripe_status().enabled:
        messages.error(request, "Stripe billing is not available in this deployment.")
        return redirect('billing')

    proxy_id = request.POST.get("proxy_id")
    if not proxy_id:
        messages.error(request, "Missing dedicated IP identifier.")
        return redirect(_billing_redirect(owner, owner_type))

    try:
        _assign_stripe_api_key()
        current_qty = DedicatedProxyService.allocated_count(owner)
        if current_qty <= 0:
            messages.info(request, "No dedicated IPs to remove.")
            return redirect(_billing_redirect(owner, owner_type))

        if not DedicatedProxyService.release_specific(owner, proxy_id):
            messages.error(request, "Dedicated IP was already released.")
            return redirect(_billing_redirect(owner, owner_type))

        desired_qty = max(current_qty - 1, 0)

        _update_stripe_dedicated_ip_quantity(owner, owner_type, desired_qty)

        messages.success(request, "Dedicated IP removed.")
    except Exception as exc:
        logger.exception("Failed to remove dedicated IP", exc_info=True)
        messages.error(request, f"Failed to remove dedicated IP: {exc}")

    return redirect(_billing_redirect(owner, owner_type))


@login_required
@require_POST
@transaction.atomic
@with_billing_owner
@tracer.start_as_current_span("BILLING Remove All Dedicated IPs")
def remove_all_dedicated_ip(request, owner, owner_type):
    if not stripe_status().enabled:
        messages.error(request, "Stripe billing is not available in this deployment.")
        return redirect('billing')

    try:
        _assign_stripe_api_key()
        current_qty = DedicatedProxyService.allocated_count(owner)
        if current_qty <= 0:
            messages.info(request, "No dedicated IPs to remove.")
            return redirect(_billing_redirect(owner, owner_type))

        DedicatedProxyService.release_for_owner(owner)

        _update_stripe_dedicated_ip_quantity(owner, owner_type, 0)

        messages.success(request, "All dedicated IPs removed.")
    except Exception as exc:
        logger.exception("Failed to remove all dedicated IPs", exc_info=True)
        messages.error(request, f"Failed to remove dedicated IPs: {exc}")

    return redirect(_billing_redirect(owner, owner_type))


def _billing_redirect(owner, owner_type: str) -> str:
    url = reverse('billing')
    if owner_type == "organization" and owner is not None:
        return f"{url}?org_id={owner.id}"
    return url
