from datetime import timezone, datetime
from urllib.parse import urlencode
import uuid

from django.http.response import JsonResponse
from django.views.generic import TemplateView, RedirectView, View
from django.http import HttpResponse, Http404
from django.utils.decorators import method_decorator
from django.views.decorators.vary import vary_on_cookie
from django.shortcuts import redirect, resolve_url
from django.http import HttpResponseRedirect
from .models import LandingPage
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from api.models import PaidPlanIntent, PersistentAgent
from api.agent.short_description import build_listing_description, build_mini_description
from agents.services import PretrainedWorkerTemplateService
from api.models import OrganizationMembership
from config.stripe_config import get_stripe_settings

import stripe
from djstripe.models import Customer, Subscription, Price
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.payments_helper import PaymentsHelper
from util.subscription_helper import get_or_create_stripe_customer, get_user_plan
from util.integrations import stripe_status, IntegrationDisabledError
from .utils_markdown import (
    load_page,
    get_prev_next,
    get_all_doc_pages,
)
from .examples_data import SIMPLE_EXAMPLES, RICH_EXAMPLES
from django.contrib import sitemaps
from django.urls import reverse
from django.utils import timezone as dj_timezone
from django.utils.html import escape
from opentelemetry import trace
import logging
logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")


def _login_url_with_utms(request) -> str:
    """Append stored UTM query params to the login URL when available."""
    base_url = resolve_url(settings.LOGIN_URL)
    utm_qs = request.session.get("utm_querystring") or ""
    if utm_qs:
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}{utm_qs}"
    return base_url


def _prepare_stripe_or_404() -> None:
    status = stripe_status()
    if not status.enabled:
        raise Http404("Stripe billing is not available.")
    key = PaymentsHelper.get_stripe_key()
    if not key:
        raise Http404("Stripe billing is not configured.")
    stripe.api_key = key

