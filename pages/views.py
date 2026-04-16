from datetime import timezone, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit
from types import SimpleNamespace
import uuid

from django.http.response import JsonResponse
from django.views.generic import TemplateView, RedirectView, View
from django.http import HttpResponse, Http404
from django.core import signing
from django.core.mail import send_mail
from django.utils.decorators import method_decorator
from django.views.decorators.vary import vary_on_cookie
from django.shortcuts import redirect, resolve_url
from django.http import HttpResponseRedirect
from .models import LandingPage
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.http import url_has_allowed_host_and_scheme
from django.template.loader import render_to_string
from django.db import DatabaseError
from api.models import MCPServerConfig, PaidPlanIntent, PersistentAgent, PersistentAgentTemplate, UserBilling
from api.agent.short_description import build_listing_description, build_mini_description
from agents.services import PretrainedWorkerTemplateService
from api.models import OrganizationMembership
from api.services.trial_abuse import (
    SIGNAL_SOURCE_CHECKOUT,
    evaluate_user_trial_eligibility,
    user_has_prior_individual_history,
)
from config.socialaccount_adapter import (
    OAUTH_ATTRIBUTION_COOKIE,
    OAUTH_ATTRIBUTION_SESSION_KEYS,
    OAUTH_CHARTER_COOKIE,
    OAUTH_CHARTER_SESSION_KEYS,
    serialize_oauth_charter_cookie_payload,
)
from billing.checkout_metadata import (
    STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY,
    STRIPE_CHECKOUT_FLOW_TYPE_PURCHASE,
    STRIPE_CHECKOUT_FLOW_TYPE_TRIAL,
    build_checkout_customer_metadata,
    build_checkout_flow_metadata,
    clear_checkout_customer_metadata,
)
from billing.checkout_context import record_checkout_context
from billing.plan_resolver import get_active_public_plan_monthly_task_credits
from config.stripe_config import get_stripe_settings

import stripe
from djstripe.models import Customer, Subscription, Price
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.payments_helper import PaymentsHelper
from util.subscription_helper import (
    ensure_single_individual_subscription,
    get_existing_individual_subscriptions,
    get_or_create_stripe_customer,
    reconcile_user_plan_from_stripe,
)
from util.integrations import stripe_status, IntegrationDisabledError
from util.onboarding import (
    TRIAL_ONBOARDING_TARGET_AGENT_UI,
    TRIAL_ONBOARDING_TARGET_API_KEYS,
    clear_trial_onboarding_intent,
    is_truthy_flag,
    normalize_trial_onboarding_target,
    set_trial_onboarding_intent,
)
from util.trial_eligibility import (
    is_trial_decision_allowed,
    is_user_trial_eligibility_enforcement_enabled,
    is_user_trial_eligibility_enforcement_one_per_user_enabled,
)
from util.trial_enforcement import can_user_use_personal_agents_and_api
from constants.plans import PlanNames
from constants.stripe import PERSONAL_CHECKOUT_PAYMENT_METHOD_TYPES
from constants.feature_flags import CTA_SIGNUP_FIRST
from util.urls import (
    IMMERSIVE_APP_BASE_PATH,
    IMMERSIVE_RETURN_TO_SESSION_KEY,
    append_query_params,
    build_immersive_chat_url,
    normalize_return_to,
)
from util.waffle_flags import is_waffle_flag_active
from util.fish_collateral import build_web_manifest_payload, is_fish_collateral_enabled
from api.services.pipedream_apps import (
    PipedreamCatalogError,
    PipedreamCatalogService,
    get_owner_selected_app_slugs,
)
from api.pipedream_app_utils import normalize_app_slugs
from marketing_events.custom_events import ConfiguredCustomEvent, emit_configured_custom_capi_event
from middleware.utm_capture import UTMTrackingMiddleware
from pages.mini_mode import set_mini_mode_cookie
from .utils_markdown import (
    load_page,
    get_prev_next,
    get_all_doc_pages,
)
from .homepage_cache import (
    get_homepage_integrations_payload,
    get_homepage_pretrained_payload,
)
from .examples_data import SIMPLE_EXAMPLES, RICH_EXAMPLES
from .forms import MarketingContactForm
from console.agent_creation import AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY
from console.views import build_llm_intelligence_props
from api.agent.core.llm_config import resolve_preferred_tier_for_owner, get_llm_tier_label
from django.contrib import sitemaps
from django.urls import NoReverseMatch, reverse
from django.utils import timezone as dj_timezone
from django.utils.html import escape, strip_tags
from opentelemetry import trace
from marketing_events.api import capi
from marketing_events.telemetry import record_fbc_synthesized
from marketing_events.value_utils import (
    calculate_start_trial_values,
    resolve_start_trial_conversion_rate,
)
import logging
logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

SIGNUP_TRACKING_SESSION_KEYS = (
    'signup_event_id',
    'signup_user_id',
    'signup_email_hash',
    'signup_auth_method',
    'signup_auth_provider',
)
PREFERRED_LLM_TIER_SESSION_KEY = "agent_preferred_llm_tier"
HOMEPAGE_INLINE_INTEGRATION_SLUGS = (
    "linkedin",
    "google_sheets",
    "trello",
    "slack",
)
_LANDING_UTM_TRACKER = UTMTrackingMiddleware(lambda request: None)

def _get_price_info_from_item(item: dict) -> tuple[str | None, str]:
    """
    Extract price ID and usage type (lowercased) from a subscription item.

    Supports Stripe objects, dicts, or string price IDs.
    """
    price_data = item.get("price")
    price_id = None
    usage_type = ""

    if isinstance(price_data, dict):
        price_id = price_data.get("id")
        usage_type = price_data.get("usage_type") or (price_data.get("recurring") or {}).get("usage_type") or ""
    elif isinstance(price_data, str):
        price_id = price_data

    return price_id, usage_type.lower()


def _subscription_contains_price(sub: dict, target_price_id: str) -> bool:
    """Return True when a subscription dict includes the target licensed price."""
    items = (sub.get("items") or {}).get("data") or []
    for item in items:
        price_id, usage_type = _get_price_info_from_item(item)

        # Only treat licensed/base items as a match; metered add-ons share the product.
        if price_id == target_price_id and usage_type != "metered":
            return True
    return False


def _subscription_contains_meter_price(sub: dict, target_price_id: str) -> bool:
    """Return True when a subscription dict includes the target metered price."""
    items = (sub.get("items") or {}).get("data") or []
    for item in items:
        price_id, usage_type = _get_price_info_from_item(item)
        if price_id == target_price_id and usage_type == "metered":
            return True
    return False


def _normalize_trial_days(value: int | str | None) -> int:
    if value is None:
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _apply_trial_checkout_fields(
    checkout_kwargs: dict,
    *,
    include_trial: bool,
    trial_days: int,
) -> None:
    if not include_trial or trial_days <= 0:
        return

    checkout_kwargs["custom_text"] = {
        "after_submit": {
            "message": "Prepaid cards are not eligible for a free trial. Subscriptions are automatically charged at the end of the trial period if not canceled."
        }
    }


def _customer_has_price_subscription(customer_id: str, target_price_id: str) -> bool:
    """Check if the customer already has an active individual subscription for the price."""
    return _customer_has_price_subscription_with_cache(customer_id, target_price_id)[0]


def _customer_has_price_subscription_with_cache(customer_id: str, target_price_id: str):
    """Return (has_price, subscriptions) with cached subscription list."""
    try:
        existing = get_existing_individual_subscriptions(customer_id)
    except Exception:
        logger.warning("Failed to load existing subscriptions for %s", customer_id, exc_info=True)
        return False, []

    return any(_subscription_contains_price(sub, target_price_id) for sub in existing), existing


def _collect_dedicated_ip_line_items(existing_subs: list[dict], stripe_settings) -> list[dict]:
    """
    Preserve dedicated IP quantities when creating a new subscription via Checkout.
    Returns a list of {"price": price_id, "quantity": qty} for any matching items.
    """
    dedicated_price_ids = {
        pid for pid in (
            getattr(stripe_settings, "startup_dedicated_ip_price_id", None),
            getattr(stripe_settings, "scale_dedicated_ip_price_id", None),
        ) if pid
    }
    if not dedicated_price_ids:
        return []

    collected: dict[str, int] = {}
    for sub in existing_subs or []:
        items = (sub.get("items") or {}).get("data") or []
        for item in items:
            price_id, _ = _get_price_info_from_item(item)
            if price_id and price_id in dedicated_price_ids:
                qty = item.get("quantity") or 0
                if qty > 0:
                    collected[price_id] = collected.get(price_id, 0) + qty

    return [{"price": pid, "quantity": qty} for pid, qty in collected.items()]


def _is_additional_tasks_auto_purchase_enabled(user) -> bool:
    """Return whether the user has additional-task auto-purchase enabled."""
    max_extra_tasks = (
        UserBilling.objects.filter(user=user)
        .values_list("max_extra_tasks", flat=True)
        .first()
    )
    return bool(max_extra_tasks and int(max_extra_tasks) != 0)


def _additional_tasks_price_id_for_plan(stripe_settings, plan_target: str) -> str:
    if plan_target == "startup":
        return getattr(stripe_settings, "startup_additional_task_price_id", "") or ""
    if plan_target == "scale":
        return getattr(stripe_settings, "scale_additional_task_price_id", "") or ""
    return ""



def _auth_url_with_utms(base_url: str, request) -> str:
    """Append stored UTM query params to an auth URL when available."""
    utm_qs = request.session.get("utm_querystring") or ""
    params = dict(parse_qsl(str(utm_qs).lstrip("?")))
    return append_query_params(base_url, params)


def _cta_auth_url_with_utms(request) -> str:
    """Resolve the auth destination for anonymous CTA flows."""
    if is_waffle_flag_active(CTA_SIGNUP_FIRST, request, default=False):
        return _auth_url_with_utms(reverse("account_signup"), request)
    return _auth_url_with_utms(resolve_url(settings.LOGIN_URL), request)


