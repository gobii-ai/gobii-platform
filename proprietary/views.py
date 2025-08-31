import logging

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.views.generic import TemplateView
from django.urls import reverse
from django.core.mail import send_mail

from util.subscription_helper import get_user_plan

logger = logging.getLogger(__name__)


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
                "features": [
                    "5 always-on agents",
                    "30 day time limit for always-on agents",
                    "Basic API access",
                    "Community support",
                    "Standard rate limits",
                ],
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
                "features": [
                    "Unlimited always-on agents",
                    "No time limit for always-on agents",
                    "$0.10 per task beyond 500",
                    "Priority support",
                    "Higher rate limits",
                ],
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
                "features": [
                    "Dedicated infrastructure",
                    "Priority support",
                    "SLA guarantees",
                    "Custom integrations",
                    "Dedicated account manager",
                ],
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


class SupportView(TemplateView):
    """Static support page."""

    template_name = "support.html"

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

