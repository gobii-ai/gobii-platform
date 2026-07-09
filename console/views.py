import io
import json
import mimetypes
from decimal import Decimal, InvalidOperation
from typing import Any

import stripe
from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.core.mail import BadHeaderError, send_mail
from django.views.generic import TemplateView, View, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, get_object_or_404, render
from django.urls import NoReverseMatch, reverse
from django.contrib import messages
from django.db import transaction, IntegrityError, DatabaseError
from django.db.models import Q
from django.http import FileResponse, HttpResponseForbidden, HttpResponseNotAllowed, HttpResponse, JsonResponse, Http404, HttpRequest
from django.core.exceptions import ValidationError, PermissionDenied, ImproperlyConfigured
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.formats import date_format
from django.middleware.csrf import get_token
from datetime import timedelta, datetime, timezone as dt_timezone
from functools import wraps
from smtplib import SMTPException
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

from config.socialaccount_adapter import OAUTH_ATTRIBUTION_COOKIE, OAUTH_CHARTER_COOKIE, restore_oauth_session_state
from billing.checkout_metadata import STRIPE_CHECKOUT_FLOW_TYPE_PURCHASE, build_checkout_flow_metadata
from billing.checkout_sessions import create_stripe_checkout_session
from billing.plan_resolver import get_active_public_plan_monthly_task_credits
from billing.services import BillingService
from api.services.agent_transfer import AgentTransferService, AgentTransferError, AgentTransferDenied
from api.services.signup_preview import user_can_access_signup_preview_agent
from api.services.dedicated_proxy_service import DedicatedProxyService, is_multi_assign_enabled
from api.services.persistent_agents import maybe_sync_agent_email_display_name
from api.agent.core.llm_config import (
    AgentLLMTier,
    TIER_ORDER,
    get_system_default_tier,
    get_llm_tier_description,
    get_llm_tier_label,
    get_llm_tier_multipliers,
    get_llm_tier_ranks,
    apply_user_quota_tier_override,
    max_allowed_tier_for_plan,
)
from api.agent.avatar import maybe_schedule_agent_avatar
from api.agent.short_description import compute_charter_hash, maybe_schedule_mini_description, maybe_schedule_short_description
from api.agent.tags import maybe_schedule_agent_tags
from api.services.daily_credit_limits import get_agent_credit_multiplier, get_tier_credit_multiplier, scale_daily_credit_limit_for_tier_change
from api.services.daily_credit_settings import get_daily_credit_settings_for_owner
from api.services.owner_execution_pause import get_owner_account_pause_state, sync_owner_customer_account_pause
from api.services.agent_settings_resume import queue_owner_task_pack_resume, queue_settings_change_resume
from api.services.agent_avatar_public import validate_public_agent_avatar_thumbnail_token
from api.services.trial_abuse import evaluate_user_trial_eligibility, user_has_prior_individual_history
from console.daily_credit import build_agent_daily_credit_context, get_daily_credit_slider_bounds, parse_daily_credit_limit, serialize_daily_credit_payload
from console.role_constants import BILLING_MANAGE_ROLES
from api.models import (
    UserBilling,
    BrowserUseAgent,
    ProxyServer,
    PersistentAgent,
    PersistentAgentInboundWebhook,
    PersistentAgentWebhook,
    IntelligenceTier,
    AgentPeerLink,
    CommsChannel,
    UserPhoneNumber,
    Organization,
    OrganizationMembership,
    OrganizationInvite,
    TaskCredit,
    AgentCollaborator,
    AgentCollaboratorInvite,
    get_agent_contact_counts,
)
from console.mixins import AgentOwnerContextOverrideMixin, ConsoleViewMixin, StripeFeatureRequiredMixin, SystemAdminRequiredMixin
from pages.account_info_cache import invalidate_account_info_cache

from .context_helpers import build_console_context
from billing.addons import AddonEntitlementService
from util.payments_helper import PaymentsHelper
from util.integrations import IntegrationDisabledError, stripe_status
from util.onboarding import TRIAL_ONBOARDING_TARGET_AGENT_UI, set_trial_onboarding_intent, set_trial_onboarding_requires_plan_selection
from util.personal_signup_preview import resolve_personal_signup_preview
from util.subscription_helper import (
    reconcile_user_plan_from_stripe,
    get_active_subscription,
    get_stripe_customer,
    get_organization_plan,
    get_user_max_contacts_per_agent,
    sync_subscription_after_direct_update as _sync_subscription_after_direct_update,
)
from util.trial_enforcement import PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE, TrialRequiredValidationError, can_user_use_personal_agents_and_api
from util.urls import (
    IMMERSIVE_APP_BASE_PATH,
    IMMERSIVE_RETURN_TO_SESSION_KEY,
    append_query_params,
    append_context_query,
    build_immersive_chat_url,
    build_immersive_contact_requests_url,
    load_daily_limit_action_payload,
)
from console.agent_chat.access import resolve_agent_for_request, resolve_manageable_agent_for_request, user_can_manage_agent, user_is_collaborator
from config import settings
from config.stripe_config import get_stripe_settings
from config.plans import PLAN_CONFIG
from waffle import flag_is_active

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