def _track_web_event_for_request(
    request,
    *,
    event: AnalyticsEvent,
    properties: dict | None = None,
    source: AnalyticsSource = AnalyticsSource.WEB,
) -> None:
    """Track a web analytics event for auth users or anonymous sessions."""
    payload = properties or {}
    if request.user.is_authenticated:
        Analytics.track_event(
            user_id=request.user.id,
            event=event,
            source=source,
            properties=payload,
        )
        return

    session_key = request.session.session_key
    if not session_key:
        request.session.save()
        session_key = request.session.session_key
    Analytics.track_event_anonymous(
        anonymous_id=str(session_key),
        event=event,
        source=source,
        properties=payload,
    )


def _build_oauth_charter_cookie_payload(
    request,
    *,
    charter: str,
    charter_source: str,
    template_code: str | None = None,
    charter_override: str | None = None,
) -> dict[str, str | bool | list[str]]:
    payload: dict[str, str | bool | list[str]] = {
        "agent_charter": charter,
        "agent_charter_source": charter_source,
    }
    if template_code:
        payload[PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY] = template_code
    if charter_override:
        payload["agent_charter_override"] = charter_override
    for key in OAUTH_CHARTER_SESSION_KEYS:
        if key in payload:
            continue
        if key in request.session:
            payload[key] = request.session.get(key)
    return payload


def _build_oauth_attribution_cookie_payload(request) -> dict[str, str | dict]:
    payload: dict[str, str | dict] = {}
    for key in OAUTH_ATTRIBUTION_SESSION_KEYS:
        if key in request.session:
            payload[key] = request.session.get(key)
    return payload


def _set_oauth_stash_cookies(
    response,
    request,
    *,
    charter_data: dict,
    attribution_data: dict,
    server_side_charter: bool = False,
) -> None:
    cookie_common = {
        "max_age": 7200,  # 2 hours
        "httponly": True,
        "samesite": "Lax",
        "secure": request.is_secure(),
    }
    charter_cookie_value = serialize_oauth_charter_cookie_payload(
        charter_data,
        server_side=server_side_charter,
    )
    if charter_cookie_value:
        response.set_cookie(
            OAUTH_CHARTER_COOKIE,
            charter_cookie_value,
            **cookie_common,
        )
    else:
        response.delete_cookie(OAUTH_CHARTER_COOKIE)
    if attribution_data:
        response.set_cookie(
            OAUTH_ATTRIBUTION_COOKIE,
            signing.dumps(attribution_data, compress=True),
            **cookie_common,
        )
    else:
        response.delete_cookie(OAUTH_ATTRIBUTION_COOKIE)


def _get_active_landing_page_or_404(code: str) -> LandingPage:
    try:
        return LandingPage.objects.get(code=code, disabled=False)
    except LandingPage.DoesNotExist as exc:
        raise Http404("Landing page not found") from exc


def _build_landing_redirect_params(request, landing: LandingPage, code: str):
    params = request.GET.copy()
    params["g"] = code

    utm_fields = (
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
    )
    for field in utm_fields:
        value = getattr(landing, field, "")
        if value and not params.get(field):
            params[field] = value

    return params


def _persist_landing_attribution(request, code: str) -> None:
    try:
        request.session.setdefault("landing_code_first", code)
        request.session["landing_code_last"] = code
        request.session.setdefault("landing_first_seen_at", dj_timezone.now().isoformat())
        request.session["landing_last_seen_at"] = dj_timezone.now().isoformat()
        request.session.modified = True
    except Exception:
        logger.exception("Failed to persist landing attribution in session for code %s", code)


def _persist_landing_tracking_params(request, params) -> bool:
    try:
        return _LANDING_UTM_TRACKER.capture_params(request, params)
    except Exception:
        logger.exception("Failed to persist landing tracking params for launch request")
        return False


def _apply_landing_attribution_cookies(response, request, code: str, *, fbc_source: str) -> None:
    try:
        cookie_max_age = 60 * 24 * 60 * 60  # 60 days
        response.set_cookie(
            "landing_code",
            code,
            max_age=cookie_max_age,
            samesite="Lax",
        )
        if "__landing_first" not in request.COOKIES:
            response.set_cookie(
                "__landing_first",
                code,
                max_age=cookie_max_age,
                samesite="Lax",
            )
    except Exception:
        logger.exception("Failed to persist landing attribution cookies for code %s", code)

    try:
        fbclid = (request.GET.get("fbclid") or "").strip()
        if fbclid:
            existing_fbc = request.COOKIES.get("_fbc") or ""
            existing_fbclid = existing_fbc.rsplit(".", 1)[-1] if existing_fbc.startswith("fb.1.") else ""
            if existing_fbclid != fbclid:
                fbc = f"fb.1.{int(datetime.now(timezone.utc).timestamp() * 1000)}.{fbclid}"
                response.set_cookie("_fbc", fbc, max_age=60 * 60 * 24 * 90)
                record_fbc_synthesized(source=fbc_source)
            response.set_cookie("fbclid", fbclid, max_age=60 * 60 * 24 * 90)
    except Exception as exc:
        logger.error("Error setting fbclid cookie: %s", exc)


def _seed_landing_launch_session(request, landing: LandingPage) -> None:
    clear_trial_onboarding_intent(request)
    request.session["agent_charter"] = landing.charter
    request.session["agent_charter_source"] = "landing"
    request.session.pop("agent_charter_override", None)
    request.session.pop(PREFERRED_LLM_TIER_SESSION_KEY, None)
    request.session.pop(AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY, None)
    request.session.pop(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY, None)
    request.session.modified = True


POST_CHECKOUT_REDIRECT_SESSION_KEY = "post_checkout_redirect"


def _is_individual_trial_eligible(user, *, request=None, capture_source: str | None = None) -> bool:
    if not user or not getattr(user, "pk", None):
        return True
    if is_user_trial_eligibility_enforcement_one_per_user_enabled(request):
        # One-per-user mode intentionally ignores broader cross-account signals.
        return not user_has_prior_individual_history(user)
    if not is_user_trial_eligibility_enforcement_enabled(request):
        return True
    result = evaluate_user_trial_eligibility(
        user,
        request=request,
        capture_source=capture_source,
        assessment_source=capture_source,
    )
    return is_trial_decision_allowed(result.decision, request=request)


