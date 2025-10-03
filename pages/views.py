import json
import uuid
from datetime import timezone, datetime

from django.core.mail import send_mail
from django.http.response import JsonResponse
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.utils.html import strip_tags
from django.utils.http import urlencode
from django.views.generic import TemplateView, RedirectView, View
from django.http import HttpResponse, Http404
from django.utils.decorators import method_decorator
from django.views.decorators.vary import vary_on_cookie
from django.shortcuts import redirect
from django.urls import reverse
from django.http import HttpResponseRedirect
from django.db.models import F, Q
from .models import LandingPage
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from api.models import PaidPlanIntent, PersistentAgent
from api.agent.short_description import build_listing_description
from agents.services import AIEmployeeTemplateService
from waffle import flag_is_active
from api.models import OrganizationMembership
from config.stripe_config import get_stripe_settings

import stripe
from djstripe.models import Customer, Subscription, Price
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.payments_helper import PaymentsHelper
from util.subscription_helper import get_or_create_stripe_customer, get_user_plan
from .utils_markdown import (
    load_page,
    get_prev_next,
    get_all_doc_pages,
    load_blog_post,
    get_all_blog_posts,
)
from .examples_data import SIMPLE_EXAMPLES, RICH_EXAMPLES
from django.contrib import sitemaps
from django.urls import reverse
from django.utils.html import escape
from opentelemetry import trace
import logging
logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

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
                    '<span class="bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">'
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

        # Featured AI employee templates for homepage
        homepage_templates = AIEmployeeTemplateService.get_active_templates().filter(
            show_on_homepage=True
        ).order_by('priority')[:3]

        templates_list = list(homepage_templates)
        tool_names = set()

        for template in templates_list:
            template.schedule_description = AIEmployeeTemplateService.describe_schedule(template.base_schedule)
            tool_names.update(template.default_tools or [])

        tool_display_map = AIEmployeeTemplateService.get_tool_display_map(tool_names)

        for template in templates_list:
            template.display_default_tools = AIEmployeeTemplateService.get_tool_display_list(
                template.default_tools or [],
                display_map=tool_display_map,
            )

        context["homepage_templates"] = templates_list

        if self.request.user.is_authenticated:
            recent_agents_qs = PersistentAgent.objects.filter(user_id=self.request.user.id)
            total_agents = recent_agents_qs.count()
            recent_agents = list(recent_agents_qs.order_by('-updated_at')[:3])

            for agent in recent_agents:
                schedule_text = None
                if agent.schedule:
                    schedule_text = AIEmployeeTemplateService.describe_schedule(agent.schedule)
                    if not schedule_text:
                        schedule_text = agent.schedule
                agent.display_schedule = schedule_text

                description, source = build_listing_description(agent, max_length=140)
                agent.listing_description = description
                agent.listing_description_source = source
                agent.is_initializing = source == "placeholder"

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
                    login_url=settings.LOGIN_URL
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


class AIEmployeeDirectoryView(TemplateView):
    template_name = "ai_directory/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        templates_queryset = AIEmployeeTemplateService.get_active_templates()

        category = self.request.GET.get('category', '').strip()
        search = self.request.GET.get('q', '').strip()

        if category:
            templates_queryset = templates_queryset.filter(category__iexact=category)

        if search:
            templates_queryset = templates_queryset.filter(
                Q(display_name__icontains=search)
                | Q(tagline__icontains=search)
                | Q(description__icontains=search)
            )

        templates = list(templates_queryset)
        tool_names = set()

        for template in templates:
            template.schedule_description = AIEmployeeTemplateService.describe_schedule(template.base_schedule)
            tool_names.update(template.default_tools or [])

        tool_display_map = AIEmployeeTemplateService.get_tool_display_map(tool_names)

        for template in templates:
            template.display_default_tools = AIEmployeeTemplateService.get_tool_display_list(
                template.default_tools or [],
                display_map=tool_display_map,
            )

        all_categories = (
            AIEmployeeTemplateService.get_active_templates()
            .exclude(category__isnull=True)
            .exclude(category__exact="")
            .values_list('category', flat=True)
            .distinct()
            .order_by('category')
        )

        context.update(
            {
                "ai_employees": templates,
                "categories": list(all_categories),
                "selected_category": category,
                "search_term": search,
            }
        )
        return context