def _posted_bool(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _wants_json_response(request) -> bool:
    return "application/json" in (request.headers.get("Accept") or "").lower()


def _enforce_personal_agent_access_or_raise(user, agent: PersistentAgent) -> None:
    if (
        agent.organization_id is None
        and agent.user_id == user.id
        and not user_can_access_signup_preview_agent(agent, user)
        and not can_user_use_personal_agents_and_api(user)
    ):
        raise PermissionDenied(PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE)


def _agent_settings_app_path(agent: PersistentAgent) -> str:
    return f"{IMMERSIVE_APP_BASE_PATH}/agents/{agent.id}/settings"


def _organization_app_path(org_id: Any | None = None) -> str:
    path = f"{IMMERSIVE_APP_BASE_PATH}/team"
    if org_id:
        return append_context_query(path, str(org_id))
    return path


def _safe_getattr(source, attr: str, default=None):
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(attr, default)
    return getattr(source, attr, default)


def build_llm_intelligence_props(
    owner,
    owner_type: str,
    organization,
    upgrade_url: str | None,
) -> dict[str, Any]:
    plan = None
    if owner is not None:
        if owner_type == 'organization':
            plan = get_organization_plan(organization) if organization is not None else None
        else:
            plan = reconcile_user_plan_from_stripe(owner)

    allowed_tier = max_allowed_tier_for_plan(plan, is_organization=(owner_type == 'organization'))
    allowed_tier = apply_user_quota_tier_override(owner, allowed_tier)
    system_default_tier = get_system_default_tier().value
    tier_ranks = get_llm_tier_ranks()
    allowed_rank = tier_ranks.get(allowed_tier.value)
    if allowed_rank is None:
        allowed_rank = TIER_ORDER.get(allowed_tier, 0)
    if settings.GOBII_PROPRIETARY_MODE:
        can_edit = bool(
            owner is not None
            and (owner_type == 'organization' or allowed_tier != AgentLLMTier.STANDARD)
        )
    else:
        can_edit = True
    disabled_reason = None
    if not can_edit and settings.GOBII_PROPRIETARY_MODE:
        disabled_reason = "Upgrade to adjust intelligence levels."

    tiers = list(IntelligenceTier.objects.order_by("credit_multiplier", "rank"))
    expected_keys = {tier.value for tier in AgentLLMTier}
    tier_keys = {tier.key for tier in tiers}
    use_db_tiers = bool(tiers) and (
        settings.GOBII_PROPRIETARY_MODE or expected_keys.issubset(tier_keys)
    )
    if use_db_tiers:
        options = []
        for tier in tiers:
            rank_value = getattr(tier, "rank", None)
            if rank_value is None:
                rank_value = tier_ranks.get(tier.key)
            try:
                rank_value = int(rank_value) if rank_value is not None else None
            except (TypeError, ValueError):
                rank_value = None
            options.append(
                {
                    "key": tier.key,
                    "label": get_llm_tier_label(tier.key, tier.display_name),
                    "description": get_llm_tier_description(tier.key),
                    "multiplier": float(tier.credit_multiplier),
                    "rank": rank_value,
                }
            )
    else:
        multipliers = get_llm_tier_multipliers()
        options = []
        for tier in sorted(TIER_ORDER.keys(), key=lambda entry: TIER_ORDER[entry]):
            options.append(
                {
                    "key": tier.value,
                    "label": get_llm_tier_label(tier.value),
                    "description": get_llm_tier_description(tier.value),
                    "multiplier": float(multipliers.get(tier.value, 1)),
                    "rank": TIER_ORDER.get(tier),
                }
            )

    max_allowed_rank = allowed_rank
    max_allowed_tier_key = allowed_tier.value
    if not settings.GOBII_PROPRIETARY_MODE and options:
        ranked = [option for option in options if isinstance(option.get("rank"), int)]
        if ranked:
            top_option = max(ranked, key=lambda option: option["rank"])
            max_allowed_rank = top_option["rank"]
            max_allowed_tier_key = top_option["key"]

    return {
        "options": options,
        "canEdit": can_edit,
        "disabledReason": disabled_reason,
        "upgradeUrl": upgrade_url,
        "maxAllowedTier": max_allowed_tier_key,
        "maxAllowedTierRank": max_allowed_rank,
        "systemDefaultTier": system_default_tier,
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


from .forms import DedicatedIpAddForm, AddonQuantityForm
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from api.services.sms_contact_purpose import track_sms_contact_approval
from waffle.mixins import WaffleFlagMixin
from constants.feature_flags import (
    CTA_CONTINUE_AGENT_BTN,
    CTA_NO_CHARGE_DURING_TRIAL,
    CTA_PICK_A_PLAN,
    CTA_PRICING_CANCEL_TEXT_UNDER_BTN,
    CTA_START_FREE_TRIAL,
    CTA_UNLOCK_AGENT_COPY,
    ORGANIZATIONS,
    PRICING_MODAL_ALMOST_FULL_SCREEN,
)
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNames, PlanNamesChoices
from constants.stripe import ORG_OVERAGE_STATE_META_KEY, ORG_OVERAGE_STATE_DETACHED_PENDING, EXCLUDED_PAYMENT_METHOD_TYPES
from util.waffle_flags import is_waffle_flag_active
from util.trial_eligibility import is_user_trial_allowed_by_policy, is_user_trial_eligibility_enforcement_enabled, is_user_trial_eligibility_enforcement_one_per_user_enabled
from opentelemetry import trace
from api.services import mcp_servers as mcp_server_service
from console.agent_creation import create_persistent_agent_from_charter
from console.agent_reassignment import reassign_agent_organization
from console.extra_tasks_settings import derive_extra_tasks_settings
import logging
from api.models import AgentAllowlistInvite, AgentTransferInvite, OrganizationMembership, MCPServerConfig
from django.apps import apps
User = get_user_model()
logger = logging.getLogger(__name__)

tracer = trace.get_tracer("gobii.utils")

BILLING_UPDATE_SUPPORT_DETAIL = (
    "An error occurred while updating billing. "
    "Please contact support@gobii.ai for help."
)


def _assign_stripe_api_key() -> str:
    """Ensure Stripe secret key is configured before making API calls."""
    key = PaymentsHelper.get_stripe_key()
    if not key:
        raise ImproperlyConfigured("Stripe secret key missing while billing is enabled.")
    stripe.api_key = key
    return key


def _normalize_non_negative_int(value: int | str | None) -> int:
    if value is None:
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _additional_tasks_metered_price_id_for_owner(owner, owner_type: str) -> str:
    """Return the metered overage price for the owner's current plan."""
    stripe_settings = get_stripe_settings()

    if owner_type == "organization":
        return getattr(stripe_settings, "org_team_additional_task_price_id", "") or ""

    plan_id = str((reconcile_user_plan_from_stripe(owner) or {}).get("id") or "").strip().lower()
    if plan_id in {PlanNames.STARTUP, "startup"}:
        return getattr(stripe_settings, "startup_additional_task_price_id", "") or ""
    if plan_id in {PlanNames.SCALE, "scale"}:
        return getattr(stripe_settings, "scale_additional_task_price_id", "") or ""
    return ""


def _sync_additional_tasks_metered_subscription_item(owner, owner_type: str, enabled: bool) -> None:
    """Attach/detach the Stripe metered overage item to match auto-purchase state."""
    subscription = get_active_subscription(owner)
    if subscription is None:
        # No active subscription yet (or free plan); there is nothing to sync.
        return

    price_id = _additional_tasks_metered_price_id_for_owner(owner, owner_type)
    if not price_id:
        if enabled:
            raise ValidationError("Additional task billing is not configured for the current plan.")
        return

    _assign_stripe_api_key()
    subscription_data = stripe.Subscription.retrieve(subscription.id, expand=["items.data.price"])
    existing_item = _get_subscription_item_for_price(subscription_data, price_id)

    if enabled:
        if existing_item is None:
            stripe.SubscriptionItem.create(subscription=subscription.id, price=price_id)
        return

    if existing_item is not None:
        stripe.SubscriptionItem.delete(existing_item.get("id"))


def _sync_additional_tasks_metered_or_error(
    owner,
    owner_type: str,
    enabled: bool,
    *,
    log_owner_label: str,
) -> JsonResponse | None:
    try:
        _sync_additional_tasks_metered_subscription_item(owner, owner_type, enabled)
    except ValidationError as exc:
        return JsonResponse(
            {
                "success": False,
                "error": "invalid_overage_configuration",
                "detail": _format_validation_error(exc),
            },
            status=400,
        )
    except (stripe.error.StripeError, ImproperlyConfigured):
        logger.exception(
            "Failed to sync additional-task metered item for %s",
            log_owner_label,
        )
        return JsonResponse(
            {"success": False, "error": "stripe_error", "detail": BILLING_UPDATE_SUPPORT_DETAIL},
            status=400,
        )
    return None


def _get_checkout_trial_days() -> tuple[int, int]:
    try:
        stripe_settings = get_stripe_settings()
    except Exception:
        logger.warning("Failed to load Stripe settings for checkout trial-day config.", exc_info=True)
        return 0, 0

    startup_trial_days = _normalize_non_negative_int(
        getattr(stripe_settings, "startup_trial_days", 0)
    )
    scale_trial_days = _normalize_non_negative_int(
        getattr(stripe_settings, "scale_trial_days", 0)
    )
    return startup_trial_days, scale_trial_days


def _is_checkout_trial_eligible(user, request: HttpRequest | None = None) -> bool:
    """Return whether a user can start an individual-plan free trial."""
    if not user or not getattr(user, "pk", None):
        return True
    enforcement_enabled = is_user_trial_eligibility_enforcement_enabled(request)
    one_per_user_enabled = is_user_trial_eligibility_enforcement_one_per_user_enabled(request)
    try:
        decision = None
        if enforcement_enabled:
            result = evaluate_user_trial_eligibility(user)
            decision = result.decision
        return is_user_trial_allowed_by_policy(
            enforcement_enabled=enforcement_enabled,
            one_per_user_enabled=one_per_user_enabled,
            has_prior_individual_history=(
                (lambda: user_has_prior_individual_history(user))
                if one_per_user_enabled
                else None
            ),
            request=request,
            decision=decision,
        )
    except (IntegrationDisabledError, stripe.error.StripeError, TypeError, ValueError):
        logger.warning(
            "Failed to resolve trial eligibility for user %s; defaulting to ineligible.",
            getattr(user, "id", None),
            exc_info=True,
        )
        return False


def _is_pricing_modal_almost_full_screen_enabled(request: HttpRequest | None) -> bool:
    """Default to enabled when the flag row is missing."""
    return is_waffle_flag_active(
        PRICING_MODAL_ALMOST_FULL_SCREEN,
        request,
        default=True,
    )


def _is_cta_pricing_cancel_text_under_btn_enabled(request: HttpRequest | None) -> bool:
    """Default to disabled until the rollout is explicitly enabled."""
    return is_waffle_flag_active(CTA_PRICING_CANCEL_TEXT_UNDER_BTN, request, default=False)

def _is_cta_start_free_trial_enabled(request: HttpRequest | None) -> bool:
    """Default to disabled until the rollout is explicitly enabled."""
    return is_waffle_flag_active(CTA_START_FREE_TRIAL, request, default=False)


def _is_cta_unlock_agent_copy_enabled(request: HttpRequest | None) -> bool:
    """Default to disabled until the rollout is explicitly enabled."""
    return is_waffle_flag_active(CTA_UNLOCK_AGENT_COPY, request, default=False)


def _is_cta_pick_a_plan_enabled(request: HttpRequest | None) -> bool:
    """Default to disabled until the rollout is explicitly enabled."""
    return is_waffle_flag_active(CTA_PICK_A_PLAN, request, default=False)


def _is_cta_continue_agent_btn_enabled(request: HttpRequest | None) -> bool:
    """Default to disabled until the rollout is explicitly enabled."""
    return is_waffle_flag_active(CTA_CONTINUE_AGENT_BTN, request, default=False)


def _is_cta_no_charge_during_trial_enabled(request: HttpRequest | None) -> bool:
    """Default to disabled until the rollout is explicitly enabled."""
    return is_waffle_flag_active(CTA_NO_CHARGE_DURING_TRIAL, request, default=False)


def _get_personal_signup_preview_config(
    request: HttpRequest,
    *,
    resolved_context=None,
):
    context_info = resolved_context or build_console_context(request)
    return resolve_personal_signup_preview(
        request.user,
        request=request,
        current_context_type=context_info.current_context.type,
    )

# Whether to skip the phone number setup screen when the user already has a
# verified phone number on their account. Toggle this to force showing the
# phone screen even when a verified number exists.
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


class BillingPortalView(StripeFeatureRequiredMixin, LoginRequiredMixin, View):
    """Open the Stripe billing portal for personal subscriptions."""

    @tracer.start_as_current_span("CONSOLE Billing Portal")
    def post(self, request, *args, **kwargs):
        customer = get_stripe_customer(request.user)
        if not customer or not getattr(customer, "id", None):
            detail = "We couldn't find a Stripe customer for your account. Please contact support."
            if _wants_json_response(request):
                return JsonResponse({"ok": False, "error": "stripe_customer_missing", "detail": detail}, status=400)
            messages.error(
                request,
                detail,
            )
            return redirect(f"{IMMERSIVE_APP_BASE_PATH}/billing")

        try:
            _assign_stripe_api_key()
            return_url = request.build_absolute_uri(f"{IMMERSIVE_APP_BASE_PATH}/billing")
            session = stripe.billing_portal.Session.create(
                customer=customer.id,
                api_key=stripe.api_key,
                return_url=return_url,
            )
            if _wants_json_response(request):
                return JsonResponse({"ok": True, "redirectUrl": session.url})
            return redirect(session.url)
        except stripe.error.StripeError:
            logger.exception("Failed to create Stripe billing portal session for user %s", request.user.id)
            detail = "We weren't able to open the Stripe billing portal. Please try again or contact support."
            if _wants_json_response(request):
                return JsonResponse({"ok": False, "error": "stripe_error", "detail": detail}, status=400)
            messages.error(
                request,
                detail,
            )
            return redirect(f"{IMMERSIVE_APP_BASE_PATH}/billing")


@login_required
@require_POST
@transaction.atomic
@tracer.start_as_current_span("BILLING Update Billing Settings")
def update_billing_settings(request):
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "invalid_json"}, status=400)

    if not isinstance(data, dict):
        return JsonResponse({"success": False, "error": "invalid_payload"}, status=400)

    enabled_raw = data.get("enabled", False)
    infinite_raw = data.get("infinite", False)
    max_tasks_raw = data.get("maxTasks", 5)

    def _coerce_bool(value, field_name: str) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        raise ValueError(field_name)

    try:
        auto_purchase = _coerce_bool(enabled_raw, "enabled")
        infinite = _coerce_bool(infinite_raw, "infinite")
    except ValueError as exc:
        field = str(exc)
        return JsonResponse({"success": False, "error": f"invalid_{field}"}, status=400)

    try:
        max_tasks = int(max_tasks_raw)
    except (TypeError, ValueError):
        return JsonResponse({"success": False, "error": "invalid_maxTasks"}, status=400)

    extra_tasks_endpoints = {
        "loadUrl": reverse("get_billing_settings"),
        "updateUrl": reverse("update_billing_settings"),
    }
    resolved = build_console_context(request)

    if resolved.current_context.type == "organization" and resolved.current_membership:
        membership = resolved.current_membership
        if membership.role not in BILLING_MANAGE_ROLES:
            return JsonResponse({"success": False, "error": "not_permitted"}, status=403)

        OrgBilling = apps.get_model("api", "OrganizationBilling")
        defaults = {"max_extra_tasks": 0, "billing_cycle_anchor": timezone.now().day}
        org_billing, _ = OrgBilling.objects.get_or_create(
            organization=membership.org,
            defaults=defaults,
        )

        sync_error_response = _sync_additional_tasks_metered_or_error(
            membership.org,
            "organization",
            auto_purchase,
            log_owner_label=f"organization {membership.org.id}",
        )
        if sync_error_response is not None:
            return sync_error_response

        if not auto_purchase:
            org_billing.max_extra_tasks = 0
        elif infinite:
            org_billing.max_extra_tasks = -1
        else:
            org_billing.max_extra_tasks = max(1, max_tasks)

        org_billing.save(update_fields=["max_extra_tasks", "updated_at"])

        configured_limit = int(org_billing.max_extra_tasks or 0)
        extra_tasks = derive_extra_tasks_settings(
            configured_limit,
            can_modify=True,
            endpoints=extra_tasks_endpoints,
        )

        transaction.on_commit(
            lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.BILLING_UPDATED,
                source=AnalyticsSource.WEB,
                properties={
                    "max_extra_tasks": configured_limit,
                    "auto_purchase": auto_purchase,
                    "infinite": infinite,
                    "owner_type": "organization",
                    "organization_id": str(membership.org.id),
                },
            )
        )

        return JsonResponse(
            {
                "success": True,
                "max_extra_tasks": configured_limit,
                "owner_type": "organization",
                "extra_tasks": extra_tasks,
            }
        )

    user_billing, _ = UserBilling.objects.get_or_create(
        user=request.user,
        defaults={"max_extra_tasks": 0},
    )

    sync_error_response = _sync_additional_tasks_metered_or_error(
        request.user,
        "user",
        auto_purchase,
        log_owner_label=f"user {request.user.id}",
    )
    if sync_error_response is not None:
        return sync_error_response

    if not auto_purchase:
        user_billing.max_extra_tasks = 0
    elif infinite:
        user_billing.max_extra_tasks = -1
    else:
        user_billing.max_extra_tasks = max(1, max_tasks)

    user_billing.save(update_fields=["max_extra_tasks"])

    configured_limit = int(user_billing.max_extra_tasks or 0)
    extra_tasks = derive_extra_tasks_settings(
        configured_limit,
        can_modify=True,
        endpoints=extra_tasks_endpoints,
    )

    transaction.on_commit(
        lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.BILLING_UPDATED,
            source=AnalyticsSource.WEB,
            properties={
                "max_extra_tasks": configured_limit,
                "auto_purchase": auto_purchase,
                "infinite": infinite,
                "owner_type": "user",
            },
        )
    )

    return JsonResponse(
        {
            "success": True,
            "max_extra_tasks": configured_limit,
            "owner_type": "user",
            "extra_tasks": extra_tasks,
        }
    )