def _pop_post_checkout_redirect(request) -> str | None:
    raw_value = (request.session.pop(POST_CHECKOUT_REDIRECT_SESSION_KEY, "") or "").strip()
    if not raw_value:
        return None

    request.session.modified = True
    if url_has_allowed_host_and_scheme(
        raw_value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return raw_value
    return None


def _prepare_stripe_or_404() -> None:
    status = stripe_status()
    if not status.enabled:
        raise Http404("Stripe billing is not available.")
    key = PaymentsHelper.get_stripe_key()
    if not key:
        raise Http404("Stripe billing is not configured.")
    stripe.api_key = key


def _build_checkout_success_url(request, *, event_id: str, price: float, plan: str) -> tuple[str, bool]:
    conversion_rate = resolve_start_trial_conversion_rate(
        plan,
        default_rate=settings.CAPI_START_TRIAL_CONV_RATE,
        scale_rate=settings.CAPI_START_TRIAL_SCALE_CONV_RATE,
    )
    _predicted_ltv, conversion_value = calculate_start_trial_values(
        price,
        ltv_multiple=settings.CAPI_LTV_MULTIPLE,
        conversion_rate=conversion_rate,
    )
    success_params = {
        "subscribe_success": 1,
        "p": f"{(conversion_value or 0.0):.2f}",
        "eid": event_id,
        "plan": plan,
    }
    redirect_path = _pop_post_checkout_redirect(request)
    if redirect_path:
        # Append tracking params to custom redirect path, preserving any fragment
        path_part, frag_sep, fragment = redirect_path.partition('#')
        separator = '&' if '?' in path_part else '?'
        redirect_with_params = f"{path_part}{separator}{urlencode(success_params)}{frag_sep}{fragment}"
        return request.build_absolute_uri(redirect_with_params), True
    default_url = f'{request.build_absolute_uri(reverse("billing"))}?{urlencode(success_params)}'
    return default_url, False


def _emit_checkout_initiated_event(
    request,
    user,
    *,
    plan_code: str,
    plan_label: str,
    value: float | None,
    currency: str | None,
    event_id: str,
    event_name: str = "InitiateCheckout",
    post_checkout_redirect_used: bool | None = None,
) -> None:
    """
    Fan out checkout events to CAPI providers with plan metadata.
    TikTok maps InitiateCheckout -> ClickButton downstream.
    """
    properties = {
        "plan": plan_code,
        "plan_label": plan_label,
        "event_id": event_id,
    }
    if value is not None:
        properties["value"] = value
    if post_checkout_redirect_used is not None:
        properties["post_checkout_redirect_used"] = post_checkout_redirect_used
    if currency:
        properties["currency"] = currency.upper()
    else:
        properties["currency"] = "USD"

    try:
        capi(
            user=user,
            event_name=event_name,
            properties=properties,
            request=request,
        )
    except Exception:
        logger.exception("Failed to emit %s marketing event for %s", event_name, plan_code)


def _set_customer_checkout_context(
    *,
    customer_id: str,
    flow_type: str,
    event_id: str,
    plan: str,
    plan_label: str,
    value: float | None,
    currency: str | None,
    checkout_source_url: str | None,
) -> None:
    """Mark the customer with the active checkout context for Radar evaluation."""
    stripe.Customer.modify(
        customer_id,
        metadata=build_checkout_customer_metadata(
            flow_type=flow_type,
            event_id=event_id,
            plan=plan,
            plan_label=plan_label,
            value=value,
            currency=(currency or "USD").upper(),
            checkout_source_url=checkout_source_url,
        ),
        api_key=stripe.api_key,
    )


def _clear_customer_checkout_context_if_matches(*, customer_id: str, expected_event_id: str) -> bool:
    """Clear transient checkout context when the same checkout is still active."""
    customer_payload = stripe.Customer.retrieve(customer_id, api_key=stripe.api_key)
    customer_metadata = getattr(customer_payload, "metadata", None)
    if customer_metadata is None and hasattr(customer_payload, "get"):
        customer_metadata = customer_payload.get("metadata")

    current_event_id = ""
    if hasattr(customer_metadata, "get"):
        current_event_id = str(
            customer_metadata.get(STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY) or ""
        ).strip()
    if current_event_id != expected_event_id:
        return False

    stripe.Customer.modify(
        customer_id,
        metadata=clear_checkout_customer_metadata(),
        api_key=stripe.api_key,
    )
    return True


def _create_checkout_session_with_customer_context(
    *,
    customer_id: str,
    flow_type: str,
    event_id: str,
    plan: str,
    plan_label: str,
    value: float | None,
    currency: str | None,
    checkout_source_url: str | None,
    checkout_kwargs: dict,
):
    """
    Set the customer context before Checkout so Radar can inspect it.

    The metadata write is best-effort, and a failed Checkout creation clears the
    transient marker when the same checkout is still the active one.
    """
    customer_checkout_context_set = False
    try:
        _set_customer_checkout_context(
            customer_id=customer_id,
            flow_type=flow_type,
            event_id=event_id,
            plan=plan,
            plan_label=plan_label,
            value=value,
            currency=currency,
            checkout_source_url=checkout_source_url,
        )
        customer_checkout_context_set = True
    except stripe.error.StripeError as exc:
        logger.warning(
            "Failed to set checkout customer context for %s before Checkout creation: %s",
            customer_id,
            exc,
        )

    try:
        session = stripe.checkout.Session.create(**checkout_kwargs)
    except stripe.error.StripeError as exc:
        if customer_checkout_context_set:
            try:
                _clear_customer_checkout_context_if_matches(
                    customer_id=customer_id,
                    expected_event_id=event_id,
                )
            except stripe.error.StripeError as cleanup_exc:
                logger.warning(
                    "Failed to clear checkout customer context for %s after Checkout creation failed: %s",
                    customer_id,
                    cleanup_exc,
                )
        raise

    session_id = getattr(session, "id", None)
    if isinstance(session_id, str) and session_id.strip():
        try:
            record_checkout_context(
                customer_id=customer_id,
                checkout_session_id=session_id.strip(),
                session_created_at=getattr(session, "created", None),
                flow_type=flow_type,
                event_id=event_id,
                plan=plan,
                plan_label=plan_label,
                value=value,
                currency=(currency or "USD").upper(),
                checkout_source_url=checkout_source_url,
            )
        except DatabaseError:
            logger.warning(
                "Failed to persist checkout context for session %s",
                session_id,
                exc_info=True,
            )

    return session


class HomePage(TemplateView):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add agent charter form for the home page spawn functionality
        from console.forms import PersistentAgentCharterForm

        initial = {}
        resolved = None

        # If 'spawn=1' parameter is present, clear any stored charter to start fresh
        if self.request.GET.get('spawn') == '1':
            if 'agent_charter' in self.request.session:
                del self.request.session['agent_charter']
            if 'agent_charter_source' in self.request.session:
                del self.request.session['agent_charter_source']
            if PREFERRED_LLM_TIER_SESSION_KEY in self.request.session:
                del self.request.session[PREFERRED_LLM_TIER_SESSION_KEY]
            initial['charter'] = ''
        # If the GET parameter 'dc' (default charter) is present, use it in the initial data
        elif 'dc' in self.request.GET:
            initial['charter'] = self.request.GET['dc'].strip()
            context['default_charter'] = initial['charter']
        elif 'g' in self.request.GET:
            # If 'g' is present, it indicates a landing page code
            try:
                landing = LandingPage.objects.get(code=self.request.GET['g'], disabled=False)
                initial['charter'] = landing.charter.strip()
                context['default_charter'] = initial['charter']

                hero_text = landing.hero_text.strip()

                # Replace {blue} and {/blue} tags with HTML span elements
                hero_text = escape(hero_text)  # Escape HTML to prevent XSS
                hero_text = hero_text.replace(
                    "{blue}",
                    '<span class="bg-gradient-to-r from-violet-700 to-purple-600 bg-clip-text text-transparent">'
                ).replace(
                    "{/blue}",
                    '</span>'
                )

                context['landing_hero_text'] = hero_text

                context['landing_preview_image'] = landing.image_url.strip() if landing.image_url else None
                context['landing_title'] = landing.title.strip() if landing.title else None
                context['landing_code'] = landing.code.strip() if landing.code else None

            except LandingPage.DoesNotExist:
                # If no valid landing page found, use an empty charter
                initial['charter'] = ''
                context['default_charter'] = ''
        elif 'agent_charter' in self.request.session:
            if self.request.session.get('agent_charter_source') != 'template':
                initial['charter'] = self.request.session['agent_charter'].strip()
                context['default_charter'] = initial['charter']
                context['agent_charter_saved'] = True

        context['agent_charter_form'] = PersistentAgentCharterForm(
            initial=initial
        )

        if not settings.VITE_USE_DEV_SERVER:
            try:
                from config.vite import ViteManifestError, get_vite_asset
                context["immersive_app_assets"] = get_vite_asset("src/main.tsx")
            except ViteManifestError:
                context["immersive_app_assets"] = None

        if self.request.user.is_authenticated:
            from console.context_helpers import build_console_context

            resolved = build_console_context(self.request)
            context['current_context'] = {
                'type': resolved.current_context.type,
                'id': resolved.current_context.id,
                'name': resolved.current_context.name,
            }
            context['can_manage_org_agents'] = resolved.can_manage_org_agents
            if resolved.current_membership is not None:
                context['current_membership'] = resolved.current_membership

            context['user_organizations'] = (
                OrganizationMembership.objects.filter(
                    user=self.request.user,
                    status=OrganizationMembership.OrgStatus.ACTIVE,
                )
                .select_related('org')
                .order_by('org__name')
            )

        intelligence_upgrade_url = None
        if settings.GOBII_PROPRIETARY_MODE:
            try:
                intelligence_upgrade_url = reverse('proprietary:pricing')
            except NoReverseMatch:
                try:
                    intelligence_upgrade_url = reverse('proprietary:startup_checkout')
                except NoReverseMatch:
                    intelligence_upgrade_url = None

        owner = None
        owner_type = 'user'
        organization = None
        if self.request.user.is_authenticated:
            owner = self.request.user
            if resolved and resolved.current_context.type == 'organization' and resolved.current_membership is not None:
                organization = resolved.current_membership.org
                owner = organization
                owner_type = 'organization'

        home_spawn_requires_trial = False
        if self.request.user.is_authenticated:
            in_organization_context = bool(
                resolved
                and resolved.current_context.type == 'organization'
                and resolved.current_membership is not None
            )
            home_spawn_requires_trial = (
                not in_organization_context
                and not can_user_use_personal_agents_and_api(self.request.user)
            )
        context["home_spawn_requires_trial"] = home_spawn_requires_trial

        preferred_llm_tier_raw = self.request.session.get(PREFERRED_LLM_TIER_SESSION_KEY)
        # Never plan-clamp in the homepage selector. Clamping happens when the agent is
        # persisted and at runtime.
        preferred_llm_tier = resolve_preferred_tier_for_owner(None, preferred_llm_tier_raw).value
        # Do not write back the clamped tier into the session.
        # We want to preserve the user's requested tier so it can take effect automatically
        # after a plan upgrade (e.g., returning from Stripe before webhooks settle).
        context['preferred_llm_tier'] = preferred_llm_tier
        context['preferred_llm_tier_label'] = get_llm_tier_label(preferred_llm_tier)

        context['llm_intelligence'] = build_llm_intelligence_props(
            owner,
            owner_type,
            organization,
            intelligence_upgrade_url,
        )
        try:
            billing_url = reverse('billing')
            if organization is not None:
                billing_url = f"{billing_url}?org_id={organization.id}"
        except NoReverseMatch:
            billing_url = ""
        context['billing_url'] = billing_url

        # Examples data
        context["simple_examples"] = SIMPLE_EXAMPLES
        context["rich_examples"] = RICH_EXAMPLES

        integrations_payload = get_homepage_integrations_payload()
        builtin_integrations = list(integrations_payload.get("builtins") or [])
        builtin_by_slug = {
            str(app.get("slug") or "").strip(): app
            for app in builtin_integrations
            if str(app.get("slug") or "").strip()
        }
        inline_builtin_integrations = [
            builtin_by_slug[slug]
            for slug in HOMEPAGE_INLINE_INTEGRATION_SLUGS
            if slug in builtin_by_slug
        ]
        integrations_enabled = bool(integrations_payload.get("enabled"))

        initial_selected_pipedream_app_slugs = normalize_app_slugs(
            self.request.session.get(AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY) or []
        )
        if integrations_enabled and self.request.user.is_authenticated:
            owner_scope = (
                MCPServerConfig.Scope.ORGANIZATION
                if organization is not None
                else MCPServerConfig.Scope.USER
            )
            enabled_pipedream_app_slugs = get_owner_selected_app_slugs(
                owner_scope,
                owner_user=None if organization is not None else self.request.user,
                owner_org=organization,
            )
            initial_selected_pipedream_app_slugs = normalize_app_slugs(
                [*enabled_pipedream_app_slugs, *initial_selected_pipedream_app_slugs]
            )

        context.update(
            {
                "homepage_integrations_enabled": integrations_enabled,
                "homepage_integrations_inline_builtins": inline_builtin_integrations,
                "homepage_integrations_modal_props": {
                    "builtins": builtin_integrations,
                    "initialSearchTerm": (self.request.GET.get("integration_search") or "").strip(),
                    "initialSelectedAppSlugs": initial_selected_pipedream_app_slugs,
                    "searchUrl": reverse("pages:homepage_integrations_search"),
                    "selectedFieldsContainerId": "homepage-integrations-selected-fields",
                },
            }
        )

        payload = get_homepage_pretrained_payload()
        all_templates = list(payload.get("templates") or [])

        category_filter = (self.request.GET.get("pretrained_category") or "").strip()
        search_term = (self.request.GET.get("pretrained_search") or "").strip()

        filtered_templates = list(all_templates)
        if category_filter:
            category_lower = category_filter.lower()
            filtered_templates = [
                template
                for template in filtered_templates
                if (template.get("category") or "").lower() == category_lower
            ]

        if search_term:
            search_lower = search_term.lower()
            filtered_templates = [
                template
                for template in filtered_templates
                if search_lower in (template.get("display_name") or "").lower()
                or search_lower in (template.get("tagline") or "").lower()
                or search_lower in (template.get("description") or "").lower()
            ]

        filtered_workers = [SimpleNamespace(**template) for template in filtered_templates]
        context.update(
            {
                "homepage_pretrained_workers": filtered_workers,
                "homepage_pretrained_total": payload.get("total", len(all_templates)),
                "homepage_pretrained_filtered_count": len(filtered_workers),
                "homepage_pretrained_categories": payload.get("categories") or [],
                "homepage_pretrained_selected_category": category_filter,
                "homepage_pretrained_search_term": search_term,
            }
        )

        if self.request.user.is_authenticated:
            recent_agents_qs = PersistentAgent.objects.non_eval().alive().filter(user_id=self.request.user.id)
            total_agents = recent_agents_qs.count()
            recent_agents = list(recent_agents_qs.order_by('-updated_at')[:3])

            for agent in recent_agents:
                schedule_text = None
                if agent.schedule:
                    schedule_text = PretrainedWorkerTemplateService.describe_schedule(agent.schedule)
                    if not schedule_text:
                        schedule_text = agent.schedule
                agent.display_schedule = schedule_text

                description, source = build_listing_description(agent, max_length=140)
                agent.listing_description = description
                agent.listing_description_source = source
                agent.is_initializing = source == "placeholder"

                mini_description, mini_source = build_mini_description(agent)
                agent.mini_description = mini_description
                agent.mini_description_source = mini_source

                if getattr(agent, "life_state", "active") == PersistentAgent.LifeState.EXPIRED:
                    agent.status_label = "Expired"
                    agent.status_class = "text-slate-500 bg-slate-100"
                else:
                    agent.status_label = "Active"
                    agent.status_class = "text-emerald-600 bg-emerald-50"
                agent.chat_url = build_immersive_chat_url(
                    self.request,
                    agent.id,
                    return_to=self.request.get_full_path(),
                )

            context['recent_agents'] = recent_agents

            fallback_total = total_agents
            if fallback_total == 0:
                account = context.get('account')
                usage = getattr(account, 'usage', None)
                fallback_total = getattr(usage, 'agents_in_use', 0) if usage else 0

            context['recent_agents_remaining'] = max(fallback_total - len(recent_agents), 0)
            context['recent_agents_total'] = fallback_total

        return context


class HomeAgentSpawnView(TemplateView):
    """Handle agent charter submission from the home page."""
    template_name = "home.html"

    def post(self, request, *args, **kwargs):
        from console.forms import PersistentAgentCharterForm
        from django.contrib.auth.views import redirect_to_login
        
        form = PersistentAgentCharterForm(request.POST)
        selected_pipedream_app_slugs = normalize_app_slugs(
            request.POST.getlist("selected_pipedream_app_slugs")
        )
        if selected_pipedream_app_slugs:
            request.session[AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY] = (
                selected_pipedream_app_slugs
            )
        else:
            request.session.pop(AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY, None)
        trial_onboarding_requested = is_truthy_flag(request.POST.get("trial_onboarding"))
        trial_onboarding_target = normalize_trial_onboarding_target(
            request.POST.get("trial_onboarding_target"),
            default=TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )
        
        if form.is_valid():
            return_to = normalize_return_to(request, request.POST.get("return_to"))
            if not return_to:
                return_to = normalize_return_to(request, request.META.get("HTTP_REFERER"))
            embed = (request.POST.get("embed") or "").lower() in {"1", "true", "yes", "on"}
            if return_to:
                request.session[IMMERSIVE_RETURN_TO_SESSION_KEY] = return_to

            # Clear any previously selected pretrained worker so we treat this as a fresh custom charter
            request.session.pop(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY, None)
            # Store charter in session for later use
            user_charter = form.cleaned_data['charter']
            if user_charter:
                request.session['agent_charter'] = user_charter
            else:
                # Empty input — use a general-purpose charter and a simple greeting
                request.session['agent_charter'] = "Hello"
                request.session['agent_charter_override'] = PersistentAgentCharterForm.DEFAULT_CHARTER
            request.session['agent_charter_source'] = 'user'
            preferred_llm_tier_raw = (request.POST.get("preferred_llm_tier") or "").strip()
            if preferred_llm_tier_raw:
                # Never plan-clamp session preference here; clamping happens at persistence/runtime.
                preferred_llm_tier = resolve_preferred_tier_for_owner(None, preferred_llm_tier_raw).value
                request.session[PREFERRED_LLM_TIER_SESSION_KEY] = preferred_llm_tier
                request.session.modified = True

            # Track analytics for home page agent creation start (only for authenticated users)
            if request.user.is_authenticated:
                Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.PERSISTENT_AGENT_CHARTER_SUBMIT,
                    source=AnalyticsSource.WEB,
                    properties={
                        'charter': request.session['agent_charter'],
                        'source_page': 'home',
                    }
                )
            
            next_url = reverse('agent_quick_spawn')
            redirect_params = {}
            if return_to:
                redirect_params["return_to"] = return_to
            if embed:
                redirect_params["embed"] = "1"
            if redirect_params:
                next_url = f"{next_url}?{urlencode(redirect_params)}"

            if request.user.is_authenticated:
                # User is already logged in, go directly to agent creation
                return redirect(next_url)
            _track_web_event_for_request(
                request,
                event=AnalyticsEvent.PERSISTENT_AGENT_CHARTER_SUBMIT,
                properties={
                    "source_page": "home",
                },
            )
            if trial_onboarding_requested:
                set_trial_onboarding_intent(
                    request,
                    target=trial_onboarding_target,
                )
            # User needs to log in first, then continue to agent creation in the app
            app_redirect_params = {**redirect_params, "spawn": "1"}
            app_next_url = append_query_params(
                f"{IMMERSIVE_APP_BASE_PATH}/agents/new",
                app_redirect_params,
            )
            response = redirect_to_login(
                next=app_next_url,
                login_url=_cta_auth_url_with_utms(request),
            )
            charter_data = _build_oauth_charter_cookie_payload(
                request,
                charter=request.session.get("agent_charter") or "",
                charter_source=str(request.session.get("agent_charter_source") or "user"),
                charter_override=request.session.get("agent_charter_override"),
            )
            attribution_data = _build_oauth_attribution_cookie_payload(request)
            _set_oauth_stash_cookies(
                response,
                request,
                charter_data=charter_data,
                attribution_data=attribution_data,
                server_side_charter=True,
            )
            return response
        
        # If form is invalid, re-render home page with errors
        context = self.get_context_data(**kwargs)
        context['agent_charter_form'] = form
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        # Reuse the same context as HomePage
        homepage_view = HomePage()
        homepage_view.request = self.request
        return homepage_view.get_context_data(**kwargs)


