import logging
import json

from django.conf import settings
from django.contrib import sitemaps
from django.http import HttpResponse, Http404
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.utils.html import strip_tags, escape
from django.views.generic import TemplateView
from django.urls import reverse
from django.core.mail import send_mail

from proprietary.forms import SupportForm
from proprietary.utils_blog import load_blog_post, get_all_blog_posts
from util.subscription_helper import get_user_plan
from constants.plans import PlanNames
from config.plans import PLAN_CONFIG

logger = logging.getLogger(__name__)


class ProprietaryModeRequiredMixin:
    """Raise 404 when proprietary mode is disabled."""

    def dispatch(self, request, *args, **kwargs):
        if not settings.GOBII_PROPRIETARY_MODE:
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

class PricingView(ProprietaryModeRequiredMixin, TemplateView):
    template_name = "pricing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        authenticated = self.request.user.is_authenticated

        # When true, we'll say Upgrade for Startup plan
        startup_cta_text = "Choose Pro"
        scale_cta_text = "Choose Scale"
        startup_cta_disabled = False
        scale_cta_disabled = False
        startup_current = False
        scale_current = False

        if authenticated:
            # Check if the user has an active subscription
            try:
                plan = get_user_plan(self.request.user)
                plan_id = str(plan.get("id", "")).lower() if plan else ""

                if plan_id == PlanNames.FREE:
                    startup_cta_text = "Upgrade to Pro"
                    scale_cta_text = "Upgrade to Scale"
                elif plan_id == PlanNames.STARTUP:
                    startup_cta_text = "Current Plan"
                    scale_cta_text = "Upgrade to Scale"
                    startup_cta_disabled = True
                    startup_current = True
                elif plan_id == PlanNames.SCALE:
                    startup_cta_text = "Switch to Pro"
                    scale_cta_text = "Current Plan"
                    scale_cta_disabled = True
                    scale_current = True
            except Exception:
                logger.exception("Error checking user plan; defaulting to standard Startup CTA")
                pass

        def format_contacts(plan_name: str) -> str:
            """Return display-friendly per-plan contact cap."""
            limit = PLAN_CONFIG.get(plan_name, {}).get("max_contacts_per_agent")
            return f"{limit} contacts/agent" if limit is not None else "Contacts/agent: —"

        # Pricing cards data - new 3-tier structure
        context["pricing_plans"] = [
            {
                "name": "Free Tier",
                "price": 0,
                "price_label": "$0",
                "desc": "Actually useful free tier",
                "tasks": "100",
                "pricing_model": "Always free",
                "highlight": False,
                "badge": None,
                "disabled": False,
                "features": [
                    format_contacts(PlanNames.FREE),
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
                "price": 50,
                "price_label": "$50",
                "desc": "For growing teams",
                "tasks": "500",
                "pricing_model": "Billed monthly",
                "highlight": False,
                "badge": "Most teams",
                "disabled": False,
                "cta_disabled": startup_cta_disabled,
                "current_plan": startup_current,
                "features": [
                    format_contacts(PlanNames.STARTUP),
                    "Unlimited always-on agents",
                    "No time limit for always-on agents",
                    "$0.10 per task beyond 500",
                    "Priority support",
                    "Higher rate limits",
                ],
                "cta": startup_cta_text,
                "cta_url": reverse("proprietary:startup_checkout") if not startup_cta_disabled else "",
            },
            {
                "name": "Scale",
                "price": 250,
                "price_label": "$250",
                "desc": "For teams scaling fast",
                "tasks": "10,000",
                "pricing_model": "Billed monthly",
                "highlight": True,
                "badge": "Best value",
                "cta_disabled": scale_cta_disabled,
                "current_plan": scale_current,
                "features": [
                    format_contacts(PlanNames.SCALE),
                    "Unlimited always-on agents",
                    "Dedicated onboarding specialist",
                    "$0.04 per task beyond 10,000",
                    "Priority work queue",
                    "1,500 requests/min API throughput",
                ],
                "cta": scale_cta_text,
                "cta_url": reverse("proprietary:scale_checkout") if not scale_cta_disabled else "",
                "disabled": False,
            },
        ]

        # Plan limits pulled from plan configuration to keep the table in sync
        max_contacts_per_agent = [
            str(PLAN_CONFIG.get(PlanNames.FREE, {}).get("max_contacts_per_agent", "—")),
            str(PLAN_CONFIG.get(PlanNames.STARTUP, {}).get("max_contacts_per_agent", "—")),
            str(PLAN_CONFIG.get(PlanNames.SCALE, {}).get("max_contacts_per_agent", "—")),
        ]

        # Comparison table rows - updated for new tiers
        context["comparison_rows"] = [
            ["Tasks included per month", "100", "500", "10,000"],
            ["Cost per additional task", "—", "$0.10", "$0.04"],
            ["API rate limit (requests/min)", "60", "600", "1,500"],
            ["Max contacts per agent", *max_contacts_per_agent],
            ["Priority task execution", "—", "✓", "✓"],
            ["Dedicated onboarding", "—", "—", "✓"],
            ["Batch scheduling & queueing", "—", "—", "✓"],
            ["Support", "Community", "Email & chat", "Dedicated channel"],
        ]

        # FAQs
        context["faqs"] = [
            (
                "What is a task?",
                "A task is a single automation job submitted to Gobii. Tasks can vary in length and complexity, but each submission counts as one task against your quota.",
            ),
            (
                "How does the pricing work?",
                "The Free tier includes 100 tasks per month. The Pro tier includes 500 tasks, then charges $0.10 for each additional task. The Scale tier includes 10,000 tasks with $0.04 pricing after that.",
            ),
            (
                "Is there any commitment?",
                "No. You can use the free tier forever, and the Pro tier is purely pay-as-you-go with no monthly commitment.",
            ),
            (
                "What happens if I exceed my free tasks?",
                "On the Free tier, you'll need to wait until your next billing cycle to run more tasks. On the Pro tier, additional tasks are $0.10 each, while Scale brings that down to $0.04 once you pass the included 10,000 tasks.",
            ),
            (
                "Do you offer enterprise features?",
                "Yes. We offer custom enterprise agreements with dedicated infrastructure, SLAs, and governance controls. Schedule a call and we'll tailor a plan to your team.",
            ),
        ]

        return context

class SupportView(ProprietaryModeRequiredMixin, TemplateView):
    """Static support page."""

    template_name = "support.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if not self.request.user.is_authenticated:
            context["support_form"] = SupportForm()

        return context

    def post(self, request, *args, **kwargs):
        form = SupportForm(request.POST)

        if not form.is_valid():
            errors = []
            for field_errors in form.errors.values():
                errors.extend(field_errors)

            error_items = "".join(f"<li>{escape(message)}</li>" for message in errors)
            error_html = (
                '<div class="p-4 mb-4 text-sm text-red-700 bg-red-100 rounded-lg" role="alert">'
                'Please correct the following errors:'
                f'<ul class="mt-2 list-disc list-inside text-red-700">{error_items}</ul>'
                '</div>'
            )
            return HttpResponse(error_html, status=400)

        # Prepare email content
        cleaned = form.cleaned_data.copy()
        cleaned.pop("turnstile", None)

        context = {
            'name': cleaned['name'],
            'email': cleaned['email'],
            'subject': cleaned['subject'],
            'message': cleaned['message'],
        }

        html_message = render_to_string('emails/support_request.html', context)
        plain_message = strip_tags(html_message)

        # Send email
        try:
            send_mail(
                f'Support Request: {cleaned['subject']}',
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

class BlogIndexView(ProprietaryModeRequiredMixin, TemplateView):
    template_name = "blog/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        posts = get_all_blog_posts()
        context["posts"] = posts

        seo_title = "Gobii Blog"
        seo_description = (
            "Updates from the Gobii team on pretrained workers, automation strategies, and product releases."
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

class BlogPostView(ProprietaryModeRequiredMixin, TemplateView):
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

class BlogSitemap(sitemaps.Sitemap):
    priority = 0.6
    changefreq = 'weekly'

    def items(self):
        return get_all_blog_posts()

    def location(self, item):
        return item["url"]

    def lastmod(self, item):
        return item.get("published_at")
