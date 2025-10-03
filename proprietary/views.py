import logging
import json

from django.conf import settings
from django.http import HttpResponse, Http404
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.utils.html import strip_tags
from django.views.generic import TemplateView
from django.urls import reverse
from django.core.mail import send_mail

from proprietary.utils_blog import load_blog_post, get_all_blog_posts
from util.subscription_helper import get_user_plan

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
                "cta_url": reverse("proprietary:startup_checkout"),
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


class SupportView(ProprietaryModeRequiredMixin, TemplateView):
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

class BlogIndexView(ProprietaryModeRequiredMixin, TemplateView):
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