@login_required
@tracer.start_as_current_span("BILLING Get Billing Settings")
def get_billing_settings(request):
    resolved = build_console_context(request)
    extra_tasks_endpoints = {
        "loadUrl": reverse("get_billing_settings"),
        "updateUrl": reverse("update_billing_settings"),
    }

    if resolved.current_context.type == "organization" and resolved.current_membership:
        membership = resolved.current_membership
        permitted = bool(membership and membership.role in BILLING_MANAGE_ROLES)

        OrgBilling = apps.get_model("api", "OrganizationBilling")
        defaults = {"max_extra_tasks": 0, "billing_cycle_anchor": timezone.now().day}
        org_billing, _ = OrgBilling.objects.get_or_create(
            organization=membership.org,
            defaults=defaults,
        )
        configured_limit = int(org_billing.max_extra_tasks or 0)

        return JsonResponse(
            {
                "max_extra_tasks": configured_limit,
                "owner_type": "organization",
                "can_modify": permitted,
                "extra_tasks": derive_extra_tasks_settings(
                    configured_limit,
                    can_modify=permitted,
                    endpoints=extra_tasks_endpoints,
                ),
            }
        )

    user_billing, _ = UserBilling.objects.get_or_create(
        user=request.user,
        defaults={"max_extra_tasks": 0},
    )
    configured_limit = int(user_billing.max_extra_tasks or 0)

    return JsonResponse(
        {
            "max_extra_tasks": configured_limit,
            "owner_type": "user",
            "can_modify": True,
            "extra_tasks": derive_extra_tasks_settings(
                configured_limit,
                can_modify=True,
                endpoints=extra_tasks_endpoints,
            ),
        }
    )


@login_required
@tracer.start_as_current_span("Get User Plan")
def get_user_plan_api(request):
    """Return the user's current subscription plan for frontend use."""
    from constants.plans import PlanNames

    startup_trial_days, scale_trial_days = _get_checkout_trial_days()
    startup_task_credits = get_active_public_plan_monthly_task_credits(PlanNames.STARTUP)
    scale_task_credits = get_active_public_plan_monthly_task_credits(PlanNames.SCALE)
    trial_eligible = _is_checkout_trial_eligible(request.user, request)
    pricing_modal_almost_full_screen = _is_pricing_modal_almost_full_screen_enabled(request)
    cta_start_free_trial = _is_cta_start_free_trial_enabled(request)
    cta_pricing_cancel_text_under_btn = _is_cta_pricing_cancel_text_under_btn_enabled(request)
    cta_unlock_agent_copy = _is_cta_unlock_agent_copy_enabled(request)
    cta_pick_a_plan = _is_cta_pick_a_plan_enabled(request)
    cta_continue_agent_btn = _is_cta_continue_agent_btn_enabled(request)
    cta_no_charge_during_trial = _is_cta_no_charge_during_trial_enabled(request)
    resolved_context = build_console_context(request)
    preview_config = _get_personal_signup_preview_config(request, resolved_context=resolved_context)
    personal_signup_preview_available = preview_config.ui_enabled
    personal_signup_preview_processing_available = preview_config.processing_limit_enabled

    try:
        plan = reconcile_user_plan_from_stripe(request.user)
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
            'startup_trial_days': startup_trial_days,
            'scale_trial_days': scale_trial_days,
            'startup_task_credits': startup_task_credits,
            'scale_task_credits': scale_task_credits,
            'trial_eligible': trial_eligible,
            'pricing_modal_almost_full_screen': pricing_modal_almost_full_screen,
            'cta_start_free_trial': cta_start_free_trial,
            'cta_pricing_cancel_text_under_btn': cta_pricing_cancel_text_under_btn,
            'cta_unlock_agent_copy': cta_unlock_agent_copy,
            'cta_pick_a_plan': cta_pick_a_plan,
            'cta_continue_agent_btn': cta_continue_agent_btn,
            'cta_no_charge_during_trial': cta_no_charge_during_trial,
            'personal_signup_preview_available': personal_signup_preview_available,
            'personal_signup_preview_processing_available': personal_signup_preview_processing_available,
        })
    except Exception as e:
        return JsonResponse({
            'plan': 'free',
            'is_proprietary_mode': settings.GOBII_PROPRIETARY_MODE,
            'startup_trial_days': startup_trial_days,
            'scale_trial_days': scale_trial_days,
            'startup_task_credits': startup_task_credits,
            'scale_task_credits': scale_task_credits,
            'trial_eligible': trial_eligible,
            'pricing_modal_almost_full_screen': pricing_modal_almost_full_screen,
            'cta_start_free_trial': cta_start_free_trial,
            'cta_pricing_cancel_text_under_btn': cta_pricing_cancel_text_under_btn,
            'cta_unlock_agent_copy': cta_unlock_agent_copy,
            'cta_pick_a_plan': cta_pick_a_plan,
            'cta_continue_agent_btn': cta_continue_agent_btn,
            'cta_no_charge_during_trial': cta_no_charge_during_trial,
            'personal_signup_preview_available': personal_signup_preview_available,
            'personal_signup_preview_processing_available': personal_signup_preview_processing_available,
            'error': str(e),
        })


_CANCEL_FEEDBACK_MAX_LENGTH = 500
_CANCEL_FEEDBACK_REASON_CODES = frozenset(
    {
        "too_expensive",
        "missing_features",
        "reliability_issues",
        "switching_tools",
        "no_longer_needed",
        "other",
    }
)