class HomepageIntegrationsSearchView(View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        query = str(request.GET.get("q") or "").strip()
        if not query:
            return JsonResponse({"results": []})

        integrations_payload = get_homepage_integrations_payload()
        if not integrations_payload.get("enabled"):
            return JsonResponse({"results": []})

        builtin_slugs = {
            str(app.get("slug") or "").strip()
            for app in (integrations_payload.get("builtins") or [])
            if str(app.get("slug") or "").strip()
        }
        try:
            results = [
                app.to_dict()
                for app in PipedreamCatalogService().search_apps(query)
                if app.slug not in builtin_slugs
            ]
        except PipedreamCatalogError as exc:
            return JsonResponse({"error": str(exc)}, status=502)
        return JsonResponse({"results": results})


class PretrainedWorkerDirectoryRedirectView(RedirectView):
    permanent = False

    def get_redirect_url(self, *args, **kwargs):
        base_url = reverse('pages:home')
        params: list[tuple[str, str]] = []

        search = (self.request.GET.get('q') or '').strip()
        category = (self.request.GET.get('category') or '').strip()

        if search:
            params.append(('pretrained_search', search))
        if category:
            params.append(('pretrained_category', category))

        for key in self.request.GET.keys():
            if key in {'q', 'category'}:
                continue
            for value in self.request.GET.getlist(key):
                params.append((key, value))

        query_string = urlencode(params, doseq=True)
        fragment = '#pretrained-workers'

        if query_string:
            return f"{base_url}?{query_string}{fragment}"
        return f"{base_url}{fragment}"


class PretrainedWorkerDetailView(TemplateView):
    template_name = "pretrained_worker_directory/detail.html"

    def dispatch(self, request, *args, **kwargs):
        self.employee = PretrainedWorkerTemplateService.get_template_by_code(kwargs.get('slug'))
        if not self.employee:
            raise Http404("This pretrained worker is no longer available.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pretrained_worker"] = self.employee
        context["schedule_jitter_minutes"] = self.employee.schedule_jitter_minutes
        context["base_schedule"] = self.employee.base_schedule
        context["schedule_description"] = PretrainedWorkerTemplateService.describe_schedule(self.employee.base_schedule)
        display_map = PretrainedWorkerTemplateService.get_tool_display_map(self.employee.default_tools or [])
        context["event_triggers"] = self.employee.event_triggers or []
        context["default_tools"] = PretrainedWorkerTemplateService.get_tool_display_list(
            self.employee.default_tools or [],
            display_map=display_map,
        )
        context["contact_method_label"] = PretrainedWorkerTemplateService.describe_contact_channel(
            self.employee.recommended_contact_channel
        )
        return context


class PretrainedWorkerHireView(View):
    def post(self, request, *args, **kwargs):
        code = kwargs.get('slug')
        template = PretrainedWorkerTemplateService.get_template_by_code(code)
        if not template:
            raise Http404("This pretrained worker is no longer available.")

        request.session['agent_charter'] = template.charter
        request.session[PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY] = template.code
        request.session['agent_charter_source'] = 'template'
        request.session.modified = True

        source_page = request.POST.get('source_page') or 'home_pretrained_workers'
        flow = (request.POST.get("flow") or "").strip().lower()
        trial_onboarding_requested = is_truthy_flag(request.POST.get("trial_onboarding"))
        trial_onboarding_target = normalize_trial_onboarding_target(
            request.POST.get("trial_onboarding_target"),
            default=TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )
        analytics_properties = {
            "source_page": source_page,
            "template_code": template.code,
        }
        if flow:
            analytics_properties["flow"] = flow

        if request.user.is_authenticated:
            Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.PERSISTENT_AGENT_CHARTER_SUBMIT,
                source=AnalyticsSource.WEB,
                properties=analytics_properties,
            )
            return redirect('agent_quick_spawn')

        next_url = reverse('agent_quick_spawn')
        if flow == "pro":
            request.session[POST_CHECKOUT_REDIRECT_SESSION_KEY] = next_url
            request.session.modified = True
            next_url = reverse('proprietary:pro_checkout')

        # Track anonymous interest
        session_key = request.session.session_key
        if not session_key:
            request.session.save()
            session_key = request.session.session_key
        Analytics.track_event_anonymous(
            anonymous_id=str(session_key),
            event=AnalyticsEvent.PERSISTENT_AGENT_CHARTER_SUBMIT,
            source=AnalyticsSource.WEB,
            properties=analytics_properties,
        )

        from django.contrib.auth.views import redirect_to_login

        app_next_url = next_url
        if flow != "pro":
            if trial_onboarding_requested:
                set_trial_onboarding_intent(
                    request,
                    target=trial_onboarding_target,
                )
            return_to = normalize_return_to(request, request.META.get("HTTP_REFERER"))
            app_params = {"spawn": "1"}
            if return_to:
                app_params["return_to"] = return_to
            app_next_url = append_query_params(
                f"{IMMERSIVE_APP_BASE_PATH}/agents/new",
                app_params,
            )

        response = redirect_to_login(
            next=app_next_url,
            login_url=_cta_auth_url_with_utms(request),
        )

        # Also store charter in a signed cookie for OAuth flows where session
        # data might be lost during the redirect chain
        charter_data = _build_oauth_charter_cookie_payload(
            request,
            charter=template.charter,
            charter_source="template",
            template_code=template.code,
        )
        attribution_data = _build_oauth_attribution_cookie_payload(request)
        _set_oauth_stash_cookies(
            response,
            request,
            charter_data=charter_data,
            attribution_data=attribution_data,
        )

        return response


