import uuid
from datetime import timezone, datetime

from django.core.mail import send_mail
from django.http.response import JsonResponse
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils.http import urlencode
from django.views.generic import TemplateView, RedirectView, View
from django.http import HttpResponse, Http404
from django.utils.decorators import method_decorator
from django.views.decorators.vary import vary_on_cookie
from django.shortcuts import redirect
from django.urls import reverse
from django.http import HttpResponseRedirect
from django.db.models import F
from .models import LandingPage
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from api.models import PaidPlanIntent

import stripe
from djstripe.models import Customer, Subscription, Price
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.payments_helper import PaymentsHelper
from util.subscription_helper import get_or_create_stripe_customer, get_user_plan
from .utils_markdown import load_page, get_prev_next, get_all_doc_pages
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
            initial['charter'] = self.request.session['agent_charter'].strip()
            context['default_charter'] = initial['charter']
            context['agent_charter_saved'] = True

        context['agent_charter_form'] = PersistentAgentCharterForm(
            initial=initial
        )

        # Examples data
        context["simple_examples"] = SIMPLE_EXAMPLES
        context["rich_examples"] = RICH_EXAMPLES

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

        user = request.user

        # 1️⃣  Get (or lazily create) the Stripe customer linked to this user
        customer = get_or_create_stripe_customer(user)

        price = 0.0
        try:
            price_object = Price.objects.get(id=settings.STRIPE_STARTUP_PRICE_ID)
            # unit_amount is in cents, convert to dollars
            if price_object.unit_amount is not None:
                price = price_object.unit_amount / 100
        except Price.DoesNotExist:
            logger.warning(f"Price with ID '{settings.STRIPE_STARTUP_PRICE_ID}' does not exist in dj-stripe.")
        except Exception as e:
            logger.error(f"An unexpected error occurred while fetching price: {e}")

        # 2️⃣  Kick off Checkout with the *existing* customer
        session = stripe.checkout.Session.create(
            customer=customer.id,                       # <-- key line
            success_url=f'{request.build_absolute_uri(reverse("console-home"))}?subscribe_success=1&p={price}',
            cancel_url=request.build_absolute_uri(reverse("pages:home")),
            mode="subscription",
            allow_promotion_codes=True,
            line_items=[
                {
                    "price": settings.STRIPE_STARTUP_PRICE_ID,
                    "quantity": 1,  # Fixed quantity for the base plan
                },
                {
                    "price": settings.STRIPE_STARTUP_ADDITIONAL_TASK_PRICE_ID,
                },
            ],
        )

        # 3️⃣  No need to sync anything here.  The webhook events
        #     (customer.subscription.created, invoice.paid, etc.)
        #     will hit your handler and use sub.customer.subscriber == user.

        return redirect(session.url)