def _build_cancellation_feedback_properties(request: HttpRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if request.body:
        try:
            parsed = json.loads(request.body)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            payload = parsed

    reason = ""
    reason_candidate = payload.get("reason")
    if isinstance(reason_candidate, str):
        normalized_reason = reason_candidate.strip().lower()
        if normalized_reason in _CANCEL_FEEDBACK_REASON_CODES:
            reason = normalized_reason

    feedback = ""
    feedback_candidate = payload.get("feedback")
    if isinstance(feedback_candidate, str):
        feedback = feedback_candidate.strip()[:_CANCEL_FEEDBACK_MAX_LENGTH]

    properties: dict[str, Any] = {"cancel_feedback_version": 1}
    if reason:
        properties["cancel_reason_code"] = reason
    if feedback:
        properties["cancel_reason_text"] = feedback
    return properties


@login_required
@require_POST
@tracer.start_as_current_span("BILLING Cancel Subscription")
def cancel_subscription(request):
    """Endpoint to cancel the user's subscription at period end."""
    if not stripe_status().enabled:
        return JsonResponse({
            'success': False,
            'error': 'Stripe billing is not available in this deployment.'
        }, status=404)

    cancellation_properties = _build_cancellation_feedback_properties(request)

    sub = get_active_subscription(request.user)
    if sub:
        try:
            _assign_stripe_api_key()
            updated_subscription = stripe.Subscription.modify(sub.id, cancel_at_period_end=True)
            _sync_subscription_after_direct_update(updated_subscription)

            Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.BILLING_CANCELLATION,
                source=AnalyticsSource.WEB,
                properties=cancellation_properties,
            )

            return JsonResponse({'success': True})
        except stripe.error.StripeError:
            return JsonResponse(
                {
                    'success': False,
                    'error': 'Error cancelling subscription'
                },
                status=500,
            )
    else:
        return JsonResponse({
            'success': False,
            'error': "You do not have an active subscription to cancel."
        }, status=400)


def _stripe_object_field(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)


def _stripe_subscription_customer_id(subscription_payload: Any) -> str | None:
    customer = _stripe_object_field(subscription_payload, "customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    customer_id = str(customer or "").strip()
    return customer_id or None


@login_required
@require_POST
@tracer.start_as_current_span("BILLING Sync Subscription State")
def sync_billing_subscription_state(request):
    if not stripe_status().enabled:
        return JsonResponse(
            {
                'success': False,
                'error': 'Stripe billing is not available in this deployment.',
            },
            status=404,
        )

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        payload = {}

    if not isinstance(payload, dict):
        return JsonResponse(
            {
                'success': False,
                'error': 'subscriptionId is required.',
            },
            status=400,
        )

    subscription_id = str((payload.get("subscriptionId") or "")).strip()
    if not subscription_id:
        return JsonResponse(
            {
                'success': False,
                'error': 'subscriptionId is required.',
            },
            status=400,
        )

    customer = get_stripe_customer(request.user)
    if not customer or not getattr(customer, "id", None):
        return JsonResponse(
            {
                'success': False,
                'error': 'No Stripe customer is associated with this account.',
            },
            status=400,
        )

    try:
        _assign_stripe_api_key()
        updated_subscription = stripe.Subscription.retrieve(subscription_id)
    except stripe.error.StripeError:
        return JsonResponse(
            {
                'success': False,
                'error': 'Error syncing subscription state.',
            },
            status=500,
        )

    if _stripe_subscription_customer_id(updated_subscription) != str(customer.id):
        return JsonResponse(
            {
                'success': False,
                'error': 'Subscription does not belong to the current account.',
            },
            status=403,
        )

    active_subscription = get_active_subscription(request.user, sync_with_stripe=True)
    active_subscription_id = getattr(active_subscription, "id", None)
    if not active_subscription_id or str(active_subscription_id) != subscription_id:
        return JsonResponse(
            {
                'success': False,
                'error': 'Subscription is not the current active billing subscription for this account.',
            },
            status=403,
        )

    _sync_subscription_after_direct_update(updated_subscription)
    sync_owner_customer_account_pause(
        request.user,
        subscription_payload=updated_subscription,
        source="console.billing.sync_subscription_state",
    )
    return JsonResponse({'success': True})


@login_required
@require_POST
@tracer.start_as_current_span("BILLING Resume Subscription")
def resume_subscription(request):
    """Resume billing immediately by clearing cancellation and/or pause_collection."""
    if not stripe_status().enabled:
        return JsonResponse(
            {
                'success': False,
                'error': 'Stripe billing is not available in this deployment.'
            },
            status=404,
        )

    sub = get_active_subscription(request.user)
    if not sub:
        return JsonResponse(
            {
                'success': False,
                'error': "You do not have an active subscription to resume."
            },
            status=400,
        )

    try:
        _assign_stripe_api_key()
        account_pause_state = get_owner_account_pause_state(request.user)
        update_type = "subscription_resume"
        modify_kwargs: dict[str, Any] = {
            "cancel_at_period_end": False,
        }
        if account_pause_state.get("customer_paused"):
            modify_kwargs["pause_collection"] = ""
            update_type = "subscription_pause_resume"

        updated_subscription = stripe.Subscription.modify(sub.id, **modify_kwargs)
        _sync_subscription_after_direct_update(updated_subscription)
        sync_owner_customer_account_pause(
            request.user,
            subscription_payload=updated_subscription,
            source="console.billing.resume_subscription",
        )

        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.BILLING_UPDATED,
            source=AnalyticsSource.WEB,
            properties={
                "update_type": update_type,
            },
        )
        return JsonResponse({'success': True})
    except stripe.error.StripeError:
        return JsonResponse(
            {
                'success': False,
                'error': 'Error resuming subscription'
            },
            status=500,
        )