class PublicTemplateDetailView(TemplateView):
    template_name = "public_templates/detail.html"

    def dispatch(self, request, *args, **kwargs):
        handle = kwargs.get("handle")
        template_slug = kwargs.get("template_slug")
        self.template = (
            PersistentAgentTemplate.objects.select_related("public_profile")
            .filter(public_profile__handle=handle, slug=template_slug, is_active=True)
            .first()
        )
        if not self.template:
            raise Http404("This template is no longer available.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["template"] = self.template
        context["public_profile_handle"] = self.template.public_profile.handle
        context["template_url"] = self.request.build_absolute_uri(
            f"/{self.template.public_profile.handle}/{self.template.slug}/"
        )
        context["schedule_jitter_minutes"] = self.template.schedule_jitter_minutes
        context["base_schedule"] = self.template.base_schedule
        context["schedule_description"] = PretrainedWorkerTemplateService.describe_schedule(self.template.base_schedule)
        display_map = PretrainedWorkerTemplateService.get_tool_display_map(self.template.default_tools or [])
        context["event_triggers"] = self.template.event_triggers or []
        context["default_tools"] = PretrainedWorkerTemplateService.get_tool_display_list(
            self.template.default_tools or [],
            display_map=display_map,
        )
        context["contact_method_label"] = PretrainedWorkerTemplateService.describe_contact_channel(
            self.template.recommended_contact_channel
        )
        return context


class PublicTemplateHireView(View):
    def post(self, request, *args, **kwargs):
        handle = kwargs.get("handle")
        template_slug = kwargs.get("template_slug")
        template = (
            PersistentAgentTemplate.objects.select_related("public_profile")
            .filter(public_profile__handle=handle, slug=template_slug, is_active=True)
            .first()
        )
        if not template:
            raise Http404("This template is no longer available.")

        request.session["agent_charter"] = template.charter
        request.session[PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY] = template.code
        request.session["agent_charter_source"] = "template"

        # Track template for referral attribution (if user signs up)
        # "Last one wins": hiring a template clears any direct referral code
        previous_referrer_code = request.session.pop("referrer_code", None)
        request.session["signup_template_code"] = template.code

        request.session.modified = True

        # Track referral template capture for analytics
        if not request.user.is_authenticated:
            # Anonymous user hiring template - potential referral signup
            session_key = request.session.session_key
            if not session_key:
                request.session.save()
                session_key = request.session.session_key
            Analytics.track_event_anonymous(
                anonymous_id=str(session_key),
                event=AnalyticsEvent.REFERRAL_TEMPLATE_CAPTURED,
                source=AnalyticsSource.WEB,
                properties={
                    'template_code': template.code,
                    'template_creator_id': str(template.created_by_id) if template.created_by_id else '',
                    'previous_referrer_code': previous_referrer_code or '',
                },
            )

        source_page = request.POST.get("source_page") or "public_template"
        flow = (request.POST.get("flow") or "").strip().lower()
        trial_onboarding_requested = is_truthy_flag(request.POST.get("trial_onboarding"))
        trial_onboarding_target = normalize_trial_onboarding_target(
            request.POST.get("trial_onboarding_target"),
            default=TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )
        analytics_properties = {
            "source_page": source_page,
            "template_code": template.code,
        }
        if flow:
            analytics_properties["flow"] = flow

        emit_configured_custom_capi_event(
            user=request.user,
            event_name=ConfiguredCustomEvent.TEMPLATE_LAUNCHED,
            plan_owner=request.user if request.user.is_authenticated else None,
            properties={
                "template_id": str(template.id),
                **analytics_properties,
            },
            request=request,
        )

        if request.user.is_authenticated:
            Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.PERSISTENT_AGENT_CHARTER_SUBMIT,
                source=AnalyticsSource.WEB,
                properties=analytics_properties,
            )
            return redirect("agent_quick_spawn")

        next_url = reverse("agent_quick_spawn")
        if flow == "pro":
            request.session[POST_CHECKOUT_REDIRECT_SESSION_KEY] = next_url
            request.session.modified = True
            next_url = reverse("proprietary:pro_checkout")

        session_key = request.session.session_key
        if not session_key:
            request.session.save()
            session_key = request.session.session_key
        Analytics.track_event_anonymous(
            anonymous_id=str(session_key),
            event=AnalyticsEvent.PERSISTENT_AGENT_CHARTER_SUBMIT,
            source=AnalyticsSource.WEB,
            properties=analytics_properties,
        )

        from django.contrib.auth.views import redirect_to_login

        app_next_url = next_url
        if flow != "pro":
            if trial_onboarding_requested:
                set_trial_onboarding_intent(
                    request,
                    target=trial_onboarding_target,
                )
            return_to = normalize_return_to(request, request.META.get("HTTP_REFERER"))
            app_params = {"spawn": "1"}
            if return_to:
                app_params["return_to"] = return_to
            app_next_url = append_query_params(
                f"{IMMERSIVE_APP_BASE_PATH}/agents/new",
                app_params,
            )

        response = redirect_to_login(
            next=app_next_url,
            login_url=_cta_auth_url_with_utms(request),
        )

        charter_data = _build_oauth_charter_cookie_payload(
            request,
            charter=template.charter,
            charter_source="template",
            template_code=template.code,
        )
        attribution_data = _build_oauth_attribution_cookie_payload(request)
        _set_oauth_stash_cookies(
            response,
            request,
            charter_data=charter_data,
            attribution_data=attribution_data,
        )

        return response


class EngineeringProSignupView(View):
    def get(self, request, *args, **kwargs):
        return self._handle(request)

    def post(self, request, *args, **kwargs):
        return self._handle(request)

    def _handle(self, request):
        if request.method == "POST":
            source_page = (
                request.POST.get("source_page")
                or request.GET.get("source_page")
                or "engineering_solution"
            )
            _track_web_event_for_request(
                request,
                event=AnalyticsEvent.PLAN_INTEREST,
                properties={
                    "source_page": source_page,
                    "target": "api_keys",
                },
            )

        trial_onboarding_requested = is_truthy_flag(
            request.POST.get("trial_onboarding") or request.GET.get("trial_onboarding")
        )
        trial_onboarding_target = normalize_trial_onboarding_target(
            request.POST.get("trial_onboarding_target") or request.GET.get("trial_onboarding_target"),
            default=TRIAL_ONBOARDING_TARGET_API_KEYS,
        )
        if trial_onboarding_requested:
            if request.user.is_authenticated:
                return redirect("api_keys")
            set_trial_onboarding_intent(
                request,
                target=trial_onboarding_target,
            )
            from django.contrib.auth.views import redirect_to_login

            app_next_url = append_query_params(
                f"{IMMERSIVE_APP_BASE_PATH}/agents/new",
                {"spawn": "1"},
            )
            return redirect_to_login(
                next=app_next_url,
                login_url=_cta_auth_url_with_utms(request),
            )

        next_url = reverse("proprietary:pro_checkout")
        request.session[POST_CHECKOUT_REDIRECT_SESSION_KEY] = reverse("api_keys")
        request.session.modified = True

        if request.user.is_authenticated:
            return redirect(next_url)

        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(
            next=next_url,
            login_url=_cta_auth_url_with_utms(request),
        )