class AIEmployeeDetailView(TemplateView):
    template_name = "ai_directory/detail.html"

    def dispatch(self, request, *args, **kwargs):
        self.employee = AIEmployeeTemplateService.get_template_by_code(kwargs.get('slug'))
        if not self.employee:
            raise Http404("This AI employee is no longer available.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["ai_employee"] = self.employee
        context["schedule_jitter_minutes"] = self.employee.schedule_jitter_minutes
        context["base_schedule"] = self.employee.base_schedule
        context["schedule_description"] = AIEmployeeTemplateService.describe_schedule(self.employee.base_schedule)
        display_map = AIEmployeeTemplateService.get_tool_display_map(self.employee.default_tools or [])
        context["event_triggers"] = self.employee.event_triggers or []
        context["default_tools"] = AIEmployeeTemplateService.get_tool_display_list(
            self.employee.default_tools or [],
            display_map=display_map,
        )
        context["contact_method_label"] = AIEmployeeTemplateService.describe_contact_channel(
            self.employee.recommended_contact_channel
        )
        return context


class AIEmployeeHireView(View):
    def post(self, request, *args, **kwargs):
        code = kwargs.get('slug')
        template = AIEmployeeTemplateService.get_template_by_code(code)
        if not template:
            raise Http404("This AI employee is no longer available.")

        request.session['agent_charter'] = template.charter
        request.session[AIEmployeeTemplateService.TEMPLATE_SESSION_KEY] = template.code
        request.session['agent_charter_source'] = 'template'
        request.session.modified = True

        if request.user.is_authenticated:
            Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.PERSISTENT_AGENT_CHARTER_SUBMIT,
                source=AnalyticsSource.WEB,
                properties={
                    "source_page": "ai_employee_directory",
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
                "source_page": "ai_employee_directory",
                "template_code": template.code,
            },
        )

        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(
            next=reverse('agent_create_contact'),
            login_url=settings.LOGIN_URL,
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
        # 3  Add/overwrite our own tracking code
        params['g'] = code

        # 4  Re-encode the combined query string
        query_string = urlencode(params, doseq=True)

        # 5  Redirect to the canonical homepage + merged params
        target_url = f"{reverse('pages:home')}?{query_string}" if query_string else reverse('pages:home')

        response = HttpResponseRedirect(target_url)

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


class BlogIndexView(TemplateView):
    template_name = "blog/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        posts = get_all_blog_posts()
        context["posts"] = posts

        seo_title = "Gobii Blog"
        seo_description = (
            "Updates from the Gobii team on AI employees, automation strategies, and product releases."
        )

        canonical_url = self.request.build_absolute_uri(self.request.path)
        default_image_url = self.request.build_absolute_uri(static("images/noBgBlue.png"))

        blog_posts_schema = []
        for post in posts[:10]:
            entry = {
                "@type": "BlogPosting",
                "headline": post["title"],
                "url": self.request.build_absolute_uri(post["url"]),
            }
            published_at = post.get("published_at")
            if published_at:
                iso_value = published_at.isoformat()
                entry["datePublished"] = iso_value
                entry["dateModified"] = iso_value
            blog_posts_schema.append(entry)

        structured_data = {
            "@context": "https://schema.org",
            "@type": "Blog",
            "name": seo_title,
            "description": seo_description,
            "url": canonical_url,
            "publisher": {
                "@type": "Organization",
                "name": "Gobii",
                "logo": {
                    "@type": "ImageObject",
                    "url": default_image_url,
                },
            },
            "blogPost": blog_posts_schema,
        }

        context.update(
            {
                "seo_title": seo_title,
                "seo_description": seo_description,
                "canonical_url": canonical_url,
                "og_image_url": default_image_url,
                "structured_data_json": json.dumps(structured_data, ensure_ascii=False),
            }
        )

        return context


class BlogPostView(TemplateView):
    template_name = "blog/detail.html"

    def get_context_data(self, **kwargs):
        slug = self.kwargs["slug"].rstrip("/")
        try:
            post = load_blog_post(slug)
        except FileNotFoundError:
            raise Http404(f"Blog post not found: {slug}")

        context = super().get_context_data(**kwargs)
        canonical_url = self.request.build_absolute_uri(self.request.path)
        default_image_url = self.request.build_absolute_uri(static("images/noBgBlue.png"))

        image_path = post["meta"].get("image")
        if image_path:
            og_image_url = image_path if image_path.startswith("http") else self.request.build_absolute_uri(image_path)
        else:
            og_image_url = default_image_url

        seo_title = post["meta"].get("seo_title") or post["meta"].get("title") or slug.replace("-", " ").title()
        seo_description = (
            post["meta"].get("seo_description")
            or post["meta"].get("description")
            or post.get("summary")
            or "Read the latest update from the Gobii team."
        )

        published_at = post.get("published_at")
        published_iso = published_at.isoformat() if published_at else None
        author_name = post["meta"].get("author")
        if author_name:
            author_type = post["meta"].get("author_type")
            if not author_type:
                lowered = str(author_name).lower()
                author_type = "Organization" if "team" in lowered or "gobii" in lowered else "Person"
        else:
            author_name = "Gobii"
            author_type = "Organization"

        structured_data = {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": seo_title,
            "description": seo_description,
            "author": {
                "@type": author_type,
                "name": author_name,
            },
            "publisher": {
                "@type": "Organization",
                "name": "Gobii",
                "logo": {
                    "@type": "ImageObject",
                    "url": default_image_url,
                },
            },
            "mainEntityOfPage": {
                "@type": "WebPage",
                "@id": canonical_url,
            },
            "image": og_image_url,
            "url": canonical_url,
        }

        if published_iso:
            structured_data["datePublished"] = published_iso
            structured_data["dateModified"] = published_iso

        recent_posts = [p for p in get_all_blog_posts() if p["slug"] != post["slug"]][:3]

        context.update(
            {
                "post": post,
                "seo_title": seo_title,
                "seo_description": seo_description,
                "canonical_url": canonical_url,
                "og_image_url": og_image_url,
                "recent_posts": recent_posts,
                "structured_data_json": json.dumps(structured_data, ensure_ascii=False),
            }
        )

        return context


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
        stripe.api_key = PaymentsHelper.get_stripe_key()
        stripe_settings = get_stripe_settings()

        user = request.user

        # 1️⃣  Get (or lazily create) the Stripe customer linked to this user
        customer = get_or_create_stripe_customer(user)

        price = 0.0
        try:
            price_object = Price.objects.get(id=stripe_settings.startup_price_id)
            # unit_amount is in cents, convert to dollars
            if price_object.unit_amount is not None:
                price = price_object.unit_amount / 100
        except Price.DoesNotExist:
            logger.warning(f"Price with ID '{stripe_settings.startup_price_id}' does not exist in dj-stripe.")
        except Exception as e:
            logger.error(f"An unexpected error occurred while fetching price: {e}")

        # 2️⃣  Kick off Checkout with the *existing* customer
        session = stripe.checkout.Session.create(
            customer=customer.id,                       # <-- key line
            api_key=stripe.api_key,
            success_url=f'{request.build_absolute_uri(reverse("console-home"))}?subscribe_success=1&p={price}',
            cancel_url=request.build_absolute_uri(reverse("pages:home")),
            mode="subscription",
            allow_promotion_codes=True,
            line_items=[
                {
                    "price": stripe_settings.startup_price_id,
                    "quantity": 1,  # Fixed quantity for the base plan
                },
                {
                    "price": stripe_settings.startup_additional_task_price_id,
                },
            ],
        )

        # 3️⃣  No need to sync anything here.  The webhook events
        #     (customer.subscription.created, invoice.paid, etc.)
        #     will hit your handler and use sub.customer.subscriber == user.

        return redirect(session.url)

class PricingView(TemplateView):
    pass


class BlogSitemap(sitemaps.Sitemap):
    priority = 0.6
    changefreq = 'weekly'

    def items(self):
        return get_all_blog_posts()

    def location(self, item):
        return item["url"]

    def lastmod(self, item):
        return item.get("published_at")


class StaticViewSitemap(sitemaps.Sitemap):
    priority = 0.5
    changefreq = 'weekly'

    def items(self):
        # List of all static view names that should be included in the sitemap
        items = [
            'pages:home',
            'pages:blog_index',
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
        except Exception:
            pass
        return items

    def location(self, item):
        return reverse(item)

class SupportView(TemplateView):
    pass


class ClearSignupTrackingView(View):
    """Clear the signup tracking cookie."""

    def get(self, request, *args, **kwargs):
        # Clear the signup tracking cookie
        response = JsonResponse({})

        if 'show_signup_tracking' in request.session:
            del request.session['show_signup_tracking']

        return response