# ────────── Persistent Agents (Feature-Flagged) ──────────
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
            restore_oauth_session_state(request, overwrite_existing=False)

        if 'agent_charter' not in request.session:
            messages.error(request, "Please start by describing what your agent should do.")
            return redirect(f'{IMMERSIVE_APP_BASE_PATH}/agents')

        contact_email = (request.user.email or "").strip()
        if not contact_email:
            messages.error(request, "Please add an email address to continue.")
            return redirect(f'{IMMERSIVE_APP_BASE_PATH}/agents')

        try:
            result = create_persistent_agent_from_charter(
                request,
                initial_message=request.session.get('agent_charter'),
                contact_email=contact_email,
                email_enabled=True,
                sms_enabled=False,
                preferred_contact_method='email',
                preferred_llm_tier_key=request.session.get("agent_preferred_llm_tier"),
                charter_override=request.session.get('agent_charter_override'),
            )
        except TrialRequiredValidationError:
            set_trial_onboarding_intent(
                request,
                target=TRIAL_ONBOARDING_TARGET_AGENT_UI,
            )
            set_trial_onboarding_requires_plan_selection(request, required=True)
            return redirect(
                append_query_params(
                    f"{IMMERSIVE_APP_BASE_PATH}/agents/new",
                    {"spawn": "1"},
                )
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
            return redirect(f'{IMMERSIVE_APP_BASE_PATH}/agents')
        except Exception:
            logger.exception("Error creating persistent agent")
            messages.error(request, "We ran into a problem creating your agent. Please try again.")
            return redirect(f'{IMMERSIVE_APP_BASE_PATH}/agents')

        session_return_to = request.session.pop(IMMERSIVE_RETURN_TO_SESSION_KEY, None)
        popped_intelligence = False
        if "agent_preferred_llm_tier" in request.session:
            request.session.pop("agent_preferred_llm_tier", None)
            popped_intelligence = True
        if session_return_to is not None or popped_intelligence:
            request.session.modified = True
        embed = (request.GET.get("embed") or "").lower() in {"1", "true", "yes", "on"}
        # Default return_to to agents list so closing the chat doesn't redirect back
        # to this view (which would fail since agent_charter was consumed)
        return_to = request.GET.get("return_to") or session_return_to or f"{IMMERSIVE_APP_BASE_PATH}/agents"
        invalidate_account_info_cache(request.user.id)
        app_url = build_immersive_chat_url(
            request,
            result.agent.id,
            return_to=return_to,
            embed=embed,
        )
        response = redirect(app_url)

        # Clear OAuth fallback cookies if present (no longer needed)
        if OAUTH_CHARTER_COOKIE in request.COOKIES:
            response.delete_cookie(OAUTH_CHARTER_COOKIE)
        if OAUTH_ATTRIBUTION_COOKIE in request.COOKIES:
            response.delete_cookie(OAUTH_ATTRIBUTION_COOKIE)

        return response


class AgentDailyLimitEmailActionView(LoginRequiredMixin, View):
    """Apply one-click daily limit actions from the hard limit email."""

    def get(self, request, *args, **kwargs):
        agent_id = kwargs.get("pk")
        action = (kwargs.get("action") or "").strip().lower()
        if not agent_id or not action:
            raise Http404()

        agent = get_object_or_404(PersistentAgent.objects.non_eval().alive(), pk=agent_id)
        _enforce_personal_agent_access_or_raise(request.user, agent)
        if not user_can_manage_agent(request.user, agent):
            raise PermissionDenied("You do not have permission to manage this agent.")
        if action not in {"double", "unlimited"}:
            raise Http404()
        redirect_url = append_context_query(
            _agent_settings_app_path(agent),
            agent.organization_id,
        )
        token_payload = load_daily_limit_action_payload((request.GET.get("token") or "").strip())
        if (
            not token_payload
            or str(token_payload.get("agent_id")) != str(agent.id)
            or token_payload.get("action") != action
        ):
            messages.error(request, "This daily limit link is invalid or expired.")
            return redirect(redirect_url)
        owner = agent.organization or agent.user
        credit_settings = get_daily_credit_settings_for_owner(owner)
        slider_bounds = get_daily_credit_slider_bounds(
            credit_settings,
            tier_multiplier=get_agent_credit_multiplier(agent),
        )
        max_limit = int(slider_bounds["slider_limit_max"])
        current_limit = agent.daily_credit_limit
        previous_daily_limit = current_limit
        daily_limit_changed = False

        if action == "double":
            if current_limit is None or current_limit <= 0:
                messages.info(request, "This agent is already unlimited.")
            else:
                new_limit = min(int(current_limit) * 2, max_limit)
                if new_limit == current_limit:
                    messages.info(request, "This agent is already at your plan maximum.")
                else:
                    agent.daily_credit_limit = new_limit
                    agent.save(update_fields=["daily_credit_limit"])
                    daily_limit_changed = True
                    messages.success(request, "Daily limit doubled.")
        elif action == "unlimited":
            if current_limit is None:
                messages.info(request, "This agent is already unlimited.")
            else:
                agent.daily_credit_limit = None
                agent.save(update_fields=["daily_credit_limit"])
                daily_limit_changed = True
                messages.success(request, "Daily limit set to unlimited.")

        if daily_limit_changed:
            queue_settings_change_resume(
                agent,
                daily_credit_limit_changed=True,
                previous_daily_credit_limit=previous_daily_limit,
                source="daily_limit_email_action",
            )

        return redirect(redirect_url)

class ConsoleStatusView(SystemAdminRequiredMixin, TemplateView):
    template_name = "console/system_status.html"

    def post(self, request, *args, **kwargs):  # pragma: no cover - view is read-only
        return HttpResponseNotAllowed(['GET'])


class LegacyConsoleStatusRedirectView(View):
    """Preserve the legacy status URL while redirecting to the staff route."""

    def get(self, request, *args, **kwargs):
        return redirect("console-status")

    def post(self, request, *args, **kwargs):  # pragma: no cover - redirect only
        return HttpResponseNotAllowed(['GET'])


class StaffUsersView(SystemAdminRequiredMixin, TemplateView):
    template_name = "console/staff_users.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user_id = kwargs.get("user_id")
        org_id = kwargs.get("org_id")
        context["selected_user_id"] = str(user_id) if user_id is not None else ""
        context["selected_org_id"] = str(org_id) if org_id is not None else ""
        return context

    def post(self, request, *args, **kwargs):  # pragma: no cover - view is read-only
        return HttpResponseNotAllowed(['GET'])


class SystemSettingsView(SystemAdminRequiredMixin, TemplateView):
    template_name = "system_settings.html"

    def post(self, request, *args, **kwargs):  # pragma: no cover - read-only shell
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
        context["admin_agent_url"] = reverse("admin:api_persistentagent_change", args=[agent.id])
        return context


class PlatformMCPServerManagementView(SystemAdminRequiredMixin, TemplateView):
    template_name = "console/staff_platform_mcp.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        placeholder_id = "00000000-0000-0000-0000-000000000000"
        context.update(
            {
                "owner_scope": MCPServerConfig.Scope.PLATFORM,
                "owner_label": "Platform",
                "allow_mcp_commands": True,
                "pipedream_integrations_enabled": False,
                "mcp_server_list_url": reverse("staff-platform-mcp-server-list"),
                "mcp_server_detail_url_template": reverse(
                    "staff-platform-mcp-server-detail",
                    args=[placeholder_id],
                ),
                "mcp_server_test_url_template": reverse(
                    "staff-platform-mcp-server-test",
                    args=[placeholder_id],
                ),
                # Platform MCP servers are globally available, so assignments stay hidden in the UI.
                "mcp_server_assign_url_template": reverse(
                    "console-mcp-server-assignments",
                    args=[placeholder_id],
                ),
            }
        )
        return context


class MCPOAuthCallbackPageView(ConsoleViewMixin, TemplateView):
    """Landing page shown after external OAuth redirects back to Gobii."""

    template_name = "console/mcp_oauth_callback.html"


class AgentEmailOAuthCallbackPageView(ConsoleViewMixin, TemplateView):
    """Landing page shown after email OAuth redirects back to Gobii."""

    template_name = "console/agent_email_oauth_callback.html"


class NativeIntegrationOAuthCallbackPageView(ConsoleViewMixin, TemplateView):
    """Landing page shown after native integration OAuth redirects back to Gobii."""

    template_name = "console/native_integration_oauth_callback.html"


class SharedAgentAccessMixin(AgentOwnerContextOverrideMixin):
    allow_delinquent_personal_chat = False

    def get_object(self, queryset=None):
        agent_id = self.kwargs.get(self.pk_url_kwarg)
        if not agent_id:
            raise Http404
        agent = resolve_agent_for_request(
            self.request,
            agent_id,
            allow_shared=True,
            allow_delinquent_personal_chat=self.allow_delinquent_personal_chat,
        )
        self._can_manage_agent = user_can_manage_agent(
            self.request.user,
            agent,
            allow_delinquent_personal_chat=self.allow_delinquent_personal_chat,
        )
        self._is_collaborator = user_is_collaborator(self.request.user, agent)
        self._can_manage_collaborators = False
        if agent.user_id == self.request.user.id:
            self._can_manage_collaborators = True
        elif agent.organization_id:
            self._can_manage_collaborators = OrganizationMembership.objects.filter(
                org=agent.organization,
                user=self.request.user,
                status=OrganizationMembership.OrgStatus.ACTIVE,
                role__in=[
                    OrganizationMembership.OrgRole.OWNER,
                    OrganizationMembership.OrgRole.ADMIN,
                    OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
                ],
            ).exists()
        return agent

    @property
    def can_manage_agent(self) -> bool:
        return bool(getattr(self, "_can_manage_agent", False))

    @property
    def is_collaborator(self) -> bool:
        return bool(getattr(self, "_is_collaborator", False))

    @property
    def can_manage_collaborators(self) -> bool:
        return bool(getattr(self, "_can_manage_collaborators", False))








AGENT_AVATAR_THUMBNAIL_SIZE = 128
AGENT_AVATAR_THUMBNAIL_CONTENT_TYPE = "image/png"


def _agent_avatar_thumbnail_name(agent_id: Any, avatar_version: str) -> str:
    return f"agent_avatar_thumbnails/{agent_id}/{avatar_version}.png"


def _serve_agent_avatar_thumbnail(agent: PersistentAgent, *, cache_control: str) -> FileResponse:
    file_field = getattr(agent, "avatar", None)
    if not file_field or not getattr(file_field, "name", None):
        raise Http404("Avatar not found.")

    storage = file_field.storage
    original_name = file_field.name
    if hasattr(storage, "exists") and not storage.exists(original_name):
        raise Http404("Avatar not found.")

    avatar_version = agent.get_avatar_version()
    if not avatar_version:
        raise Http404("Avatar not found.")

    thumbnail_version = agent.get_avatar_thumbnail_version() or avatar_version
    thumbnail_name = _agent_avatar_thumbnail_name(agent.id, thumbnail_version)
    if not default_storage.exists(thumbnail_name):
        _generate_agent_avatar_thumbnail(storage, original_name, thumbnail_name)

    try:
        file_handle = default_storage.open(thumbnail_name, "rb")
    except (FileNotFoundError, OSError):
        raise Http404("Avatar thumbnail not found.")

    response = FileResponse(file_handle, content_type=AGENT_AVATAR_THUMBNAIL_CONTENT_TYPE)
    response["Cache-Control"] = cache_control
    return response


def _generate_agent_avatar_thumbnail(storage, original_name: str, thumbnail_name: str) -> None:
    try:
        with storage.open(original_name, "rb") as original_file:
            with Image.open(original_file) as image:
                image = ImageOps.exif_transpose(image)
                thumbnail = ImageOps.fit(
                    image,
                    (AGENT_AVATAR_THUMBNAIL_SIZE, AGENT_AVATAR_THUMBNAIL_SIZE),
                    method=Image.Resampling.LANCZOS,
                )
                output = io.BytesIO()
                thumbnail.convert("RGBA").save(output, format="PNG", optimize=True)
    except (FileNotFoundError, OSError, UnidentifiedImageError):
        raise Http404("Avatar not found.")

    saved_name = default_storage.save(thumbnail_name, ContentFile(output.getvalue()))
    if saved_name != thumbnail_name:
        try:
            default_storage.delete(saved_name)
        except OSError:
            pass


class AgentAvatarProxyView(SharedAgentAccessMixin, ConsoleViewMixin, DetailView):
    model = PersistentAgent
    context_object_name = "agent"
    pk_url_kwarg = "pk"
    http_method_names = ["get"]
    allow_delinquent_personal_chat = True

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


class AgentAvatarThumbnailProxyView(AgentAvatarProxyView):
    """Serve a cached live-chat-sized avatar thumbnail behind the same access checks."""

    def get(self, request, *args, **kwargs):
        agent = self.get_object()
        return _serve_agent_avatar_thumbnail(agent, cache_control="private, max-age=86400")


class PublicAgentAvatarThumbnailView(View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        agent = get_object_or_404(PersistentAgent.objects.alive(), pk=kwargs.get("pk"))
        token = str(request.GET.get("token") or "")
        if not validate_public_agent_avatar_thumbnail_token(agent, token):
            raise Http404("Avatar not found.")
        return _serve_agent_avatar_thumbnail(agent, cache_control="public, max-age=86400")


class AgentDeleteView(LoginRequiredMixin, View):
    """Handle agent deletion."""

    @transaction.atomic
    @tracer.start_as_current_span("CONSOLE Agent Delete View - delete")
    def delete(self, request, *args, **kwargs):
        try:
            agent = PersistentAgent.objects.non_eval().alive().get(
                pk=self.kwargs['pk'],
                user=request.user,
            )
            _enforce_personal_agent_access_or_raise(request.user, agent)

            agent_name = agent.name
            agent_id = str(agent.pk)
            agent_org = agent.organization

            # Keep historical rows and usage while removing the agent from active views.
            agent.soft_delete()

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
            invalidate_account_info_cache(request.user.id)

            response = HttpResponse(status=200)
            response['HX-Redirect'] = f'{IMMERSIVE_APP_BASE_PATH}/agents'
            return response
            
        except PersistentAgent.DoesNotExist:
            return HttpResponse("Agent not found or you don't have permission.", status=404)
        except PermissionDenied:
            raise

@login_required
@require_POST
@tracer.start_as_current_span("GRANT_CREDITS")
def grant_credits(request):
    """Endpoint to grant 100 task credits to a user. Admin only."""

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
            grant_date = timezone.now()
            expiration_date = grant_date + timedelta(days=365)

            TaskCredit.objects.create(
                user=user,
                credits=100,
                credits_used=0,
                granted_date=grant_date,
                expiration_date=expiration_date,
                plan=PlanNamesChoices.FREE,
                grant_type=GrantTypeChoices.COMPENSATION,
                additional_task=False,
                voided=False,
            )

            logger.info("Admin %s granted 100 task credits to user %s", request.user.id, user.id)

            return JsonResponse({
                'success': True,
                'message': f"100 task credits granted to {user.email or user.username}.",
                'credits_granted': 100,
            })

    except DatabaseError as exc:
        logger.error("Failed to grant credits to user %s: %s", user_id, exc)
        return JsonResponse({'success': False, 'error': "Failed to grant credits."}, status=500)


class AgentContactRequestsView(LoginRequiredMixin, View):
    """Compatibility route for legacy contact request approval links."""
    
    def _resolve_agent_or_issue(self):
        """Return (agent, issue) where issue is one of: None, 'invalid', 'wrong_account'."""
        pk = self.kwargs['pk']
        current_span = trace.get_current_span()
        agent = (
            PersistentAgent.objects.non_eval().alive()
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

        if agent.organization_id is None and not can_user_use_personal_agents_and_api(self.request.user):
            if current_span:
                current_span.set_attribute("approval.issue", "wrong_account")
            logger.info(
                "Agent contact-requests blocked by personal trial enforcement",
                extra={"agent_id": str(pk), "user_id": self.request.user.id},
            )
            return None, "wrong_account"
            
        return agent, None

    @tracer.start_as_current_span("CONSOLE Agent Contact Requests View - get")
    def get(self, request, *args, **kwargs):
        agent, issue = self._resolve_agent_or_issue()
        if issue:
            return self._issue_response(request, action='view', issue=issue)
        return self._redirect_to_immersive(request, agent)
    
    def post(self, request, *args, **kwargs):
        agent, issue = self._resolve_agent_or_issue()
        if issue:
            return self._issue_response(request, action='update', issue=issue)
        return self._redirect_to_immersive(request, agent)

    def _redirect_to_immersive(self, request, agent):
        return redirect(
            build_immersive_contact_requests_url(
                request,
                agent.id,
                organization_id=str(agent.organization_id) if agent.organization_id else None,
            )
        )

    def _issue_response(self, request, action: str, issue: str, extra: dict | None = None):
        ctx = {
            'issue': issue,
            'context_type': 'agent_allowlist',
            'action': action,
        }
        if extra:
            ctx.update(extra)
        return render(request, "console/approval_link_issue.html", ctx, status=200)



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

    def _accept_invite(self, request, token: str, *, add_message: bool = True):
        invite, issue, extra = self._resolve_invite_or_issue(request, token)
        if issue:
            return None, issue, extra

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
        seat_eligible = membership.role != OrganizationMembership.OrgRole.SOLUTIONS_PARTNER
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

        if seat_eligible and (created or not was_active):
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_SEAT_ASSIGNED,
                source=AnalyticsSource.WEB,
                properties=seat_props.copy(),
            ))
        if add_message:
            messages.success(request, f"Joined {invite.org.name}.")
        return invite, None, {}

    def _accept(self, request, token: str):
        invite, issue, extra = self._accept_invite(request, token)
        if issue:
            ctx = {"issue": issue, "context_type": "organization_invite", "action": "accept"}
            ctx.update(extra)
            return render(request, "console/approval_link_issue.html", ctx, status=200)
        return redirect(_organization_app_path(invite.org.id))

    @tracer.start_as_current_span("CONSOLE Organization Invite Accept")
    @transaction.atomic
    def post(self, request, token: str):
        return self._accept(request, token)

    @tracer.start_as_current_span("CONSOLE Organization Invite Accept")
    @transaction.atomic
    def get(self, request, token: str):
        return self._accept(request, token)


class OrganizationInviteAcceptAPIView(OrganizationInviteAcceptView):
    """Accept an organization invite from the immersive app shell."""

    http_method_names = ["post"]

    @tracer.start_as_current_span("APP Organization Invite Accept")
    @transaction.atomic
    def post(self, request, token: str):
        invite, issue, extra = self._accept_invite(request, token, add_message=False)
        if issue:
            payload: dict[str, object] = {
                "ok": False,
                "issue": issue,
                "action": "accept",
            }
            org = extra.get("org")
            if org is not None:
                payload["organization"] = {
                    "id": str(org.id),
                    "name": org.name,
                }
            invited_email = extra.get("invited_email")
            if invited_email:
                payload["invitedEmail"] = invited_email
            invited_by = extra.get("invited_by")
            if invited_by is not None:
                payload["invitedBy"] = invited_by.email or invited_by.username
            return JsonResponse(payload)

        return JsonResponse({
            "ok": True,
            "organization": {
                "id": str(invite.org.id),
                "name": invite.org.name,
            },
            "redirectUrl": _organization_app_path(invite.org.id),
        })


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
            seat_eligible = invite.role != OrganizationMembership.OrgRole.SOLUTIONS_PARTNER
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
            if seat_eligible:
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
        return redirect(_organization_app_path())

    @tracer.start_as_current_span("CONSOLE Organization Invite Reject")
    @transaction.atomic
    def post(self, request, token: str):
        return self._reject(request, token)

    @tracer.start_as_current_span("CONSOLE Organization Invite Reject")
    @transaction.atomic
    def get(self, request, token: str):
        return self._reject(request, token)


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
            role__in=BILLING_MANAGE_ROLES,
        ).first()

        if membership is None:
            return HttpResponseForbidden()

        billing = getattr(org, "billing", None)
        if not billing or not billing.stripe_customer_id:
            detail = "This organization does not have an active Stripe subscription yet."
            if _wants_json_response(request):
                return JsonResponse({"ok": False, "error": "stripe_customer_missing", "detail": detail}, status=400)
            messages.error(request, "This organization does not have an active Stripe subscription yet.")
            return redirect(f"{IMMERSIVE_APP_BASE_PATH}/billing")

        try:
            _assign_stripe_api_key()

            return_url = request.build_absolute_uri(f"{IMMERSIVE_APP_BASE_PATH}/billing")

            session = stripe.billing_portal.Session.create(
                customer=billing.stripe_customer_id,
                api_key=stripe.api_key,
                return_url=return_url,
            )

            if _wants_json_response(request):
                return JsonResponse({"ok": True, "redirectUrl": session.url})
            return redirect(session.url)
        except stripe.error.StripeError:
            logger.exception("Failed to create Stripe billing portal session for org %s", org.id)
            detail = "We weren’t able to open the Stripe billing portal. Please try again or contact support."
            if _wants_json_response(request):
                return JsonResponse({"ok": False, "error": "stripe_error", "detail": detail}, status=400)
            messages.error(
                request,
                detail,
            )
            return redirect(f"{IMMERSIVE_APP_BASE_PATH}/billing")