def health_check(request):
    """Basic health endpoint used by Kubernetes readiness/liveness probes.

    If the `/tmp/shutdown` sentinel file exists (created by the pod's preStop
    hook) we return HTTP 503 so that Kubernetes (and external load balancers)
    immediately mark the pod as *NotReady* and stop routing new traffic. This
    allows the pod to finish any in-flight requests while draining.
    """
    import os  # Local import to avoid at-import cost on hot path

    if os.path.exists("/tmp/shutdown"):
        # Indicate we are shutting down – fail readiness checks
        return HttpResponse("Shutting down", status=503)

    return HttpResponse("OK")


class WebManifestView(View):
    def get(self, request, *args, **kwargs):
        payload = build_web_manifest_payload(
            fish_collateral_enabled=is_fish_collateral_enabled(),
        )
        response = JsonResponse(payload, content_type="application/manifest+json")
        response["Cache-Control"] = "no-store, max-age=0"
        return response


class LandingRedirectView(View):
    """Short URL redirector for landing pages."""

    @tracer.start_as_current_span("LandingRedirectView.get")
    def get(self, request, code, *args, **kwargs):
        span = trace.get_current_span()
        landing = _get_active_landing_page_or_404(code)
        span.set_attribute("landing_page.code", code)
        landing.increment_hits()
        params = _build_landing_redirect_params(request, landing, code)
        _persist_landing_attribution(request, code)
        query_string = params.urlencode()
        target_url = f"{reverse('pages:home')}?{query_string}" if query_string else reverse('pages:home')
        response = HttpResponseRedirect(target_url)
        _apply_landing_attribution_cookies(
            response,
            request,
            code,
            fbc_source="pages.views.landing_page_redirect",
        )
        return response


class LandingLaunchView(View):
    """Launch a landing page charter directly into the immersive app."""

    @tracer.start_as_current_span("LandingLaunchView.get")
    def get(self, request, code, *args, **kwargs):
        from django.contrib.auth.views import redirect_to_login

        span = trace.get_current_span()
        landing = _get_active_landing_page_or_404(code)
        span.set_attribute("landing_page.code", code)

        landing.increment_hits()
        _persist_landing_attribution(request, code)
        _seed_landing_launch_session(request, landing)

        params = _build_landing_redirect_params(request, landing, code)
        should_set_mini_mode_cookie = _persist_landing_tracking_params(request, params)
        params["spawn"] = "1"

        raw_return_to = params.get("return_to")
        if "return_to" in params:
            del params["return_to"]
        normalized_return_to = normalize_return_to(request, raw_return_to)
        if normalized_return_to:
            params["return_to"] = normalized_return_to

        embed_requested = is_truthy_flag(params.get("embed"))
        if "embed" in params:
            del params["embed"]
        if embed_requested:
            params["embed"] = "1"

        app_query = params.urlencode()
        app_next_url = (
            f"{IMMERSIVE_APP_BASE_PATH}/agents/new?{app_query}"
            if app_query
            else f"{IMMERSIVE_APP_BASE_PATH}/agents/new"
        )

        if request.user.is_authenticated:
            response = HttpResponseRedirect(app_next_url)
        else:
            response = redirect_to_login(
                next=app_next_url,
                login_url=_cta_auth_url_with_utms(request),
            )
            charter_data = _build_oauth_charter_cookie_payload(
                request,
                charter=request.session.get("agent_charter") or "",
                charter_source=str(request.session.get("agent_charter_source") or "landing"),
            )
            attribution_data = _build_oauth_attribution_cookie_payload(request)
            _set_oauth_stash_cookies(
                response,
                request,
                charter_data=charter_data,
                attribution_data=attribution_data,
                server_side_charter=True,
            )

        _apply_landing_attribution_cookies(
            response,
            request,
            code,
            fbc_source="pages.views.landing_page_launch",
        )
        if should_set_mini_mode_cookie:
            set_mini_mode_cookie(response, request)
        return response


@method_decorator(vary_on_cookie, name="dispatch")
class MarkdownPageView(TemplateView):
    """
    View for rendering markdown pages.
    """
    template_name = "page.html"

    def get_context_data(self, **kwargs):
        slug = self.kwargs["slug"].rstrip("/")
        try:
            page = load_page(slug)
        except FileNotFoundError:
            raise Http404(f"Page not found: {slug}")

        ctx = super().get_context_data(**kwargs)
        ctx.update(page)
        ctx.update(get_prev_next(page["slug"]))
        ctx["all_doc_pages"] = get_all_doc_pages()
        return ctx

class DocsIndexRedirectView(RedirectView):
    """
    Redirect /docs/ to the first available documentation page.
    """
    permanent = False
    
    def get_redirect_url(self, *args, **kwargs):
        # Get all docs and redirect to the first one by order
        all_pages = get_all_doc_pages()
        if all_pages:
            return all_pages[0]["url"]
        # Fallback to a 404 if no pages exist
        raise Http404("No documentation pages found.")


# -----------------------------
# Legal / Terms of Service
# -----------------------------


class TermsOfServiceView(TemplateView):
    """Simple static Terms of Service page."""

    template_name = "tos.html"


# -----------------------------
# Privacy Policy
# -----------------------------


class PrivacyPolicyView(TemplateView):
    """Static Privacy Policy page."""

    template_name = "privacy.html"


class DataDeletionPolicyView(TemplateView):
    """Static Data Deletion Policy page."""

    template_name = "data-deletion.html"


class AboutView(TemplateView):
    """Simple static About page."""

    template_name = "about.html"


class TeamView(TemplateView):
    """Team page showcasing the people behind Gobii."""

    template_name = "team.html"


class CareersView(TemplateView):
    """Simple static Careers page."""

    template_name = "careers.html"


class PaidPlanLanding(LoginRequiredMixin, TemplateView):
    """Landing page for users interested in paid plans"""
    template_name = "plan_landing.html"
    
    def dispatch(self, request, *args, **kwargs):
        """Ensure we don't touch DB until user is authenticated."""
        if request.user.is_authenticated:
            plan_slug = kwargs.get("plan")
            valid_plans = dict(PaidPlanIntent.PlanChoices.choices)
            if plan_slug in valid_plans:
                PaidPlanIntent.objects.get_or_create(
                    user=request.user,
                    plan_name=plan_slug,
                )

        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        plan_slug = self.kwargs.get('plan', 'startup')
        
        # Plan-specific copy
        startup_task_credits = get_active_public_plan_monthly_task_credits(PlanNames.STARTUP)
        plan_info = {
            'startup': {
                'name': 'Pro',
                'tagline': 'When you need to get more work done',
                'features': [
                    f'{startup_task_credits:,} tasks included per month',
                    '25 always-on agents',
                    'Priority API access', 
                    'Email support',
                    'Higher rate limits',
                ]
            },
            'enterprise': {
                'name': 'Enterprise',
                'tagline': 'For mission-critical needs',
                'features': [
                    'Custom task allocation',
                    'Dedicated infrastructure',
                    'Priority support',
                    'SLA guarantees',
                    'Custom integrations',
                    'Dedicated account manager'
                ]
            }
        }
        
        context['plan'] = plan_info.get(plan_slug, plan_info['startup'])
        context['plan_slug'] = plan_slug
        return context

