import logging
import json
from smtplib import SMTPException
from email.utils import formataddr

from anymail.exceptions import AnymailAPIError
from billing.plan_resolver import get_active_public_plan_context, get_active_public_plan_monthly_task_credits
from django.conf import settings
from django.contrib import sitemaps
from django.http import HttpResponse, Http404, JsonResponse
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import strip_tags, escape
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, RedirectView
from django.core.mail import send_mail, BadHeaderError, EmailMultiAlternatives

from proprietary.forms import SupportForm, PrequalifyForm
from proprietary.utils_blog import extract_blog_faq_items, get_all_blog_posts, load_blog_post
from util.waffle_flags import is_waffle_flag_active
from util.subscription_helper import get_user_plan
from api.services.trial_abuse import evaluate_user_trial_eligibility, user_has_prior_individual_history
from constants.feature_flags import CTA_NO_CHARGE_DURING_TRIAL, CTA_PRICING_CANCEL_TEXT_UNDER_BTN, CTA_START_FREE_TRIAL, CTA_UNLOCK_AGENT_COPY, SUPPORT_INTERCOM
from util.trial_eligibility import is_user_trial_allowed_by_policy, is_user_trial_eligibility_enforcement_enabled, is_user_trial_eligibility_enforcement_one_per_user_enabled
from constants.plans import PlanNames
from config.plans import PLAN_CONFIG, get_plan_config
from config.stripe_config import get_stripe_settings
from waffle import flag_is_active, get_waffle_flag_model

logger = logging.getLogger(__name__)


def _keyword_list(value):
    if isinstance(value, str):
        return [keyword.strip() for keyword in value.split(",") if keyword.strip()]
    if isinstance(value, (list, tuple)):
        return [str(keyword).strip() for keyword in value if str(keyword).strip()]
    return []


def _blog_faq_items(value):
    if not isinstance(value, list):
        return []

    items = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        question = str(entry.get("question") or "").strip()
        answer = str(entry.get("answer") or "").strip()
        if question and answer:
            items.append({"question": question, "answer": answer})
    return items


def _blog_author_schema(meta, request):
    author_name = meta.get("author")
    if author_name:
        author_type = meta.get("author_type")
        if not author_type:
            lowered = str(author_name).lower()
            author_type = (
                "Organization"
                if "team" in lowered or "gobii" in lowered
                else "Person"
            )
    else:
        author_name = "Gobii"
        author_type = "Organization"

    author = {
        "@type": author_type,
        "name": author_name,
    }
    author_url = meta.get("author_url")
    if author_url:
        author["url"] = (
            author_url
            if str(author_url).startswith("http")
            else request.build_absolute_uri(str(author_url))
        )
    author_job_title = meta.get("author_job_title")
    if author_type == "Person" and author_job_title:
        author["jobTitle"] = str(author_job_title)
    return author


BLOG_INDEX_KEYWORDS = (
    "AI agent automation",
    "browser agents",
    "MCP integrations",
    "production AI safety",
    "persistent AI agents",
    "agent workflows",
)

BLOG_INDEX_FEATURED_POSTS = (
    ("how-we-sandbox-ai-agents-in-production", "Production safety"),
    ("newsletter-2026-06-09-browser-intelligence", "Browser agents"),
    ("newsletter-2026-05-19-remote-mcp", "MCP integrations"),
)

