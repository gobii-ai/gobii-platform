import logging
import json
from smtplib import SMTPException

from django.conf import settings
from django.contrib import sitemaps
from django.http import HttpResponse, Http404, JsonResponse
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.utils.html import strip_tags, escape
from django.views.generic import TemplateView
from django.urls import reverse
from django.core.mail import send_mail, BadHeaderError, EmailMultiAlternatives

from proprietary.forms import SupportForm, PrequalifyForm
from proprietary.utils_blog import load_blog_post, get_all_blog_posts
from util.subscription_helper import (
    customer_has_any_individual_subscription,
    get_stripe_customer,
    get_user_plan,
)
from util.fish_collateral import is_fish_collateral_enabled
from constants.plans import PlanNames
from config.plans import PLAN_CONFIG, get_plan_config
from config.stripe_config import get_stripe_settings

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

        stripe_settings = get_stripe_settings()
        startup_trial_days = max(int(getattr(stripe_settings, "startup_trial_days", 0) or 0), 0)
        scale_trial_days = max(int(getattr(stripe_settings, "scale_trial_days", 0) or 0), 0)

        def _is_trial_eligible() -> bool:
            if not authenticated:
                return True
            try:
                customer = get_stripe_customer(self.request.user)
                if not customer or not getattr(customer, "id", None):
                    return True
                return not customer_has_any_individual_subscription(str(customer.id))
            except Exception:
                logger.warning(
                    "Failed to resolve trial eligibility; defaulting to no trial for user %s",
                    getattr(self.request.user, "id", None),
                    exc_info=True,
                )
                return False

        trial_eligible = _is_trial_eligible()

        def _trial_cta(days: int, label: str) -> str:
            if days > 0 and trial_eligible:
                return f"Start {days}-day Free Trial"
            return f"Subscribe to {label}"

        def _trial_pricing_model(days: int) -> str:
            if days > 0 and trial_eligible:
                return f"{days}-day free trial, then billed monthly"
            return "Billed monthly"

        if startup_trial_days > 0 or scale_trial_days > 0:
            context["trial_note"] = "Free trials are for first-time customers only."

        # When true, we'll say Upgrade for Startup plan
        startup_cta_text = _trial_cta(
            startup_trial_days,
            "Pro",
        )
        scale_cta_text = _trial_cta(
            scale_trial_days,
            "Scale",
        )
        startup_cta_disabled = False
        scale_cta_disabled = False
        startup_current = False
        scale_current = False

        current_plan_id = ""
        plan_id = ""
        if authenticated:
            # Check if the user has an active subscription
            try:
                plan = get_user_plan(self.request.user)
                plan_id = str(plan.get("id", "")).lower() if plan else ""
                current_plan_id = plan_id

                if plan_id == PlanNames.FREE:
                    startup_cta_text = _trial_cta(
                        startup_trial_days,
                        "Pro",
                    )
                    scale_cta_text = _trial_cta(
                        scale_trial_days,
                        "Scale",
                    )
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

        context["current_plan_id"] = current_plan_id
        context["current_plan_is_paid"] = current_plan_id in (PlanNames.STARTUP, PlanNames.SCALE)
        context["PlanNames"] = PlanNames

        def format_contacts(plan_name: str) -> str:
            """Return display-friendly per-plan contact cap."""
            limit = PLAN_CONFIG.get(plan_name, {}).get("max_contacts_per_agent")
            return f"{limit} contacts/agent" if limit is not None else "Contacts/agent: —"

        # Get plan prices from config (refreshed from StripeConfig)
        startup_config = get_plan_config(PlanNames.STARTUP) or {}
        scale_config = get_plan_config(PlanNames.SCALE) or {}
        startup_price = startup_config.get("price", 50)
        scale_price = scale_config.get("price", 250)

        # Pricing cards data - new 3-tier structure
        startup_features = []
        if startup_trial_days > 0 and trial_eligible:
            startup_features.append(f"{startup_trial_days}-day free trial")
        startup_features.extend(
            [
                format_contacts(PlanNames.STARTUP),
                "Unlimited always-on agents",
                "No time limit for always-on agents",
                "Agents never expire or turn off",
                "$0.10 per task beyond 500",
                "Priority support",
                "Higher rate limits",
            ]
        )

        scale_features = []
        if scale_trial_days > 0 and trial_eligible:
            scale_features.append(f"{scale_trial_days}-day free trial")
        scale_features.extend(
            [
                format_contacts(PlanNames.SCALE),
                "Unlimited always-on agents",
                "Agents never expire or turn off",
                "Highest intelligence levels available",
                "$0.04 per task beyond 10,000",
                "Priority work queue",
                "1,500 requests/min API throughput",
            ]
        )

        context["pricing_plans"] = [
            {
                "code": PlanNames.STARTUP,
                "name": "Pro",
                "price": startup_price,
                "price_label": f"${startup_price}",
                "desc": "For growing teams",
                "tasks": "500",
                "pricing_model": _trial_pricing_model(startup_trial_days),
                "highlight": False,
                "badge": "Most teams",
                "disabled": False,
                "cta_disabled": startup_cta_disabled,
                "current_plan": startup_current,
                "features": startup_features,
                "cta": startup_cta_text,
                "cta_url": reverse("proprietary:startup_checkout") if not startup_cta_disabled else "",
            },
            {
                "code": PlanNames.SCALE,
                "name": "Scale",
                "price": scale_price,
                "price_label": f"${scale_price}",
                "desc": "For teams scaling fast",
                "tasks": "10,000",
                "pricing_model": _trial_pricing_model(scale_trial_days),
                "highlight": True,
                "badge": "Best value",
                "cta_disabled": scale_cta_disabled,
                "current_plan": scale_current,
                "features": scale_features,
                "cta": scale_cta_text,
                "cta_url": reverse("proprietary:scale_checkout") if not scale_cta_disabled else "",
                "disabled": False,
            },
        ]

        # Plan limits pulled from plan configuration to keep the table in sync
        max_contacts_per_agent = [
            str(PLAN_CONFIG.get(PlanNames.STARTUP, {}).get("max_contacts_per_agent", "—")),
            str(PLAN_CONFIG.get(PlanNames.SCALE, {}).get("max_contacts_per_agent", "—")),
        ]

        # Comparison table rows - updated for new tiers
        context["comparison_rows"] = [
            ["Tasks included", "500/month", "10,000/month"],
            ["Cost per additional task", "$0.10", "$0.04"],
            ["API rate limit (requests/min)", "600", "1,500"],
            ["Max contacts per agent", *max_contacts_per_agent],
            ["Agents never expire or turn off", "✓", "✓"],
            ["Priority task execution", "✓", "✓"],
            ["Batch scheduling & queueing", "—", "✓"],
            ["Support", "Email & chat", "Dedicated channel"],
        ]

        # FAQs
        context["faqs"] = [
            (
                "What is a task?",
                "A task is a single automation job submitted to Gobii. Tasks can vary in length and complexity, but each submission counts as one task against your quota.",
            ),
            (
                "How does the pricing work?",
                "Pro includes 500 tasks per month, then charges $0.10 for each additional task. Scale includes 10,000 tasks per month with $0.04 pricing after that.",
            ),
            (
                "Is there any commitment?",
                "No. Pro and Scale are month-to-month, and you can cancel before your trial ends to avoid charges.",
            ),
            (
                "What happens if I exceed my included tasks?",
                "On the Pro tier, additional tasks are $0.10 each, while Scale brings that down to $0.04 once you pass the included 10,000 tasks.",
            ),
            (
                "Do you offer enterprise features?",
                "Yes. We offer custom enterprise agreements with dedicated infrastructure, SLAs, and governance controls. Schedule a call and we'll tailor a plan to your team.",
            ),
        ]

        return context