class StartupCheckoutView(LoginRequiredMixin, View):
    """Initiate Stripe Checkout for the Startup subscription plan."""

    def get(self, request, *args, **kwargs):
        user = request.user
        return_to = normalize_return_to(request, request.GET.get("return_to"))
        if return_to:
            request.session[POST_CHECKOUT_REDIRECT_SESSION_KEY] = return_to
            request.session.modified = True

        plan = reconcile_user_plan_from_stripe(user) or {}
        plan_id = str(plan.get("id") or "").lower()
        if plan_id and plan_id != PlanNames.FREE:
            redirect_path = _pop_post_checkout_redirect(request) or reverse("billing")
            return redirect(redirect_path)

        _prepare_stripe_or_404()
        stripe_settings = get_stripe_settings()

        # 1️⃣  Get (or lazily create) the Stripe customer linked to this user
        customer = get_or_create_stripe_customer(user)

        price = 0.0
        price_currency = None
        price_id = stripe_settings.startup_price_id
        if not price_id:
            raise Http404("Pro plan is not configured yet.")
        try:
            price_object = Price.objects.get(id=price_id)
            # unit_amount is in cents, convert to dollars
            if price_object.unit_amount is not None:
                price = price_object.unit_amount / 100
            price_currency = getattr(price_object, "currency", None)
        except Price.DoesNotExist:
            logger.warning("Price with ID '%s' does not exist in dj-stripe.", price_id)
            raise Http404("Pro plan pricing is not ready.")
        except Exception as e:
            logger.error(f"An unexpected error occurred while fetching price: {e}")

        event_id = f"startup-sub-{uuid.uuid4()}"

        success_url, post_checkout_redirect_used = _build_checkout_success_url(
            request,
            event_id=event_id,
            price=price,
            plan=PlanNames.STARTUP,
        )

        line_items = [
            {
                "price": price_id,
                "quantity": 1,
            }
        ]
        auto_purchase_enabled = _is_additional_tasks_auto_purchase_enabled(user)
        additional_price_id = (
            _additional_tasks_price_id_for_plan(stripe_settings, "startup")
            if auto_purchase_enabled
            else ""
        )
        if additional_price_id:
            line_items.append({"price": additional_price_id})

        base_metadata = {
            "gobii_event_id": event_id,
            "plan": PlanNames.STARTUP,
            "checkout_source_url": urlsplit(request.META.get("HTTP_REFERER") or settings.PUBLIC_SITE_URL)._replace(query="", fragment="").geturl()[:500],
        }

        _emit_checkout_initiated_event(
            request=request,
            user=user,
            plan_code=PlanNames.STARTUP,
            plan_label="Pro",
            value=price,
            currency=price_currency,
            event_id=event_id,
            post_checkout_redirect_used=post_checkout_redirect_used,
        )

        try:
            # Reuse/modify existing subscription when present; keep checkout for first purchase.
            ensure_kwargs: dict[str, object] = {
                "customer_id": customer.id,
                "licensed_price_id": price_id,
                "metadata": base_metadata,
                "idempotency_key": f"startup-individual-{customer.id}-{event_id}",
                "create_if_missing": False,
            }
            if additional_price_id:
                ensure_kwargs["metered_price_id"] = additional_price_id
            subscription, action = ensure_single_individual_subscription(**ensure_kwargs)

            if action != "absent" and subscription is not None:
                try:
                    Subscription.sync_from_stripe_data(subscription)
                except Exception:
                    logger.warning(
                        "Failed to sync Stripe subscription %s after %s",
                        getattr(subscription, "id", None)
                        or (subscription.get("id") if isinstance(subscription, dict) else ""),
                        action,
                        exc_info=True,
                    )

                return redirect(success_url)
        except stripe.error.InvalidRequestError as ensure_exc:
            logger.info(
                "Subscription ensure fell back to checkout for customer %s: %s",
                customer.id,
                ensure_exc,
            )
        except Exception:
            logger.exception(
                "Failed to ensure single subscription for customer %s", customer.id,
            )
            raise

        # 2️⃣  Kick off Checkout with the *existing* customer
        trial_days = _normalize_trial_days(getattr(stripe_settings, "startup_trial_days", 0))
        include_trial = trial_days > 0 and _is_individual_trial_eligible(
            user,
            request=request,
            capture_source=SIGNAL_SOURCE_CHECKOUT,
        )

        flow_type = (
            STRIPE_CHECKOUT_FLOW_TYPE_TRIAL
            if include_trial
            else STRIPE_CHECKOUT_FLOW_TYPE_PURCHASE
        )
        checkout_metadata = build_checkout_flow_metadata(
            base_metadata,
            flow_type=flow_type,
        )
        subscription_data = {"metadata": checkout_metadata}
        if include_trial:
            subscription_data["trial_period_days"] = trial_days

        checkout_kwargs = {
            "customer": customer.id,
            "api_key": stripe.api_key,
            "success_url": success_url,
            "cancel_url": request.build_absolute_uri(reverse("pages:home")),
            "mode": "subscription",
            "payment_method_types": PERSONAL_CHECKOUT_PAYMENT_METHOD_TYPES,
            "allow_promotion_codes": True,
            "metadata": checkout_metadata,
            "subscription_data": subscription_data,
            "line_items": line_items,
            "idempotency_key": f"checkout-startup-{customer.id}-{event_id}",
        }

        _apply_trial_checkout_fields(
            checkout_kwargs,
            include_trial=include_trial,
            trial_days=trial_days,
        )

        rewardful_referral = request.COOKIES.get("rewardful-referral", "")
        if rewardful_referral:
            checkout_kwargs["client_reference_id"] = rewardful_referral
        session = _create_checkout_session_with_customer_context(
            customer_id=customer.id,
            flow_type=flow_type,
            event_id=event_id,
            plan=PlanNames.STARTUP,
            plan_label="Pro",
            value=price,
            currency=price_currency,
            checkout_source_url=base_metadata.get("checkout_source_url"),
            checkout_kwargs=checkout_kwargs,
        )
        # Webhook-based AddPaymentInfo is authoritative so we only send once a
        # payment method is actually saved. Keep the old checkout-time send here
        # commented out for quick rollback if needed.
        # _emit_checkout_initiated_event(
        #     request=request,
        #     user=user,
        #     plan_code=PlanNames.STARTUP,
        #     plan_label="Pro",
        #     value=price,
        #     currency=price_currency,
        #     event_id=event_id,
        #     event_name="AddPaymentInfo",
        #     post_checkout_redirect_used=post_checkout_redirect_used,
        # )

        # 3️⃣  No need to sync anything here.  The webhook events
        #     (customer.subscription.created, invoice.paid, etc.)
        #     will hit your handler and use sub.customer.subscriber == user.

        return redirect(session.url)


class ScaleCheckoutView(LoginRequiredMixin, View):
    """Initiate Stripe Checkout for the Scale subscription plan."""

    def get(self, request, *args, **kwargs):
        _prepare_stripe_or_404()
        stripe_settings = get_stripe_settings()

        user = request.user
        return_to = normalize_return_to(request, request.GET.get("return_to"))
        if return_to:
            request.session[POST_CHECKOUT_REDIRECT_SESSION_KEY] = return_to
            request.session.modified = True

        customer = get_or_create_stripe_customer(user)

        price = 0.0
        price_currency = None
        price_id = stripe_settings.scale_price_id
        if not price_id:
            raise Http404("Scale plan is not configured yet.")
        try:
            price_object = Price.objects.get(id=price_id)
            if price_object.unit_amount is not None:
                price = price_object.unit_amount / 100
            price_currency = getattr(price_object, "currency", None)
        except Price.DoesNotExist:
            logger.warning("Price with ID '%s' does not exist in dj-stripe.", price_id)
            raise Http404("Scale plan pricing is not ready.")
        except Exception:
            logger.exception("Unexpected error while fetching scale plan price %s", price_id)
            raise Http404("An unexpected error occurred while preparing your checkout.")

        event_id = f"scale-sub-{uuid.uuid4()}"

        success_url, post_checkout_redirect_used = _build_checkout_success_url(
            request,
            event_id=event_id,
            price=price,
            plan=PlanNames.SCALE,
        )

        line_items = [
            {
                "price": price_id,
                "quantity": 1,
            }
        ]
        auto_purchase_enabled = _is_additional_tasks_auto_purchase_enabled(user)
        additional_price_id = (
            _additional_tasks_price_id_for_plan(stripe_settings, "scale")
            if auto_purchase_enabled
            else ""
        )
        if additional_price_id:
            line_items.append({"price": additional_price_id})

        base_metadata = {
            "gobii_event_id": event_id,
            "plan": PlanNames.SCALE,
            "checkout_source_url": urlsplit(request.META.get("HTTP_REFERER") or settings.PUBLIC_SITE_URL)._replace(query="", fragment="").geturl()[:500],
        }

        _emit_checkout_initiated_event(
            request=request,
            user=user,
            plan_code=PlanNames.SCALE,
            plan_label="Scale",
            value=price,
            currency=price_currency,
            event_id=event_id,
            post_checkout_redirect_used=post_checkout_redirect_used,
        )

        _, existing_subs = _customer_has_price_subscription_with_cache(str(customer.id), price_id)

        if existing_subs:
            try:
                ensure_kwargs: dict[str, object] = {
                    "customer_id": customer.id,
                    "licensed_price_id": price_id,
                    "metadata": base_metadata,
                    "idempotency_key": f"scale-individual-upgrade-{customer.id}-{event_id}",
                    "create_if_missing": False,
                }
                if additional_price_id:
                    ensure_kwargs["metered_price_id"] = additional_price_id
                subscription, action = ensure_single_individual_subscription(**ensure_kwargs)

                if action != "absent" and subscription is not None:
                    try:
                        Subscription.sync_from_stripe_data(subscription)
                    except Exception:
                        logger.warning(
                            "Failed to sync Stripe subscription %s after %s",
                            getattr(subscription, "id", None)
                            or (subscription.get("id") if isinstance(subscription, dict) else ""),
                            action,
                            exc_info=True,
                        )

                    return redirect(success_url)
            except stripe.error.InvalidRequestError as ensure_exc:
                logger.info(
                    "Upgrade via ensure failed; falling back to checkout for customer %s: %s",
                    customer.id,
                    ensure_exc,
                )
            except Exception:
                logger.exception(
                    "Failed to upgrade subscription for customer %s; falling back to checkout", customer.id,
                )

        trial_days = _normalize_trial_days(getattr(stripe_settings, "scale_trial_days", 0))
        include_trial = trial_days > 0 and _is_individual_trial_eligible(
            user,
            request=request,
            capture_source=SIGNAL_SOURCE_CHECKOUT,
        )

        flow_type = (
            STRIPE_CHECKOUT_FLOW_TYPE_TRIAL
            if include_trial
            else STRIPE_CHECKOUT_FLOW_TYPE_PURCHASE
        )
        checkout_metadata = build_checkout_flow_metadata(
            base_metadata,
            flow_type=flow_type,
        )
        subscription_data = {"metadata": checkout_metadata}
        if include_trial:
            subscription_data["trial_period_days"] = trial_days

        checkout_kwargs = {
            "customer": customer.id,
            "api_key": stripe.api_key,
            "success_url": success_url,
            "cancel_url": request.build_absolute_uri(reverse("pages:home")),
            "mode": "subscription",
            "payment_method_types": PERSONAL_CHECKOUT_PAYMENT_METHOD_TYPES,
            "allow_promotion_codes": True,
            "metadata": checkout_metadata,
            "subscription_data": subscription_data,
            "line_items": line_items,
            "idempotency_key": f"checkout-scale-{customer.id}-{event_id}",
        }
        _apply_trial_checkout_fields(
            checkout_kwargs,
            include_trial=include_trial,
            trial_days=trial_days,
        )
        rewardful_referral = request.COOKIES.get("rewardful-referral", "")
        if rewardful_referral:
            checkout_kwargs["client_reference_id"] = rewardful_referral
        session = _create_checkout_session_with_customer_context(
            customer_id=customer.id,
            flow_type=flow_type,
            event_id=event_id,
            plan=PlanNames.SCALE,
            plan_label="Scale",
            value=price,
            currency=price_currency,
            checkout_source_url=base_metadata.get("checkout_source_url"),
            checkout_kwargs=checkout_kwargs,
        )
        # Webhook-based AddPaymentInfo is authoritative so we only send once a
        # payment method is actually saved. Keep the old checkout-time send here
        # commented out for quick rollback if needed.
        # _emit_checkout_initiated_event(
        #     request=request,
        #     user=user,
        #     plan_code=PlanNames.SCALE,
        #     plan_label="Scale",
        #     value=price,
        #     currency=price_currency,
        #     event_id=event_id,
        #     event_name="AddPaymentInfo",
        #     post_checkout_redirect_used=post_checkout_redirect_used,
        # )

        return redirect(session.url)