class _OrgPermissionMixin:
    """Utilities for checking org membership/role permissions."""

    def _require_org_admin(self, request, org: Organization):
        try:
            membership = OrganizationMembership.objects.get(org=org, user=request.user)
        except OrganizationMembership.DoesNotExist:
            return None
        if membership.status != OrganizationMembership.OrgStatus.ACTIVE:
            return None
        # Allow owner-equivalent roles to manage invites
        if membership.role not in (
            OrganizationMembership.OrgRole.OWNER,
            OrganizationMembership.OrgRole.ADMIN,
            OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
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
            seat_eligible = invite.role != OrganizationMembership.OrgRole.SOLUTIONS_PARTNER
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
            if seat_eligible:
                transaction.on_commit(lambda: Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.ORGANIZATION_SEAT_UNASSIGNED,
                    source=AnalyticsSource.WEB,
                    properties=seat_props.copy(),
                ))
            messages.success(request, "Invitation revoked.")
        return redirect(_organization_app_path(org.id))


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
            return redirect(_organization_app_path(org.id))

        try:
            accept_url = request.build_absolute_uri(
                f"/app/organizations/invites/{invite.token}/accept"
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
                subject=subject,
                message=text_body,
                from_email=None,
                recipient_list=[invite.email],
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

        return redirect(_organization_app_path(org.id))


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
            return redirect(_organization_app_path(org.id))

        target_membership = get_object_or_404(
            OrganizationMembership,
            org=org,
            user_id=user_id,
        )

        if target_membership.status != OrganizationMembership.OrgStatus.ACTIVE:
            messages.info(request, "This member is already removed.")
            return redirect(_organization_app_path(org.id))

        # Admins cannot remove owner-equivalent roles
        if (
            acting_membership.role == OrganizationMembership.OrgRole.ADMIN
            and target_membership.role in (
                OrganizationMembership.OrgRole.OWNER,
                OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
            )
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
                return redirect(_organization_app_path(org.id))

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
        seat_eligible = target_membership.role != OrganizationMembership.OrgRole.SOLUTIONS_PARTNER
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
        if seat_eligible:
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_SEAT_UNASSIGNED,
                source=AnalyticsSource.WEB,
                properties=seat_props.copy(),
            ))
        messages.success(request, "Member removed.")
        return redirect(_organization_app_path(org.id))


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
            return redirect(_organization_app_path())

        # Prevent leaving if this is the last remaining owner
        if membership.role == OrganizationMembership.OrgRole.OWNER:
            active_owner_count = OrganizationMembership.objects.filter(
                org=org,
                role=OrganizationMembership.OrgRole.OWNER,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).count()
            if active_owner_count <= 1:
                messages.error(request, "You are the last owner. Transfer ownership or add another owner before leaving.")
                return redirect(_organization_app_path(org.id))

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
        seat_eligible = membership.role != OrganizationMembership.OrgRole.SOLUTIONS_PARTNER
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
        if seat_eligible:
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
        return redirect(_organization_app_path())


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
            return redirect(_organization_app_path(org.id))

        # Admins cannot modify owner-equivalent roles, nor assign them
        if acting_membership.role == OrganizationMembership.OrgRole.ADMIN:
            if target_membership.role in (
                OrganizationMembership.OrgRole.OWNER,
                OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
            ):
                return HttpResponseForbidden()
            if new_role in (
                OrganizationMembership.OrgRole.OWNER,
                OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
            ):
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
                return redirect(_organization_app_path(org.id))

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
        return redirect(_organization_app_path(org.id))


def _agent_transfer_invite_queryset():
    return AgentTransferInvite.objects.select_related("agent", "agent__user", "initiated_by")