BLOG_INDEX_TOPIC_SECTIONS = (
    {
        "name": "Production safety",
        "description": (
            "Sandboxing, isolation, cost controls, and reliability patterns for agents "
            "that touch real systems."
        ),
        "slugs": (
            "how-we-sandbox-ai-agents-in-production",
            "turning-deepseek-into-real-work",
            "gobii-vs-openclaw",
        ),
    },
    {
        "name": "Browser agents and files",
        "description": (
            "How Gobii agents use browsers, logged-in sites, files, spreadsheets, and "
            "documents to finish work."
        ),
        "slugs": (
            "newsletter-2026-06-09-browser-intelligence",
            "newsletter-2025-07-28-gobii-now-supports-websites-that-need-logins-yeah-its-a-big-deal",
            "newsletter-2026-01-08-your-agents-can-now-read-and-create-files",
        ),
    },
    {
        "name": "MCP and integrations",
        "description": (
            "Connect Gobii to API tools, SaaS apps, webhooks, Discord, email, and "
            "MCP-compatible workflows."
        ),
        "slugs": (
            "newsletter-2026-05-19-remote-mcp",
            "newsletter-2026-04-08-inbound-webhooks",
            "newsletter-2026-06-02-discord-integration",
            "newsletter-2025-11-11-agents-just-got-way-more-connected-mcp-support-is-here",
            "newsletter-2026-03-17-one-click-integrations-for-your-agents",
        ),
    },
    {
        "name": "Reliability and operations",
        "description": (
            "Runtime reliability, usage controls, memory, reporting, and other details "
            "that make agent work predictable."
        ),
        "slugs": (
            "newsletter-2026-06-16-reliability-combo",
            "newsletter-2025-12-02-your-gobii-agent-now-lasts-longer",
            "newsletter-2025-10-12-by-popular-request-usage-reports-and-agent-budgeting-are-here",
        ),
    },
    {
        "name": "Collaboration and multi-agent work",
        "description": (
            "Agent handoffs, shared workspaces, team workflows, and ways to manage a "
            "fleet of always-on agents."
        ),
        "slugs": (
            "newsletter-2026-05-26-meta-gobii",
            "newsletter-2026-03-24-let-your-agents-pass-the-baton",
            "newsletter-2025-09-15-your-gobii-agent-just-unlocked-squad-mode",
        ),
    },
)


def _posts_by_slug(posts):
    return {post["slug"]: post for post in posts}


def _blog_index_featured_posts(posts):
    posts_by_slug = _posts_by_slug(posts)
    featured_posts = []
    for slug, label in BLOG_INDEX_FEATURED_POSTS:
        post = posts_by_slug.get(slug)
        if post:
            featured_posts.append({**post, "topic_label": label})
    return featured_posts


def _blog_index_topic_sections(posts):
    posts_by_slug = _posts_by_slug(posts)
    sections = []
    for section in BLOG_INDEX_TOPIC_SECTIONS:
        section_posts = [
            posts_by_slug[slug]
            for slug in section["slugs"]
            if slug in posts_by_slug
        ]
        if section_posts:
            sections.append(
                {
                    "name": section["name"],
                    "description": section["description"],
                    "posts": section_posts,
                }
            )
    return sections


class ProprietaryModeRequiredMixin:
    """Raise 404 when proprietary mode is disabled."""

    def dispatch(self, request, *args, **kwargs):
        if not settings.GOBII_PROPRIETARY_MODE:
            raise Http404()
        return super().dispatch(request, *args, **kwargs)


TEAM_START_URL = "/app/team"
SHIRT_REDIRECT_URL = "/?utm_source=shirt&utm_medium=clothing"


class ShirtRedirectView(ProprietaryModeRequiredMixin, RedirectView):
    url = SHIRT_REDIRECT_URL
    permanent = False
    query_string = True


def _coerce_plan_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_team_offer_context():
    team_plan_context = get_active_public_plan_context(PlanNames.ORG_TEAM, is_org=True) or {}
    team_config = get_plan_config(PlanNames.ORG_TEAM) or {}
    price_per_seat = _coerce_plan_int(
        team_config.get("price_per_seat") or team_config.get("price"),
        default=50,
    )
    credits_per_seat = _coerce_plan_int(
        team_plan_context.get("credits_per_seat") or team_config.get("credits_per_seat"),
        default=1000,
    )
    api_rate_limit = _coerce_plan_int(
        team_plan_context.get("api_rate_limit") or team_config.get("api_rate_limit"),
        default=2000,
    )
    max_contacts_per_agent = _coerce_plan_int(
        team_plan_context.get("max_contacts_per_agent") or team_config.get("max_contacts_per_agent"),
        default=50,
    )

    return {
        "price_per_seat": price_per_seat,
        "price_per_seat_display": f"${price_per_seat:,}",
        "credits_per_seat": credits_per_seat,
        "credits_per_seat_display": f"{credits_per_seat:,}",
        "example_ten_seat_credits_display": f"{credits_per_seat * 10:,}",
        "api_rate_limit": api_rate_limit,
        "api_rate_limit_display": f"{api_rate_limit:,}",
        "max_contacts_per_agent": max_contacts_per_agent,
        "max_contacts_per_agent_display": f"{max_contacts_per_agent:,}",
        "start_url": TEAM_START_URL,
        "signup_next": TEAM_START_URL,
    }