class PricingView(TemplateView):
    pass

class StaticViewSitemap(sitemaps.Sitemap):
    priority = 0.5
    changefreq = 'weekly'

    def items(self):
        # List of all static view names that should be included in the sitemap
        items = [
            'pages:home',
            'pages:library',
            'pages:docs_index',
        ]
        # Include pricing only when proprietary mode is enabled
        try:
            if settings.GOBII_PROPRIETARY_MODE:
                items.insert(1, 'proprietary:pricing')
                items.insert(2, 'proprietary:tos')
                items.insert(3, 'proprietary:privacy')
                items.insert(4, 'proprietary:about')
                items.insert(5, 'proprietary:team')
                items.insert(6, 'proprietary:careers')
                items.insert(7, 'proprietary:startup_checkout')
                items.insert(8, 'proprietary:blog_index')
        except Exception:
            pass
        return items

    def location(self, item):
        return reverse(item)


class PretrainedWorkerTemplateSitemap(sitemaps.Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        try:
            return list(PretrainedWorkerTemplateService.get_active_templates())
        except Exception as e:  # pragma: no cover - defensive fallback to keep sitemap working
            logger.error("Failed to generate PretrainedWorkerTemplateSitemap items: %s", e, exc_info=True)
            return []

    def location(self, template):
        return reverse('pages:pretrained_worker_detail', kwargs={'slug': template.code})

    def lastmod(self, template):
        return getattr(template, "updated_at", None)


class PublicTemplateSitemap(sitemaps.Sitemap):
    changefreq = "weekly"
    priority = 0.7

    def items(self):
        return (
            PersistentAgentTemplate.objects.select_related("public_profile")
            .filter(public_profile__isnull=False, is_active=True)
            .exclude(slug="")
        )

    def location(self, template):
        return reverse(
            "pages:public_template_detail",
            kwargs={
                "handle": template.public_profile.handle,
                "template_slug": template.slug,
            },
        )

    def lastmod(self, template):
        return getattr(template, "updated_at", None)


class SolutionsSitemap(sitemaps.Sitemap):
    changefreq = "monthly"
    priority = 0.5

    def items(self):
        try:
            return list(SolutionView.SOLUTION_DATA.keys())
        except Exception as e:
            logger.error("Failed to generate SolutionsSitemap items: %s", e, exc_info=True)
            return []

    def location(self, slug):
        return reverse('pages:solution', kwargs={'slug': slug})


class SupportView(TemplateView):
    pass


class MarketingContactRequestView(View):
    SOURCE_CONFIG = {
        "healthcare_landing_page": {
            "subject": "Healthcare Demo Request",
            "label": "Healthcare demo request",
        },
        "defense_landing_page": {
            "subject": "Defense Contact Request",
            "label": "Defense contact request",
        },
    }

    @staticmethod
    def _render_form_errors(form: MarketingContactForm) -> HttpResponse:
        errors = []
        for field_errors in form.errors.values():
            errors.extend(field_errors)

        error_items = "".join(f"<li>{escape(message)}</li>" for message in errors)
        error_html = (
            '<div class="rounded-xl border border-red-200 bg-white/90 px-4 py-3 text-sm text-red-700" role="alert">'
            'Please correct the following errors:'
            f'<ul class="mt-2 list-disc list-inside">{error_items}</ul>'
            "</div>"
        )
        return HttpResponse(error_html, status=400)

    def post(self, request, *args, **kwargs):
        form = MarketingContactForm(request.POST)
        if not form.is_valid():
            return self._render_form_errors(form)

        cleaned = form.cleaned_data
        source = cleaned.get("source")
        source_config = self.SOURCE_CONFIG.get(source)
        if not source_config:
            return HttpResponse(
                '<div class="rounded-xl border border-red-200 bg-white/90 px-4 py-3 text-sm text-red-700" role="alert">'
                "Invalid request source."
                "</div>",
                status=400,
            )

        recipient_email = settings.PUBLIC_CONTACT_EMAIL or settings.SUPPORT_EMAIL
        if not recipient_email:
            return HttpResponse(
                '<div class="rounded-xl border border-red-200 bg-white/90 px-4 py-3 text-sm text-red-700" role="alert">'
                "Contact email is not configured."
                "</div>",
                status=500,
            )

        inquiry_label = ""
        inquiry_value = cleaned.get("inquiry_type") or ""
        if inquiry_value:
            inquiry_choices = dict(MarketingContactForm.INQUIRY_CHOICES)
            inquiry_label = inquiry_choices.get(inquiry_value, inquiry_value)

        context = {
            "source_label": source_config["label"],
            "email": cleaned.get("email"),
            "organization": cleaned.get("organization"),
            "inquiry_type": inquiry_label,
            "message": cleaned.get("message"),
            "referrer": request.META.get("HTTP_REFERER", ""),
        }

        html_message = render_to_string("emails/marketing_contact_request.html", context)
        plain_message = strip_tags(html_message)

        try:
            send_mail(
                subject=source_config["subject"],
                message=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient_email],
                html_message=html_message,
                fail_silently=False,
            )
        except Exception:
            logger.exception("Error sending marketing contact request email.")
            return HttpResponse(
                '<div class="rounded-xl border border-red-200 bg-white/90 px-4 py-3 text-sm text-red-700" role="alert">'
                "Sorry, there was an error sending your message. Please try again later."
                "</div>",
                status=500,
            )

        analytics_properties = {
            "source_page": source,
            "inquiry_type": inquiry_value or "",
        }
        _track_web_event_for_request(
            request,
            event=AnalyticsEvent.MARKETING_CONTACT_REQUEST_SUBMITTED,
            properties=analytics_properties,
        )

        return HttpResponse(
            '<div class="rounded-xl border border-emerald-200 bg-white/90 px-4 py-3 text-sm text-emerald-700" role="status">'
            "Thanks for reaching out. We will follow up shortly."
            "</div>"
        )


class ClearSignupTrackingView(View):
    """Return signup tracking data (if any) and clear the session flag.

    Used by static app shells that can't access Django template context.
    Returns tracking data needed to fire conversion pixels client-side.
    """

    def get(self, request, *args, **kwargs):
        # Check if there's pending signup tracking
        show_tracking = request.session.get('show_signup_tracking', False)

        if not show_tracking:
            return JsonResponse({'tracking': False})

        # Gather tracking data before clearing
        from pages.context_processors import analytics
        analytics_data = analytics(request).get('analytics', {}).get('data', {})

        data = {
            'tracking': True,
            'eventId': request.session.get('signup_event_id', ''),
            'userId': str(request.user.id) if request.user.is_authenticated else '',
            'emailHash': analytics_data.get('email_hash', ''),
            'idHash': analytics_data.get('id_hash', ''),
            'authMethod': request.session.get('signup_auth_method', 'email'),
            'authProvider': request.session.get('signup_auth_provider', ''),
            'registrationValue': float(getattr(settings, 'CAPI_REGISTRATION_VALUE', 0) or 0),
            # Include pixel IDs so client knows which to fire
            'pixels': {
                'ga': getattr(settings, 'GA_MEASUREMENT_ID', ''),
                'reddit': getattr(settings, 'REDDIT_PIXEL_ID', ''),
                'tiktok': getattr(settings, 'TIKTOK_PIXEL_ID', ''),
                'meta': getattr(settings, 'META_PIXEL_ID', ''),
                'linkedin': getattr(settings, 'LINKEDIN_SIGNUP_CONVERSION_ID', ''),
            },
        }

        # Clear the session flag and related data
        del request.session['show_signup_tracking']
        for key in SIGNUP_TRACKING_SESSION_KEYS:
            if key in request.session:
                del request.session[key]

        return JsonResponse(data)


class SolutionView(TemplateView):
    template_name = "solutions/solution.html"

    # Solutions with dedicated landing page templates
    DEDICATED_TEMPLATES = {
        'recruiting': 'solutions/recruiting.html',
        'sales': 'solutions/sales.html',
        'health-care': 'solutions/health-care.html',
        'defense': 'solutions/defense.html',
        'engineering': 'solutions/engineering.html',
    }

    SOLUTION_DATA = {
        'recruiting': {
            'title': 'Recruiting',
            'tagline': 'Automate candidate sourcing and screening.',
            'description': 'Find top talent faster with AI agents that work 24/7 to source, screen, and engage candidates.'
        },
        'sales': {
            'title': 'Sales',
            'tagline': 'Supercharge your outbound outreach.',
            'description': 'Scale your prospecting and personalized messaging to fill your pipeline automatically.'
        },
        'health-care': {
            'title': 'Health Care',
            'tagline': 'Streamline patient intake and administrative tasks.',
            'description': 'Secure, HIPAA-compliant automation for modern healthcare providers and payers.'
        },
        'defense': {
            'title': 'Defense',
            'tagline': 'Secure, on-premise AI intelligence.',
            'description': 'Mission-critical automation for national security with strict data governance.'
        },
        'engineering': {
            'title': 'Engineering',
            'tagline': 'Accelerate development workflows.',
            'description': 'Automate code reviews, testing, and deployment pipelines to ship software faster.'
        },
    }

    def get_template_names(self):
        slug = self.kwargs.get('slug', '')
        if slug in self.DEDICATED_TEMPLATES:
            return [self.DEDICATED_TEMPLATES[slug]]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        slug = self.kwargs['slug']
        data = self.SOLUTION_DATA.get(slug, {
            'title': slug.replace('-', ' ').title(),
            'tagline': 'AI Solutions for your industry.',
            'description': 'Tailored AI agents and automation to help you scale.'
        })

        solution_spawn_requires_trial = False
        if self.request.user.is_authenticated:
            solution_spawn_requires_trial = not can_user_use_personal_agents_and_api(self.request.user)

        context.update({
            'solution_title': data['title'],
            'solution_tagline': data['tagline'],
            'solution_description': data['description'],
            'solution_spawn_requires_trial': solution_spawn_requires_trial,
        })
        if slug in {"health-care", "defense"}:
            context["marketing_contact_form"] = MarketingContactForm()
        return context