class PrequalifyView(ProprietaryModeRequiredMixin, TemplateView):
    """Pre-qualification intake page."""

    template_name = "prequalify.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", PrequalifyForm())
        return context

    @staticmethod
    def _wants_json(request) -> bool:
        accept = request.headers.get("accept", "")
        return "application/json" in accept or (
            request.content_type and "application/json" in request.content_type
        )

    @staticmethod
    def _parse_payload(request):
        if request.content_type and "application/json" in request.content_type:
            if not request.body:
                return {}, None
            try:
                payload = json.loads(request.body.decode("utf-8"))
            except json.JSONDecodeError:
                return None, "Invalid JSON payload."
            if not isinstance(payload, dict):
                return None, "Invalid JSON payload."
            return payload, None
        return request.POST, None

    @staticmethod
    def _format_form_errors(form: PrequalifyForm) -> list[str]:
        errors: list[str] = []
        for field, field_errors in form.errors.items():
            label = ""
            if field == "turnstile":
                label = "Verification"
            elif field in form.fields:
                label = form.fields[field].label or ""
            if not label:
                label = field.replace("_", " ").title()
            for error in field_errors:
                errors.append(f"{label}: {error}" if label else str(error))
        for error in form.non_field_errors():
            errors.append(str(error))
        return errors

    def post(self, request, *args, **kwargs):
        wants_json = self._wants_json(request)
        payload, payload_error = self._parse_payload(request)
        if payload_error:
            if wants_json:
                return JsonResponse({"ok": False, "message": payload_error}, status=400)
            context = self.get_context_data()
            context["error_messages"] = [payload_error]
            return self.render_to_response(context, status=400)

        form = PrequalifyForm(payload)
        if not form.is_valid():
            error_messages = self._format_form_errors(form)
            if wants_json:
                return JsonResponse({"ok": False, "errors": error_messages}, status=400)
            context = self.get_context_data()
            context["form"] = form
            context["error_messages"] = error_messages
            return self.render_to_response(context, status=400)

        support_email = settings.SUPPORT_EMAIL
        if not support_email:
            message = "Support email is not configured."
            if wants_json:
                return JsonResponse({"ok": False, "message": message}, status=500)
            context = self.get_context_data()
            context["error_messages"] = [message]
            return self.render_to_response(context, status=500)

        cleaned = form.cleaned_data.copy()
        cleaned.pop("turnstile", None)

        def _choice_label(field_name: str) -> str:
            field = form.fields.get(field_name)
            value = cleaned.get(field_name, "")
            if not field:
                return value
            return dict(field.choices).get(value, value)

        context = {
            "name": cleaned["name"],
            "email": cleaned["email"],
            "company": cleaned["company"],
            "role": cleaned["role"],
            "team_size": _choice_label("team_size"),
            "monthly_volume": _choice_label("monthly_volume"),
            "budget_range": _choice_label("budget_range"),
            "timeline": _choice_label("timeline"),
            "use_case": cleaned["use_case"],
            "website": cleaned.get("website"),
            "notes": cleaned.get("notes"),
            "referrer": request.META.get("HTTP_REFERER", ""),
            "page_url": request.build_absolute_uri(),
            "utm_source": request.COOKIES.get("utm_source") or request.GET.get("utm_source", ""),
            "utm_medium": request.COOKIES.get("utm_medium") or request.GET.get("utm_medium", ""),
            "utm_campaign": request.COOKIES.get("utm_campaign") or request.GET.get("utm_campaign", ""),
            "utm_content": request.COOKIES.get("utm_content") or request.GET.get("utm_content", ""),
            "utm_term": request.COOKIES.get("utm_term") or request.GET.get("utm_term", ""),
        }

        html_message = render_to_string("emails/prequal_request.html", context)
        plain_message = strip_tags(html_message)
        subject = f"Pre-qualification request: {cleaned['company'] or cleaned['name']}"

        try:
            email = EmailMultiAlternatives(
                subject,
                plain_message,
                settings.DEFAULT_FROM_EMAIL,
                [support_email],
                reply_to=[cleaned["email"]],
            )
            email.attach_alternative(html_message, "text/html")
            email.send(fail_silently=False)
        except (BadHeaderError, SMTPException) as exc:
            logger.exception("Error sending pre-qualification request email: %s", exc)
            message = "Sorry, there was an error sending your request. Please try again later."
            if wants_json:
                return JsonResponse({"ok": False, "message": message}, status=500)
            context = self.get_context_data()
            context["error_messages"] = [message]
            return self.render_to_response(context, status=500)

        success_message = (
            "Thanks for sharing the details. We will review and follow up within 1-2 business days."
        )
        if wants_json:
            return JsonResponse({"ok": True, "message": success_message})

        context = self.get_context_data()
        context["form"] = PrequalifyForm()
        context["success_message"] = success_message
        return self.render_to_response(context)