def _send_agent_transfer_owner_notification(request, action: str, original_owner, agent: PersistentAgent) -> None:
    original_owner_email = getattr(original_owner, "email", "") or ""
    if not original_owner_email:
        return

    recipient_name = request.user.get_full_name() or request.user.email or request.user.get_username() or "A user"
    context = {
        "owner_name": original_owner.get_full_name() or original_owner_email,
        "recipient_name": recipient_name,
        "agent": agent,
        "agent_url": request.build_absolute_uri(_agent_settings_app_path(agent)),
    }
    if action == "accept":
        subject = f"{recipient_name} accepted your agent {agent.name}"
        text_template = "emails/agent_transfer_owner_accepted.txt"
        html_template = "emails/agent_transfer_owner_accepted.html"
        warning_label = "acceptance"
    else:
        subject = f"{recipient_name} declined your agent {agent.name}"
        text_template = "emails/agent_transfer_owner_declined.txt"
        html_template = "emails/agent_transfer_owner_declined.html"
        warning_label = "decline"

    try:
        send_mail(
            subject=subject,
            message=render_to_string(text_template, context),
            from_email=None,
            recipient_list=[original_owner_email],
            html_message=render_to_string(html_template, context),
            fail_silently=False,
        )
    except (BadHeaderError, OSError, SMTPException) as email_exc:  # pragma: no cover - best effort
        logger.warning(
            "Failed to send transfer %s email to %s: %s",
            warning_label,
            original_owner_email,
            email_exc,
        )


def _agent_transfer_response_agent_payload(request, agent: PersistentAgent) -> dict[str, Any]:
    return {
        "id": str(agent.id),
        "name": agent.name or "",
        "isActive": bool(agent.is_active),
        "detailUrl": _agent_settings_app_path(agent),
        "chatUrl": build_immersive_chat_url(
            request,
            agent.id,
            return_to=f"{IMMERSIVE_APP_BASE_PATH}/agents",
        ),
    }


class AgentTransferInviteRespondAPIView(LoginRequiredMixin, View):
    """JSON accept/decline endpoint for app sidebar transfer invites."""

    http_method_names = ["post"]

    def post(self, request, invite_id: uuid.UUID, action: str):
        if action not in {"accept", "decline"}:
            return JsonResponse({"ok": False, "error": "Unsupported invite action."}, status=400)

        invite = _agent_transfer_invite_queryset().filter(pk=invite_id).first()
        if invite is None:
            return JsonResponse({"ok": False, "error": "Transfer invite not found."}, status=404)
        if invite.status != AgentTransferInvite.Status.PENDING:
            return JsonResponse({"ok": False, "error": "This transfer invite has already been handled."}, status=409)

        user_email = (request.user.email or "").strip().lower()
        if not user_email or (invite.to_email or "").strip().lower() != user_email:
            return JsonResponse({"ok": False, "error": "This transfer invite is not addressed to your account."}, status=403)

        try:
            original_owner = invite.initiated_by
            if action == "accept":
                accepted_invite = AgentTransferService.accept_invite(invite, request.user)
                agent = PersistentAgent.objects.get(pk=accepted_invite.agent_id)
                _send_agent_transfer_owner_notification(request, action, original_owner, agent)
                if not agent.is_active:
                    message = f"You now own {agent.name}, but it has been paused because you are at your agent limit."
                else:
                    message = f"You now own {agent.name}."
                payload = {
                    "ok": True,
                    "action": action,
                    "message": message,
                    "agent": _agent_transfer_response_agent_payload(request, agent),
                }
            else:
                agent = invite.agent
                AgentTransferService.decline_invite(invite, request.user)
                _send_agent_transfer_owner_notification(request, action, original_owner, agent)
                payload = {
                    "ok": True,
                    "action": action,
                    "message": "Transfer invitation declined.",
                    "agent": None,
                }
        except AgentTransferDenied as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=403)
        except AgentTransferError as exc:
            return JsonResponse(
                {"ok": False, "error": f"Could not process the transfer invite: {exc}"},
                status=400,
            )
        return JsonResponse(payload)


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
            
        except AgentAllowlistInvite.DoesNotExist:
            messages.error(request, "Invalid invitation token.")
        except Exception as e:
            messages.error(request, f"Error rejecting invitation: {e}")
            
        return redirect("agent_allowlist_invite_reject", token=token)


def _agent_collaborator_invite_app_path(token: str, action: str) -> str:
    return f"{IMMERSIVE_APP_BASE_PATH}/agent-collaborator-invites/{token}/{action}"


def _agent_collaborator_invite_agent_payload(invite: AgentCollaboratorInvite) -> dict[str, str]:
    return {
        "id": str(invite.agent_id),
        "name": invite.agent.name,
    }


class AgentCollaboratorInviteValidationMixin:
    def _user_matches_invite(self, user, invite: AgentCollaboratorInvite) -> bool:
        invite_email = (invite.email or "").strip().lower()
        if not invite_email:
            return False
        if (user.email or "").strip().lower() == invite_email:
            return True
        from allauth.account.models import EmailAddress

        return EmailAddress.objects.filter(user=user, email__iexact=invite_email).exists()

    def _resolve_invite_or_issue(self, request, token: str):
        try:
            invite = AgentCollaboratorInvite.objects.select_related(
                "agent",
                "agent__user",
                "agent__organization",
                "invited_by",
            ).get(token=token)
        except AgentCollaboratorInvite.DoesNotExist:
            return None, "invalid", {}

        extra = {
            "agent": invite.agent,
            "invited_email": invite.email,
            "invited_by": invite.invited_by,
            "status": invite.get_status_display(),
        }
        if invite.status != AgentCollaboratorInvite.InviteStatus.PENDING:
            return invite, "already_responded", extra
        if invite.is_expired():
            return invite, "expired", extra
        if not self._user_matches_invite(request.user, invite):
            return invite, "wrong_account", extra
        return invite, None, {}

    def _issue_payload(self, issue: str, extra: dict, action: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": False,
            "issue": issue,
            "action": action,
        }
        agent = extra.get("agent")
        if agent is not None:
            payload["agent"] = {
                "id": str(agent.id),
                "name": agent.name,
            }
        invited_email = extra.get("invited_email")
        if invited_email:
            payload["invitedEmail"] = invited_email
        invited_by = extra.get("invited_by")
        if invited_by is not None:
            payload["invitedBy"] = invited_by.email or invited_by.username
        status = extra.get("status")
        if status:
            payload["status"] = status
        return payload


class AgentCollaboratorInviteAcceptAPIView(AgentCollaboratorInviteValidationMixin, LoginRequiredMixin, View):
    http_method_names = ["post"]

    @transaction.atomic
    def post(self, request, token: str):
        invite, issue, extra = self._resolve_invite_or_issue(request, token)
        if issue:
            return JsonResponse(self._issue_payload(issue, extra, "accept"))

        try:
            invite.accept(request.user)
        except ValidationError as exc:
            message_text = exc.messages[0] if getattr(exc, "messages", None) else "Unable to accept invitation."
            return JsonResponse({
                "ok": False,
                "issue": "invalid",
                "action": "accept",
                "message": message_text,
            })

        accept_props = Analytics.with_org_properties(
            {
                'agent_id': str(invite.agent_id),
                'agent_name': invite.agent.name,
                'invite_id': str(invite.id),
                'invite_email': invite.email,
                'invited_by_id': str(invite.invited_by_id),
                'collaborator_user_id': str(request.user.id),
                'actor_id': str(request.user.id),
            },
            organization=getattr(invite.agent, "organization", None),
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.AGENT_COLLABORATOR_INVITE_ACCEPTED,
            source=AnalyticsSource.WEB,
            properties=accept_props.copy(),
        ))
        return JsonResponse({
            "ok": True,
            "action": "accept",
            "agent": _agent_collaborator_invite_agent_payload(invite),
            "redirectUrl": build_immersive_chat_url(
                request,
                invite.agent_id,
                return_to=f"{IMMERSIVE_APP_BASE_PATH}/agents",
            ),
        })


class AgentCollaboratorInviteDeclineAPIView(AgentCollaboratorInviteValidationMixin, LoginRequiredMixin, View):
    http_method_names = ["post"]

    @transaction.atomic
    def post(self, request, token: str):
        invite, issue, extra = self._resolve_invite_or_issue(request, token)
        if issue:
            return JsonResponse(self._issue_payload(issue, extra, "decline"))

        invite.reject()
        decline_props = Analytics.with_org_properties(
            {
                'agent_id': str(invite.agent_id),
                'agent_name': invite.agent.name,
                'invite_id': str(invite.id),
                'invite_email': invite.email,
                'invited_by_id': str(invite.invited_by_id),
                'collaborator_user_id': str(request.user.id),
                'actor_id': str(request.user.id),
            },
            organization=getattr(invite.agent, "organization", None),
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.AGENT_COLLABORATOR_INVITE_DECLINED,
            source=AnalyticsSource.WEB,
            properties=decline_props.copy(),
        ))
        return JsonResponse({
            "ok": True,
            "action": "decline",
            "agent": _agent_collaborator_invite_agent_payload(invite),
            "redirectUrl": f"{IMMERSIVE_APP_BASE_PATH}/agents",
        })


class AgentCollaboratorInviteAcceptView(AgentCollaboratorInviteValidationMixin, LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        return redirect(_agent_collaborator_invite_app_path(kwargs.get("token"), "accept"))

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
            accept_props = Analytics.with_org_properties(
                {
                    'agent_id': str(invite.agent_id),
                    'agent_name': invite.agent.name,
                    'invite_id': str(invite.id),
                    'invite_email': invite.email,
                    'invited_by_id': str(invite.invited_by_id),
                    'collaborator_user_id': str(request.user.id),
                    'actor_id': str(request.user.id),
                },
                organization=getattr(invite.agent, "organization", None),
            )
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.AGENT_COLLABORATOR_INVITE_ACCEPTED,
                source=AnalyticsSource.WEB,
                properties=accept_props.copy(),
            ))
            return redirect(
                build_immersive_chat_url(
                    request,
                    invite.agent_id,
                    return_to=f"{IMMERSIVE_APP_BASE_PATH}/agents",
                )
            )
        except ValidationError as exc:
            message_text = exc.messages[0] if getattr(exc, "messages", None) else "Unable to accept invitation."
            messages.error(request, message_text)
        except Exception as exc:
            messages.error(request, f"Error accepting invitation: {exc}")

        return redirect("agent_collaborator_invite_accept", token=token)