class HomePage(TemplateView):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Add agent charter form for the home page spawn functionality
        from console.forms import PersistentAgentCharterForm

        initial = {}

        # If 'spawn=1' parameter is present, clear any stored charter to start fresh
        if self.request.GET.get('spawn') == '1':
            if 'agent_charter' in self.request.session:
                del self.request.session['agent_charter']
            if 'agent_charter_source' in self.request.session:
                del self.request.session['agent_charter_source']
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
                    '<span class="bg-gradient-to-r from-violet-600 to-purple-500 bg-clip-text text-transparent">'
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

        # Examples data
        context["simple_examples"] = SIMPLE_EXAMPLES
        context["rich_examples"] = RICH_EXAMPLES

        # Featured and full pretrained worker templates for homepage
        all_templates = list(PretrainedWorkerTemplateService.get_active_templates())

        tool_names = set()
        for template in all_templates:
            template.schedule_description = PretrainedWorkerTemplateService.describe_schedule(template.base_schedule)
            tool_names.update(template.default_tools or [])

        tool_display_map = PretrainedWorkerTemplateService.get_tool_display_map(tool_names)
        for template in all_templates:
            template.display_default_tools = PretrainedWorkerTemplateService.get_tool_display_list(
                template.default_tools or [],
                display_map=tool_display_map,
            )

        category_filter = (self.request.GET.get("pretrained_category") or "").strip()
        search_term = (self.request.GET.get("pretrained_search") or "").strip()

        filtered_templates = list(all_templates)
        if category_filter:
            category_lower = category_filter.lower()
            filtered_templates = [
                template
                for template in filtered_templates
                if (template.category or "").lower() == category_lower
            ]

        if search_term:
            search_lower = search_term.lower()
            filtered_templates = [
                template
                for template in filtered_templates
                if search_lower in template.display_name.lower()
                or search_lower in template.tagline.lower()
                or search_lower in template.description.lower()
            ]

        context.update(
            {
                "homepage_pretrained_workers": filtered_templates,
                "homepage_pretrained_total": len(all_templates),
                "homepage_pretrained_filtered_count": len(filtered_templates),
                "homepage_pretrained_categories": sorted(
                    {template.category for template in all_templates if template.category}
                ),
                "homepage_pretrained_selected_category": category_filter,
                "homepage_pretrained_search_term": search_term,
            }
        )

        if self.request.user.is_authenticated:
            recent_agents_qs = PersistentAgent.objects.filter(user_id=self.request.user.id)
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
            # Clear any previously selected pretrained worker so we treat this as a fresh custom charter
            request.session.pop(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY, None)
            # Store charter in session for later use
            request.session['agent_charter'] = form.cleaned_data['charter']
            request.session['agent_charter_source'] = 'user'

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
            
            if request.user.is_authenticated:
                # User is already logged in, go directly to contact form
                return redirect('agent_create_contact')
            else:
                # User needs to log in first, then continue to contact form
                return redirect_to_login(
                    next=reverse('agent_create_contact'),
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

        if request.user.is_authenticated:
            Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.PERSISTENT_AGENT_CHARTER_SUBMIT,
                source=AnalyticsSource.WEB,
                properties={
                    "source_page": source_page,
                    "template_code": template.code,
                },
            )
            return redirect('agent_create_contact')

        # Track anonymous interest
        session_key = request.session.session_key
        if not session_key:
            request.session.save()
            session_key = request.session.session_key
        Analytics.track_event_anonymous(
            anonymous_id=str(session_key),
            event=AnalyticsEvent.PERSISTENT_AGENT_CHARTER_SUBMIT,
            source=AnalyticsSource.WEB,
            properties={
                "source_page": source_page,
                "template_code": template.code,
            },
        )

        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(
            next=reverse('agent_create_contact'),
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
        _prepare_stripe_or_404()
        stripe_settings = get_stripe_settings()

        user = request.user

        # 1️⃣  Get (or lazily create) the Stripe customer linked to this user
        customer = get_or_create_stripe_customer(user)

        price = 0.0
        price_id = stripe_settings.startup_price_id
        if not price_id:
            raise Http404("Pro plan is not configured yet.")
        try:
            price_object = Price.objects.get(id=price_id)
            # unit_amount is in cents, convert to dollars
            if price_object.unit_amount is not None:
                price = price_object.unit_amount / 100
        except Price.DoesNotExist:
            logger.warning("Price with ID '%s' does not exist in dj-stripe.", price_id)
            raise Http404("Pro plan pricing is not ready.")
        except Exception as e:
            logger.error(f"An unexpected error occurred while fetching price: {e}")

        event_id = f"sub-{uuid.uuid4()}"

        success_params = {
            "subscribe_success": 1,
            "p": f"{price:.2f}",
            "eid": event_id,
        }
        success_url = f'{request.build_absolute_uri(reverse("console-home"))}?{urlencode(success_params)}'

        line_items = [
            {
                "price": price_id,
                "quantity": 1,
            }
        ]
        additional_price_id = stripe_settings.startup_additional_task_price_id
        if additional_price_id:
            line_items.append({"price": additional_price_id})

        # 2️⃣  Kick off Checkout with the *existing* customer
        session = stripe.checkout.Session.create(
            customer=customer.id,                       # <-- key line
            api_key=stripe.api_key,
            success_url=success_url,
            cancel_url=request.build_absolute_uri(reverse("pages:home")),
            mode="subscription",
            allow_promotion_codes=True,
            subscription_data={
                "metadata": {
                    "gobii_event_id": event_id,
                }
            },
            line_items=line_items,
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

        customer = get_or_create_stripe_customer(user)

        price = 0.0
        price_id = stripe_settings.scale_price_id
        if not price_id:
            raise Http404("Scale plan is not configured yet.")
        try:
            price_object = Price.objects.get(id=price_id)
            if price_object.unit_amount is not None:
                price = price_object.unit_amount / 100
        except Price.DoesNotExist:
            logger.warning("Price with ID '%s' does not exist in dj-stripe.", price_id)
            raise Http404("Scale plan pricing is not ready.")
        except Exception:
            logger.exception("Unexpected error while fetching scale plan price %s", price_id)
            raise Http404("An unexpected error occurred while preparing your checkout.")

        event_id = f"scale-sub-{uuid.uuid4()}"

        success_params = {
            "subscribe_success": 1,
            "p": f"{price:.2f}",
            "eid": event_id,
        }
        success_url = f'{request.build_absolute_uri(reverse("console-home"))}?{urlencode(success_params)}'

        line_items = [
            {
                "price": price_id,
                "quantity": 1,
            }
        ]
        additional_price_id = stripe_settings.scale_additional_task_price_id
        if additional_price_id:
            line_items.append({"price": additional_price_id})

        session = stripe.checkout.Session.create(
            customer=customer.id,
            api_key=stripe.api_key,
            success_url=success_url,
            cancel_url=request.build_absolute_uri(reverse("pages:home")),
            mode="subscription",
            allow_promotion_codes=True,
            subscription_data={
                "metadata": {
                    "gobii_event_id": event_id,
                }
            },
            line_items=line_items,
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
                items.insert(5, 'proprietary:careers')
                items.insert(5, 'proprietary:startup_checkout')
                items.insert(6, 'proprietary:blog_index')
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


class SupportView(TemplateView):
    pass


class ClearSignupTrackingView(View):
    """Clear the signup tracking cookie."""

    def get(self, request, *args, **kwargs):
        # Clear the signup tracking cookie
        response = JsonResponse({})

        if 'show_signup_tracking' in request.session:
            del request.session['show_signup_tracking']

        for key in ('signup_event_id', 'signup_user_id', 'signup_email_hash'):
            if key in request.session:
                del request.session[key]

        return response