class SupportView(ProprietaryModeRequiredMixin, TemplateView):
    """Static support page."""

    template_name = "support.html"
    email_template_name = "emails/support_request.html"
    email_subject_prefix = "Support Request"
    missing_recipient_message = "Support email is not configured."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["support_form"] = SupportForm()

        return context

    def get_recipient_email(self) -> str:
        return settings.SUPPORT_EMAIL

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

        recipient_email = self.get_recipient_email()
        if not recipient_email:
            return HttpResponse(
                '<div class="p-4 mb-4 text-sm text-red-700 bg-red-100 rounded-lg" role="alert">'
                f"{escape(self.missing_recipient_message)}"
                "</div>",
                status=500,
            )

        html_message = render_to_string(self.email_template_name, context)
        plain_message = strip_tags(html_message)

        # Send email
        try:
            send_mail(
                subject=f"{self.email_subject_prefix}: {cleaned['subject']}",
                message=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient_email],
                html_message=html_message,
                fail_silently=False,
            )

            # Return success message (for HTMX response)
            return HttpResponse(
                '<div class="p-4 mb-4 text-sm text-green-700 bg-green-100 rounded-lg" role="alert">'
                'Thank you for your message! We will get back to you soon.'
                '</div>'
            )

        except (BadHeaderError, SMTPException):
            logger.exception("Error sending %s email.", self.email_subject_prefix.lower())

            # Return error message (for HTMX response)
            return HttpResponse(
                '<div class="p-4 mb-4 text-sm text-red-700 bg-red-100 rounded-lg" role="alert">'
                'Sorry, there was an error sending your message. Please try again later or contact us on Discord.'
                '</div>',
                status=500
            )