class AgentCollaboratorInviteRejectView(AgentCollaboratorInviteValidationMixin, LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        return redirect(_agent_collaborator_invite_app_path(kwargs.get("token"), "decline"))

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
            decline_props = Analytics.with_org_properties(
                {
                    'agent_id': str(invite.agent_id),
                    'agent_name': invite.agent.name,
                    'invite_id': str(invite.id),
                    'invite_email': invite.email,
                    'invited_by_id': str(invite.invited_by_id),
                    'collaborator_user_id': str(request.user.id),
                    'actor_id': str(request.user.id),
                },
                organization=getattr(invite.agent, "organization", None),
            )
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.AGENT_COLLABORATOR_INVITE_DECLINED,
                source=AnalyticsSource.WEB,
                properties=decline_props.copy(),
            ))
        except Exception as exc:
            messages.error(request, f"Error rejecting invitation: {exc}")

        return redirect("agent_collaborator_invite_reject", token=token)


def _resolve_billing_owner(request):
    resolved = build_console_context(request)

    if resolved.current_context.type == "organization":
        membership = resolved.current_membership
        if membership is None:
            messages.error(request, "You no longer have access to manage this organization.")
            return redirect(f'{IMMERSIVE_APP_BASE_PATH}/billing')
        if membership.role not in BILLING_MANAGE_ROLES:
            messages.error(request, "You do not have permission to modify billing settings for this organization.")
            return redirect(f'{IMMERSIVE_APP_BASE_PATH}/billing')
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
        plan = reconcile_user_plan_from_stripe(owner)
    return (plan or {}).get("id")



from typing import Mapping


def _get_subscription_item_for_price(subscription_data: Mapping[str, Any], price_id: str) -> Mapping[str, Any] | None:
    items = (subscription_data.get("items") or {}).get("data", []) if isinstance(subscription_data, Mapping) else []
    for item in items or []:
        price = item.get("price") or {}
        if price.get("id") == price_id:
            return item
    return None




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
        return redirect(f"{IMMERSIVE_APP_BASE_PATH}/billing")

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
        addon_changed = False
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
            addon_changed = True
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
        if addon_kind == "task_pack" and addon_changed:
            queue_owner_task_pack_resume(
                owner_id=getattr(owner, "id", None),
                owner_type=owner_type,
                source="billing_addon_quantity_update",
            )
        return redirect(_billing_redirect(owner, owner_type))
    except stripe.error.StripeError as exc:
        logger.warning("Stripe API error while updating addon quantity: %s", exc)
        messages.error(request, f"A billing error occurred: {exc}")
    except Exception:
        logger.exception("Failed to update %s quantity for %s", addon_kind, getattr(owner, "id", None) or owner)
        messages.error(request, f"An unexpected error occurred while updating {failure_noun}.")

    return redirect(_billing_redirect(owner, owner_type))


@login_required
@require_POST
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
@with_billing_owner
@tracer.start_as_current_span("BILLING Update Add-ons Batch")
def update_addons(request, owner, owner_type):
    from console.billing_update_service import BillingUpdateError, SUPPORT_DETAIL, apply_addon_price_quantities

    if not stripe_status().enabled:
        messages.error(request, "Stripe billing is not available in this deployment.")
        return redirect(f"{IMMERSIVE_APP_BASE_PATH}/billing")

    desired_quantities: dict[str, int] = {}
    for key, value in request.POST.items():
        if not key.startswith("quantity__"):
            continue
        price_id = key.replace("quantity__", "", 1)
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

    try:
        action_url = apply_addon_price_quantities(
            owner,
            owner_type,
            desired_quantities=desired_quantities,
            created_via="console_batch_update",
            end_trial_on_purchase=False,
        )
        if action_url:
            return redirect(action_url)
        plan_id = (reconcile_user_plan_from_stripe(owner) or {}).get("id") if owner_type == "user" else (get_organization_plan(owner) or {}).get("id")
        task_options = AddonEntitlementService.get_price_options(owner_type, plan_id, "task_pack")
        task_price_ids = {opt.price_id for opt in (task_options or []) if getattr(opt, "price_id", None)}
        if task_price_ids & set(desired_quantities.keys()):
            queue_owner_task_pack_resume(
                owner_id=getattr(owner, "id", None),
                owner_type=owner_type,
                source="billing_addons_batch_update",
            )
        messages.success(request, "Add-ons updated.")
    except BillingUpdateError as exc:
        if exc.detail:
            messages.error(request, exc.detail)
        elif exc.code == "invalid_addon_price":
            messages.error(request, "That add-on tier is not available for your plan.")
        elif exc.code == "addons_not_configured":
            messages.error(request, "No add-ons are configured for your plan.")
        else:
            messages.error(request, SUPPORT_DETAIL if exc.code in {"stripe_error", "server_error"} else "Unable to update add-ons.")

    return redirect(_billing_redirect(owner, owner_type))


@login_required
@require_POST
@with_billing_owner
@tracer.start_as_current_span("BILLING Add Dedicated IP Quantity")
def add_dedicated_ip_quantity(request, owner, owner_type):
    if not stripe_status().enabled:
        messages.error(request, "Stripe billing is not available in this deployment.")
        return redirect(f'{IMMERSIVE_APP_BASE_PATH}/billing')

    owner_plan_id = None
    if owner_type == "user":
        plan = reconcile_user_plan_from_stripe(owner)
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

    from console.billing_update_service import BillingUpdateError, SUPPORT_DETAIL, apply_dedicated_ip_changes

    add_quantity = int(form.cleaned_data["quantity"])
    try:
        action_url = apply_dedicated_ip_changes(
            owner,
            owner_type,
            add_quantity=add_quantity,
            remove_proxy_ids=[],
            unassign_proxy_ids=set(),
        )
        if action_url:
            return redirect(action_url)
        messages.success(request, "Dedicated IP quantity updated.")
    except BillingUpdateError as exc:
        messages.error(request, exc.detail or SUPPORT_DETAIL)
    except Exception:
        logger.exception("Failed to update dedicated IP quantity", exc_info=True)
        messages.error(request, SUPPORT_DETAIL)

    return redirect(_billing_redirect(owner, owner_type))


@login_required
@require_POST
@with_billing_owner
@tracer.start_as_current_span("BILLING Remove Dedicated IP")
def remove_dedicated_ip(request, owner, owner_type):
    if not stripe_status().enabled:
        messages.error(request, "Stripe billing is not available in this deployment.")
        return redirect(f'{IMMERSIVE_APP_BASE_PATH}/billing')

    proxy_id = request.POST.get("proxy_id")
    if not proxy_id:
        messages.error(request, "Missing dedicated IP identifier.")
        return redirect(_billing_redirect(owner, owner_type))

    from console.billing_update_service import BillingUpdateError, SUPPORT_DETAIL, apply_dedicated_ip_changes

    try:
        action_url = apply_dedicated_ip_changes(
            owner,
            owner_type,
            add_quantity=0,
            remove_proxy_ids=[proxy_id],
            # Legacy endpoint: automatically unassign scoped agents before removing.
            unassign_proxy_ids={proxy_id},
        )
        if action_url:
            return redirect(action_url)
        messages.success(request, "Dedicated IP removed.")
    except BillingUpdateError as exc:
        messages.error(request, exc.detail or SUPPORT_DETAIL)
    except Exception:
        logger.exception("Failed to remove dedicated IP", exc_info=True)
        messages.error(request, SUPPORT_DETAIL)

    return redirect(_billing_redirect(owner, owner_type))


@login_required
@require_POST
@with_billing_owner
@tracer.start_as_current_span("BILLING Remove All Dedicated IPs")
def remove_all_dedicated_ip(request, owner, owner_type):
    if not stripe_status().enabled:
        messages.error(request, "Stripe billing is not available in this deployment.")
        return redirect(f'{IMMERSIVE_APP_BASE_PATH}/billing')

    from console.billing_update_service import BillingUpdateError, SUPPORT_DETAIL, apply_dedicated_ip_changes

    try:
        proxy_ids = list(
            DedicatedProxyService.allocated_proxies(owner).values_list("id", flat=True)
        )
        if not proxy_ids:
            messages.info(request, "No dedicated IPs to remove.")
            return redirect(_billing_redirect(owner, owner_type))

        action_url = apply_dedicated_ip_changes(
            owner,
            owner_type,
            add_quantity=0,
            remove_proxy_ids=[str(pid) for pid in proxy_ids],
            unassign_proxy_ids={str(pid) for pid in proxy_ids},
        )
        if action_url:
            return redirect(action_url)
        messages.success(request, "All dedicated IPs removed.")
    except BillingUpdateError as exc:
        messages.error(request, exc.detail or SUPPORT_DETAIL)
    except Exception:
        logger.exception("Failed to remove all dedicated IPs", exc_info=True)
        messages.error(request, SUPPORT_DETAIL)

    return redirect(_billing_redirect(owner, owner_type))


def _billing_redirect(owner, owner_type: str) -> str:
    url = f"{IMMERSIVE_APP_BASE_PATH}/billing"
    if owner_type == "organization" and owner is not None:
        return append_context_query(url, str(owner.id))
    return url


@login_required
@require_POST
@tracer.start_as_current_span("CONSOLE Billing Update (JSON)")
def console_billing_update(request):
    from console.billing_update_service import handle_console_billing_update

    payload, status = handle_console_billing_update(request)
    return JsonResponse(payload, status=status)
