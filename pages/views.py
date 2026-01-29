from datetime import timezone, datetime
from urllib.parse import urlencode
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
from api.models import PaidPlanIntent, PersistentAgent, PersistentAgentTemplate
from api.agent.short_description import build_listing_description, build_mini_description
from agents.services import PretrainedWorkerTemplateService
from api.models import OrganizationMembership
from config.socialaccount_adapter import OAUTH_CHARTER_COOKIE
from config.stripe_config import get_stripe_settings

import stripe
from djstripe.models import Customer, Subscription, Price
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.payments_helper import PaymentsHelper
from util.subscription_helper import (
    ensure_single_individual_subscription,
    get_existing_individual_subscriptions,
    get_or_create_stripe_customer,
    get_user_plan,
)
from util.integrations import stripe_status, IntegrationDisabledError
from constants.plans import PlanNames
from util.urls import IMMERSIVE_RETURN_TO_SESSION_KEY, build_immersive_chat_url, normalize_return_to
from .utils_markdown import (
    load_page,
    get_prev_next,
    get_all_doc_pages,
)
from .homepage_cache import get_homepage_pretrained_payload
from .examples_data import SIMPLE_EXAMPLES, RICH_EXAMPLES
from .forms import MarketingContactForm
from console.views import build_llm_intelligence_props
from django.contrib import sitemaps
from django.urls import NoReverseMatch, reverse
from django.utils import timezone as dj_timezone
from django.utils.html import escape, strip_tags
from opentelemetry import trace
from marketing_events.api import capi
import logging
logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")
PREFERRED_LLM_TIER_SESSION_KEY = "agent_preferred_llm_tier"

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



def _login_url_with_utms(request) -> str:
    """Append stored UTM query params to the login URL when available."""
    base_url = resolve_url(settings.LOGIN_URL)
    utm_qs = request.session.get("utm_querystring") or ""
    if utm_qs:
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}{utm_qs}"
    return base_url