class PricingView(TemplateView):
    template_name = "pricing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        authenticated = self.request.user.is_authenticated

        # When true, we'll say Upgrade for Startup plan
        startup_cta_text = "Choose Pro"

        if authenticated:
            # Check if the user has an active subscription
            try:
                plan = get_user_plan(self.request.user)

                if plan is not None and plan["id"] == "free":
                    startup_cta_text = "Upgrade to Pro"  # User is on free plan
                elif plan is not None and plan["id"] == "startup":
                    startup_cta_text = "Current Plan"
            except Exception:
                logger.exception("Error checking user plan; defaulting to standard Startup CTA")
                pass

        # Pricing cards data - new 3-tier structure
        context["pricing_plans"] = [
            {
                "name": "Free Tier",
                "price": 0,
                "price_label": "$0",
                "desc": "Actually useful free tier",
                "tasks": "100",
                "pricing_model": "",
                "highlight": False,
                "features": ["5 always-on agents", "30 day time limit for always-on agents", "Basic API access", "Community support", "Standard rate limits"],
                "cta": "Get started for free",
                "cta_url": "/accounts/login/",
            },
            {
                "name": "Pro",
                "price": 30,
                "price_label": "$30",
                "desc": "For growing businesses",
                "tasks": "500",
                "pricing_model": "Billed monthly",
                "highlight": True,
                "features": ["Unlimited always-on agents", "No time limit for always-on agents", "$0.10 per task beyond 500", "Priority support", "Higher rate limits",],
                "cta": startup_cta_text,
                "cta_url": reverse("pages:startup_checkout"),
            },
            {
                "name": "Enterprise",
                "price": None,
                "price_label": "Custom pricing",
                "desc": "For mission-critical needs",
                "tasks": "Custom",
                "pricing_model": "Tailored to your needs",
                "highlight": False,
                "features": ["Dedicated infrastructure", "Priority support", "SLA guarantees", "Custom integrations", "Dedicated account manager"],
                "cta": "Book a demo",
                "cta_url": "https://cal.com/andrew-gobii",
            },
        ]

        # Comparison table rows - updated for new tiers
        context["comparison_rows"] = [
            ["Tasks included per month", "100", "500", "Custom"],
            ["Cost per additional task", "—", "$0.10", "Custom"],
            ["API rate limit (requests/min)", "60", "600", "Custom"],
            ["Priority task execution", "—", "✓", "✓"],
            ["Dedicated infrastructure", "—", "—", "✓"],
            ["SLA guarantee", "—", "—", "✓"],
            ["Support", "Community", "Email", "Dedicated"],
        ]

        # FAQs
        context["faqs"] = [
            (
                "What is a task?",
                "A task is a single automation job submitted to Gobii. Tasks can vary in length and complexity, but each submission counts as one task against your quota.",
            ),
            (
                "How does the pricing work?",
                "The Free tier includes 100 tasks per month. The Pro tier includes 500 tasks, then charges $0.10 for each additional task. Enterprise pricing is customized based on your needs.",
            ),
            (
                "Is there any commitment?",
                "No. You can use the free tier forever, and the Pro tier is purely pay-as-you-go with no monthly commitment.",
            ),
            (
                "What happens if I exceed my free tasks?",
                "On the Free tier, you'll need to wait until your next billing cycle to run more tasks. On the Pro tier, you'll automatically be charged $0.10 per additional task.",
            ),
            (
                "Do you offer enterprise features?",
                "Yes. Our Enterprise tier includes dedicated infrastructure, SLA guarantees, and custom integrations. Schedule a demo to learn more.",
            ),
        ]

        return context


class StaticViewSitemap(sitemaps.Sitemap):
    priority = 0.5
    changefreq = 'weekly'

    def items(self):
        # List of all static view names that should be included in the sitemap
        return [
            'pages:home',
            'pages:pricing',
            'pages:docs_index',
            'pages:tos',
            'pages:privacy',
            'pages:about',
            'pages:careers',
            'pages:startup_checkout',
        ]

    def location(self, item):
        return reverse(item)

class SupportView(TemplateView):
    """Static support page."""

    template_name = "support.html"

    def dispatch(self, request, *args, **kwargs):

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context

    def post(self, request, *args, **kwargs):
        # Get form data
        name = request.POST.get('name', '')
        email = request.POST.get('email', '')
        subject = request.POST.get('subject', '')
        message = request.POST.get('message', '')

        if not all([name, email, subject, message]):
            return HttpResponse(
                '<div class="p-4 mb-4 text-sm text-red-700 bg-red-100 rounded-lg" role="alert">Please fill in all fields.</div>',
                status=400
            )

        # Prepare email content
        context = {
            'name': name,
            'email': email,
            'subject': subject,
            'message': message,
        }

        html_message = render_to_string('emails/support_request.html', context)
        plain_message = strip_tags(html_message)

        # Send email
        try:
            send_mail(
                f'Support Request: {subject}',
                plain_message,
                settings.DEFAULT_FROM_EMAIL,
                [settings.SUPPORT_EMAIL],  # Use a support email address
                html_message=html_message,
                fail_silently=False,
            )

            # Return success message (for HTMX response)
            return HttpResponse(
                '<div class="p-4 mb-4 text-sm text-green-700 bg-green-100 rounded-lg" role="alert">'
                'Thank you for your message! We will get back to you soon.'
                '</div>'
            )

        except Exception as e:
            logger.error(f"Error sending support request email: {e}")

            # Return error message (for HTMX response)
            return HttpResponse(
                '<div class="p-4 mb-4 text-sm text-red-700 bg-red-100 rounded-lg" role="alert">'
                'Sorry, there was an error sending your message. Please try again later or contact us on Discord.'
                '</div>',
                status=500
            )


class ClearSignupTrackingView(View):
    """Clear the signup tracking cookie."""

    def get(self, request, *args, **kwargs):
        # Clear the signup tracking cookie
        response = JsonResponse({})

        if 'show_signup_tracking' in request.session:
            del request.session['show_signup_tracking']

        return response