class ContactView(SupportView):
    """Contact page that reuses support request form handling."""

    template_name = "contact.html"
    email_template_name = "emails/contact_request.html"
    email_subject_prefix = "Contact Request"
    missing_recipient_message = "Contact email is not configured."

    def get_recipient_email(self) -> str:
        return settings.PUBLIC_CONTACT_EMAIL


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
        if is_fish_collateral_enabled():
            brand_logo_path = "images/gobii_fish.png"
            default_image_path = "images/gobii_fish_social_1280x640.png"
        else:
            brand_logo_path = "images/noBgBlue.png"
            default_image_path = "images/noBgBlue.png"
        brand_logo_url = self.request.build_absolute_uri(static(brand_logo_path))
        default_image_url = self.request.build_absolute_uri(static(default_image_path))

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
                    "url": brand_logo_url,
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
        if is_fish_collateral_enabled():
            brand_logo_path = "images/gobii_fish.png"
            default_image_path = "images/gobii_fish_social_1280x640.png"
        else:
            brand_logo_path = "images/noBgBlue.png"
            default_image_path = "images/noBgBlue.png"
        brand_logo_url = self.request.build_absolute_uri(static(brand_logo_path))
        default_image_url = self.request.build_absolute_uri(static(default_image_path))

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
                    "url": brand_logo_url,
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