class TeamsView(ProprietaryModeRequiredMixin, TemplateView):
    template_name = "teams.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["team_offer"] = _get_team_offer_context()
        context["canonical_url"] = self.request.build_absolute_uri(self.request.path)
        return context


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
            enforcement_enabled = is_user_trial_eligibility_enforcement_enabled(self.request)
            one_per_user_enabled = is_user_trial_eligibility_enforcement_one_per_user_enabled(
                self.request
            )
            try:
                decision = None
                if enforcement_enabled:
                    result = evaluate_user_trial_eligibility(self.request.user)
                    decision = result.decision
                return is_user_trial_allowed_by_policy(
                    enforcement_enabled=enforcement_enabled,
                    one_per_user_enabled=one_per_user_enabled,
                    has_prior_individual_history=(
                        (lambda: user_has_prior_individual_history(self.request.user))
                        if one_per_user_enabled
                        else None
                    ),
                    request=self.request,
                    decision=decision,
                )
            except Exception:
                logger.warning(
                    "Failed to resolve trial eligibility; defaulting to no trial for user %s",
                    getattr(self.request.user, "id", None),
                    exc_info=True,
                )
                return False

        trial_eligible = _is_trial_eligible()
        cta_pricing_cancel_text_under_btn = is_waffle_flag_active(
            CTA_PRICING_CANCEL_TEXT_UNDER_BTN,
            self.request,
            default=False,
        )
        cta_start_free_trial = is_waffle_flag_active(
            CTA_START_FREE_TRIAL,
            self.request,
            default=False,
        )
        cta_unlock_agent_copy = is_waffle_flag_active(
            CTA_UNLOCK_AGENT_COPY,
            self.request,
            default=False,
        )
        cta_no_charge_during_trial = is_waffle_flag_active(
            CTA_NO_CHARGE_DURING_TRIAL,
            self.request,
            default=False,
        )
        def _trial_cta(days: int, label: str) -> str:
            if days > 0 and trial_eligible:
                if cta_unlock_agent_copy:
                    return "Start for Free"
                if cta_start_free_trial:
                    return "Start Free Trial"
                return f"Start {days}-day Free Trial"
            return f"Subscribe to {label}"

        def _trial_cancel_text(days: int, *, show_trial_copy: bool) -> str | None:
            if not show_trial_copy:
                return None
            if cta_unlock_agent_copy:
                return "No charge today. Cancel anytime."
            if not (cta_pricing_cancel_text_under_btn or cta_no_charge_during_trial):
                return None
            if cta_no_charge_during_trial:
                return f"No charge if you cancel during the {days}-day trial. Takes 30 seconds."
            return f"Cancel anytime during the {days}-day trial"

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

        startup_task_credits = get_active_public_plan_monthly_task_credits(PlanNames.STARTUP)
        scale_task_credits = get_active_public_plan_monthly_task_credits(PlanNames.SCALE)
        startup_task_credits_display = f"{startup_task_credits:,}"
        scale_task_credits_display = f"{scale_task_credits:,}"

        # Get plan prices from config (refreshed from StripeConfig)
        startup_config = get_plan_config(PlanNames.STARTUP) or {}
        scale_config = get_plan_config(PlanNames.SCALE) or {}
        team_offer = _get_team_offer_context()
        startup_price = startup_config.get("price", 50)
        scale_price = scale_config.get("price", 250)
        team_price = team_offer["price_per_seat"]

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
                f"$0.10 per task beyond {startup_task_credits_display}",
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
                f"$0.04 per task beyond {scale_task_credits_display}",
                "Priority work queue",
                "1,500 requests/min API throughput",
            ]
        )

        team_features = [
            f"{team_offer['credits_per_seat_display']} pooled task credits per seat",
            "Shared agents and private templates",
            "Shared credentials, integrations, API keys, and secrets",
            "Roles, invites, and team billing",
        ]

        startup_uses_trial_copy = startup_cta_text.startswith("Start ")
        scale_uses_trial_copy = scale_cta_text.startswith("Start ")

        pricing_plans = [
            {
                "code": PlanNames.STARTUP,
                "name": "Pro",
                "price": startup_price,
                "price_label": f"${startup_price}",
                "price_prefix": "$",
                "price_amount": startup_price,
                "desc": "For growing teams",
                "task_credits": startup_task_credits,
                "tasks": startup_task_credits_display,
                "pricing_model": _trial_pricing_model(startup_trial_days),
                "highlight": False,
                "badge": "Most teams",
                "disabled": False,
                "cta_disabled": startup_cta_disabled,
                "current_plan": startup_current,
                "trial_cancel_text": _trial_cancel_text(
                    startup_trial_days,
                    show_trial_copy=startup_uses_trial_copy,
                ),
                "features": startup_features,
                "cta": startup_cta_text,
                "cta_url": reverse("proprietary:startup_checkout") if not startup_cta_disabled else "",
                "cta_variant": "primary",
            },
            {
                "code": PlanNames.SCALE,
                "name": "Scale",
                "price": scale_price,
                "price_label": f"${scale_price}",
                "price_prefix": "$",
                "price_amount": scale_price,
                "desc": "For teams scaling fast",
                "task_credits": scale_task_credits,
                "tasks": scale_task_credits_display,
                "pricing_model": _trial_pricing_model(scale_trial_days),
                "highlight": True,
                "badge": "Best value",
                "cta_disabled": scale_cta_disabled,
                "current_plan": scale_current,
                "trial_cancel_text": _trial_cancel_text(
                    scale_trial_days,
                    show_trial_copy=scale_uses_trial_copy,
                ),
                "features": scale_features,
                "cta": scale_cta_text,
                "cta_url": reverse("proprietary:scale_checkout") if not scale_cta_disabled else "",
                "cta_variant": "primary",
                "disabled": False,
            },
            {
                "code": PlanNames.ORG_TEAM,
                "name": "Team",
                "price": team_price,
                "price_label": f"${team_price}",
                "price_prefix": "$",
                "price_amount": team_price,
                "desc": "For shared team workspaces",
                "task_credits": None,
                "tasks": None,
                "pricing_model": "per seat / month",
                "highlight": False,
                "badge": "New",
                "cta_disabled": False,
                "current_plan": False,
                "trial_cancel_text": None,
                "features": team_features,
                "cta": "Start a Team",
                "cta_url": team_offer["start_url"],
                "cta_variant": "primary",
                "analytics_intent": "start_team",
                "disabled": False,
            },
        ]

        pricing_plans.insert(
            0,
            {
                "code": "free_oss",
                "name": _("Free"),
                "price": 0,
                "price_label": "$0",
                "price_prefix": "$",
                "price_amount": 0,
                "desc": _("Self-Hosted Agents"),
                "tasks": None,
                "pricing_model": _("Self-hosted, open source"),
                "highlight": False,
                "badge": _("Open source"),
                "disabled": False,
                "cta_disabled": False,
                "current_plan": False,
                "trial_cancel_text": None,
                "features": [
                    _("Run on your own computer or server"),
                    _("Bring your own AI models"),
                    _("Always-on agents with browser automation"),
                    _("Open source and MIT licensed"),
                ],
                "cta": _("View on GitHub"),
                "cta_url": "https://github.com/gobii-ai/gobii-platform",
                "cta_icon": "github",
                "cta_variant": "outline",
                "external": True,
                "signup_modal": False,
                "analytics_cta_id": "pricing_free_oss_plan",
                "analytics_intent": "view_open_source",
            },
        )

        context["pricing_plans"] = pricing_plans
        context["pricing_grid_has_free_oss_plan"] = True
        context["team_offer"] = team_offer

        # Plan limits pulled from plan configuration to keep the table in sync
        max_contacts_per_agent = [
            str(PLAN_CONFIG.get(PlanNames.STARTUP, {}).get("max_contacts_per_agent", "—")),
            str(PLAN_CONFIG.get(PlanNames.SCALE, {}).get("max_contacts_per_agent", "—")),
            team_offer["max_contacts_per_agent_display"],
        ]

        # Comparison table rows - updated for new tiers
        context["comparison_plan_labels"] = ["Pro", "Scale", "Team"]
        context["comparison_rows"] = [
            [
                "Tasks included",
                f"{startup_task_credits_display}/month",
                f"{scale_task_credits_display}/month",
                f"{team_offer['credits_per_seat_display']} pooled/seat/month",
            ],
            ["Cost per additional task", "$0.10", "$0.04", "Metered overage available"],
            ["API rate limit (requests/min)", "600", "1,500", team_offer["api_rate_limit_display"]],
            ["Max contacts per agent", *max_contacts_per_agent],
            ["Agents never expire or turn off", "✓", "✓", "✓"],
            ["Priority task execution", "✓", "✓", "✓"],
            ["Batch scheduling & queueing", "—", "✓", "✓"],
            ["Shared agents and private templates", "—", "—", "✓"],
            ["Team roles and invites", "—", "—", "✓"],
            ["Support", "Email & chat", "Dedicated channel", "Priority support"],
        ]

        # FAQs
        context["faqs"] = [
            (
                "What is a task?",
                "A task is a single automation job submitted to Gobii. Tasks can vary in length and complexity, but each submission counts as one task against your quota.",
            ),
            (
                "How does the pricing work?",
                (
                    f"Pro includes {startup_task_credits_display} tasks per month, then charges "
                    f"$0.10 for each additional task. Scale includes {scale_task_credits_display} "
                    "tasks per month with $0.04 pricing after that. Team is "
                    f"{team_offer['price_per_seat_display']} per seat per month, and each seat adds "
                    f"{team_offer['credits_per_seat_display']} pooled task credits to the team workspace."
                ),
            ),
            (
                "Is there any commitment?",
                "No. Pro and Scale are month-to-month, and you can cancel before your trial ends to avoid charges.",
            ),
            (
                "What happens if I exceed my included tasks?",
                (
                    "On the Pro tier, additional tasks are $0.10 each, while Scale brings that "
                    f"down to $0.04 once you pass the included {scale_task_credits_display} tasks."
                ),
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

        recipient_email = settings.PUBLIC_CONTACT_EMAIL or settings.SUPPORT_EMAIL
        if not recipient_email:
            message = "Contact email is not configured."
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
                [recipient_email],
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
        context["support_intercom_enabled"] = self.is_intercom_mode(self.request)

        return context

    def is_intercom_mode(self, request) -> bool:
        if flag_is_active(request, SUPPORT_INTERCOM):
            return True

        if request.user.is_authenticated:
            return False

        # Support requests are often anonymous, so treat an authenticated
        # rollout as active for public support intake as well.
        flag_model = get_waffle_flag_model()
        try:
            support_intercom_flag = flag_model.objects.get(name=SUPPORT_INTERCOM)
        except flag_model.DoesNotExist:
            return False

        if support_intercom_flag.everyone is not None:
            return support_intercom_flag.everyone

        return bool(support_intercom_flag.authenticated)

    def get_recipient_email(self, *, intercom_mode: bool) -> str:
        if intercom_mode:
            return settings.INTERCOM_SUPPORT_EMAIL
        return settings.SUPPORT_EMAIL

    def _send_intercom_email_with_fallback(
        self,
        *,
        subject: str,
        message_body: str,
        recipient_email: str,
        sender_name: str,
        user_email: str,
    ) -> None:
        sender_address = formataddr((sender_name, user_email))
        reply_to_address = formataddr((sender_name, user_email))

        try:
            email = EmailMultiAlternatives(
                subject,
                message_body,
                sender_address,
                [recipient_email],
                reply_to=[reply_to_address],
            )
            email.send(fail_silently=False)
            return
        except (SMTPException, AnymailAPIError):
            logger.warning(
                "Support intercom send rejected user from address %s; retrying with default sender.",
                user_email,
                exc_info=True,
            )

        fallback_email = EmailMultiAlternatives(
            subject,
            message_body,
            settings.DEFAULT_FROM_EMAIL,
            [recipient_email],
            reply_to=[reply_to_address],
        )
        fallback_email.send(fail_silently=False)

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

        intercom_mode = self.is_intercom_mode(request)
        recipient_email = self.get_recipient_email(intercom_mode=intercom_mode)
        if not recipient_email:
            return HttpResponse(
                '<div class="p-4 mb-4 text-sm text-red-700 bg-red-100 rounded-lg" role="alert">'
                f"{escape(self.missing_recipient_message)}"
                "</div>",
                status=500,
            )

        if intercom_mode:
            subject = cleaned["subject"]
            message_body = cleaned["message"]
        else:
            html_message = render_to_string(self.email_template_name, context)
            message_body = strip_tags(html_message)
            subject = f"{self.email_subject_prefix}: {cleaned['subject']}"

        # Send email
        try:
            if intercom_mode:
                self._send_intercom_email_with_fallback(
                    subject=subject,
                    message_body=message_body,
                    recipient_email=recipient_email,
                    sender_name=cleaned["name"],
                    user_email=cleaned["email"],
                )
            else:
                send_mail(
                    subject=subject,
                    message=message_body,
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

        except (BadHeaderError, SMTPException, AnymailAPIError):
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

    def is_intercom_mode(self, request) -> bool:
        return False

    def get_recipient_email(self, *, intercom_mode: bool) -> str:
        return settings.PUBLIC_CONTACT_EMAIL


class BlogIndexView(ProprietaryModeRequiredMixin, TemplateView):
    template_name = "blog/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        posts = get_all_blog_posts()
        context["posts"] = posts
        context["featured_posts"] = _blog_index_featured_posts(posts)
        context["topic_sections"] = _blog_index_topic_sections(posts)

        blog_name = "Gobii AI Agent Automation Blog"
        seo_title = "AI Agent Automation, Browser Agents, and MCP Blog"
        seo_description = (
            "Read Gobii articles on always-on AI agents, browser automation, production safety, "
            "MCP integrations, persistent memory, and reliable agent workflows."
        )
        blog_intro = (
            "Guides, release notes, and engineering lessons for teams building with "
            "always-on AI agents, browser automation, MCP integrations, and production "
            "safety controls."
        )

        canonical_url = self.request.build_absolute_uri(self.request.path)
        brand_logo_path = "images/gobii_fish.png"
        default_image_path = "images/gobii_fish_social_1280x640.png"
        brand_logo_url = self.request.build_absolute_uri(static(brand_logo_path))
        default_image_url = self.request.build_absolute_uri(static(default_image_path))

        blog_posts_schema = []
        for post in posts:
            entry = {
                "@type": "BlogPosting",
                "headline": post["title"],
                "url": self.request.build_absolute_uri(post["url"]),
                "author": _blog_author_schema(post["meta"], self.request),
                "isPartOf": {
                    "@type": "Blog",
                    "name": blog_name,
                    "url": canonical_url,
                },
            }
            if post.get("summary"):
                entry["description"] = post["summary"]
            published_at = post.get("published_at")
            if published_at:
                entry["datePublished"] = published_at.isoformat()
            updated_at = post.get("updated_at") or published_at
            if updated_at:
                entry["dateModified"] = updated_at.isoformat()
            keywords = _keyword_list(post["meta"].get("keywords")) or _keyword_list(
                post["meta"].get("tags")
            )
            if keywords:
                entry["keywords"] = keywords
            blog_posts_schema.append(entry)

        structured_data = {
            "@context": "https://schema.org",
            "@type": "Blog",
            "name": blog_name,
            "headline": seo_title,
            "description": seo_description,
            "url": canonical_url,
            "inLanguage": "en-US",
            "keywords": list(BLOG_INDEX_KEYWORDS),
            "about": [
                {
                    "@type": "Thing",
                    "name": keyword,
                }
                for keyword in BLOG_INDEX_KEYWORDS
            ],
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
                "blog_heading": "AI Agent Automation Blog",
                "blog_intro": blog_intro,
                "canonical_url": canonical_url,
                "og_image_url": default_image_url,
                "og_image_alt": "Gobii AI agent automation blog",
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
        brand_logo_path = "images/gobii_fish.png"
        default_image_path = "images/gobii_fish_social_1280x640.png"
        default_image_alt = "Gobii logo"
        brand_logo_url = self.request.build_absolute_uri(static(brand_logo_path))
        default_image_url = self.request.build_absolute_uri(static(default_image_path))

        seo_title = post["meta"].get("seo_title") or post["meta"].get("title") or slug.replace("-", " ").title()
        browser_title = (
            seo_title
            if str(seo_title).rstrip().endswith((" | Gobii", " - Gobii"))
            else f"{seo_title} - Gobii"
        )
        seo_description = (
            post["meta"].get("seo_description")
            or post["meta"].get("description")
            or post.get("summary")
            or "Read the latest update from the Gobii team."
        )

        image_path = post["meta"].get("image")
        if image_path:
            og_image_url = image_path if image_path.startswith("http") else self.request.build_absolute_uri(image_path)
            og_image_alt = post.get("image_alt") or f"{seo_title} image"
        else:
            og_image_url = default_image_url
            og_image_alt = default_image_alt
        image_extension = og_image_url.split("?", 1)[0].lower().rsplit(".", 1)[-1]
        og_image_type = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
            "svg": "image/svg+xml",
        }.get(image_extension)

        keywords = _keyword_list(post["meta"].get("keywords")) or _keyword_list(post["meta"].get("tags"))
        faq_items = extract_blog_faq_items(post["html"])

        published_at = post.get("published_at")
        published_iso = published_at.isoformat() if published_at else None
        updated_at = post.get("updated_at") or published_at
        updated_iso = updated_at.isoformat() if updated_at else None
        author = _blog_author_schema(post["meta"], self.request)

        structured_data = {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": seo_title,
            "description": seo_description,
            "author": author,
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
            "thumbnailUrl": og_image_url,
            "url": canonical_url,
            "inLanguage": "en-US",
            "isPartOf": {
                "@type": "Blog",
                "name": "Gobii Blog",
                "url": self.request.build_absolute_uri(reverse("proprietary:blog_index")),
            },
        }

        if published_iso:
            structured_data["datePublished"] = published_iso
        if updated_iso:
            structured_data["dateModified"] = updated_iso
        if post.get("word_count"):
            structured_data["wordCount"] = post["word_count"]
        if keywords:
            structured_data["keywords"] = keywords

        if post["meta"].get("schema_graph"):
            site_url = self.request.build_absolute_uri("/")
            organization_id = f"{site_url}#organization"
            article_id = f"{canonical_url}#article"
            image_id = f"{canonical_url}#primaryimage"
            author_type = author["@type"]
            author_url = author.get("url")
            author_is_publisher = (
                author_type == "Organization"
                and str(author["name"]).casefold() == "gobii"
            )
            if author_is_publisher:
                author_id = organization_id
            elif author_url:
                author_fragment = (
                    "person" if author_type == "Person" else "organization"
                )
                author_id = f"{str(author_url).rstrip('/')}#{author_fragment}"
            else:
                author_id = f"{canonical_url}#author"

            article_schema = {
                key: value
                for key, value in structured_data.items()
                if key != "@context"
            }
            article_schema.update(
                {
                    "@id": article_id,
                    "author": {"@id": author_id},
                    "publisher": {"@id": organization_id},
                    "image": {"@id": image_id},
                }
            )

            author_schema = {
                "@type": author_type,
                "@id": author_id,
                "name": author["name"],
            }
            if author_type == "Person":
                author_schema["worksFor"] = {"@id": organization_id}
            if author_url:
                author_schema["url"] = author_url
            if author_type == "Person" and author.get("jobTitle"):
                author_schema["jobTitle"] = author["jobTitle"]
            if post["meta"].get("author_bio"):
                author_schema["description"] = post["meta"]["author_bio"]
            author_same_as = _keyword_list(post["meta"].get("author_same_as"))
            if author_same_as:
                author_schema["sameAs"] = author_same_as

            organization_schema = {
                "@type": "Organization",
                "@id": organization_id,
                "name": "Gobii",
                "url": site_url,
                "logo": {
                    "@type": "ImageObject",
                    "url": brand_logo_url,
                },
            }
            if author_is_publisher:
                if post["meta"].get("author_bio"):
                    organization_schema["description"] = post["meta"]["author_bio"]
                if author_same_as:
                    organization_schema["sameAs"] = author_same_as
            image_schema = {
                "@type": "ImageObject",
                "@id": image_id,
                "url": og_image_url,
                "contentUrl": og_image_url,
                "caption": og_image_alt,
            }
            if post["meta"].get("image_width"):
                image_schema["width"] = post["meta"]["image_width"]
            if post["meta"].get("image_height"):
                image_schema["height"] = post["meta"]["image_height"]

            breadcrumb_schema = {
                "@type": "BreadcrumbList",
                "@id": f"{canonical_url}#breadcrumb",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": 1,
                        "name": "Home",
                        "item": site_url,
                    },
                    {
                        "@type": "ListItem",
                        "position": 2,
                        "name": "Blog",
                        "item": self.request.build_absolute_uri(
                            reverse("proprietary:blog_index")
                        ),
                    },
                    {
                        "@type": "ListItem",
                        "position": 3,
                        "name": post["meta"].get("breadcrumb_title") or seo_title,
                        "item": canonical_url,
                    },
                ],
            }

            graph = [article_schema]
            if not author_is_publisher:
                graph.append(author_schema)
            graph.extend(
                [
                    organization_schema,
                    image_schema,
                    breadcrumb_schema,
                ]
            )
            if faq_items:
                graph.append(
                    {
                        "@type": "FAQPage",
                        "@id": f"{canonical_url}#faq",
                        "mainEntity": [
                            {
                                "@type": "Question",
                                "name": item["question"],
                                "acceptedAnswer": {
                                    "@type": "Answer",
                                    "text": item["answer"],
                                },
                            }
                            for item in faq_items
                        ],
                    }
                )
            structured_data = {
                "@context": "https://schema.org",
                "@graph": graph,
            }

        faq_items = _blog_faq_items(post["meta"].get("faq"))
        if faq_items:
            article_schema = dict(structured_data)
            article_schema.pop("@context", None)
            article_schema["@id"] = f"{canonical_url}#article"
            structured_data = {
                "@context": "https://schema.org",
                "@graph": [
                    article_schema,
                    {
                        "@type": "FAQPage",
                        "@id": f"{canonical_url}#faq",
                        "mainEntity": [
                            {
                                "@type": "Question",
                                "name": item["question"],
                                "acceptedAnswer": {
                                    "@type": "Answer",
                                    "text": item["answer"],
                                },
                            }
                            for item in faq_items
                        ],
                    },
                ],
            }

        recent_posts = [p for p in get_all_blog_posts() if p["slug"] != post["slug"]][:3]

        context.update(
            {
                "post": post,
                "seo_title": seo_title,
                "browser_title": browser_title,
                "seo_description": seo_description,
                "canonical_url": canonical_url,
                "og_image_url": og_image_url,
                "og_image_alt": og_image_alt,
                "og_image_type": og_image_type,
                "og_image_width": post["meta"].get("image_width"),
                "og_image_height": post["meta"].get("image_height"),
                "recent_posts": recent_posts,
                "structured_data_json": json.dumps(structured_data, ensure_ascii=False),
                "suppress_preline": True,
                "suppress_htmx": True,
                "suppress_public_conversion_assets": True,
                "suppress_phone_format_js": True,
                "suppress_rewardful_js": True,
                "suppress_stripe_js": True,
            }
        )

        return context

class BlogSitemap(sitemaps.Sitemap):
    priority = 0.6
    changefreq = 'weekly'

    def items(self):
        if not settings.GOBII_PROPRIETARY_MODE:
            return []
        return get_all_blog_posts()

    def location(self, item):
        return item["url"]

    def lastmod(self, item):
        return item.get("published_at")