POST_CHECKOUT_REDIRECT_SESSION_KEY = "post_checkout_redirect"


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
    ltv_multiple = float(getattr(settings, "CAPI_LTV_MULTIPLE", 1.0) or 1.0)
    ltv_price = price * ltv_multiple
    success_params = {
        "subscribe_success": 1,
        "p": f"{ltv_price:.2f}",
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

        preferred_llm_tier = self.request.session.get(PREFERRED_LLM_TIER_SESSION_KEY) or 'standard'
        context['preferred_llm_tier'] = preferred_llm_tier
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
            recent_agents_qs = PersistentAgent.objects.non_eval().filter(user_id=self.request.user.id)
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
        
        if form.is_valid():
            return_to = normalize_return_to(request, request.POST.get("return_to"))
            embed = (request.POST.get("embed") or "").lower() in {"1", "true", "yes", "on"}
            if return_to:
                request.session[IMMERSIVE_RETURN_TO_SESSION_KEY] = return_to

            # Clear any previously selected pretrained worker so we treat this as a fresh custom charter
            request.session.pop(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY, None)
            # Store charter in session for later use
            request.session['agent_charter'] = form.cleaned_data['charter']
            request.session['agent_charter_source'] = 'user'
            preferred_llm_tier = (request.POST.get("preferred_llm_tier") or "").strip()
            if preferred_llm_tier:
                request.session[PREFERRED_LLM_TIER_SESSION_KEY] = preferred_llm_tier
                request.session.modified = True

            # Track analytics for home page agent creation start (only for authenticated users)
            if request.user.is_authenticated:
                Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.PERSISTENT_AGENT_CHARTER_SUBMIT,
                    source=AnalyticsSource.WEB,
                    properties={
                        'charter': form.cleaned_data['charter'],
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
            # User needs to log in first, then continue to agent creation
            return redirect_to_login(
                next=next_url,
                login_url=_login_url_with_utms(request),
            )
        
        # If form is invalid, re-render home page with errors
        context = self.get_context_data(**kwargs)
        context['agent_charter_form'] = form
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        # Reuse the same context as HomePage
        homepage_view = HomePage()
        homepage_view.request = self.request
        return homepage_view.get_context_data(**kwargs)


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

        response = redirect_to_login(
            next=next_url,
            login_url=_login_url_with_utms(request),
        )

        # Also store charter in a signed cookie for OAuth flows where session
        # data might be lost during the redirect chain
        charter_data = {
            "agent_charter": template.charter,
            PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY: template.code,
            "agent_charter_source": "template",
        }
        response.set_cookie(
            OAUTH_CHARTER_COOKIE,
            signing.dumps(charter_data, compress=True),
            max_age=3600,  # 1 hour
            httponly=True,
            samesite="Lax",
            secure=request.is_secure(),
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

        response = redirect_to_login(
            next=next_url,
            login_url=_login_url_with_utms(request),
        )

        charter_data = {
            "agent_charter": template.charter,
            PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY: template.code,
            "agent_charter_source": "template",
        }
        response.set_cookie(
            OAUTH_CHARTER_COOKIE,
            signing.dumps(charter_data, compress=True),
            max_age=3600,  # 1 hour
            httponly=True,
            samesite="Lax",
            secure=request.is_secure(),
        )

        return response


class EngineeringProSignupView(View):
    def get(self, request, *args, **kwargs):
        return self._handle(request)

    def post(self, request, *args, **kwargs):
        return self._handle(request)

    def _handle(self, request):
        next_url = reverse("proprietary:pro_checkout")
        request.session[POST_CHECKOUT_REDIRECT_SESSION_KEY] = reverse("api_keys")
        request.session.modified = True

        if request.user.is_authenticated:
            return redirect(next_url)

        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(
            next=next_url,
            login_url=_login_url_with_utms(request),
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


class LandingRedirectView(View):
    """Short URL redirector for landing pages."""

    @tracer.start_as_current_span("LandingRedirectView.get")
    def get(self, request, code, *args, **kwargs):
        span = trace.get_current_span()
        try:
            landing = LandingPage.objects.get(code=code, disabled=False)
            span.set_attribute("landing_page.code", code)
        except LandingPage.DoesNotExist:
            raise Http404("Landing page not found")



        landing.increment_hits()

        # 2  Start with whatever query-params came in (UTMs, fbclid, etc.)
        params = request.GET.copy()          # QueryDict → mutable
        params['g'] = code  # Always tag with the landing code

        # Persist landing attribution in the session so we can reach it during signup.
        try:
            request.session.setdefault('landing_code_first', code)
            request.session['landing_code_last'] = code
            request.session.setdefault('landing_first_seen_at', dj_timezone.now().isoformat())
            request.session['landing_last_seen_at'] = dj_timezone.now().isoformat()
            request.session.modified = True
        except Exception:
            logger.exception("Failed to persist landing attribution in session for code %s", code)

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

        # 4  Re-encode the combined query string
        query_string = params.urlencode()

        # 5  Redirect to the canonical homepage + merged params
        target_url = f"{reverse('pages:home')}?{query_string}" if query_string else reverse('pages:home')

        response = HttpResponseRedirect(target_url)

        # Mirror landing attribution details in cookies (fallback if sessions are disabled).
        try:
            cookie_max_age = 60 * 24 * 60 * 60  # 60 days
            response.set_cookie(
                'landing_code',
                code,
                max_age=cookie_max_age,
                samesite='Lax',
            )
            if '__landing_first' not in request.COOKIES:
                response.set_cookie(
                    '__landing_first',
                    code,
                    max_age=cookie_max_age,
                    samesite='Lax',
                )
        except Exception:
            logger.exception("Failed to persist landing attribution cookies for code %s", code)

        # Store the fbclid cookie if it exists
        try:
            if 'fbclid' in request.GET and request.COOKIES.get('_fbc') is None:
                fbc = f"fb.1.{int(datetime.now(timezone.utc).timestamp() * 1000)}.{request.GET['fbclid']}"
                response.set_cookie('_fbc', fbc, max_age=60*60*24*90)
                response.set_cookie('fbclid', request.GET['fbclid'], max_age=60*60*24*90)
        except Exception as e:
            logger.error(f"Error setting fbclid cookie: {e}")

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
        plan_info = {
            'startup': {
                'name': 'Pro',
                'tagline': 'When you need to get more work done',
                'features': [
                    '500 tasks included per month',
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

        plan = get_user_plan(user) or {}
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
        additional_price_id = stripe_settings.startup_additional_task_price_id
        if additional_price_id:
            line_items.append({"price": additional_price_id})

        metadata = {
            "gobii_event_id": event_id,
            "plan": PlanNames.STARTUP,
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
            subscription, action = ensure_single_individual_subscription(
                customer_id=customer.id,
                licensed_price_id=price_id,
                metered_price_id=additional_price_id,
                metadata=metadata,
                idempotency_key=f"startup-individual-{customer.id}-{event_id}",
                create_if_missing=False,
            )

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
        checkout_kwargs = {
            "customer": customer.id,
            "api_key": stripe.api_key,
            "success_url": success_url,
            "cancel_url": request.build_absolute_uri(reverse("pages:home")),
            "mode": "subscription",
            "allow_promotion_codes": True,
            "subscription_data": {
                "metadata": metadata,
            },
            "line_items": line_items,
            "idempotency_key": f"checkout-startup-{customer.id}-{event_id}",
        }
        rewardful_referral = request.COOKIES.get("rewardful-referral", "")
        if rewardful_referral:
            checkout_kwargs["client_reference_id"] = rewardful_referral
        session = stripe.checkout.Session.create(**checkout_kwargs)

        _emit_checkout_initiated_event(
            request=request,
            user=user,
            plan_code=PlanNames.STARTUP,
            plan_label="Pro",
            value=price,
            currency=price_currency,
            event_id=event_id,
            event_name="AddPaymentInfo",
            post_checkout_redirect_used=post_checkout_redirect_used,
        )

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
        additional_price_id = stripe_settings.scale_additional_task_price_id
        if additional_price_id:
            line_items.append({"price": additional_price_id})

        metadata = {
            "gobii_event_id": event_id,
            "plan": PlanNames.SCALE,
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
                subscription, action = ensure_single_individual_subscription(
                    customer_id=customer.id,
                    licensed_price_id=price_id,
                    metered_price_id=additional_price_id,
                    metadata=metadata,
                    idempotency_key=f"scale-individual-upgrade-{customer.id}-{event_id}",
                    create_if_missing=False,
                )

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

        checkout_kwargs = {
            "customer": customer.id,
            "api_key": stripe.api_key,
            "success_url": success_url,
            "cancel_url": request.build_absolute_uri(reverse("pages:home")),
            "mode": "subscription",
            "allow_promotion_codes": True,
            "subscription_data": {
                "metadata": metadata,
            },
            "line_items": line_items,
            "idempotency_key": f"checkout-scale-{customer.id}-{event_id}",
        }
        rewardful_referral = request.COOKIES.get("rewardful-referral", "")
        if rewardful_referral:
            checkout_kwargs["client_reference_id"] = rewardful_referral
        session = stripe.checkout.Session.create(**checkout_kwargs)

        _emit_checkout_initiated_event(
            request=request,
            user=user,
            plan_code=PlanNames.SCALE,
            plan_label="Scale",
            value=price,
            currency=price_currency,
            event_id=event_id,
            event_name="AddPaymentInfo",
            post_checkout_redirect_used=post_checkout_redirect_used,
        )

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
                source_config["subject"],
                plain_message,
                settings.DEFAULT_FROM_EMAIL,
                [recipient_email],
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
        for key in ('signup_event_id', 'signup_user_id', 'signup_email_hash'):
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

        context.update({
            'solution_title': data['title'],
            'solution_tagline': data['tagline'],
            'solution_description': data['description'],
        })
        if slug in {"health-care", "defense"}:
            context["marketing_contact_form"] = MarketingContactForm()
        return context
