from datetime import timedelta
from urllib.parse import parse_qs, urlparse
import json
import re
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from bs4 import BeautifulSoup
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.contrib.sites.models import Site
from django.core import signing
from django.templatetags.static import static
from django.test import Client, RequestFactory, TestCase, modify_settings, override_settings, tag
from django.urls import reverse
from django.utils import timezone
from waffle.testutils import override_flag, override_switch
from api.models import (
    BrowserUseAgent,
    MCPServerConfig,
    PersistentAgent,
    UserBilling,
    UserFingerprintVisit,
    UserFingerprintVisitFetchStatusChoices,
    UserFlags,
)
from config.socialaccount_adapter import (
    OAUTH_ATTRIBUTION_COOKIE,
    OAUTH_CHARTER_COOKIE,
    OAUTH_CHARTER_SERVER_SIDE_TOKEN_KEY,
    build_oauth_charter_stash_cache_key,
)
from constants.feature_flags import (
    STRIPE_CHECKOUT_TOS_CONSENT_REQUIRED,
)
from pages import views as page_views
from pages.models import LandingPage
from agents.services import PretrainedWorkerTemplateService
from proprietary.utils_blog import get_all_blog_posts
from config.redis_client import get_redis_client
from billing.checkout_metadata import (
    STRIPE_CHECKOUT_CUSTOMER_FP_BOT_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_FP_COUNTRY_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_FP_PROXY_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_FP_SUSPECT_SCORE_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_FP_TAMPERING_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_FP_VISITOR_ID_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_CURRENCY_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_PLAN_LABEL_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_PLAN_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_SOURCE_URL_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_VALUE_META_KEY,
    STRIPE_CHECKOUT_FP_BOT_META_KEY,
    STRIPE_CHECKOUT_FP_COUNTRY_META_KEY,
    STRIPE_CHECKOUT_FP_PROXY_META_KEY,
    STRIPE_CHECKOUT_FP_SUSPECT_SCORE_META_KEY,
    STRIPE_CHECKOUT_FP_TAMPERING_META_KEY,
    STRIPE_CHECKOUT_FP_VISITOR_ID_META_KEY,
    clear_checkout_fingerprint_metadata,
    clear_checkout_customer_metadata,
)
from constants.plans import PlanNames
from constants.stripe import PERSONAL_CHECKOUT_PAYMENT_METHOD_TYPES
from api.services.pipedream_apps import PipedreamCatalogError
from util.onboarding import (
    TRIAL_ONBOARDING_PENDING_SESSION_KEY,
    TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY,
    TRIAL_ONBOARDING_TARGET_AGENT_UI,
    TRIAL_ONBOARDING_TARGET_API_KEYS,
    TRIAL_ONBOARDING_TARGET_SESSION_KEY,
)
from util.personal_signup_preview import (
    GENERIC_STARTER_CHARTER,
)
from util.analytics import AnalyticsEvent, AnalyticsSource


@tag("batch_pages")
class HomePageTests(TestCase):
    @staticmethod
    def _normalized_button_text(button) -> str:
        return " ".join(
            segment for segment in button.stripped_strings if segment and segment != "→"
        ).strip()

    def test_home_page_renders(self):
        """Basic smoke test for home page."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "preline.min.js")
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        main_landmarks = soup.find_all("main")
        self.assertEqual(len(main_landmarks), 1)
        self.assertEqual(main_landmarks[0].get("id"), "main-content")

    @override_settings(
        PUBLIC_BRAND_NAME="Acme",
        PUBLIC_SITE_URL="https://gobii.ai",
        GOBII_RELEASE_ENV="prod",
        GOBII_PROPRIETARY_MODE=True,
    )
    def test_home_page_includes_social_metadata(self):
        response = self.client.get("/", HTTP_HOST="preview.local")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        title = "Acme - Enter a Job Description. Get Qualified Candidates."
        description = (
            "Paste a job description into Acme and get qualified candidates sourced, "
            "researched, and organized for recruiter review."
        )
        image_url = "https://gobii.ai/static/images/gobii_og_image_1200x630.png"

        self.assertEqual(
            soup.find("link", rel="canonical")["href"],
            "https://gobii.ai/",
        )
        self.assertEqual(
            soup.find("meta", attrs={"name": "description"})["content"],
            description,
        )
        self.assertEqual(soup.find("meta", property="og:type")["content"], "website")
        self.assertEqual(soup.find("meta", property="og:locale")["content"], "en_US")
        self.assertEqual(soup.find("meta", property="og:title")["content"], title)
        self.assertEqual(soup.find("meta", property="og:description")["content"], description)
        self.assertEqual(soup.find("meta", property="og:url")["content"], "https://gobii.ai/")
        self.assertEqual(soup.find("meta", property="og:site_name")["content"], "Acme")
        self.assertEqual(soup.find("meta", property="og:image")["content"], image_url)
        self.assertEqual(soup.find("meta", property="og:image:type")["content"], "image/png")
        self.assertEqual(soup.find("meta", property="og:image:width")["content"], "1200")
        self.assertEqual(soup.find("meta", property="og:image:height")["content"], "630")
        self.assertEqual(
            soup.find("meta", property="og:image:alt")["content"],
            "Acme qualified candidate sourcing platform preview",
        )
        self.assertEqual(
            soup.find("meta", attrs={"name": "twitter:card"})["content"],
            "summary_large_image",
        )
        self.assertEqual(soup.find("meta", attrs={"name": "twitter:title"})["content"], title)
        self.assertEqual(
            soup.find("meta", attrs={"name": "twitter:description"})["content"],
            description,
        )
        self.assertEqual(soup.find("meta", attrs={"name": "twitter:image"})["content"], image_url)
        self.assertEqual(
            soup.find("meta", attrs={"name": "twitter:image:alt"})["content"],
            "Acme qualified candidate sourcing platform preview",
        )

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_home_page_omits_social_metadata_in_community_mode(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")

        self.assertIsNone(soup.find("meta", property="og:image"))
        self.assertIsNone(soup.find("meta", property="og:title"))
        self.assertIsNone(soup.find("meta", attrs={"name": "twitter:card"}))
        self.assertIsNone(soup.find("meta", attrs={"name": "twitter:image"}))

    @override_settings(
        PUBLIC_SITE_URL="https://gobii.ai",
        GOBII_RELEASE_ENV="prod",
        GOBII_PROPRIETARY_MODE=True,
    )
    def test_home_page_landing_render_omits_generic_canonical(self):
        landing = LandingPage.objects.create(
            charter="Find vendor security updates every morning.",
            title="Vendor Security Watch",
        )

        response = self.client.get("/", {"g": landing.code}, HTTP_HOST="preview.local")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")

        self.assertIsNone(soup.find("link", rel="canonical"))
        self.assertEqual(
            soup.find("meta", attrs={"name": "robots"})["content"],
            "noindex, follow",
        )
        self.assertEqual(
            soup.find("meta", property="og:title")["content"],
            "Vendor Security Watch - Gobii",
        )

    def test_home_page_organization_schema_uses_configured_linkedin_url(self):
        linkedin_url = "https://www.linkedin.com/company/example-ai"

        with override_settings(PUBLIC_LINKEDIN_URL=linkedin_url):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        schemas = [
            json.loads(script.string)
            for script in soup.find_all("script", {"type": "application/ld+json"})
        ]
        organization_schema = next(
            schema for schema in schemas if schema.get("@id", "").endswith("/#organization")
        )

        self.assertIn(linkedin_url, organization_schema["sameAs"])
        self.assertNotIn("https://www.linkedin.com/company/gobii-ai", organization_schema["sameAs"])

    def test_home_page_organization_schema_omits_empty_linkedin_url(self):
        with override_settings(PUBLIC_LINKEDIN_URL=""):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        schemas = [
            json.loads(script.string)
            for script in soup.find_all("script", {"type": "application/ld+json"})
        ]
        organization_schema = next(
            schema for schema in schemas if schema.get("@id", "").endswith("/#organization")
        )

        self.assertNotIn("https://www.linkedin.com/company/gobii-ai", organization_schema["sameAs"])

    def test_home_page_charter_textarea_has_hidden_accessible_label(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        textarea = soup.find("textarea", {"name": "charter"})
        self.assertIsNotNone(textarea)
        label = soup.find("label", {"for": textarea.get("id")})
        self.assertIsNotNone(label)
        self.assertIn("sr-only", label.get("class", []))
        self.assertEqual(label.get_text(strip=True), "Describe what you want your Gobii to do")

    def test_home_page_form_controls_have_accessible_names(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        ignored_input_types = {"hidden", "submit", "button", "image", "reset"}
        controls = [
            control
            for control in soup.find_all(["input", "textarea", "select"])
            if (control.get("type") or "").lower() not in ignored_input_types
        ]

        self.assertGreater(len(controls), 0)
        for control in controls:
            control_id = control.get("id")
            has_label = bool(control_id and soup.find("label", {"for": control_id}))
            has_wrapping_label = control.find_parent("label") is not None
            has_aria_name = bool(control.get("aria-label") or control.get("aria-labelledby"))
            with self.subTest(control=control.get("name") or control_id):
                self.assertTrue(has_label or has_wrapping_label or has_aria_name)

    def test_home_page_defers_csrf_token_for_passive_get(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(settings.CSRF_COOKIE_NAME, response.cookies)
        content = response.content.decode("utf-8")
        self.assertNotIn('name="csrfmiddlewaretoken"', content)

        soup = BeautifulSoup(content, "html.parser")
        lazy_post_forms = [
            form for form in soup.find_all("form")
            if (form.get("method") or "").lower() == "post"
        ]
        self.assertGreater(len(lazy_post_forms), 0)
        for form in lazy_post_forms:
            self.assertTrue(form.has_attr("data-lazy-csrf"))

    def test_home_page_defers_csrf_token_with_signup_modal_enabled(self):
        with override_flag("cta_signup_modal", active=True):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "gobii-cta-signup-modal-config")
        self.assertNotIn(settings.CSRF_COOKIE_NAME, response.cookies)
        self.assertNotIn(
            'name="csrfmiddlewaretoken"',
            response.content.decode("utf-8"),
        )

    def test_homepage_csrf_token_endpoint_sets_cookie_and_returns_token(self):
        response = self.client.get(reverse("pages:homepage_csrf_token"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)
        self.assertTrue(response.json().get("csrfToken"))

    def test_home_spawn_accepts_lazy_csrf_token_for_anonymous_user(self):
        client = Client(enforce_csrf_checks=True)
        csrf_response = client.get(reverse("pages:homepage_csrf_token"))
        csrf_token = csrf_response.json()["csrfToken"]

        response = client.post(
            reverse("pages:home_agent_spawn"),
            {
                "charter": "Custom charter",
                "csrfmiddlewaretoken": csrf_token,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response["Location"]).path, reverse("account_login"))

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_home_page_omits_stripe_js_without_checkout_cta(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "https://js.stripe.com/dahlia/stripe.js")
        self.assertNotContains(response, "https://js.stripe.com")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_home_page_includes_stripe_js_when_upgrade_checkout_cta_is_present(self):
        cache.clear()
        User = get_user_model()
        user = User.objects.create_user(
            username="home-at-capacity@example.com",
            email="home-at-capacity@example.com",
            password="password123",
        )
        for index in range(5):
            browser = BrowserUseAgent.objects.create(user=user, name=f"Browser {index}")
            PersistentAgent.objects.create(
                user=user,
                name=f"Agent {index}",
                charter="Test charter",
                browser_use_agent=browser,
            )
        self.client.force_login(user)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("proprietary:startup_checkout"))
        self.assertContains(response, "Upgrade Your Plan")
        self.assertContains(response, '<link rel="preconnect" href="https://js.stripe.com">')
        self.assertContains(response, "https://js.stripe.com/dahlia/stripe.js")

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_home_page_omits_stripe_js_in_community_mode(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "https://js.stripe.com/dahlia/stripe.js")

    def test_home_page_shows_fish_in_both_modes(self):
        """The Gobii fish mascot should render in both proprietary and community modes."""
        for proprietary_mode in (False, True):
            with self.subTest(proprietary_mode=proprietary_mode):
                with override_settings(GOBII_PROPRIETARY_MODE=proprietary_mode):
                    response = self.client.get("/")
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, 'data-gobii-fish-cursor')

    @override_settings(PUBLIC_BRAND_NAME="Acme")
    def test_home_page_has_meta_description(self):
        response = self.client.get("/")
        self.assertContains(
            response,
            '<meta name="description" content="Acme agents are virtual coworkers with their own identity, memory, and tools. Email them, text them — they browse the web, collect data, and deliver reports 24/7.">',
        )

    @override_settings(PUBLIC_BRAND_NAME="Acme", GOBII_PROPRIETARY_MODE=True)
    def test_home_page_has_proprietary_qualified_candidate_meta_description(self):
        response = self.client.get("/")
        self.assertContains(
            response,
            '<meta name="description" content="Paste a job description into Acme and get qualified candidates sourced, researched, and organized for recruiter review.">',
        )

    def test_home_page_does_not_render_signup_modal_shell_when_flag_is_off(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "gobii-cta-signup-modal-config")
        self.assertNotContains(response, 'id="cta-signup-modal"')

    def test_home_page_renders_signup_modal_shell_when_flag_is_on_for_anonymous_users(self):
        with override_flag("cta_signup_modal", active=True):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "gobii-cta-signup-modal-config")
        self.assertContains(response, 'id="cta-signup-modal"')

    def test_home_page_signup_modal_config_includes_analytics_events_when_flag_is_on(self):
        with override_flag("cta_signup_modal", active=True):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        modal_config = response.context.get("cta_signup_modal_config")
        self.assertIsNotNone(modal_config)
        self.assertEqual(modal_config["events"]["opened"], AnalyticsEvent.CTA_AUTH_MODAL_OPENED.value)
        self.assertEqual(modal_config["events"]["closed"], AnalyticsEvent.CTA_AUTH_MODAL_CLOSED.value)
        self.assertEqual(modal_config["events"]["step_viewed"], AnalyticsEvent.CTA_AUTH_MODAL_STEP_VIEWED.value)
        self.assertEqual(modal_config["events"]["email_routed"], AnalyticsEvent.CTA_AUTH_MODAL_EMAIL_ROUTED.value)
        self.assertEqual(modal_config["events"]["failed"], AnalyticsEvent.CTA_AUTH_MODAL_FAILED.value)

    @modify_settings(INSTALLED_APPS={"append": "turnstile"})
    @override_settings(
        TURNSTILE_ENABLED=True,
        ACCOUNT_FORMS={
            "signup": "turnstile_signup.SignupFormWithTurnstile",
            "login": "turnstile_signup.LoginFormWithTurnstile",
        },
    )
    def test_home_page_includes_turnstile_api_for_signup_modal_when_enabled(self):
        with override_flag("cta_signup_modal", active=True):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "turnstile/v0/api.js?render=explicit")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_home_page_uses_legacy_hero_illustration_when_fish_homepage_is_off(self):
        with override_flag("fish_homepage", active=False):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        legacy_hero_image = soup.find("img", {"src": "/static/images/undraw/texting.svg"})
        self.assertIsNotNone(legacy_hero_image)
        self.assertIsNone(soup.select_one("[data-gobii-fish-cursor]"))

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_home_page_uses_fish_hero_animation_when_fish_homepage_is_on(self):
        with override_flag("fish_homepage", active=True):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        self.assertIsNotNone(soup.select_one("[data-gobii-fish-cursor]"))
        self.assertIsNone(soup.find("img", {"src": "/static/images/undraw/texting.svg"}))

    def test_home_page_includes_perf_motion_reduction_when_switch_is_on(self):
        with override_switch("homepage_perf_motion_reduction", active=True):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("window.GobiiHomePerf = window.GobiiHomePerf ||", content)
        self.assertIn("window.GobiiHomePerf.runWhenIdle(initFishCursor, 1800)", content)
        self.assertNotIn("runWhenIdle(initScrollAnimations", content)
        self.assertIn("initScrollAnimations();", content)
        self.assertIn("@media (pointer: coarse), (max-width: 767px)", content)

    def test_home_page_omits_perf_motion_reduction_when_switch_is_off(self):
        with override_switch("homepage_perf_motion_reduction", active=False):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertNotIn("window.GobiiHomePerf = window.GobiiHomePerf ||", content)
        self.assertNotIn("window.GobiiHomePerf.runWhenIdle(initFishCursor, 1800)", content)
        self.assertNotIn("@media (pointer: coarse), (max-width: 767px)", content)
        self.assertIn("initFishCursor();", content)
        self.assertIn("initScrollAnimations();", content)

    def test_home_page_excludes_eval_agents(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="homeuser@example.com",
            email="homeuser@example.com",
            password="password123",
        )
        self.client.force_login(user)

        visible_browser = BrowserUseAgent.objects.create(user=user, name="Visible Browser")
        PersistentAgent.objects.create(
            user=user,
            name="Visible Agent",
            charter="Visible charter",
            browser_use_agent=visible_browser,
        )

        eval_browser = BrowserUseAgent.objects.create(user=user, name="Eval Browser")
        PersistentAgent.objects.create(
            user=user,
            name="Eval Agent",
            charter="Eval charter",
            browser_use_agent=eval_browser,
            execution_environment="eval",
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        recent_agents = response.context.get("recent_agents") or []
        names = {agent.name for agent in recent_agents}
        self.assertIn("Visible Agent", names)
        self.assertNotIn("Eval Agent", names)

    def test_home_page_recent_agents_view_all_opens_immersive_app(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="home-view-all@example.com",
            email="home-view-all@example.com",
            password="password123",
        )
        self.client.force_login(user)

        browser_agent = BrowserUseAgent.objects.create(user=user, name="Homepage Browser")
        PersistentAgent.objects.create(
            user=user,
            name="Homepage Agent",
            charter="Visible charter",
            browser_use_agent=browser_agent,
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        view_all_link = next(
            (
                anchor
                for anchor in soup.find_all("a")
                if "View all" in " ".join(anchor.stripped_strings)
            ),
            None,
        )

        self.assertIsNotNone(view_all_link)
        self.assertEqual(view_all_link.get("data-immersive-link"), "")

        parsed = urlparse(view_all_link["href"])
        self.assertEqual(parsed.path, "/app/agents")
        self.assertEqual(parse_qs(parsed.query).get("return_to"), ["/"])

    def test_home_page_omits_public_template_directory(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("homepage_pretrained_workers", response.context)
        self.assertNotContains(response, "Spawn a Pretrained Worker")
        self.assertNotContains(response, "Start with a candidate sourcing workflow")
        self.assertNotContains(response, 'name="source_page" value="home_pretrained_workers"')
        self.assertNotContains(response, 'href="/library/')
        self.assertNotContains(response, 'href="/pretrained-workers/')

    @patch("pages.views.get_homepage_integrations_payload", return_value={"enabled": False, "builtins": []})
    def test_home_page_hides_integrations_section_when_pipedream_is_disabled(self, _mock_integrations):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context.get("homepage_integrations_enabled"))
        self.assertNotContains(response, "Search more integrations")

    @override_settings(
        PIPEDREAM_CLIENT_ID="",
        PIPEDREAM_CLIENT_SECRET="",
        PIPEDREAM_PROJECT_ID="",
    )
    def test_home_page_keeps_native_integrations_when_pipedream_config_is_missing(self):
        MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="pipedream",
            display_name="Pipedream",
            url="https://remote.mcp.pipedream.net",
            is_active=True,
            prefetch_apps=["slack"],
        )

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("homepage_integrations_enabled"))
        self.assertContains(response, "Search more integrations")

    @patch(
        "pages.views.get_homepage_integrations_payload",
        return_value={
            "enabled": True,
            "builtins": [
                {
                    "slug": "notion",
                    "name": "Notion",
                    "description": "Notes",
                    "icon_url": "https://example.com/notion.png",
                },
                {
                    "slug": "slack",
                    "name": "Slack",
                    "description": "Team messaging",
                    "icon_url": "https://example.com/slack.png",
                },
                {
                    "slug": "trello",
                    "name": "Trello",
                    "description": "Boards",
                    "icon_url": "https://example.com/trello.png",
                },
                {
                    "slug": "linkedin",
                    "name": "LinkedIn",
                    "description": "Professional network",
                    "icon_url": "https://example.com/linkedin.png",
                },
                {
                    "slug": "google_sheets",
                    "name": "Google Sheets",
                    "description": "Spreadsheets",
                    "icon_url": "https://example.com/sheets.png",
                },
            ],
        },
    )
    def test_home_page_renders_built_in_integrations(self, _mock_integrations):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("homepage_integrations_enabled"))
        self.assertContains(response, 'data-integrations-open')
        self.assertContains(response, 'id="homepage-integrations-root"')
        self.assertContains(response, "Apps")
        self.assertEqual(
            response.context.get("homepage_integrations_modal_props"),
            {
                "builtins": _mock_integrations.return_value["builtins"],
                "initialSearchTerm": "",
                "initialSelectedAppSlugs": [],
                "searchUrl": reverse("pages:homepage_integrations_search"),
                "nativeIntegrationsUrl": reverse("console-native-integration-list"),
                "nativeProviders": page_views._homepage_native_integration_providers(),
                "isAuthenticated": False,
                "selectedFieldsContainerId": "homepage-integrations-selected-fields",
            },
        )
        self.assertEqual(
            [app["slug"] for app in response.context.get("homepage_integrations_inline_builtins")],
            ["linkedin", "google_sheets", "trello", "slack"],
        )
        self.assertEqual(
            [
                app["inline_icon_url"]
                for app in response.context.get("homepage_integrations_inline_builtins")
            ],
            [
                static("images/integrations/pipedream/linkedin.svg"),
                static("images/integrations/pipedream/google_sheets.svg"),
                static("images/integrations/pipedream/trello.svg"),
                static("images/integrations/pipedream/slack.svg"),
            ],
        )

    @patch(
        "pages.views.get_homepage_integrations_payload",
        return_value={
            "enabled": True,
            "builtins": [
                {
                    "slug": "notion",
                    "name": "Notion",
                    "description": "Docs",
                    "icon_url": "",
                }
            ],
        },
    )
    def test_home_page_keeps_integrations_trigger_when_no_inline_icons_match(self, _mock_integrations):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context.get("homepage_integrations_inline_builtins"), [])
        self.assertContains(response, 'id="homepage-integrations-root"')
        self.assertContains(response, 'data-integrations-open')

    @patch(
        "pages.views.get_homepage_integrations_payload",
        return_value={"enabled": True, "builtins": []},
    )
    @patch("pages.views.PipedreamCatalogService.search_apps")
    def test_homepage_integrations_search_api_error_is_non_fatal(self, mock_search, _mock_integrations):
        mock_search.side_effect = PipedreamCatalogError("Pipedream catalog unavailable.")

        response = self.client.get(
            reverse("pages:homepage_integrations_search"),
            {"q": "slack"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json(),
            {"error": "Pipedream catalog unavailable."},
        )

    @patch(
        "pages.views.get_homepage_integrations_payload",
        return_value={
            "enabled": True,
            "builtins": [
                {
                    "slug": "slack",
                    "name": "Slack Builtin",
                    "description": "Builtin messaging",
                    "icon_url": "",
                }
            ],
        },
    )
    @patch("pages.views.PipedreamCatalogService.search_apps")
    def test_homepage_integrations_search_api_excludes_built_in_integrations(self, mock_search, _mock_integrations):
        mock_search.return_value = [
            MagicMock(
                slug="slack",
                to_dict=lambda: {
                    "slug": "slack",
                    "name": "Slack Builtin",
                    "description": "Builtin messaging",
                    "icon_url": "",
                },
            ),
            MagicMock(
                slug="notion",
                to_dict=lambda: {
                    "slug": "notion",
                    "name": "Notion Search Result",
                    "description": "Knowledge base",
                    "icon_url": "",
                },
            ),
        ]

        response = self.client.get(
            reverse("pages:homepage_integrations_search"),
            {"q": "slack"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "results": [
                    {
                        "slug": "notion",
                        "name": "Notion Search Result",
                        "description": "Knowledge base",
                        "icon_url": "",
                    }
                ]
            },
        )

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
    def test_home_cta_text_changes_for_authenticated_users(self):
        unauth_response = self.client.get("/")
        self.assertEqual(unauth_response.status_code, 200)
        unauth_soup = BeautifulSoup(unauth_response.content, "html.parser")
        unauth_hero_form = unauth_soup.find("form", {"id": "create-agent-form"})
        self.assertIsNotNone(unauth_hero_form)
        unauth_hero_button = unauth_hero_form.find("button", {"type": "submit"})
        self.assertIsNotNone(unauth_hero_button)
        self.assertEqual(self._normalized_button_text(unauth_hero_button), "Start Free Trial")

        user = get_user_model().objects.create_user(
            username="home_cta_auth@example.com",
            email="home_cta_auth@example.com",
            password="password123",
        )
        self.client.force_login(user)

        auth_response = self.client.get("/")
        self.assertEqual(auth_response.status_code, 200)
        auth_soup = BeautifulSoup(auth_response.content, "html.parser")
        auth_hero_form = auth_soup.find("form", {"id": "create-agent-form"})
        self.assertIsNotNone(auth_hero_form)
        auth_hero_button = auth_hero_form.find("button", {"type": "submit"})
        self.assertIsNotNone(auth_hero_button)
        self.assertEqual(self._normalized_button_text(auth_hero_button), "Spawn Agent")

    @override_settings(GOBII_PROPRIETARY_MODE=True, PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
    def test_home_cta_text_is_candidate_sourcing_specific_in_proprietary_mode(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        soup = BeautifulSoup(response.content, "html.parser")
        self.assertIn("Get qualified candidates.", soup.get_text(" "))
        self.assertIn("Enter a job description.", soup.get_text(" "))
        self.assertNotIn("Enter a job description. Get qualified candidates.", soup.get_text(" "))
        hero_form = soup.find("form", {"id": "create-agent-form"})
        self.assertIsNotNone(hero_form)
        hero_button = hero_form.find("button", {"type": "submit"})
        self.assertIsNotNone(hero_button)
        self.assertEqual(self._normalized_button_text(hero_button), "Source Qualified Candidates")

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    def test_home_cta_text_shows_trial_when_authenticated_user_requires_trial(self):
        user = get_user_model().objects.create_user(
            username="home_cta_trial_required@example.com",
            email="home_cta_trial_required@example.com",
            password="password123",
        )
        self.client.force_login(user)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")

        hero_form = soup.find("form", {"id": "create-agent-form"})
        self.assertIsNotNone(hero_form)
        self.assertEqual(hero_form.get("data-requires-trial"), "true")
        hero_button = hero_form.find("button", {"type": "submit"})
        self.assertIsNotNone(hero_button)
        self.assertEqual(self._normalized_button_text(hero_button), "Start Free Trial")

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    def test_home_cta_text_stays_spawn_for_grandfathered_user(self):
        user = get_user_model().objects.create_user(
            username="home_cta_grandfathered@example.com",
            email="home_cta_grandfathered@example.com",
            password="password123",
        )
        UserFlags.objects.create(user=user, is_freemium_grandfathered=True)
        self.client.force_login(user)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")

        hero_form = soup.find("form", {"id": "create-agent-form"})
        self.assertIsNotNone(hero_form)
        self.assertEqual(hero_form.get("data-requires-trial"), "false")
        hero_button = hero_form.find("button", {"type": "submit"})
        self.assertIsNotNone(hero_button)
        self.assertEqual(self._normalized_button_text(hero_button), "Spawn Agent")

    def test_custom_spawn_clears_pretrained_worker_selection(self):
        session = self.client.session
        session[PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY] = "sales-pipeline-whisperer"
        session["agent_charter"] = "Template charter"
        session["agent_charter_source"] = "template"
        session.save()

        response = self.client.post("/spawn-agent/", {"charter": "Custom charter"})
        self.assertEqual(response.status_code, 302)

        session = self.client.session
        self.assertNotIn(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY, session)
        self.assertEqual(session["agent_charter_source"], "user")
        self.assertEqual(session["agent_charter"], "Custom charter")

    def test_home_spawn_redirects_to_login(self):
        session = self.client.session
        session["utm_querystring"] = "utm_source=newsletter"
        session.save()

        response = self.client.post(reverse("pages:home_agent_spawn"), {"charter": "Custom charter"})
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_login"))

        params = parse_qs(parsed.query)
        next_url = params.get("next")[0]
        next_parts = urlparse(next_url)
        self.assertEqual(next_parts.path, "/app/agents/new")
        next_params = parse_qs(next_parts.query)
        self.assertEqual(next_params.get("spawn"), ["1"])
        self.assertEqual(params.get("utm_source"), ["newsletter"])

    def test_home_spawn_redirects_to_signup_when_cta_signup_first_enabled(self):
        session = self.client.session
        session["utm_querystring"] = "utm_source=newsletter"
        session.save()

        with override_flag("cta_signup_first", active=True):
            response = self.client.post(reverse("pages:home_agent_spawn"), {"charter": "Custom charter"})

        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_signup"))

        params = parse_qs(parsed.query)
        next_url = params.get("next")[0]
        next_parts = urlparse(next_url)
        self.assertEqual(next_parts.path, "/app/agents/new")
        next_params = parse_qs(next_parts.query)
        self.assertEqual(next_params.get("spawn"), ["1"])
        self.assertEqual(params.get("utm_source"), ["newsletter"])

    def test_home_spawn_redirect_stashes_oauth_fallback_cookie(self):
        session = self.client.session
        session["utm_querystring"] = "utm_source=newsletter"
        session.save()
        self.client.cookies["first_referrer"] = "https://agentic.ai/"
        self.client.cookies["last_referrer"] = "https://agentic.ai/pricing/"
        self.client.cookies["first_path"] = "/"
        self.client.cookies["last_path"] = "/pricing/"

        response = self.client.post(
            reverse("pages:home_agent_spawn"),
            {
                "charter": "Custom charter",
                "preferred_llm_tier": "premium",
                "selected_pipedream_app_slugs": ["slack", "trello", "slack"],
                "trial_onboarding": "1",
                "trial_onboarding_target": TRIAL_ONBOARDING_TARGET_AGENT_UI,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(OAUTH_CHARTER_COOKIE, response.cookies)
        self.assertIn(OAUTH_ATTRIBUTION_COOKIE, response.cookies)

        charter_payload = signing.loads(response.cookies[OAUTH_CHARTER_COOKIE].value, max_age=7200)
        self.assertNotIn("agent_charter", charter_payload)
        stash_token = charter_payload.get(OAUTH_CHARTER_SERVER_SIDE_TOKEN_KEY)
        self.assertIsInstance(stash_token, str)

        cached_charter_payload = signing.loads(
            get_redis_client().get(build_oauth_charter_stash_cache_key(stash_token))
        )
        self.assertEqual(cached_charter_payload.get("agent_charter"), "Custom charter")
        self.assertEqual(cached_charter_payload.get("agent_charter_source"), "user")
        self.assertEqual(cached_charter_payload.get("agent_preferred_llm_tier"), "premium")
        self.assertEqual(
            cached_charter_payload.get(page_views.AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY),
            ["slack", "trello"],
        )
        self.assertTrue(cached_charter_payload.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))
        self.assertEqual(
            cached_charter_payload.get(TRIAL_ONBOARDING_TARGET_SESSION_KEY),
            TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )
        self.assertFalse(cached_charter_payload.get(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY, False))

        attribution_payload = signing.loads(response.cookies[OAUTH_ATTRIBUTION_COOKIE].value, max_age=7200)
        self.assertEqual(attribution_payload.get("utm_querystring"), "utm_source=newsletter")
        self.assertEqual(attribution_payload.get("first_referrer"), "https://agentic.ai/")
        self.assertEqual(attribution_payload.get("last_referrer"), "https://agentic.ai/pricing/")
        self.assertEqual(attribution_payload.get("first_path"), "/")
        self.assertEqual(attribution_payload.get("last_path"), "/pricing/")

        user = get_user_model().objects.create_user(
            email="home-spawn-cookie@test.com",
            password="pw",
            username="home_spawn_cookie_user",
        )
        self.client.force_login(user)

        session = self.client.session
        for key in (
            "agent_charter",
            "agent_preferred_llm_tier",
            page_views.AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY,
            TRIAL_ONBOARDING_PENDING_SESSION_KEY,
            TRIAL_ONBOARDING_TARGET_SESSION_KEY,
            TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY,
        ):
            session.pop(key, None)
        session.save()

        spawn_intent_response = self.client.get(reverse("console_agent_spawn_intent"))
        self.assertEqual(spawn_intent_response.status_code, 200)
        spawn_intent_payload = spawn_intent_response.json()
        self.assertEqual(spawn_intent_payload.get("charter"), "Custom charter")
        self.assertEqual(spawn_intent_payload.get("preferred_llm_tier"), "premium")
        self.assertEqual(spawn_intent_payload.get("selected_pipedream_app_slugs"), ["slack", "trello"])
        self.assertEqual(spawn_intent_payload.get("onboarding_target"), TRIAL_ONBOARDING_TARGET_AGENT_UI)

    def test_home_spawn_modal_prep_returns_modal_signup_url_and_preserves_state(self):
        session = self.client.session
        session["utm_querystring"] = "utm_source=newsletter"
        session.save()

        with override_flag("cta_signup_modal", active=True):
            response = self.client.post(
                reverse("pages:home_agent_spawn"),
                {
                    "charter": "Custom charter",
                    "preferred_llm_tier": "premium",
                    "selected_pipedream_app_slugs": ["slack", "trello", "slack"],
                    "trial_onboarding": "1",
                    "trial_onboarding_target": TRIAL_ONBOARDING_TARGET_AGENT_UI,
                    "auth_modal": "1",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        parsed = urlparse(payload["auth_url"])
        self.assertEqual(parsed.path, reverse("account_signup_modal"))
        params = parse_qs(parsed.query)
        next_url = params.get("next", [None])[0]
        self.assertIsNotNone(next_url)
        next_parts = urlparse(next_url)
        self.assertEqual(next_parts.path, "/app/agents/new")
        self.assertEqual(parse_qs(next_parts.query).get("spawn"), ["1"])

        session = self.client.session
        self.assertEqual(session.get("agent_charter"), "Custom charter")
        self.assertEqual(session.get("agent_charter_source"), "user")
        self.assertEqual(session.get(page_views.PREFERRED_LLM_TIER_SESSION_KEY), "premium")
        self.assertEqual(
            session.get(page_views.AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY),
            ["slack", "trello"],
        )
        self.assertTrue(session.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))
        self.assertEqual(
            session.get(TRIAL_ONBOARDING_TARGET_SESSION_KEY),
            TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )
        self.assertIn(OAUTH_CHARTER_COOKIE, response.cookies)
        self.assertIn(OAUTH_ATTRIBUTION_COOKIE, response.cookies)

    def test_home_spawn_trial_onboarding_sets_session_intent(self):
        response = self.client.post(
            reverse("pages:home_agent_spawn"),
            {
                "charter": "Custom charter",
                "trial_onboarding": "1",
                "trial_onboarding_target": TRIAL_ONBOARDING_TARGET_AGENT_UI,
            },
        )
        self.assertEqual(response.status_code, 302)

        session = self.client.session
        self.assertTrue(session.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))
        self.assertEqual(
            session.get(TRIAL_ONBOARDING_TARGET_SESSION_KEY),
            TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )
        self.assertFalse(session.get(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY, False))

    def test_home_spawn_stores_selected_pipedream_apps_in_session(self):
        response = self.client.post(
            reverse("pages:home_agent_spawn"),
            {
                "charter": "Custom charter",
                "selected_pipedream_app_slugs": ["slack", "trello", "slack"],
            },
        )
        self.assertEqual(response.status_code, 302)

        session = self.client.session
        self.assertEqual(
            session.get(page_views.AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY),
            ["slack", "trello"],
        )

    @patch(
        "pages.views.get_homepage_integrations_payload",
        return_value={"enabled": True, "builtins": []},
    )
    def test_home_page_uses_session_selected_pipedream_apps_in_modal_props(self, _mock_integrations):
        session = self.client.session
        session[page_views.AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY] = ["slack", "trello"]
        session.save()

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context.get("homepage_integrations_initial_selected_app_slugs"),
            ["slack", "trello"],
        )
        self.assertEqual(
            response.context.get("homepage_integrations_modal_props"),
            {
                "builtins": [],
                "initialSearchTerm": "",
                "initialSelectedAppSlugs": ["slack", "trello"],
                "searchUrl": reverse("pages:homepage_integrations_search"),
                "nativeIntegrationsUrl": reverse("console-native-integration-list"),
                "nativeProviders": page_views._homepage_native_integration_providers(),
                "isAuthenticated": False,
                "selectedFieldsContainerId": "homepage-integrations-selected-fields",
            },
        )
        soup = BeautifulSoup(response.content, "html.parser")
        selected_fields = soup.select(
            '#homepage-integrations-selected-fields input[name="selected_pipedream_app_slugs"]'
        )
        self.assertEqual([field["value"] for field in selected_fields], ["slack", "trello"])

    @patch(
        "pages.views.get_owner_selected_app_slugs",
        return_value=["notion", "slack"],
    )
    @patch(
        "pages.views.get_homepage_integrations_payload",
        return_value={"enabled": True, "builtins": []},
    )
    def test_home_page_merges_context_enabled_and_session_selected_pipedream_apps(
        self,
        _mock_integrations,
        mock_get_owner_selected_app_slugs,
    ):
        User = get_user_model()
        user = User.objects.create_user(
            username="homepage-apps@example.com",
            email="homepage-apps@example.com",
            password="password123",
        )
        self.client.force_login(user)
        session = self.client.session
        session[page_views.AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY] = ["trello", "slack"]
        session.save()

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context.get("homepage_integrations_modal_props"),
            {
                "builtins": [],
                "initialSearchTerm": "",
                "initialSelectedAppSlugs": ["notion", "slack", "trello"],
                "searchUrl": reverse("pages:homepage_integrations_search"),
                "nativeIntegrationsUrl": reverse("console-native-integration-list"),
                "nativeProviders": page_views._homepage_native_integration_providers(),
                "isAuthenticated": True,
                "selectedFieldsContainerId": "homepage-integrations-selected-fields",
            },
        )
        mock_get_owner_selected_app_slugs.assert_called_once_with(
            page_views.MCPServerConfig.Scope.USER,
            owner_user=user,
            owner_org=None,
        )

@tag("batch_pages")
class LandingPageRedirectTests(TestCase):
    def test_landing_redirect(self):
        """Landing page shortlink redirects to marketing page."""
        lp = LandingPage.objects.create(charter="x")

        resp = self.client.get(f"/g/{lp.code}/")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].endswith(f"?g={lp.code}"))

    def test_disabled_landing_returns_404(self):
        lp = LandingPage.objects.create(charter="x", disabled=True)

        resp = self.client.get(f"/g/{lp.code}/")
        self.assertEqual(resp.status_code, 404)

    def test_landing_redirect_increments_hits(self):
        lp = LandingPage.objects.create(charter="x", hits=0)
        self.client.get(f"/g/{lp.code}/")
        lp.refresh_from_db()
        self.assertEqual(lp.hits, 1)

    def test_landing_redirect_includes_stored_utms(self):
        lp = LandingPage.objects.create(
            charter="x",
            utm_source="newsletter",
            utm_campaign="october_push",
        )

        resp = self.client.get(f"/g/{lp.code}/")
        self.assertEqual(resp.status_code, 302)
        parsed = urlparse(resp["Location"])
        params = parse_qs(parsed.query)

        self.assertEqual(params.get("g"), [lp.code])
        self.assertEqual(params.get("utm_source"), ["newsletter"])
        self.assertEqual(params.get("utm_campaign"), ["october_push"])

    def test_existing_query_params_take_precedence(self):
        lp = LandingPage.objects.create(
            charter="x",
            utm_source="newsletter",
            utm_medium="email",
        )

        resp = self.client.get(f"/g/{lp.code}/", {"utm_source": "override", "fbclid": "abc123"})
        self.assertEqual(resp.status_code, 302)
        parsed = urlparse(resp["Location"])
        params = parse_qs(parsed.query)

        self.assertEqual(params.get("utm_source"), ["override"])
        self.assertEqual(params.get("utm_medium"), ["email"])
        self.assertEqual(params.get("fbclid"), ["abc123"])

    @patch("pages.views.record_fbc_synthesized")
    def test_landing_redirect_refreshes_fbc_when_fbclid_changes(self, mock_record_fbc_synthesized):
        lp = LandingPage.objects.create(charter="x")
        self.client.cookies["_fbc"] = "fb.1.1111111111111.old-click"

        resp = self.client.get(f"/g/{lp.code}/", {"fbclid": "new-click"})
        self.assertEqual(resp.status_code, 302)

        self.assertIn("_fbc", resp.cookies)
        self.assertIn("fbclid", resp.cookies)
        self.assertTrue(resp.cookies["_fbc"].value.startswith("fb.1."))
        self.assertTrue(resp.cookies["_fbc"].value.endswith(".new-click"))
        self.assertEqual(resp.cookies["fbclid"].value, "new-click")
        mock_record_fbc_synthesized.assert_called_once_with(
            source="pages.views.landing_page_redirect"
        )

    @patch("pages.views.record_fbc_synthesized")
    def test_landing_redirect_does_not_rotate_fbc_for_same_fbclid(self, mock_record_fbc_synthesized):
        lp = LandingPage.objects.create(charter="x")
        self.client.cookies["_fbc"] = "fb.1.1111111111111.same-click"

        resp = self.client.get(f"/g/{lp.code}/", {"fbclid": "same-click"})
        self.assertEqual(resp.status_code, 302)

        self.assertNotIn("_fbc", resp.cookies)
        self.assertIn("fbclid", resp.cookies)
        self.assertEqual(resp.cookies["fbclid"].value, "same-click")
        mock_record_fbc_synthesized.assert_not_called()


@tag("batch_pages")
class LandingPageLaunchTests(TestCase):
    def test_landing_launch_redirects_authenticated_user_into_app_spawn(self):
        user = get_user_model().objects.create_user(
            email="launch@test.com",
            password="pw",
            username="launch_user",
        )
        self.client.force_login(user)

        landing = LandingPage.objects.create(
            charter="Launch this agent",
            utm_source="newsletter",
            utm_campaign="launch-campaign",
        )
        session = self.client.session
        session["agent_charter"] = "Old draft"
        session["agent_charter_override"] = "Old override"
        session["agent_preferred_llm_tier"] = "premium"
        session[page_views.AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY] = ["slack"]
        session[PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY] = "sales-pipeline-whisperer"
        session.save()

        response = self.client.get(reverse("pages:landing_launch", kwargs={"code": landing.code}))
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, "/app/agents/new")
        params = parse_qs(parsed.query)
        self.assertEqual(params.get("spawn"), ["1"])
        self.assertEqual(params.get("g"), [landing.code])
        self.assertEqual(params.get("utm_source"), ["newsletter"])
        self.assertEqual(params.get("utm_campaign"), ["launch-campaign"])

        session = self.client.session
        self.assertEqual(session.get("agent_charter"), landing.charter)
        self.assertEqual(session.get("agent_charter_source"), "landing")
        self.assertNotIn("agent_charter_override", session)
        self.assertNotIn("agent_preferred_llm_tier", session)
        self.assertNotIn(page_views.AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY, session)
        self.assertNotIn(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY, session)
        self.assertEqual(session.get("landing_code_last"), landing.code)

        landing.refresh_from_db()
        self.assertEqual(landing.hits, 1)

    def test_landing_launch_redirects_anon_to_login_and_stashes_charter(self):
        landing = LandingPage.objects.create(
            charter="Launch anonymously",
            utm_source="paid-social",
        )

        session = self.client.session
        session["utm_querystring"] = "utm_medium=ads"
        session["utm_first_touch"] = {"utm_source": "meta", "utm_medium": "paid_social"}
        session["utm_last_touch"] = {"utm_source": "meta", "utm_campaign": "retargeting"}
        session.save()

        response = self.client.get(reverse("pages:landing_launch", kwargs={"code": landing.code}))
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_login"))
        params = parse_qs(parsed.query)
        self.assertEqual(params.get("utm_source"), ["paid-social"])
        self.assertEqual(params.get("utm_medium"), ["paid_social"])
        self.assertEqual(params.get("utm_campaign"), ["retargeting"])

        next_url = params.get("next")[0]
        next_parts = urlparse(next_url)
        self.assertEqual(next_parts.path, "/app/agents/new")
        next_params = parse_qs(next_parts.query)
        self.assertEqual(next_params.get("spawn"), ["1"])
        self.assertEqual(next_params.get("g"), [landing.code])
        self.assertEqual(next_params.get("utm_source"), ["paid-social"])

        self.assertIn(OAUTH_CHARTER_COOKIE, response.cookies)
        self.assertIn(OAUTH_ATTRIBUTION_COOKIE, response.cookies)

        stash_token_payload = signing.loads(response.cookies[OAUTH_CHARTER_COOKIE].value, max_age=7200)
        stash_token = stash_token_payload.get(OAUTH_CHARTER_SERVER_SIDE_TOKEN_KEY)
        self.assertIsNotNone(stash_token)
        cached_charter_payload = signing.loads(
            get_redis_client().get(build_oauth_charter_stash_cache_key(stash_token))
        )
        self.assertEqual(cached_charter_payload.get("agent_charter"), landing.charter)
        self.assertEqual(cached_charter_payload.get("agent_charter_source"), "landing")
        self.assertNotIn("agent_charter_override", cached_charter_payload)
        self.assertNotIn("agent_preferred_llm_tier", cached_charter_payload)

        attribution_payload = signing.loads(response.cookies[OAUTH_ATTRIBUTION_COOKIE].value, max_age=7200)
        self.assertEqual(
            attribution_payload.get("utm_first_touch"),
            {"utm_source": "meta", "utm_medium": "paid_social"},
        )
        self.assertEqual(
            attribution_payload.get("utm_last_touch"),
            {"utm_source": "paid-social", "utm_campaign": "retargeting"},
        )
        self.assertEqual(
            attribution_payload.get("utm_querystring"),
            "utm_source=paid-social&utm_medium=paid_social&utm_campaign=retargeting",
        )

    def test_landing_launch_redirects_anon_to_signup_when_cta_signup_first_enabled(self):
        landing = LandingPage.objects.create(
            charter="Launch anonymously",
            utm_source="paid-social",
        )

        session = self.client.session
        session["utm_querystring"] = "utm_medium=ads"
        session["utm_first_touch"] = {"utm_source": "meta", "utm_medium": "paid_social"}
        session["utm_last_touch"] = {"utm_source": "meta", "utm_campaign": "retargeting"}
        session.save()

        with override_flag("cta_signup_first", active=True):
            response = self.client.get(reverse("pages:landing_launch", kwargs={"code": landing.code}))

        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_signup"))
        params = parse_qs(parsed.query)
        self.assertEqual(params.get("utm_source"), ["paid-social"])
        self.assertEqual(params.get("utm_medium"), ["paid_social"])
        self.assertEqual(params.get("utm_campaign"), ["retargeting"])

        next_url = params.get("next")[0]
        next_parts = urlparse(next_url)
        self.assertEqual(next_parts.path, "/app/agents/new")
        next_params = parse_qs(next_parts.query)
        self.assertEqual(next_params.get("spawn"), ["1"])
        self.assertEqual(next_params.get("g"), [landing.code])
        self.assertEqual(next_params.get("utm_source"), ["paid-social"])

        self.assertIn(OAUTH_CHARTER_COOKIE, response.cookies)
        self.assertIn(OAUTH_ATTRIBUTION_COOKIE, response.cookies)

    def test_landing_launch_clears_stale_trial_onboarding_state(self):
        user = get_user_model().objects.create_user(
            email="launch-onboarding@test.com",
            password="pw",
            username="launch_onboarding_user",
        )
        self.client.force_login(user)

        landing = LandingPage.objects.create(charter="Launch without stale onboarding")
        session = self.client.session
        session[TRIAL_ONBOARDING_PENDING_SESSION_KEY] = True
        session[TRIAL_ONBOARDING_TARGET_SESSION_KEY] = TRIAL_ONBOARDING_TARGET_API_KEYS
        session[TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY] = True
        session.save()

        response = self.client.get(reverse("pages:landing_launch", kwargs={"code": landing.code}))
        self.assertEqual(response.status_code, 302)

        session = self.client.session
        self.assertNotIn(TRIAL_ONBOARDING_PENDING_SESSION_KEY, session)
        self.assertNotIn(TRIAL_ONBOARDING_TARGET_SESSION_KEY, session)
        self.assertNotIn(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY, session)

        spawn_intent_response = self.client.get(reverse("console_agent_spawn_intent"))
        self.assertEqual(spawn_intent_response.status_code, 200)
        payload = spawn_intent_response.json()
        self.assertIsNone(payload.get("onboarding_target"))
        self.assertFalse(payload.get("requires_plan_selection"))

    def test_landing_launch_persists_landing_utms_into_oauth_attribution(self):
        landing = LandingPage.objects.create(
            charter="Launch with landing defaults",
            utm_source="newsletter",
            utm_medium="email",
            utm_campaign="spring-launch",
        )

        response = self.client.get(reverse("pages:landing_launch", kwargs={"code": landing.code}))
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_login"))
        params = parse_qs(parsed.query)
        self.assertEqual(params.get("utm_source"), ["newsletter"])
        self.assertEqual(params.get("utm_medium"), ["email"])
        self.assertEqual(params.get("utm_campaign"), ["spring-launch"])

        attribution_payload = signing.loads(response.cookies[OAUTH_ATTRIBUTION_COOKIE].value, max_age=7200)
        expected_touch = {
            "utm_source": "newsletter",
            "utm_medium": "email",
            "utm_campaign": "spring-launch",
        }
        self.assertEqual(attribution_payload.get("utm_first_touch"), expected_touch)
        self.assertEqual(attribution_payload.get("utm_last_touch"), expected_touch)
        self.assertEqual(
            attribution_payload.get("utm_querystring"),
            "utm_source=newsletter&utm_medium=email&utm_campaign=spring-launch",
        )

    def test_disabled_landing_launch_returns_404(self):
        landing = LandingPage.objects.create(charter="x", disabled=True)

        response = self.client.get(reverse("pages:landing_launch", kwargs={"code": landing.code}))
        self.assertEqual(response.status_code, 404)


@tag("batch_pages")
class RobotsTxtTests(TestCase):
    @override_settings(GOBII_RELEASE_ENV="prod")
    def test_production_allows_indexing(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Allow: /")
        self.assertContains(response, "Sitemap:")
        lines = [line.strip() for line in response.content.decode().splitlines() if line.strip()]
        self.assertIn("Disallow: /console/agents/", lines)
        self.assertNotIn("Disallow: /accounts/modal/", lines)
        self.assertNotIn("Disallow: /d/", lines)
        self.assertNotIn("Disallow: /m/", lines)
        self.assertNotIn("Disallow: /", lines)

    @override_settings(GOBII_RELEASE_ENV="staging")
    def test_non_production_blocks_indexing(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Disallow: /")
        self.assertNotContains(response, "Allow: /")
        self.assertNotContains(response, "Sitemap:")


@tag("batch_pages")
class LlmsTxtTests(TestCase):
    def test_llms_txt_is_served_from_root(self):
        response = self.client.get("/llms.txt")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertContains(response, "# Gobii")
        self.assertContains(response, "http://testserver/llms-full.txt")
        self.assertContains(response, "https://docs.gobii.ai/")
        self.assertContains(response, "Gobii solves one problem")
        self.assertContains(response, "enter a job description, get qualified candidates")
        self.assertContains(response, "## Candidate Sourcing")
        self.assertNotContains(response, "http://testserver/solutions/")
        self.assertNotContains(response, "http://testserver/pretrained-workers/")
        self.assertNotContains(response, "http://testserver/library/")

    def test_llms_full_txt_is_served_from_root(self):
        response = self.client.get("/llms-full.txt")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertContains(response, "# Gobii")
        self.assertContains(response, "## Overview")
        self.assertContains(response, "turn a job description into a recruiter-reviewed candidate shortlist")
        self.assertContains(response, "## Candidate Sourcing")
        self.assertContains(response, "Public template and solution pages are no longer the primary product surface.")
        self.assertNotContains(response, "http://testserver/solutions/")
        self.assertNotContains(response, "http://testserver/pretrained-workers/")
        self.assertNotContains(response, "http://testserver/library/")


@tag("batch_pages")
class InstallScriptTests(TestCase):
    def test_install_script_is_served_from_root(self):
        response = self.client.get("/install.sh")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(response["Content-Disposition"], 'inline; filename="install.sh"')
        self.assertContains(response, '#!/usr/bin/env bash')
        self.assertContains(response, 'REPO_URL="https://github.com/gobii-ai/gobii-platform.git"')
        self.assertContains(response, 'INSTALL_DIR="${GOBII_INSTALL_DIR:-$HOME/gobii-platform}"')


@tag("batch_pages")
class CanonicalLinkTests(TestCase):
    def assert_blog_pages_render_single_canonical_link(self):
        blog_post = get_all_blog_posts()[0]
        cases = [
            (reverse("proprietary:blog_index"), "http://testserver/blog/"),
            (
                reverse("proprietary:blog_post", kwargs={"slug": blog_post["slug"]}),
                f"http://testserver/blog/{blog_post['slug']}/",
            ),
        ]

        for path, expected_url in cases:
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                soup = BeautifulSoup(response.content, "html.parser")
                canonical_hrefs = [
                    link.get("href")
                    for link in soup.find_all("link", rel="canonical")
                ]
                self.assertEqual(canonical_hrefs, [expected_url])

    @override_settings(
        PUBLIC_SITE_URL="https://gobii.ai",
        GOBII_RELEASE_ENV="prod",
        GOBII_PROPRIETARY_MODE=True,
    )
    def test_canonical_present_in_production_proprietary(self):
        response = self.client.get("/")
        self.assertContains(response, '<link rel="canonical" href="https://gobii.ai/">')

    @override_settings(GOBII_RELEASE_ENV="prod", GOBII_PROPRIETARY_MODE=False)
    def test_canonical_absent_when_not_proprietary(self):
        response = self.client.get("/")
        self.assertNotContains(response, 'rel="canonical"')

    @override_settings(GOBII_RELEASE_ENV="staging", GOBII_PROPRIETARY_MODE=True)
    def test_canonical_absent_when_not_production(self):
        response = self.client.get("/")
        self.assertNotContains(response, 'rel="canonical"')

    @override_settings(GOBII_RELEASE_ENV="prod", GOBII_PROPRIETARY_MODE=True)
    def test_blog_pages_render_single_canonical_link(self):
        self.assert_blog_pages_render_single_canonical_link()

    @override_settings(GOBII_RELEASE_ENV="staging", GOBII_PROPRIETARY_MODE=True)
    def test_blog_pages_keep_canonical_when_global_link_is_disabled(self):
        self.assert_blog_pages_render_single_canonical_link()


@tag("batch_pages")
class SitemapTests(TestCase):
    def test_removed_public_template_and_solution_urls_are_excluded(self):
        response = self.client.get("/sitemap.xml")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("<loc>http://example.com/</loc>", content)
        self.assertNotIn("http://example.com/solutions/", content)
        self.assertNotIn("http://example.com/library/", content)
        self.assertNotIn("http://example.com/pretrained-workers/", content)

    def test_removed_public_template_and_solution_urls_redirect_home(self):
        for path in (
            "/solutions/",
            "/solutions/recruiting/",
            "/solutions/recruiting/candidate-sourcing/",
            "/library/",
            "/library/recruiting/",
            "/pretrained-workers/",
            "/pretrained-workers/talent-scout/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], "/")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_proprietary_sitemap_excludes_redirects_and_checkout_start_urls(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("http://example.com/pricing/", content)
        self.assertIn("http://example.com/blog/", content)
        self.assertNotIn("http://example.com/docs/", content)
        self.assertNotIn("/subscribe/startup/", content)
        self.assertNotIn("/subscribe/pro/", content)
        self.assertNotIn("/subscribe/scale/", content)

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_community_sitemap_includes_local_docs(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertIn("http://example.com/docs/", response.content.decode())


@tag("batch_pages")
class ComparisonPageTests(TestCase):
    comparison_slug = "openclaw-vs-gobii"
    n8n_comparison_slug = "n8n-vs-gobii"
    zapier_agents_comparison_slug = "zapier-agents-vs-gobii"
    lindy_comparison_slug = "lindy-vs-gobii"

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_comparisons_page_renders_with_metadata_and_published_links(self):
        response = self.client.get(reverse("proprietary:comparisons"))

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        published_comparisons = page_views.get_published_comparisons()
        expected_title = page_views.ComparisonsIndexView.seo_title
        expected_description = page_views.ComparisonsIndexView.seo_description
        expected_url = response.wsgi_request.build_absolute_uri(response.wsgi_request.path)
        expected_image_url = response.wsgi_request.build_absolute_uri(
            static(page_views.ComparisonsIndexView.social_image_path)
        )

        self.assertEqual(soup.title.get_text(strip=True), expected_title)
        self.assertEqual(len(soup.find_all("meta", {"name": "description"})), 1)
        self.assertEqual(
            soup.find("meta", {"name": "description"}).get("content"),
            expected_description,
        )
        self.assertEqual(soup.find("link", {"rel": "canonical"}).get("href"), expected_url)
        self.assertEqual(soup.find("meta", {"property": "og:type"}).get("content"), "website")
        self.assertEqual(soup.find("meta", {"property": "og:title"}).get("content"), expected_title)
        self.assertEqual(
            soup.find("meta", {"property": "og:description"}).get("content"),
            expected_description,
        )
        self.assertEqual(soup.find("meta", {"property": "og:url"}).get("content"), expected_url)
        self.assertEqual(soup.find("meta", {"property": "og:image"}).get("content"), expected_image_url)
        self.assertEqual(
            soup.find("meta", {"name": "twitter:card"}).get("content"),
            "summary_large_image",
        )
        self.assertEqual(soup.find("meta", {"name": "twitter:title"}).get("content"), expected_title)
        self.assertEqual(
            soup.find("meta", {"name": "twitter:description"}).get("content"),
            expected_description,
        )
        self.assertEqual(soup.find("meta", {"name": "twitter:image"}).get("content"), expected_image_url)

        json_ld_scripts = soup.find_all("script", {"type": "application/ld+json"})
        self.assertEqual(len(json_ld_scripts), 2)
        structured_data = json.loads(json_ld_scripts[0].string)
        self.assertEqual(structured_data["@context"], "https://schema.org")
        self.assertEqual(structured_data["@type"], "CollectionPage")
        self.assertEqual(structured_data["@id"], f"{expected_url}#collection")
        self.assertEqual(structured_data["name"], expected_title)
        self.assertEqual(structured_data["description"], expected_description)
        self.assertEqual(structured_data["url"], expected_url)
        self.assertEqual(structured_data["dateModified"], page_views.ComparisonsIndexView.last_modified_date)
        self.assertEqual(
            structured_data["publisher"],
            {
                "@type": "Organization",
                "name": "Gobii",
                "url": response.wsgi_request.build_absolute_uri(reverse("pages:home")),
            },
        )
        self.assertEqual(structured_data["mainEntity"]["@type"], "ItemList")
        self.assertEqual(
            structured_data["mainEntity"]["itemListElement"],
            [
                {
                    "@type": "ListItem",
                    "position": index,
                    "url": response.wsgi_request.build_absolute_uri(
                        reverse("proprietary:comparison_detail", kwargs={"slug": comparison["slug"]})
                    ),
                    "name": comparison["title"],
                    "description": comparison["summary"],
                }
                for index, comparison in enumerate(published_comparisons, start=1)
            ],
        )
        self.assertEqual(
            structured_data["about"],
            [
                page_views._comparison_competitor_application(comparison)
                for comparison in published_comparisons
            ],
        )

        for comparison in published_comparisons:
            self.assertIn(
                page_views._comparison_competitor_application(comparison),
                structured_data["about"],
            )

        self.assertIn(
            {
                "@type": "SoftwareApplication",
                "name": "OpenClaw",
                "applicationCategory": "AI agent platform",
                "url": page_views.get_comparison(self.comparison_slug)["competitor_url"],
            },
            structured_data["about"],
        )

        breadcrumb_data = json.loads(json_ld_scripts[1].string)
        self.assertEqual(breadcrumb_data["@type"], "BreadcrumbList")
        self.assertEqual(
            breadcrumb_data["itemListElement"],
            [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": "Home",
                    "item": response.wsgi_request.build_absolute_uri(reverse("pages:home")),
                },
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": "Comparisons",
                    "item": expected_url,
                },
            ],
        )

        headings = soup.find_all("h1")
        self.assertEqual(len(headings), 1)
        self.assertEqual(headings[0].get_text(" ", strip=True), "AI agent platform comparisons")
        webp_source = soup.find("source", {"type": "image/webp"})
        self.assertIsNotNone(webp_source)
        self.assertIn("engineering-hero-1280.webp", webp_source.get("srcset"))
        hero_image = soup.find("img", {"alt": "AI agent platform evaluation workspace"})
        self.assertIsNotNone(hero_image)
        self.assertIn("engineering-hero-1280.jpg", hero_image.get("src"))
        self.assertNotIn("engineering-hero.jpg", hero_image.get("src"))
        self.assertNotContains(response, "django_htmx/htmx")
        self.assertNotContains(response, "https://js.stripe.com")
        self.assertNotContains(response, "js/account_auth_forms.js")
        self.assertNotContains(response, "js/cta_signup_modal.js")
        self.assertNotContains(response, "libphonenumber-js")
        self.assertNotContains(response, "js/phone_format.js")
        openclaw_card = soup.find("article", {"id": "openclaw"})
        self.assertIsNotNone(openclaw_card)
        self.assertIn("OpenClaw vs Gobii", openclaw_card.get_text(" ", strip=True))
        self.assertIn("Published", openclaw_card.get_text(" ", strip=True))
        n8n_card = soup.find("article", {"id": "n8n"})
        self.assertIsNotNone(n8n_card)
        self.assertIn("n8n vs Gobii", n8n_card.get_text(" ", strip=True))
        self.assertIn("Published", n8n_card.get_text(" ", strip=True))
        zapier_agents_card = soup.find("article", {"id": "zapier-agents"})
        self.assertIsNotNone(zapier_agents_card)
        self.assertIn("Zapier Agents vs Gobii", zapier_agents_card.get_text(" ", strip=True))
        self.assertIn("Published", zapier_agents_card.get_text(" ", strip=True))
        lindy_card = soup.find("article", {"id": "lindy"})
        self.assertIsNotNone(lindy_card)
        self.assertIn("Lindy vs Gobii", lindy_card.get_text(" ", strip=True))
        self.assertIn("Published", lindy_card.get_text(" ", strip=True))
        self.assertIsNotNone(
            soup.find(
                "a",
                {"href": reverse("proprietary:comparison_detail", kwargs={"slug": self.comparison_slug})},
            )
        )
        self.assertIsNotNone(
            soup.find(
                "a",
                {"href": reverse("proprietary:comparison_detail", kwargs={"slug": self.n8n_comparison_slug})},
            )
        )
        self.assertIsNotNone(
            soup.find(
                "a",
                {"href": reverse("proprietary:comparison_detail", kwargs={"slug": self.zapier_agents_comparison_slug})},
            )
        )
        self.assertIsNotNone(
            soup.find(
                "a",
                {"href": reverse("proprietary:comparison_detail", kwargs={"slug": self.lindy_comparison_slug})},
            )
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_comparisons_page_schema_ignores_unpublished_comparisons_without_competitor_url(self):
        published_comparison = page_views.get_comparison(self.comparison_slug)
        coming_soon_comparison = {
            "slug": "future-platform-vs-gobii",
            "competitor_name": "Future Platform",
            "title": "Future Platform vs Gobii",
            "summary": "A planned comparison for a future AI agent platform.",
            "status": "coming_soon",
            "target_keywords": ("Future Platform alternative",),
        }

        with (
            patch.object(
                page_views,
                "COMPARISON_CATALOG",
                (published_comparison, coming_soon_comparison),
            ),
            patch.object(
                page_views,
                "get_published_comparisons",
                return_value=(published_comparison,),
            ),
        ):
            response = self.client.get(reverse("proprietary:comparisons"))

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        structured_data = json.loads(
            soup.find_all("script", {"type": "application/ld+json"})[0].string
        )

        self.assertEqual(
            [item["name"] for item in structured_data["about"]],
            ["OpenClaw"],
        )
        self.assertEqual(
            [item["name"] for item in structured_data["mainEntity"]["itemListElement"]],
            ["OpenClaw vs Gobii"],
        )
        self.assertIn("Future Platform vs Gobii", soup.get_text(" ", strip=True))

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_openclaw_comparison_page_renders_with_metadata_and_decision_copy(self):
        response = self.client.get(
            reverse("proprietary:comparison_detail", kwargs={"slug": self.comparison_slug})
        )

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        comparison = page_views.get_comparison(self.comparison_slug)
        expected_url = response.wsgi_request.build_absolute_uri(response.wsgi_request.path)
        expected_image_url = response.wsgi_request.build_absolute_uri(
            static(page_views.ComparisonDetailView.social_image_path)
        )

        self.assertEqual(soup.title.get_text(strip=True), comparison["seo_title"])
        self.assertEqual(len(soup.find_all("meta", {"name": "description"})), 1)
        self.assertEqual(
            soup.find("meta", {"name": "description"}).get("content"),
            comparison["seo_description"],
        )
        self.assertEqual(soup.find("link", {"rel": "canonical"}).get("href"), expected_url)
        self.assertEqual(soup.find("meta", {"property": "og:type"}).get("content"), "website")
        self.assertEqual(soup.find("meta", {"property": "og:title"}).get("content"), comparison["seo_title"])
        self.assertEqual(soup.find("meta", {"property": "og:url"}).get("content"), expected_url)
        self.assertEqual(soup.find("meta", {"property": "og:image"}).get("content"), expected_image_url)
        self.assertEqual(soup.find("meta", {"name": "twitter:card"}).get("content"), "summary_large_image")

        json_ld_scripts = soup.find_all("script", {"type": "application/ld+json"})
        self.assertEqual(len(json_ld_scripts), 2)
        structured_data = json.loads(json_ld_scripts[0].string)
        self.assertEqual(structured_data["@type"], "WebPage")
        self.assertEqual(structured_data["@id"], f"{expected_url}#webpage")
        self.assertEqual(structured_data["name"], comparison["seo_title"])
        self.assertEqual(structured_data["url"], expected_url)
        self.assertEqual(structured_data["datePublished"], comparison["published_date"])
        self.assertEqual(structured_data["dateModified"], comparison["last_reviewed_date"])
        self.assertEqual(structured_data["publisher"]["url"], response.wsgi_request.build_absolute_uri(reverse("pages:home")))
        self.assertEqual(structured_data["reviewedBy"]["name"], comparison["reviewed_by"])
        self.assertEqual(
            [item["name"] for item in structured_data["about"]],
            ["Gobii", "OpenClaw"],
        )
        self.assertEqual(structured_data["about"][1]["url"], comparison["competitor_url"])

        breadcrumb_data = json.loads(json_ld_scripts[1].string)
        self.assertEqual(breadcrumb_data["@type"], "BreadcrumbList")
        self.assertEqual(
            [item["name"] for item in breadcrumb_data["itemListElement"]],
            ["Home", "Comparisons", "OpenClaw vs Gobii"],
        )

        content = soup.get_text(" ", strip=True)
        self.assertIn("OpenClaw vs Gobii", content)
        self.assertIn("OpenClaw vs Gobii: AI agents for real business workflows", content)
        self.assertIn("Choose OpenClaw if", content)
        self.assertIn("Choose Gobii if", content)
        self.assertIn("Production team automation", content)
        self.assertIn("Create Your First Gobii Agent", content)
        self.assertIn("Source note", content)
        self.assertIn("June 2026", content)
        self.assertIn("Last reviewed June 3, 2026 by Gobii editorial team.", content)
        webp_source = soup.find("source", {"type": "image/webp"})
        self.assertIsNotNone(webp_source)
        self.assertIn("engineering-hero-1280.webp", webp_source.get("srcset"))
        hero_image = soup.find("img", {"alt": "AI agent platform evaluation workspace"})
        self.assertIsNotNone(hero_image)
        self.assertIn("engineering-hero-1280.jpg", hero_image.get("src"))
        self.assertNotIn("engineering-hero.jpg", hero_image.get("src"))
        self.assertNotContains(response, "https://js.stripe.com")
        self.assertNotContains(response, "js/account_auth_forms.js")
        self.assertNotContains(response, "js/cta_signup_modal.js")
        self.assertNotContains(response, "libphonenumber-js")
        self.assertNotContains(response, "js/phone_format.js")
        main = soup.find("main")
        self.assertIsNotNone(main)
        self.assertGreaterEqual(
            len(main.find_all("a", {"href": "https://github.com/gobii-ai/gobii-platform"})),
            2,
        )
        self.assertIsNone(main.find("a", {"href": "https://github.com/gobii-ai"}))

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_n8n_comparison_page_renders_with_metadata_and_decision_copy(self):
        response = self.client.get(
            reverse("proprietary:comparison_detail", kwargs={"slug": self.n8n_comparison_slug})
        )

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        comparison = page_views.get_comparison(self.n8n_comparison_slug)
        expected_url = response.wsgi_request.build_absolute_uri(response.wsgi_request.path)
        expected_image_url = response.wsgi_request.build_absolute_uri(
            static(page_views.ComparisonDetailView.social_image_path)
        )

        self.assertEqual(soup.title.get_text(strip=True), comparison["seo_title"])
        self.assertEqual(len(soup.find_all("meta", {"name": "description"})), 1)
        self.assertEqual(
            soup.find("meta", {"name": "description"}).get("content"),
            comparison["seo_description"],
        )
        self.assertEqual(soup.find("link", {"rel": "canonical"}).get("href"), expected_url)
        self.assertEqual(soup.find("meta", {"property": "og:type"}).get("content"), "website")
        self.assertEqual(soup.find("meta", {"property": "og:title"}).get("content"), comparison["seo_title"])
        self.assertEqual(soup.find("meta", {"property": "og:url"}).get("content"), expected_url)
        self.assertEqual(soup.find("meta", {"property": "og:image"}).get("content"), expected_image_url)
        self.assertEqual(
            soup.find("meta", {"property": "og:image:alt"}).get("content"),
            "Gobii and n8n AI agent platform comparison",
        )
        self.assertEqual(soup.find("meta", {"name": "twitter:card"}).get("content"), "summary_large_image")

        json_ld_scripts = soup.find_all("script", {"type": "application/ld+json"})
        self.assertEqual(len(json_ld_scripts), 2)
        structured_data = json.loads(json_ld_scripts[0].string)
        self.assertEqual(structured_data["@type"], "WebPage")
        self.assertEqual(structured_data["@id"], f"{expected_url}#webpage")
        self.assertEqual(structured_data["name"], comparison["seo_title"])
        self.assertEqual(structured_data["url"], expected_url)
        self.assertEqual(structured_data["datePublished"], comparison["published_date"])
        self.assertEqual(structured_data["dateModified"], comparison["last_reviewed_date"])
        self.assertEqual(
            [item["name"] for item in structured_data["about"]],
            ["Gobii", "n8n"],
        )
        self.assertEqual(
            structured_data["about"][0]["description"],
            (
                "Always-on AI coworker platform for recurring business work across "
                "integrations, browsers, files, and communication channels."
            ),
        )
        self.assertEqual(
            structured_data["about"][1]["applicationCategory"],
            "Workflow automation platform",
        )
        self.assertEqual(
            structured_data["about"][1]["description"],
            "Workflow automation platform for apps, APIs, integrations, and technical automation.",
        )
        self.assertEqual(structured_data["about"][1]["url"], comparison["competitor_url"])

        breadcrumb_data = json.loads(json_ld_scripts[1].string)
        self.assertEqual(breadcrumb_data["@type"], "BreadcrumbList")
        self.assertEqual(
            [item["name"] for item in breadcrumb_data["itemListElement"]],
            ["Home", "Comparisons", "n8n vs Gobii"],
        )

        content = soup.get_text(" ", strip=True)
        self.assertIn("n8n vs Gobii", content)
        self.assertIn("n8n vs Gobii: workflow automation or AI coworkers?", content)
        self.assertIn("Choose n8n if", content)
        self.assertIn("Choose Gobii if", content)
        self.assertIn("n8n helps builders connect systems. Gobii helps teams delegate work.", content)
        self.assertIn("n8n is a canvas. Gobii is a coworker runtime.", content)
        self.assertIn("Gobii vs n8n comparison", content)
        self.assertIn("n8n alternative", content)
        self.assertIn("Source note", content)
        self.assertIn("Last reviewed June 4, 2026 by Gobii editorial team.", content)
        webp_source = soup.find("source", {"type": "image/webp"})
        self.assertIsNotNone(webp_source)
        self.assertIn("engineering-hero-1280.webp", webp_source.get("srcset"))
        hero_image = soup.find("img", {"alt": "AI agent platform evaluation workspace"})
        self.assertIsNotNone(hero_image)
        self.assertIn("engineering-hero-1280.jpg", hero_image.get("src"))
        self.assertNotContains(response, "https://js.stripe.com")
        self.assertNotContains(response, "js/account_auth_forms.js")
        self.assertNotContains(response, "js/cta_signup_modal.js")
        self.assertNotContains(response, "libphonenumber-js")
        self.assertNotContains(response, "js/phone_format.js")
        main = soup.find("main")
        self.assertIsNotNone(main)
        self.assertGreaterEqual(
            len(main.find_all("a", {"href": "https://github.com/gobii-ai/gobii-platform"})),
            2,
        )
        self.assertIsNotNone(main.find("a", {"href": "https://n8n.io/"}))
        self.assertIsNotNone(main.find("a", {"href": "https://n8n.io/integrations/browser-use/"}))
        self.assertIsNotNone(main.find("a", {"href": "https://docs.n8n.io/sustainable-use-license/"}))
        self.assertIsNone(main.find("a", {"href": "https://github.com/gobii-ai"}))

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_zapier_agents_comparison_page_renders_with_metadata_and_decision_copy(self):
        response = self.client.get(
            reverse("proprietary:comparison_detail", kwargs={"slug": self.zapier_agents_comparison_slug})
        )

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        comparison = page_views.get_comparison(self.zapier_agents_comparison_slug)
        expected_url = response.wsgi_request.build_absolute_uri(response.wsgi_request.path)
        expected_image_url = response.wsgi_request.build_absolute_uri(
            static(page_views.ComparisonDetailView.social_image_path)
        )

        self.assertEqual(soup.title.get_text(strip=True), comparison["seo_title"])
        self.assertEqual(len(soup.find_all("meta", {"name": "description"})), 1)
        self.assertEqual(
            soup.find("meta", {"name": "description"}).get("content"),
            comparison["seo_description"],
        )
        self.assertEqual(soup.find("link", {"rel": "canonical"}).get("href"), expected_url)
        self.assertEqual(soup.find("meta", {"property": "og:type"}).get("content"), "website")
        self.assertEqual(soup.find("meta", {"property": "og:title"}).get("content"), comparison["seo_title"])
        self.assertEqual(soup.find("meta", {"property": "og:url"}).get("content"), expected_url)
        self.assertEqual(soup.find("meta", {"property": "og:image"}).get("content"), expected_image_url)
        self.assertEqual(
            soup.find("meta", {"property": "og:image:alt"}).get("content"),
            "Gobii and Zapier Agents AI agent platform comparison",
        )
        self.assertEqual(soup.find("meta", {"name": "twitter:card"}).get("content"), "summary_large_image")

        json_ld_scripts = soup.find_all("script", {"type": "application/ld+json"})
        self.assertEqual(len(json_ld_scripts), 2)
        structured_data = json.loads(json_ld_scripts[0].string)
        self.assertEqual(structured_data["@type"], "WebPage")
        self.assertEqual(structured_data["@id"], f"{expected_url}#webpage")
        self.assertEqual(structured_data["name"], comparison["seo_title"])
        self.assertEqual(structured_data["url"], expected_url)
        self.assertEqual(structured_data["datePublished"], comparison["published_date"])
        self.assertEqual(structured_data["dateModified"], comparison["last_reviewed_date"])
        self.assertEqual(
            [item["name"] for item in structured_data["about"]],
            ["Gobii", "Zapier Agents"],
        )
        self.assertEqual(
            structured_data["about"][0]["description"],
            (
                "Always-on AI coworker platform for recurring business work across "
                "integrations, browsers, files, and communication channels."
            ),
        )
        self.assertEqual(structured_data["about"][0]["operatingSystem"], "Web")
        self.assertEqual(
            structured_data["about"][0]["sameAs"],
            [
                "https://gobii.ai/",
                "https://github.com/gobii-ai",
                "https://docs.gobii.ai/",
            ],
        )
        self.assertEqual(
            structured_data["about"][1]["applicationCategory"],
            "AI agent automation platform",
        )
        self.assertEqual(structured_data["about"][1]["operatingSystem"], "Web")
        self.assertEqual(
            structured_data["about"][1]["sameAs"],
            list(comparison["competitor_same_as"]),
        )
        self.assertEqual(
            structured_data["about"][1]["description"],
            "AI agents for automating work across Zapier's connected app ecosystem.",
        )
        self.assertEqual(structured_data["about"][1]["url"], comparison["competitor_url"])

        breadcrumb_data = json.loads(json_ld_scripts[1].string)
        self.assertEqual(breadcrumb_data["@type"], "BreadcrumbList")
        self.assertEqual(
            [item["name"] for item in breadcrumb_data["itemListElement"]],
            ["Home", "Comparisons", "Zapier Agents vs Gobii"],
        )

        content = soup.get_text(" ", strip=True)
        self.assertIn("Zapier Agents vs Gobii", content)
        self.assertIn("Zapier Agents vs Gobii: connected-app automation or always-on AI coworkers?", content)
        self.assertIn("Choose Zapier Agents if", content)
        self.assertIn("Choose Gobii if", content)
        self.assertIn("Zapier Agents adds AI to app automation. Gobii gives recurring business work an always-on coworker runtime.", content)
        self.assertIn("Zapier Agents is strongest across connected apps. Gobii is strongest for delegated work that spans tools, web apps, and files.", content)
        self.assertIn("Gobii vs Zapier Agents comparison", content)
        self.assertIn("Zapier Agents alternative", content)
        self.assertIn("Source note", content)
        self.assertIn("Last reviewed June 7, 2026 by Gobii editorial team.", content)
        webp_source = soup.find("source", {"type": "image/webp"})
        self.assertIsNotNone(webp_source)
        self.assertIn("engineering-hero-1280.webp", webp_source.get("srcset"))
        hero_image = soup.find("img", {"alt": "AI agent platform evaluation workspace"})
        self.assertIsNotNone(hero_image)
        self.assertIn("engineering-hero-1280.jpg", hero_image.get("src"))
        self.assertNotContains(response, "https://js.stripe.com")
        self.assertNotContains(response, "js/account_auth_forms.js")
        self.assertNotContains(response, "js/cta_signup_modal.js")
        self.assertNotContains(response, "libphonenumber-js")
        self.assertNotContains(response, "js/phone_format.js")
        main = soup.find("main")
        self.assertIsNotNone(main)
        self.assertGreaterEqual(
            len(main.find_all("a", {"href": "https://github.com/gobii-ai/gobii-platform"})),
            2,
        )
        self.assertIsNotNone(main.find("a", {"href": "https://zapier.com/agents"}))
        self.assertIsNotNone(
            main.find(
                "a",
                {"href": "https://help.zapier.com/hc/en-us/articles/26559132765325-How-is-Zapier-Agents-usage-measured"},
            )
        )
        self.assertIsNotNone(
            main.find(
                "a",
                {"href": "https://help.zapier.com/hc/en-us/articles/44452375167885-Read-web-pages-in-your-workflows"},
            )
        )
        self.assertIsNotNone(main.find("a", {"href": "https://zapier.com/pricing"}))
        self.assertIsNone(main.find("a", {"href": "https://github.com/gobii-ai"}))

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_lindy_comparison_page_renders_with_metadata_and_decision_copy(self):
        response = self.client.get(
            reverse("proprietary:comparison_detail", kwargs={"slug": self.lindy_comparison_slug})
        )

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        comparison = page_views.get_comparison(self.lindy_comparison_slug)
        expected_url = response.wsgi_request.build_absolute_uri(response.wsgi_request.path)
        expected_image_url = response.wsgi_request.build_absolute_uri(
            static(page_views.ComparisonDetailView.social_image_path)
        )

        self.assertEqual(soup.title.get_text(strip=True), comparison["seo_title"])
        self.assertEqual(len(soup.find_all("meta", {"name": "description"})), 1)
        self.assertEqual(
            soup.find("meta", {"name": "description"}).get("content"),
            comparison["seo_description"],
        )
        self.assertEqual(soup.find("link", {"rel": "canonical"}).get("href"), expected_url)
        self.assertEqual(soup.find("meta", {"property": "og:type"}).get("content"), "website")
        self.assertEqual(soup.find("meta", {"property": "og:title"}).get("content"), comparison["seo_title"])
        self.assertEqual(soup.find("meta", {"property": "og:url"}).get("content"), expected_url)
        self.assertEqual(soup.find("meta", {"property": "og:image"}).get("content"), expected_image_url)
        self.assertEqual(
            soup.find("meta", {"property": "og:image:alt"}).get("content"),
            "Gobii and Lindy AI agent platform comparison",
        )
        self.assertEqual(soup.find("meta", {"name": "twitter:card"}).get("content"), "summary_large_image")

        json_ld_scripts = soup.find_all("script", {"type": "application/ld+json"})
        self.assertEqual(len(json_ld_scripts), 2)
        structured_data = json.loads(json_ld_scripts[0].string)
        self.assertEqual(structured_data["@type"], "WebPage")
        self.assertEqual(structured_data["@id"], f"{expected_url}#webpage")
        self.assertEqual(structured_data["name"], comparison["seo_title"])
        self.assertEqual(structured_data["url"], expected_url)
        self.assertEqual(structured_data["datePublished"], comparison["published_date"])
        self.assertEqual(structured_data["dateModified"], comparison["last_reviewed_date"])
        self.assertEqual(
            [item["name"] for item in structured_data["about"]],
            ["Gobii", "Lindy"],
        )
        self.assertEqual(
            structured_data["about"][0]["description"],
            (
                "Always-on AI coworker platform for recurring business work across "
                "integrations, browsers, files, and communication channels."
            ),
        )
        self.assertEqual(structured_data["about"][0]["operatingSystem"], "Web")
        self.assertEqual(
            structured_data["about"][1]["applicationCategory"],
            "AI assistant and agent workflow platform",
        )
        self.assertEqual(structured_data["about"][1]["operatingSystem"], "Web")
        self.assertEqual(
            structured_data["about"][1]["sameAs"],
            list(comparison["competitor_same_as"]),
        )
        self.assertEqual(
            structured_data["about"][1]["description"],
            (
                "AI assistant and custom agent workflow platform for inbox, meetings, "
                "calendar, follow-ups, and connected work automation."
            ),
        )
        self.assertEqual(structured_data["about"][1]["url"], comparison["competitor_url"])

        breadcrumb_data = json.loads(json_ld_scripts[1].string)
        self.assertEqual(breadcrumb_data["@type"], "BreadcrumbList")
        self.assertEqual(
            [item["name"] for item in breadcrumb_data["itemListElement"]],
            ["Home", "Comparisons", "Lindy vs Gobii"],
        )

        content = soup.get_text(" ", strip=True)
        self.assertIn("Lindy vs Gobii", content)
        self.assertIn("Lindy vs Gobii: AI assistant or AI coworker?", content)
        self.assertIn("Choose Lindy if", content)
        self.assertIn("Choose Gobii if", content)
        self.assertIn(
            "Lindy helps manage the workday. Gobii helps teams delegate recurring web work.",
            content,
        )
        self.assertIn(
            "Lindy is a polished AI assistant. Gobii is a browser-native AI coworker platform.",
            content,
        )
        self.assertIn("Gobii vs Lindy comparison", content)
        self.assertIn("Lindy alternative", content)
        self.assertIn("Source note", content)
        self.assertIn("Last reviewed June 14, 2026 by Gobii editorial team.", content)
        webp_source = soup.find("source", {"type": "image/webp"})
        self.assertIsNotNone(webp_source)
        self.assertIn("engineering-hero-1280.webp", webp_source.get("srcset"))
        hero_image = soup.find("img", {"alt": "AI agent platform evaluation workspace"})
        self.assertIsNotNone(hero_image)
        self.assertIn("engineering-hero-1280.jpg", hero_image.get("src"))
        self.assertNotContains(response, "https://js.stripe.com")
        self.assertNotContains(response, "js/account_auth_forms.js")
        self.assertNotContains(response, "js/cta_signup_modal.js")
        self.assertNotContains(response, "libphonenumber-js")
        self.assertNotContains(response, "js/phone_format.js")
        main = soup.find("main")
        self.assertIsNotNone(main)
        self.assertGreaterEqual(
            len(main.find_all("a", {"href": "https://github.com/gobii-ai/gobii-platform"})),
            2,
        )
        self.assertIsNotNone(main.find("a", {"href": "https://docs.lindy.ai/"}))
        self.assertIsNotNone(
            main.find("a", {"href": "https://docs.lindy.ai/fundamentals/lindy-101/introduction"})
        )
        self.assertIsNotNone(
            main.find("a", {"href": "https://docs.lindy.ai/skills/web-browsing/web-browser"})
        )
        self.assertIsNotNone(
            main.find("a", {"href": "https://docs.lindy.ai/skills/by-lindy/computer-use"})
        )
        self.assertIsNotNone(main.find("a", {"href": "https://www.lindy.ai/pricing"}))
        self.assertIsNotNone(main.find("a", {"href": "https://www.lindy.ai/security"}))
        self.assertIsNotNone(main.find("a", {"href": "https://gobii.ai/pricing/"}))
        self.assertIsNone(main.find("a", {"href": "https://github.com/gobii-ai"}))

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_footer_includes_comparisons_hub_link_in_proprietary_mode(self):
        response = self.client.get(reverse("proprietary:comparisons"))

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        footer = soup.find("footer")
        self.assertIsNotNone(footer)
        comparisons_heading = footer.find("h3", string="Comparisons")
        self.assertIsNotNone(comparisons_heading)
        comparisons_link = footer.find("a", {"href": reverse("proprietary:comparisons")})
        self.assertIsNotNone(comparisons_link)
        self.assertEqual(comparisons_link.get_text(strip=True), "AI agent comparisons")
        openclaw_link = footer.find(
            "a",
            {"href": reverse("proprietary:comparison_detail", kwargs={"slug": self.comparison_slug})},
        )
        self.assertIsNotNone(openclaw_link)
        self.assertEqual(openclaw_link.get_text(strip=True), "OpenClaw vs Gobii")
        n8n_link = footer.find(
            "a",
            {"href": reverse("proprietary:comparison_detail", kwargs={"slug": self.n8n_comparison_slug})},
        )
        self.assertIsNotNone(n8n_link)
        self.assertEqual(n8n_link.get_text(strip=True), "n8n vs Gobii")
        zapier_agents_link = footer.find(
            "a",
            {"href": reverse("proprietary:comparison_detail", kwargs={"slug": self.zapier_agents_comparison_slug})},
        )
        self.assertIsNotNone(zapier_agents_link)
        self.assertEqual(zapier_agents_link.get_text(strip=True), "Zapier Agents vs Gobii")
        lindy_link = footer.find(
            "a",
            {"href": reverse("proprietary:comparison_detail", kwargs={"slug": self.lindy_comparison_slug})},
        )
        self.assertIsNotNone(lindy_link)
        self.assertEqual(lindy_link.get_text(strip=True), "Lindy vs Gobii")

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_comparison_pages_and_footer_column_are_absent_in_community_mode(self):
        comparisons_response = self.client.get(reverse("proprietary:comparisons"))
        self.assertEqual(comparisons_response.status_code, 404)
        detail_response = self.client.get(
            reverse("proprietary:comparison_detail", kwargs={"slug": self.comparison_slug})
        )
        self.assertEqual(detail_response.status_code, 404)
        n8n_detail_response = self.client.get(
            reverse("proprietary:comparison_detail", kwargs={"slug": self.n8n_comparison_slug})
        )
        self.assertEqual(n8n_detail_response.status_code, 404)
        zapier_agents_detail_response = self.client.get(
            reverse("proprietary:comparison_detail", kwargs={"slug": self.zapier_agents_comparison_slug})
        )
        self.assertEqual(zapier_agents_detail_response.status_code, 404)
        lindy_detail_response = self.client.get(
            reverse("proprietary:comparison_detail", kwargs={"slug": self.lindy_comparison_slug})
        )
        self.assertEqual(lindy_detail_response.status_code, 404)

        home_response = self.client.get(reverse("pages:home"))
        self.assertEqual(home_response.status_code, 200)
        soup = BeautifulSoup(home_response.content, "html.parser")
        footer = soup.find("footer")
        self.assertIsNotNone(footer)
        self.assertIsNone(footer.find("h3", string="Comparisons"))
        self.assertIsNone(footer.find("a", {"href": reverse("proprietary:comparisons")}))

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_unknown_comparison_page_returns_404(self):
        response = self.client.get(reverse("proprietary:comparison_detail", kwargs={"slug": "unknown"}))
        self.assertEqual(response.status_code, 404)

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_proprietary_sitemap_includes_comparison_urls(self):
        response = self.client.get("/sitemap.xml")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("<loc>http://example.com/comparisons/</loc>", content)
        self.assertIn(f"<loc>http://example.com/comparisons/{self.comparison_slug}/</loc>", content)
        self.assertIn(f"<loc>http://example.com/comparisons/{self.n8n_comparison_slug}/</loc>", content)
        self.assertIn(f"<loc>http://example.com/comparisons/{self.zapier_agents_comparison_slug}/</loc>", content)
        self.assertIn(f"<loc>http://example.com/comparisons/{self.lindy_comparison_slug}/</loc>", content)
        self.assertNotIn("<loc>http://example.com/comparisons/gobii-vs-openclaw/</loc>", content)

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_community_sitemap_excludes_comparison_urls(self):
        response = self.client.get("/sitemap.xml")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("<loc>http://example.com/comparisons/</loc>", content)
        self.assertNotIn(f"<loc>http://example.com/comparisons/{self.comparison_slug}/</loc>", content)
        self.assertNotIn(f"<loc>http://example.com/comparisons/{self.n8n_comparison_slug}/</loc>", content)
        self.assertNotIn(f"<loc>http://example.com/comparisons/{self.zapier_agents_comparison_slug}/</loc>", content)
        self.assertNotIn(f"<loc>http://example.com/comparisons/{self.lindy_comparison_slug}/</loc>", content)


@tag("batch_pages")
class DocsRedirectTests(TestCase):
    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_docs_redirects_are_permanent_in_proprietary_mode(self):
        for path in ("/docs/", "/docs/guides/api/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], "https://docs.gobii.ai/")


@tag("batch_pages")
class ApiDocsRobotsTests(TestCase):
    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_swagger_ui_is_noindex_follow(self):
        for url_name in ("schema-swagger-ui", "api_docs"):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["X-Robots-Tag"], "noindex, follow")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_swagger_ui_redirect_is_noindex_follow_in_proprietary_mode(self):
        response = self.client.get(reverse("schema-swagger-ui"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["X-Robots-Tag"], "noindex, follow")

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_redoc_robots_header_unchanged(self):
        response = self.client.get(reverse("schema-redoc"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.has_header("X-Robots-Tag"))


@tag("batch_pages")
class RemovedPretrainedWorkerSurfaceTests(TestCase):
    def test_pretrained_worker_urls_redirect_home(self):
        for path in (
            "/pretrained-workers/",
            "/pretrained-workers/talent-scout/",
            "/pretrained-workers/talent-scout/hire/",
            "/pretrained-workers/talent-scout/spawn/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], "/")


@tag("batch_pages")
class RemovedPublicMarketingSurfaceTests(TestCase):
    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_home_header_omits_solution_and_template_navigation(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        self.assertIsNone(soup.find("a", {"href": reverse("pages:solutions")}))
        self.assertIsNone(soup.find("a", {"href": reverse("pages:library")}))
        self.assertIsNone(
            soup.find("a", {"href": reverse("pages:solution", kwargs={"slug": "engineering"})})
        )
        self.assertNotIn("Solutions", soup.get_text(" ", strip=True))
        self.assertNotIn("Discover", soup.get_text(" ", strip=True))
        self.assertNotIn("Developers", soup.get_text(" ", strip=True))

    def test_removed_solution_urls_redirect_home(self):
        for path in (
            "/solutions/",
            "/solutions/recruiting/",
            "/solutions/recruiting/candidate-sourcing/",
            "/solutions/sales/",
            "/solutions/operations/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], "/")


@tag("batch_pages")
class EngineeringProSignupTests(TestCase):
    def test_engineering_trial_onboarding_redirects_anon_to_login(self):
        session = self.client.session
        session["utm_querystring"] = "utm_medium=ads"
        session.save()

        response = self.client.post(
            reverse("pages:engineering_pro_signup"),
            {
                "trial_onboarding": "1",
                "trial_onboarding_target": TRIAL_ONBOARDING_TARGET_API_KEYS,
            },
        )
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_login"))

        params = parse_qs(parsed.query)
        next_url = params.get("next")[0]
        next_parts = urlparse(next_url)
        self.assertEqual(next_parts.path, "/app/agents/new")
        next_params = parse_qs(next_parts.query)
        self.assertEqual(next_params.get("spawn"), ["1"])
        self.assertEqual(params.get("utm_medium"), ["ads"])

        session = self.client.session
        self.assertTrue(session.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))
        self.assertEqual(
            session.get(TRIAL_ONBOARDING_TARGET_SESSION_KEY),
            TRIAL_ONBOARDING_TARGET_API_KEYS,
        )
        self.assertFalse(session.get(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY, False))

    def test_engineering_default_redirects_anon_to_signup_when_cta_signup_first_enabled(self):
        session = self.client.session
        session["utm_querystring"] = "utm_medium=ads"
        session.save()

        with override_flag("cta_signup_first", active=True):
            response = self.client.post(reverse("pages:engineering_pro_signup"))
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_signup"))

        params = parse_qs(parsed.query)
        self.assertEqual(params.get("next"), [reverse("proprietary:pro_checkout")])
        self.assertEqual(params.get("utm_medium"), ["ads"])

        session = self.client.session
        self.assertEqual(
            session.get(page_views.POST_CHECKOUT_REDIRECT_SESSION_KEY),
            "/app/api-keys",
        )

    def test_engineering_trial_onboarding_redirects_anon_to_signup_when_cta_signup_first_enabled(self):
        session = self.client.session
        session["utm_querystring"] = "utm_medium=ads"
        session.save()

        with override_flag("cta_signup_first", active=True):
            response = self.client.post(
                reverse("pages:engineering_pro_signup"),
                {
                    "trial_onboarding": "1",
                    "trial_onboarding_target": TRIAL_ONBOARDING_TARGET_API_KEYS,
                },
            )
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_signup"))

        params = parse_qs(parsed.query)
        next_url = params.get("next")[0]
        next_parts = urlparse(next_url)
        self.assertEqual(next_parts.path, "/app/agents/new")
        next_params = parse_qs(next_parts.query)
        self.assertEqual(next_params.get("spawn"), ["1"])
        self.assertEqual(params.get("utm_medium"), ["ads"])

        session = self.client.session
        self.assertTrue(session.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))
        self.assertEqual(
            session.get(TRIAL_ONBOARDING_TARGET_SESSION_KEY),
            TRIAL_ONBOARDING_TARGET_API_KEYS,
        )
        self.assertFalse(session.get(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY, False))

    def test_engineering_trial_onboarding_redirects_authenticated_to_api_keys(self):
        user = get_user_model().objects.create_user(
            email="engineer@test.com",
            password="pw",
            username="engineer_user",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("pages:engineering_pro_signup"),
            {
                "trial_onboarding": "1",
                "trial_onboarding_target": TRIAL_ONBOARDING_TARGET_API_KEYS,
            },
        )
        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, "/app/api-keys")


@tag("batch_pages")
class AgentSpawnIntentApiTests(TestCase):
    def test_spawn_intent_includes_trial_onboarding_fields(self):
        user = get_user_model().objects.create_user(
            email="spawn-intent@test.com",
            password="pw",
            username="spawn_intent_user",
        )
        self.client.force_login(user)

        session = self.client.session
        session["agent_charter"] = "Draft charter"
        session["agent_preferred_llm_tier"] = "premium"
        session[TRIAL_ONBOARDING_PENDING_SESSION_KEY] = True
        session[TRIAL_ONBOARDING_TARGET_SESSION_KEY] = TRIAL_ONBOARDING_TARGET_AGENT_UI
        session[TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY] = True
        session.save()

        response = self.client.get(reverse("console_agent_spawn_intent"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("charter"), "Draft charter")
        self.assertEqual(payload.get("preferred_llm_tier"), "premium")
        self.assertEqual(payload.get("selected_pipedream_app_slugs"), [])
        self.assertEqual(payload.get("onboarding_target"), TRIAL_ONBOARDING_TARGET_AGENT_UI)
        self.assertTrue(payload.get("requires_plan_selection"))

    def test_spawn_intent_includes_selected_pipedream_app_slugs(self):
        user = get_user_model().objects.create_user(
            email="spawn-intent-apps@test.com",
            password="pw",
            username="spawn_intent_apps_user",
        )
        self.client.force_login(user)

        session = self.client.session
        session["agent_charter"] = "Draft charter"
        session[page_views.AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY] = ["slack", "trello"]
        session.save()

        response = self.client.get(reverse("console_agent_spawn_intent"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("selected_pipedream_app_slugs"), ["slack", "trello"])

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_spawn_intent_uses_starter_charter_for_proprietary_personal_preview(self):
        user = get_user_model().objects.create_user(
            email="spawn-intent-preview@test.com",
            password="pw",
            username="spawn_intent_preview_user",
        )
        self.client.force_login(user)

        with (
            override_flag("personal_agent_signup_starter_charter", active=True),
            override_flag("personal_agent_signup_preview_processing_limit", active=True),
            patch("util.personal_signup_preview.can_user_use_personal_agents_and_api", return_value=False),
        ):
            response = self.client.get(reverse("console_agent_spawn_intent"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload.get("charter"),
            GENERIC_STARTER_CHARTER,
        )
        self.assertFalse(payload.get("requires_plan_selection"))
        self.assertIsNone(payload.get("onboarding_target"))

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_spawn_intent_suppresses_trial_onboarding_modal_for_preview_with_saved_charter(self):
        user = get_user_model().objects.create_user(
            email="spawn-intent-preview-saved@test.com",
            password="pw",
            username="spawn_intent_preview_saved_user",
        )
        self.client.force_login(user)

        session = self.client.session
        session["agent_charter"] = "Draft charter"
        session[TRIAL_ONBOARDING_PENDING_SESSION_KEY] = True
        session[TRIAL_ONBOARDING_TARGET_SESSION_KEY] = TRIAL_ONBOARDING_TARGET_AGENT_UI
        session[TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY] = True
        session.save()

        with (
            override_flag("personal_agent_signup_preview_ui", active=True),
            override_flag("personal_agent_signup_preview_processing_limit", active=True),
            patch("util.personal_signup_preview.can_user_use_personal_agents_and_api", return_value=False),
        ):
            response = self.client.get(reverse("console_agent_spawn_intent"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("charter"), "Draft charter")
        self.assertFalse(payload.get("requires_plan_selection"))
        self.assertIsNone(payload.get("onboarding_target"))
        session = self.client.session
        self.assertNotIn(TRIAL_ONBOARDING_PENDING_SESSION_KEY, session)
        self.assertNotIn(TRIAL_ONBOARDING_TARGET_SESSION_KEY, session)
        self.assertNotIn(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY, session)

    def test_spawn_intent_restores_onboarding_fields_from_oauth_cookie(self):
        user = get_user_model().objects.create_user(
            email="spawn-intent-cookie@test.com",
            password="pw",
            username="spawn_intent_cookie_user",
        )
        self.client.force_login(user)

        session = self.client.session
        for key in (
            "agent_charter",
            "agent_preferred_llm_tier",
            TRIAL_ONBOARDING_PENDING_SESSION_KEY,
            TRIAL_ONBOARDING_TARGET_SESSION_KEY,
            TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY,
        ):
            session.pop(key, None)
        session.save()

        self.client.cookies[OAUTH_CHARTER_COOKIE] = signing.dumps(
            {
                "agent_charter": "Cookie charter",
                "agent_preferred_llm_tier": "premium",
                page_views.AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY: ["slack", "trello"],
                PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY: "sales-pipeline-whisperer",
                "agent_charter_source": "template",
                TRIAL_ONBOARDING_PENDING_SESSION_KEY: True,
                TRIAL_ONBOARDING_TARGET_SESSION_KEY: TRIAL_ONBOARDING_TARGET_AGENT_UI,
                TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY: True,
            },
            compress=True,
        )

        response = self.client.get(reverse("console_agent_spawn_intent"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("charter"), "Cookie charter")
        self.assertEqual(payload.get("preferred_llm_tier"), "premium")
        self.assertEqual(payload.get("selected_pipedream_app_slugs"), ["slack", "trello"])
        self.assertEqual(payload.get("onboarding_target"), TRIAL_ONBOARDING_TARGET_AGENT_UI)
        self.assertTrue(payload.get("requires_plan_selection"))


@tag("batch_pages")
class CheckoutRedirectTests(TestCase):
    def test_checkout_start_pages_are_noindex_follow(self):
        for url_name in ("proprietary:startup_checkout", "proprietary:pro_checkout", "proprietary:scale_checkout"):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["X-Robots-Tag"], "noindex, follow")

    @patch("pages.views.reconcile_user_plan_from_stripe")
    @patch("pages.views._prepare_stripe_or_404")
    def test_startup_checkout_skips_paid_users(
        self,
        mock_prepare,
        mock_get_user_plan,
    ):
        user = get_user_model().objects.create_user(
            email="scale@test.com",
            password="pw",
            username="scale_user",
        )
        self.client.force_login(user)

        session = self.client.session
        session[page_views.POST_CHECKOUT_REDIRECT_SESSION_KEY] = "/app/api-keys"
        session.save()

        mock_get_user_plan.return_value = {"id": PlanNames.SCALE}

        resp = self.client.get(reverse("proprietary:pro_checkout"))

        self.assertEqual(resp.status_code, 302)
        parsed = urlparse(resp["Location"])
        self.assertEqual(parsed.path, "/app/api-keys")
        mock_prepare.assert_not_called()

        session = self.client.session
        self.assertIsNone(session.get(page_views.POST_CHECKOUT_REDIRECT_SESSION_KEY))

    @patch("pages.views.reconcile_user_plan_from_stripe")
    def test_startup_checkout_sets_return_to_param(self, mock_get_user_plan):
        user = get_user_model().objects.create_user(
            email="returnto@test.com",
            password="pw",
            username="returnto_user",
        )
        self.client.force_login(user)

        mock_get_user_plan.return_value = {"id": PlanNames.SCALE}

        return_to = "/console/agents/123/chat/"
        resp = self.client.get(reverse("proprietary:pro_checkout"), {"return_to": return_to})

        self.assertEqual(resp.status_code, 302)
        parsed = urlparse(resp["Location"])
        self.assertEqual(parsed.path, return_to)

        session = self.client.session
        self.assertIsNone(session.get(page_views.POST_CHECKOUT_REDIRECT_SESSION_KEY))

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views._is_individual_trial_eligible", return_value=True)
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.get_existing_individual_subscriptions")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    def test_startup_checkout_uses_session_redirect(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        mock_existing_subs,
        mock_ensure,
        _mock_trial_eligible,
        _,
    ):
        user = get_user_model().objects.create_user(
            email="pro@test.com",
            password="pw",
            username="pro_user",
        )
        self.client.force_login(user)

        session = self.client.session
        session[page_views.POST_CHECKOUT_REDIRECT_SESSION_KEY] = reverse("agent_quick_spawn")
        session.save()

        mock_stripe_settings.return_value = SimpleNamespace(
            startup_price_id="price_startup",
            startup_additional_task_price_id="price_startup_meter",
        )
        mock_customer.return_value = SimpleNamespace(id="cus_pro")
        mock_price_get.return_value = MagicMock(unit_amount=12000, currency="usd")
        mock_session_create.return_value = MagicMock(url="https://stripe.test/checkout-startup")
        mock_ensure.return_value = ({"id": "sub_updated"}, "updated")
        mock_existing_subs.return_value = []
        resp = self.client.get(reverse("proprietary:pro_checkout"))

        self.assertEqual(resp.status_code, 302)
        parsed = urlparse(resp["Location"])
        self.assertEqual(parsed.path, reverse("agent_quick_spawn"))
        ensure_kwargs = mock_ensure.call_args.kwargs
        self.assertNotIn("metered_price_id", ensure_kwargs)

        session = self.client.session
        self.assertIsNone(session.get(page_views.POST_CHECKOUT_REDIRECT_SESSION_KEY))

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views._is_individual_trial_eligible", return_value=True)
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.record_checkout_context")
    @patch("pages.views.stripe.Customer.modify")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    def test_startup_checkout_applies_trial_when_eligible(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        mock_customer_modify,
        mock_record_checkout_context,
        mock_ensure,
        _mock_trial_eligible,
        _,
    ):
        user = get_user_model().objects.create_user(
            email="trial@test.com",
            password="pw",
            username="trial_user",
        )
        UserFingerprintVisit.objects.create(
            user=user,
            source="signup",
            fingerprint_event_id="request-fp-startup",
            fingerprint_visitor_id="visitor-fp-startup",
            fetch_status=UserFingerprintVisitFetchStatusChoices.SUCCEEDED,
            suspect_score=7.0,
            country_code="VN",
            proxy=True,
            tampering=False,
            bot="not_detected",
        )
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            startup_price_id="price_startup",
            startup_additional_task_price_id="price_startup_meter",
            startup_trial_days=7,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_trial")
        mock_price_get.return_value = MagicMock(unit_amount=12000, currency="usd")
        mock_session_create.return_value = SimpleNamespace(
            id="cs_trial_checkout_context",
            created=1_700_000_000,
            url="https://stripe.test/checkout-startup",
        )
        mock_ensure.return_value = (None, "absent")
        resp = self.client.get(reverse("proprietary:pro_checkout"))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-startup")

        kwargs = mock_session_create.call_args.kwargs
        self.assertNotIn("excluded_payment_method_types", kwargs)
        self.assertEqual(
            kwargs["payment_method_types"],
            PERSONAL_CHECKOUT_PAYMENT_METHOD_TYPES,
        )
        self.assertEqual(kwargs["metadata"]["flow_type"], "trial")
        self.assertEqual(kwargs["subscription_data"]["metadata"]["flow_type"], "trial")
        self.assertEqual(kwargs["metadata"][STRIPE_CHECKOUT_FP_SUSPECT_SCORE_META_KEY], "7.0")
        self.assertEqual(kwargs["metadata"][STRIPE_CHECKOUT_FP_COUNTRY_META_KEY], "VN")
        self.assertEqual(kwargs["metadata"][STRIPE_CHECKOUT_FP_PROXY_META_KEY], "true")
        self.assertEqual(kwargs["metadata"][STRIPE_CHECKOUT_FP_TAMPERING_META_KEY], "false")
        self.assertEqual(kwargs["metadata"][STRIPE_CHECKOUT_FP_BOT_META_KEY], "notDetected")
        self.assertEqual(kwargs["metadata"][STRIPE_CHECKOUT_FP_VISITOR_ID_META_KEY], "visitor-fp-startup")
        self.assertNotIn(STRIPE_CHECKOUT_FP_SUSPECT_SCORE_META_KEY, kwargs["subscription_data"]["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_COUNTRY_META_KEY, kwargs["subscription_data"]["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_PROXY_META_KEY, kwargs["subscription_data"]["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_TAMPERING_META_KEY, kwargs["subscription_data"]["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_BOT_META_KEY, kwargs["subscription_data"]["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_VISITOR_ID_META_KEY, kwargs["subscription_data"]["metadata"])
        self.assertEqual(kwargs["subscription_data"]["trial_period_days"], 7)
        self.assertNotIn("billing_address_collection", kwargs)
        self.assertNotIn("name_collection", kwargs)
        self.assertEqual(
            kwargs["custom_text"],
            {
                "after_submit": {
                    "message": "Prepaid cards are not eligible for a free trial. Subscriptions are automatically charged at the end of the trial period if not canceled."
                }
            },
        )
        self.assertEqual(
            kwargs["line_items"],
            [{"price": "price_startup", "quantity": 1}],
        )
        customer_modify_args, customer_modify_kwargs = mock_customer_modify.call_args
        self.assertEqual(customer_modify_args[0], "cus_trial")
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY],
            "trial",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY],
            kwargs["metadata"]["gobii_event_id"],
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_PLAN_META_KEY],
            PlanNames.STARTUP,
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_PLAN_LABEL_META_KEY],
            "Pro",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_VALUE_META_KEY],
            "120.0",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_CURRENCY_META_KEY],
            "USD",
        )
        self.assertTrue(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_SOURCE_URL_META_KEY]
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FP_SUSPECT_SCORE_META_KEY],
            "7.0",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FP_COUNTRY_META_KEY],
            "VN",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FP_PROXY_META_KEY],
            "true",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FP_TAMPERING_META_KEY],
            "false",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FP_BOT_META_KEY],
            "notDetected",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FP_VISITOR_ID_META_KEY],
            "visitor-fp-startup",
        )
        mock_record_checkout_context.assert_called_once_with(
            customer_id="cus_trial",
            checkout_session_id="cs_trial_checkout_context",
            session_created_at=1_700_000_000,
            flow_type="trial",
            event_id=kwargs["metadata"]["gobii_event_id"],
            plan=PlanNames.STARTUP,
            plan_label="Pro",
            value=120.0,
            currency="USD",
            checkout_source_url=kwargs["metadata"]["checkout_source_url"],
        )

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views.evaluate_user_trial_eligibility", return_value=SimpleNamespace(eligible=False))
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.get_existing_individual_subscriptions", return_value=[])
    @patch("pages.views.stripe.Customer.modify")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    def test_startup_checkout_applies_trial_when_enforcement_flag_disabled(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        _mock_customer_modify,
        _mock_existing_subs,
        mock_ensure,
        mock_trial_eligibility,
        _,
    ):
        user = get_user_model().objects.create_user(
            email="trial_flag_off@test.com",
            password="pw",
            username="trial_flag_off_user",
        )
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            startup_price_id="price_startup",
            startup_additional_task_price_id="price_startup_meter",
            startup_trial_days=7,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_trial_flag_off")
        mock_price_get.return_value = MagicMock(unit_amount=12000, currency="usd")
        mock_session_create.return_value = MagicMock(url="https://stripe.test/checkout-startup")
        mock_ensure.return_value = (None, "absent")

        with override_flag("user_trial_eligibility_enforcement", active=False):
            resp = self.client.get(reverse("proprietary:pro_checkout"))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-startup")

        kwargs = mock_session_create.call_args.kwargs
        self.assertEqual(kwargs["metadata"]["flow_type"], "trial")
        self.assertEqual(kwargs["subscription_data"]["metadata"]["flow_type"], "trial")
        self.assertEqual(kwargs["subscription_data"]["trial_period_days"], 7)
        mock_trial_eligibility.assert_not_called()

    @patch("pages.views.evaluate_user_trial_eligibility", return_value=SimpleNamespace(decision="eligible"))
    @patch("pages.views.user_has_prior_individual_history", return_value=True)
    def test_individual_trial_eligibility_uses_one_per_user_flag_before_abuse_matching(
        self,
        mock_prior_history,
        mock_trial_eligibility,
    ):
        user = get_user_model().objects.create_user(
            email="one_per_user@test.com",
            password="pw",
            username="one_per_user_user",
        )

        with (
            override_flag("user_trial_eligibility_enforcement", active=False),
            override_flag("user_trial_eligibility_enforcement_one_per_user", active=True),
        ):
            eligible = page_views._is_individual_trial_eligible(user)

        self.assertFalse(eligible)
        mock_prior_history.assert_called_once_with(user)
        mock_trial_eligibility.assert_not_called()

    @patch("pages.views.evaluate_user_trial_eligibility", return_value=SimpleNamespace(decision="no_trial"))
    @patch("pages.views.user_has_prior_individual_history", return_value=False)
    def test_individual_trial_eligibility_blocks_no_trial_before_one_per_user(
        self,
        mock_prior_history,
        mock_trial_eligibility,
    ):
        user = get_user_model().objects.create_user(
            email="no_trial_before_one_per_user@test.com",
            password="pw",
            username="no_trial_before_one_per_user_user",
        )

        with (
            override_flag("user_trial_eligibility_enforcement", active=True),
            override_flag("user_trial_eligibility_enforcement_one_per_user", active=True),
        ):
            eligible = page_views._is_individual_trial_eligible(user)

        self.assertFalse(eligible)
        mock_trial_eligibility.assert_called_once_with(
            user,
            request=None,
            capture_source=None,
            assessment_source=None,
        )
        mock_prior_history.assert_not_called()

    @patch("pages.views.evaluate_user_trial_eligibility", return_value=SimpleNamespace(decision="review"))
    @patch("pages.views.user_has_prior_individual_history", return_value=False)
    def test_individual_trial_eligibility_blocks_review_before_one_per_user_when_review_flag_disabled(
        self,
        mock_prior_history,
        mock_trial_eligibility,
    ):
        user = get_user_model().objects.create_user(
            email="review_before_one_per_user@test.com",
            password="pw",
            username="review_before_one_per_user_user",
        )

        with (
            override_flag("user_trial_eligibility_enforcement", active=True),
            override_flag("user_trial_eligibility_enforcement_one_per_user", active=True),
            override_flag("user_trial_review_allows_trial", active=False),
        ):
            eligible = page_views._is_individual_trial_eligible(user)

        self.assertFalse(eligible)
        mock_trial_eligibility.assert_called_once_with(
            user,
            request=None,
            capture_source=None,
            assessment_source=None,
        )
        mock_prior_history.assert_not_called()

    @patch("pages.views.logger.warning")
    @patch("pages.views.evaluate_user_trial_eligibility", side_effect=TypeError("boom"))
    def test_individual_trial_eligibility_defaults_to_ineligible_when_evaluation_fails(
        self,
        mock_trial_eligibility,
        mock_warning,
    ):
        user = get_user_model().objects.create_user(
            email="trial_eligibility_failure@test.com",
            password="pw",
            username="trial_eligibility_failure_user",
        )

        eligible = page_views._is_individual_trial_eligible(
            user,
            capture_source="checkout",
        )

        self.assertFalse(eligible)
        mock_trial_eligibility.assert_called_once_with(
            user,
            request=None,
            capture_source="checkout",
            assessment_source="checkout",
        )
        mock_warning.assert_called_once()

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views.evaluate_user_trial_eligibility", return_value=SimpleNamespace(decision="review"))
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.stripe.Customer.modify")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    def test_startup_checkout_applies_trial_when_review_decision_allowed(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        _mock_customer_modify,
        mock_ensure,
        _mock_trial_eligibility,
        _,
    ):
        user = get_user_model().objects.create_user(
            email="trial_review@test.com",
            password="pw",
            username="trial_review_user",
        )
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            startup_price_id="price_startup",
            startup_additional_task_price_id="price_startup_meter",
            startup_trial_days=7,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_trial_review")
        mock_price_get.return_value = MagicMock(unit_amount=12000, currency="usd")
        mock_session_create.return_value = MagicMock(url="https://stripe.test/checkout-startup")
        mock_ensure.return_value = (None, "absent")

        with override_flag("user_trial_review_allows_trial", active=True):
            resp = self.client.get(reverse("proprietary:pro_checkout"))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-startup")

        kwargs = mock_session_create.call_args.kwargs
        self.assertEqual(kwargs["metadata"]["flow_type"], "trial")
        self.assertEqual(kwargs["subscription_data"]["metadata"]["flow_type"], "trial")
        self.assertEqual(kwargs["subscription_data"]["trial_period_days"], 7)

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views._is_individual_trial_eligible", return_value=True)
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.stripe.Customer.modify")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    def test_startup_checkout_includes_metered_line_item_when_auto_purchase_enabled(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        _mock_customer_modify,
        mock_ensure,
        _mock_trial_eligible,
        _,
    ):
        user = get_user_model().objects.create_user(
            email="trial_metered@test.com",
            password="pw",
            username="trial_metered_user",
        )
        UserBilling.objects.update_or_create(
            user=user,
            defaults={"max_extra_tasks": 25},
        )
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            startup_price_id="price_startup",
            startup_additional_task_price_id="price_startup_meter",
            startup_trial_days=7,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_trial")
        mock_price_get.return_value = MagicMock(unit_amount=12000, currency="usd")
        mock_session_create.return_value = MagicMock(url="https://stripe.test/checkout-startup")
        mock_ensure.return_value = (None, "absent")
        resp = self.client.get(reverse("proprietary:pro_checkout"))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-startup")

        ensure_kwargs = mock_ensure.call_args.kwargs
        self.assertEqual(ensure_kwargs.get("metered_price_id"), "price_startup_meter")

        kwargs = mock_session_create.call_args.kwargs
        self.assertEqual(
            kwargs["line_items"],
            [
                {"price": "price_startup", "quantity": 1},
                {"price": "price_startup_meter"},
            ],
        )

    def test_startup_checkout_tracks_redirected_to_checkout_event(self):
        user = get_user_model().objects.create_user(
            email="redirected_startup@test.com",
            password="pw",
            username="redirected_startup_user",
        )
        self.client.force_login(user)

        with (
            patch("pages.views.reconcile_user_plan_from_stripe", return_value={}),
            patch(
                "pages.views.get_stripe_settings",
                return_value=SimpleNamespace(
                    startup_price_id="price_startup",
                    startup_trial_days=7,
                ),
            ),
            patch(
                "pages.views.get_or_create_stripe_customer",
                return_value=SimpleNamespace(id="cus_redirected_startup"),
            ),
            patch(
                "pages.views.Price.objects.get",
                return_value=MagicMock(unit_amount=12000, currency="usd"),
            ),
            patch(
                "pages.views.ensure_single_individual_subscription",
                return_value=(None, "absent"),
            ),
            patch("pages.views._is_individual_trial_eligible", return_value=True),
            patch("pages.views._prepare_stripe_or_404"),
            patch("pages.views._emit_checkout_initiated_event"),
            patch(
                "pages.views._create_checkout_session_with_customer_context",
                return_value=SimpleNamespace(url="https://stripe.test/checkout-startup"),
            ) as mock_create_checkout,
            patch("pages.views.Analytics.track_event") as mock_track_event,
        ):
            resp = self.client.get(reverse("proprietary:pro_checkout"))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-startup")
        mock_create_checkout.assert_called_once()
        mock_track_event.assert_called_once_with(
            user_id=user.id,
            event=AnalyticsEvent.REDIRECTED_TO_CHECKOUT,
            source=AnalyticsSource.WEB,
            properties={
                "plan_type": "pro",
                "trial_enabled": True,
            },
        )

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views._is_individual_trial_eligible", return_value=True)
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.get_existing_individual_subscriptions")
    @patch("pages.views.record_checkout_context")
    @patch("pages.views.stripe.Customer.modify")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    def test_scale_checkout_applies_trial_checkout_fields_when_trial_eligible(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        _mock_customer_modify,
        _mock_record_checkout_context,
        mock_existing_subs,
        mock_ensure,
        _mock_trial_eligible,
        _,
    ):
        user = get_user_model().objects.create_user(
            email="scale_trial_fields@test.com",
            password="pw",
            username="scale_trial_fields_user",
        )
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            scale_price_id="price_scale",
            scale_additional_task_price_id="price_scale_meter",
            scale_trial_days=7,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_scale_trial_fields")
        mock_price_get.return_value = MagicMock(unit_amount=25000, currency="usd")
        mock_session_create.return_value = MagicMock(url="https://stripe.test/checkout-scale")
        mock_existing_subs.return_value = []
        mock_ensure.return_value = (None, "absent")

        resp = self.client.get("/subscribe/scale/")

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-scale")

        kwargs = mock_session_create.call_args.kwargs
        self.assertNotIn("billing_address_collection", kwargs)
        self.assertNotIn("name_collection", kwargs)
        self.assertEqual(
            kwargs["custom_text"],
            {
                "after_submit": {
                    "message": "Prepaid cards are not eligible for a free trial. Subscriptions are automatically charged at the end of the trial period if not canceled."
                }
            },
        )

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views._is_individual_trial_eligible", return_value=True)
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.get_existing_individual_subscriptions")
    @patch("pages.views.record_checkout_context")
    @patch("pages.views.stripe.Customer.modify")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    def test_scale_checkout_applies_collection_fields_when_switches_enabled_for_trial(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        _mock_customer_modify,
        _mock_record_checkout_context,
        mock_existing_subs,
        mock_ensure,
        _mock_trial_eligible,
        _,
    ):
        user = get_user_model().objects.create_user(
            email="scale_trial_switches@test.com",
            password="pw",
            username="scale_trial_switches_user",
        )
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            scale_price_id="price_scale",
            scale_additional_task_price_id="price_scale_meter",
            scale_trial_days=7,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_scale_trial_switches")
        mock_price_get.return_value = MagicMock(unit_amount=25000, currency="usd")
        mock_session_create.return_value = MagicMock(url="https://stripe.test/checkout-scale")
        mock_existing_subs.return_value = []
        mock_ensure.return_value = (None, "absent")

        with (
            override_switch("stripe_scale_trial_checkout_billing_address_required", active=True),
            override_switch("stripe_scale_trial_checkout_individual_name_enabled", active=True),
            override_switch("stripe_scale_trial_checkout_individual_name_optional", active=True),
            override_switch(STRIPE_CHECKOUT_TOS_CONSENT_REQUIRED, active=True),
            patch("pages.views._emit_checkout_initiated_event"),
        ):
            resp = self.client.get("/subscribe/scale/")

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-scale")

        kwargs = mock_session_create.call_args.kwargs
        self.assertEqual(
            kwargs["consent_collection"],
            {"terms_of_service": "required"},
        )
        self.assertEqual(kwargs["billing_address_collection"], "required")
        self.assertEqual(
            kwargs["name_collection"],
            {
                "individual": {
                    "enabled": True,
                    "optional": True,
                }
            },
        )

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views._is_individual_trial_eligible", return_value=False)
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.get_existing_individual_subscriptions")
    @patch("pages.views.record_checkout_context")
    @patch("pages.views.stripe.Customer.modify")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    def test_scale_checkout_skips_collection_fields_when_switches_enabled_without_trial(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        _mock_customer_modify,
        _mock_record_checkout_context,
        mock_existing_subs,
        mock_ensure,
        _mock_trial_eligible,
        _,
    ):
        user = get_user_model().objects.create_user(
            email="scale_purchase_switches@test.com",
            password="pw",
            username="scale_purchase_switches_user",
        )
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            scale_price_id="price_scale",
            scale_additional_task_price_id="price_scale_meter",
            scale_trial_days=7,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_scale_purchase_switches")
        mock_price_get.return_value = MagicMock(unit_amount=25000, currency="usd")
        mock_session_create.return_value = MagicMock(url="https://stripe.test/checkout-scale")
        mock_existing_subs.return_value = []
        mock_ensure.return_value = (None, "absent")

        with (
            override_switch("stripe_scale_trial_checkout_billing_address_required", active=True),
            override_switch("stripe_scale_trial_checkout_individual_name_enabled", active=True),
            override_switch("stripe_scale_trial_checkout_individual_name_optional", active=True),
        ):
            resp = self.client.get("/subscribe/scale/")

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-scale")

        kwargs = mock_session_create.call_args.kwargs
        self.assertNotIn("billing_address_collection", kwargs)
        self.assertNotIn("name_collection", kwargs)

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views._is_individual_trial_eligible", return_value=True)
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.record_checkout_context")
    @patch("pages.views.stripe.Customer.modify")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    def test_startup_checkout_skips_scale_collection_fields_when_switches_enabled(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        _mock_customer_modify,
        _mock_record_checkout_context,
        mock_ensure,
        _mock_trial_eligible,
        _,
    ):
        user = get_user_model().objects.create_user(
            email="startup_trial_switches@test.com",
            password="pw",
            username="startup_trial_switches_user",
        )
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            startup_price_id="price_startup",
            startup_additional_task_price_id="price_startup_meter",
            startup_trial_days=7,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_startup_trial_switches")
        mock_price_get.return_value = MagicMock(unit_amount=12000, currency="usd")
        mock_session_create.return_value = MagicMock(url="https://stripe.test/checkout-startup")
        mock_ensure.return_value = (None, "absent")

        with (
            override_switch("stripe_scale_trial_checkout_billing_address_required", active=True),
            override_switch("stripe_scale_trial_checkout_individual_name_enabled", active=True),
            override_switch("stripe_scale_trial_checkout_individual_name_optional", active=True),
        ):
            resp = self.client.get(reverse("proprietary:pro_checkout"))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-startup")

        kwargs = mock_session_create.call_args.kwargs
        self.assertNotIn("billing_address_collection", kwargs)
        self.assertNotIn("name_collection", kwargs)

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views._is_individual_trial_eligible", return_value=False)
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.get_existing_individual_subscriptions")
    @patch("pages.views.record_checkout_context")
    @patch("pages.views.stripe.Customer.modify")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    def test_scale_checkout_skips_trial_for_prior_customers(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        mock_customer_modify,
        mock_record_checkout_context,
        mock_existing_subs,
        mock_ensure,
        _mock_trial_eligible,
        _,
    ):
        user = get_user_model().objects.create_user(
            email="scale_trial@test.com",
            password="pw",
            username="scale_trial_user",
        )
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            scale_price_id="price_scale",
            scale_additional_task_price_id="price_scale_meter",
            scale_trial_days=7,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_scale_trial")
        mock_price_get.return_value = MagicMock(unit_amount=25000, currency="usd")
        mock_session_create.return_value = SimpleNamespace(
            id="cs_purchase_checkout_context",
            created=1_700_000_100,
            url="https://stripe.test/checkout-scale",
        )
        mock_existing_subs.return_value = []
        mock_ensure.return_value = (None, "absent")
        resp = self.client.get("/subscribe/scale/")

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-scale")

        kwargs = mock_session_create.call_args.kwargs
        self.assertNotIn("excluded_payment_method_types", kwargs)
        self.assertEqual(
            kwargs["payment_method_types"],
            PERSONAL_CHECKOUT_PAYMENT_METHOD_TYPES,
        )
        self.assertEqual(kwargs["metadata"]["flow_type"], "purchase")
        self.assertEqual(kwargs["subscription_data"]["metadata"]["flow_type"], "purchase")
        self.assertNotIn(STRIPE_CHECKOUT_FP_SUSPECT_SCORE_META_KEY, kwargs["subscription_data"]["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_COUNTRY_META_KEY, kwargs["subscription_data"]["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_PROXY_META_KEY, kwargs["subscription_data"]["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_TAMPERING_META_KEY, kwargs["subscription_data"]["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_BOT_META_KEY, kwargs["subscription_data"]["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_VISITOR_ID_META_KEY, kwargs["subscription_data"]["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_SUSPECT_SCORE_META_KEY, kwargs["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_COUNTRY_META_KEY, kwargs["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_PROXY_META_KEY, kwargs["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_TAMPERING_META_KEY, kwargs["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_BOT_META_KEY, kwargs["metadata"])
        self.assertNotIn(STRIPE_CHECKOUT_FP_VISITOR_ID_META_KEY, kwargs["metadata"])
        self.assertNotIn("trial_period_days", kwargs["subscription_data"])
        self.assertNotIn("billing_address_collection", kwargs)
        self.assertNotIn("name_collection", kwargs)
        self.assertNotIn("custom_text", kwargs)
        self.assertEqual(
            kwargs["line_items"],
            [{"price": "price_scale", "quantity": 1}],
        )
        customer_modify_args, customer_modify_kwargs = mock_customer_modify.call_args
        self.assertEqual(customer_modify_args[0], "cus_scale_trial")
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY],
            "purchase",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY],
            kwargs["metadata"]["gobii_event_id"],
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_PLAN_META_KEY],
            PlanNames.SCALE,
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_PLAN_LABEL_META_KEY],
            "Scale",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_VALUE_META_KEY],
            "250.0",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_CURRENCY_META_KEY],
            "USD",
        )
        self.assertTrue(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_SOURCE_URL_META_KEY]
        )
        for key, value in clear_checkout_fingerprint_metadata(customer_context=True).items():
            self.assertEqual(customer_modify_kwargs["metadata"][key], value)
        mock_record_checkout_context.assert_called_once_with(
            customer_id="cus_scale_trial",
            checkout_session_id="cs_purchase_checkout_context",
            session_created_at=1_700_000_100,
            flow_type="purchase",
            event_id=kwargs["metadata"]["gobii_event_id"],
            plan=PlanNames.SCALE,
            plan_label="Scale",
            value=250.0,
            currency="USD",
            checkout_source_url=kwargs["metadata"]["checkout_source_url"],
        )

    def test_scale_checkout_tracks_redirected_to_checkout_event(self):
        user = get_user_model().objects.create_user(
            email="redirected_scale@test.com",
            password="pw",
            username="redirected_scale_user",
        )
        self.client.force_login(user)

        with (
            patch(
                "pages.views.get_stripe_settings",
                return_value=SimpleNamespace(
                    scale_price_id="price_scale",
                    scale_trial_days=7,
                ),
            ),
            patch(
                "pages.views.get_or_create_stripe_customer",
                return_value=SimpleNamespace(id="cus_redirected_scale"),
            ),
            patch(
                "pages.views.Price.objects.get",
                return_value=MagicMock(unit_amount=25000, currency="usd"),
            ),
            patch(
                "pages.views._customer_has_price_subscription_with_cache",
                return_value=(False, []),
            ),
            patch("pages.views._is_individual_trial_eligible", return_value=False),
            patch("pages.views._prepare_stripe_or_404"),
            patch("pages.views._emit_checkout_initiated_event"),
            patch(
                "pages.views._create_checkout_session_with_customer_context",
                return_value=SimpleNamespace(url="https://stripe.test/checkout-scale"),
            ) as mock_create_checkout,
            patch("pages.views.Analytics.track_event") as mock_track_event,
        ):
            resp = self.client.get("/subscribe/scale/")

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-scale")
        mock_create_checkout.assert_called_once()
        mock_track_event.assert_called_once_with(
            user_id=user.id,
            event=AnalyticsEvent.REDIRECTED_TO_CHECKOUT,
            source=AnalyticsSource.WEB,
            properties={
                "plan_type": "scale",
                "trial_enabled": False,
            },
        )

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views._is_individual_trial_eligible", return_value=True)
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.stripe.Customer.modify")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    def test_startup_checkout_continues_when_customer_context_write_fails(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        mock_customer_modify,
        mock_ensure,
        _mock_trial_eligible,
        _,
    ):
        user = get_user_model().objects.create_user(
            email="trial_context_write_fail@test.com",
            password="pw",
            username="trial_context_write_fail_user",
        )
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            startup_price_id="price_startup",
            startup_additional_task_price_id="price_startup_meter",
            startup_trial_days=7,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_context_write_fail")
        mock_price_get.return_value = MagicMock(unit_amount=12000, currency="usd")
        mock_customer_modify.side_effect = page_views.stripe.error.APIConnectionError("boom")
        mock_session_create.return_value = MagicMock(url="https://stripe.test/checkout-startup")
        mock_ensure.return_value = (None, "absent")

        resp = self.client.get(reverse("proprietary:pro_checkout"))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-startup")
        mock_customer_modify.assert_called_once()
        mock_session_create.assert_called_once()

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views._is_individual_trial_eligible", return_value=True)
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.stripe.Customer.modify")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    def test_startup_checkout_uses_unknown_fp_metadata_when_latest_visit_is_stale(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        mock_customer_modify,
        mock_ensure,
        _mock_trial_eligible,
        _,
    ):
        user = get_user_model().objects.create_user(
            email="trial_stale_fp@test.com",
            password="pw",
            username="trial_stale_fp_user",
        )
        visit = UserFingerprintVisit.objects.create(
            user=user,
            source="signup",
            fingerprint_event_id="request-fp-stale",
            fingerprint_visitor_id="visitor-fp-stale",
            fetch_status=UserFingerprintVisitFetchStatusChoices.SUCCEEDED,
            suspect_score=7.0,
            country_code="VN",
            proxy=True,
            tampering=False,
            bot="bad",
        )
        UserFingerprintVisit.objects.filter(pk=visit.pk).update(
            event_timestamp=timezone.now() - timedelta(days=2),
        )
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            startup_price_id="price_startup",
            startup_additional_task_price_id="price_startup_meter",
            startup_trial_days=7,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_trial_stale_fp")
        mock_price_get.return_value = MagicMock(unit_amount=12000, currency="usd")
        mock_session_create.return_value = SimpleNamespace(
            id="cs_trial_stale_fp",
            created=1_700_000_000,
            url="https://stripe.test/checkout-startup",
        )
        mock_ensure.return_value = (None, "absent")

        resp = self.client.get(reverse("proprietary:pro_checkout"))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-startup")

        kwargs = mock_session_create.call_args.kwargs
        self.assertEqual(kwargs["metadata"][STRIPE_CHECKOUT_FP_SUSPECT_SCORE_META_KEY], "unknown")
        self.assertEqual(kwargs["metadata"][STRIPE_CHECKOUT_FP_COUNTRY_META_KEY], "unknown")
        self.assertEqual(kwargs["metadata"][STRIPE_CHECKOUT_FP_PROXY_META_KEY], "unknown")
        self.assertEqual(kwargs["metadata"][STRIPE_CHECKOUT_FP_TAMPERING_META_KEY], "unknown")
        self.assertEqual(kwargs["metadata"][STRIPE_CHECKOUT_FP_BOT_META_KEY], "unknown")
        self.assertEqual(kwargs["metadata"][STRIPE_CHECKOUT_FP_VISITOR_ID_META_KEY], "unknown")

        customer_modify_args, customer_modify_kwargs = mock_customer_modify.call_args
        self.assertEqual(customer_modify_args[0], "cus_trial_stale_fp")
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FP_SUSPECT_SCORE_META_KEY],
            "unknown",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FP_COUNTRY_META_KEY],
            "unknown",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FP_PROXY_META_KEY],
            "unknown",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FP_TAMPERING_META_KEY],
            "unknown",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FP_BOT_META_KEY],
            "unknown",
        )
        self.assertEqual(
            customer_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FP_VISITOR_ID_META_KEY],
            "unknown",
        )

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views._is_individual_trial_eligible", return_value=False)
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.get_existing_individual_subscriptions")
    @patch("pages.views.uuid.uuid4", return_value="cleanup-token")
    @patch("pages.views.stripe.Customer.retrieve")
    @patch("pages.views.stripe.Customer.modify")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    def test_scale_checkout_creation_failure_clears_customer_context(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        mock_customer_modify,
        mock_customer_retrieve,
        _mock_uuid4,
        mock_existing_subs,
        mock_ensure,
        _mock_trial_eligible,
        _,
    ):
        user = get_user_model().objects.create_user(
            email="scale_context_cleanup@test.com",
            password="pw",
            username="scale_context_cleanup_user",
        )
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            scale_price_id="price_scale",
            scale_additional_task_price_id="price_scale_meter",
            scale_trial_days=7,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_scale_context_cleanup")
        mock_price_get.return_value = MagicMock(unit_amount=25000, currency="usd")
        mock_existing_subs.return_value = []
        mock_ensure.return_value = (None, "absent")
        mock_session_create.side_effect = page_views.stripe.error.InvalidRequestError(
            "bad checkout session",
            "line_items",
        )
        mock_customer_retrieve.return_value = {
            "metadata": {
                STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY: "purchase",
                STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY: "scale-sub-cleanup-token",
            }
        }

        with self.assertRaises(page_views.stripe.error.InvalidRequestError):
            self.client.get("/subscribe/scale/")

        self.assertEqual(mock_customer_modify.call_count, 2)
        first_modify_args, first_modify_kwargs = mock_customer_modify.call_args_list[0]
        self.assertEqual(first_modify_args[0], "cus_scale_context_cleanup")
        self.assertEqual(
            first_modify_kwargs["metadata"][STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY],
            "purchase",
        )
        second_modify_args, second_modify_kwargs = mock_customer_modify.call_args_list[1]
        self.assertEqual(second_modify_args[0], "cus_scale_context_cleanup")
        self.assertEqual(
            second_modify_kwargs["metadata"],
            clear_checkout_customer_metadata(),
        )
        mock_customer_retrieve.assert_called_once()


@tag("batch_pages")
@override_settings(GOBII_PROPRIETARY_MODE=True)
class ProprietaryPricingTrialCopyTests(TestCase):
    def _get_pricing_context_for_user(self, user):
        from django.test.client import RequestFactory
        from proprietary.views import PricingView

        request = RequestFactory().get("/pricing/")
        request.user = user

        view = PricingView()
        view.setup(request)
        return view.get_context_data()

    @patch("proprietary.views.get_user_plan", return_value={"id": PlanNames.FREE})
    @patch("proprietary.views.evaluate_user_trial_eligibility", return_value=SimpleNamespace(decision="no_trial"))
    @patch("proprietary.views.get_stripe_settings")
    def test_pricing_cta_uses_subscribe_copy_when_trial_ineligible(
        self,
        mock_get_stripe_settings,
        _mock_trial_eligibility,
        _mock_get_user_plan,
    ):
        user = get_user_model().objects.create_user(
            email="pricing_ineligible@test.com",
            password="pw",
            username="pricing_ineligible_user",
        )
        self.client.force_login(user)
        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=7,
            scale_trial_days=14,
        )

        context = self._get_pricing_context_for_user(user)
        plans = context["pricing_plans"]
        self.assertEqual(plans[0]["cta"], "Subscribe to Pro")
        self.assertEqual(plans[1]["cta"], "Subscribe to Scale")

    @patch("proprietary.views.get_user_plan", return_value={"id": PlanNames.FREE})
    @patch("proprietary.views.evaluate_user_trial_eligibility", return_value=SimpleNamespace(decision="eligible"))
    @patch("proprietary.views.get_stripe_settings")
    def test_pricing_cta_shows_trial_copy_when_trial_eligible(
        self,
        mock_get_stripe_settings,
        _mock_trial_eligibility,
        _mock_get_user_plan,
    ):
        user = get_user_model().objects.create_user(
            email="pricing_eligible@test.com",
            password="pw",
            username="pricing_eligible_user",
        )
        self.client.force_login(user)
        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=7,
            scale_trial_days=14,
        )

        context = self._get_pricing_context_for_user(user)
        plans = context["pricing_plans"]
        self.assertEqual(plans[0]["cta"], "Start 7-day Free Trial")
        self.assertEqual(plans[1]["cta"], "Start 14-day Free Trial")

    @patch("proprietary.views.get_user_plan", return_value={"id": PlanNames.FREE})
    @patch("proprietary.views.evaluate_user_trial_eligibility", return_value=SimpleNamespace(decision="eligible"))
    @patch("proprietary.views.get_stripe_settings")
    def test_pricing_cta_omits_trial_days_when_flag_enabled(
        self,
        mock_get_stripe_settings,
        _mock_trial_eligibility,
        _mock_get_user_plan,
    ):
        user = get_user_model().objects.create_user(
            email="pricing_flagged@test.com",
            password="pw",
            username="pricing_flagged_user",
        )
        self.client.force_login(user)
        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=7,
            scale_trial_days=14,
        )

        with override_flag("cta_start_free_trial", active=True):
            context = self._get_pricing_context_for_user(user)

        plans = context["pricing_plans"]
        self.assertEqual(plans[0]["cta"], "Start Free Trial")
        self.assertEqual(plans[1]["cta"], "Start Free Trial")


@tag("batch_pages")
class AuthLinkTests(TestCase):
    MODAL_REQUEST_HEADER = {"HTTP_X_GOBII_AUTH_MODAL": "1"}

    @staticmethod
    def _create_social_app(provider: str) -> SocialApp:
        app_kwargs = {
            "provider": provider,
            "name": f"{provider}-oauth",
            "client_id": "dummy-client",
            "secret": "dummy-secret",
        }
        if provider == "linkedin":
            app_kwargs.update(
                {
                    "provider": "openid_connect",
                    "provider_id": "linkedin",
                    "name": "LinkedIn",
                    "settings": {"server_url": "https://www.linkedin.com/oauth"},
                }
            )
        app = SocialApp.objects.create(
            **app_kwargs,
        )
        app.sites.add(Site.objects.get_current())
        return app

    def test_auth_url_with_utms_preserves_existing_query_and_fragment(self):
        request = SimpleNamespace(
            session={
                "utm_querystring": "?utm_source=newsletter&utm_campaign=fall",
            }
        )

        url = page_views._auth_url_with_utms("/accounts/signup/?existing=1#top", request)
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        self.assertEqual(parsed.path, "/accounts/signup/")
        self.assertEqual(parsed.fragment, "top")
        self.assertEqual(params.get("existing"), ["1"])
        self.assertEqual(params.get("utm_source"), ["newsletter"])
        self.assertEqual(params.get("utm_campaign"), ["fall"])

    def test_signup_page_signin_link_includes_utms(self):
        session = self.client.session
        session["utm_querystring"] = "utm_source=newsletter"
        session.save()

        next_url = reverse("agent_quick_spawn")
        response = self.client.get(reverse("account_signup"), {"next": next_url})
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        match = re.search(
            r"Already have an account\\?.*?href=\"([^\"]+)\"[^>]*>Sign in</a>",
            content,
            re.S,
        )
        self.assertIsNotNone(match)
        href = match.group(1)
        parsed = urlparse(href)
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.path, reverse("account_login"))
        self.assertEqual(params.get("utm_source"), ["newsletter"])
        self.assertEqual(params.get("next"), [next_url])

    def test_login_page_signup_link_includes_utms(self):
        session = self.client.session
        session["utm_querystring"] = "utm_campaign=fall"
        session.save()

        next_url = reverse("agent_quick_spawn")
        response = self.client.get(reverse("account_login"), {"next": next_url})
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        match = re.search(
            r"Don't have an account yet\\?.*?href=\"([^\"]+)\"[^>]*>Sign up here</a>",
            content,
            re.S,
        )
        self.assertIsNotNone(match)
        href = match.group(1)
        parsed = urlparse(href)
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.path, reverse("account_signup"))
        self.assertEqual(params.get("utm_campaign"), ["fall"])
        self.assertEqual(params.get("next"), [next_url])

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        FINGERPRINT_JS_ENABLED=True,
        FINGERPRINT_JS_URL="https://fp.example/v3/loader.js",
        FINGERPRINT_JS_API_KEY="fp_test_key",
        GA_MEASUREMENT_ID="G-TEST1234",
    )
    def test_signup_page_waits_for_client_signals_before_password_submit(self):
        response = self.client.get(reverse("account_signup"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "js/account_identity_signals.js")
        self.assertContains(response, "js/account_auth_forms.js")
        self.assertContains(response, "data-account-auth-root")
        self.assertContains(response, "data-password-signup-form")
        self.assertContains(response, 'data-fpjs-enabled="true"')
        self.assertContains(response, 'data-fpjs-loader-url="https://fp.example/v3/loader.js?apiKey=fp_test_key"')
        self.assertContains(response, 'data-auth-fpjs-visitor-field')
        self.assertContains(response, 'data-auth-fpjs-request-field')
        self.assertContains(response, 'data-auth-ga-client-field')

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        FINGERPRINT_JS_ENABLED=True,
        FINGERPRINT_JS_URL="https://fp.example/v3/loader.js",
        FINGERPRINT_JS_API_KEY="fp_test_key",
        GA_MEASUREMENT_ID="G-TEST1234",
    )
    def test_login_page_renders_social_auth_signal_staging_script(self):
        response = self.client.get(reverse("account_login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "js/account_identity_signals.js")
        self.assertContains(response, "js/account_auth_forms.js")
        self.assertContains(response, 'data-account-auth-root')
        self.assertContains(response, 'data-fpjs-enabled="true"')
        self.assertContains(response, 'data-fpjs-loader-url="https://fp.example/v3/loader.js?apiKey=fp_test_key"')

    def test_login_page_renders_configured_social_providers_in_fixed_order_with_tracking_attrs(self):
        for provider in ("facebook", "google", "linkedin", "microsoft"):
            self._create_social_app(provider)

        response = self.client.get(reverse("account_login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-analytics-cta-tracking-enabled="true"')

        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        buttons = soup.select("a[data-social-provider]")
        provider_ids = [button["data-social-provider"] for button in buttons]

        self.assertEqual(provider_ids, ["google", "facebook", "microsoft", "linkedin"])
        for button in buttons:
            provider_id = button["data-social-provider"]
            self.assertTrue(button.has_attr("data-social-auth-link"))
            self.assertEqual(button["data-social-surface"], "login")
            self.assertEqual(button["data-analytics-intent"], "social_auth")
            self.assertEqual(button["data-analytics-auth-provider"], provider_id)
            self.assertEqual(button["data-analytics-auth-surface"], "login")

    def test_signup_page_omits_unconfigured_social_providers(self):
        for provider in ("facebook", "google"):
            self._create_social_app(provider)

        response = self.client.get(reverse("account_signup"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-analytics-cta-tracking-enabled="true"')

        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        buttons = soup.select("a[data-social-provider]")
        provider_ids = [button["data-social-provider"] for button in buttons]

        self.assertEqual(provider_ids, ["google", "facebook"])
        self.assertNotIn("linkedin", provider_ids)
        self.assertNotIn("microsoft", provider_ids)
        for button in buttons:
            self.assertTrue(button.has_attr("data-social-signup-link"))
            self.assertEqual(button["data-social-surface"], "signup")
            self.assertEqual(button["data-analytics-auth-surface"], "signup")

    def test_social_signup_completion_page_uses_custom_template(self):
        app = self._create_social_app("linkedin")
        request = RequestFactory().get(reverse("openid_connect_login", kwargs={"provider_id": "linkedin"}))
        provider = app.get_provider(request)
        sociallogin = provider.sociallogin_from_response(
            request,
            {
                "userinfo": {
                    "sub": "linkedin-user-123",
                    "given_name": "Pat",
                    "family_name": "Lee",
                },
            },
        )

        session = self.client.session
        session["socialaccount_sociallogin"] = sociallogin.serialize()
        session.save()

        response = self.client.get(reverse("socialaccount_signup"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Complete sign up")
        self.assertContains(response, "You started with LinkedIn. Finish creating your account below.")
        self.assertContains(response, f'action="{reverse("socialaccount_signup")}"')
        self.assertContains(response, "bg-white max-w-md")

    def test_signup_modal_renders_email_start_and_popup_social_urls(self):
        for provider in ("facebook", "google"):
            self._create_social_app(provider)

        next_url = "/app/agents/new?spawn=1"
        response = self.client.get(
            reverse("account_signup_modal"),
            {"next": next_url},
            **self.MODAL_REQUEST_HEADER,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-auth-mode="modal"')
        self.assertContains(response, 'data-auth-email-start-form')
        self.assertContains(response, f'action="{reverse("account_signup_modal")}?next=%2Fapp%2Fagents%2Fnew%3Fspawn%3D1"')
        self.assertContains(response, "Log in or sign up")

        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        email_start_form = soup.select_one("form[data-auth-email-start-form]")
        self.assertIsNotNone(email_start_form)
        self.assertEqual(email_start_form["data-analytics-cta-id"], "auth_modal_email_start_continue")
        self.assertEqual(email_start_form["data-analytics-placement"], "auth_modal_start")
        self.assertEqual(email_start_form["data-analytics-intent"], "continue_with_email")

        buttons = soup.select("a[data-social-provider]")
        self.assertEqual([button["data-social-provider"] for button in buttons], ["google", "facebook"])
        for button in buttons:
            self.assertEqual(button["data-auth-social-popup"], "true")
            self.assertEqual(button["data-analytics-placement"], "auth_modal_start")
            self.assertEqual(button["data-analytics-auth-surface"], "modal_start")
            self.assertEqual(
                button["data-analytics-cta-id"],
                f"auth_modal_start_social_{button['data-social-provider']}",
            )
            parsed = urlparse(button["href"])
            params = parse_qs(parsed.query)
            popup_next = params.get("next", [None])[0]
            self.assertIsNotNone(popup_next)
            self.assertEqual(urlparse(popup_next).path, reverse("account_auth_popup_complete"))

    def test_signup_modal_email_continue_routes_existing_account_to_login(self):
        user = get_user_model().objects.create_user(
            username="email-first@example.com",
            email="email-first@example.com",
            password="password123",
        )
        response = self.client.post(
            reverse("account_signup_modal"),
            {
                "email_first": "1",
                "email": user.email,
                "next": "/pricing/",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            **self.MODAL_REQUEST_HEADER,
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        parsed = urlparse(payload["auth_url"])
        self.assertEqual(parsed.path, reverse("account_login_modal"))
        params = parse_qs(parsed.query)
        self.assertEqual(params.get("email"), [user.email])
        self.assertEqual(params.get("lock_email"), ["1"])
        self.assertEqual(params.get("next"), ["/pricing/"])

    def test_signup_modal_email_continue_routes_new_email_to_signup_password_step(self):
        response = self.client.post(
            reverse("account_signup_modal"),
            {
                "email_first": "1",
                "email": "new-user@example.com",
                "next": "/pricing/",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            **self.MODAL_REQUEST_HEADER,
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        parsed = urlparse(payload["auth_url"])
        self.assertEqual(parsed.path, reverse("account_signup_modal"))
        params = parse_qs(parsed.query)
        self.assertEqual(params.get("email"), ["new-user@example.com"])
        self.assertEqual(params.get("lock_email"), ["1"])
        self.assertEqual(params.get("step"), ["password"])
        self.assertEqual(params.get("next"), ["/pricing/"])

    def test_auth_modal_fragments_are_noindex(self):
        for route_name in ("account_signup_modal", "account_login_modal"):
            response = self.client.get(reverse(route_name), **self.MODAL_REQUEST_HEADER)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow, noarchive")

    def test_auth_modal_fragments_require_modal_header(self):
        for route_name in ("account_signup_modal", "account_login_modal"):
            response = self.client.get(reverse(route_name))

            self.assertEqual(response.status_code, 404)
            self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow, noarchive")

    @modify_settings(INSTALLED_APPS={"append": "turnstile"})
    @override_settings(
        TURNSTILE_ENABLED=True,
        ACCOUNT_FORMS={
            "signup": "turnstile_signup.SignupFormWithTurnstile",
            "login": "turnstile_signup.LoginFormWithTurnstile",
        },
    )
    def test_modal_password_steps_render_turnstile_and_autocomplete_attrs(self):
        login_response = self.client.get(
            reverse("account_login_modal"),
            {"email": "saved@example.com", "lock_email": "1", "next": "/pricing/"},
            **self.MODAL_REQUEST_HEADER,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertContains(login_response, "cf-turnstile")
        self.assertNotContains(login_response, "turnstile/v0/api.js")
        self.assertContains(login_response, 'autocomplete="username"')
        self.assertContains(login_response, 'autocomplete="current-password"')
        self.assertContains(
            login_response,
            f'data-auth-modal-url="{reverse("account_signup_modal")}?next=%2Fpricing%2F&amp;email=saved%40example.com"',
        )
        login_soup = BeautifulSoup(login_response.content.decode("utf-8"), "html.parser")
        login_back = login_soup.select_one('button[data-analytics-cta-id="auth_modal_login_back"]')
        self.assertIsNotNone(login_back)
        self.assertEqual(login_back["data-analytics-placement"], "auth_modal_login")
        self.assertEqual(login_back["data-analytics-intent"], "back_to_start")
        login_form = login_soup.select_one('form[data-analytics-cta-id="auth_modal_login_submit"]')
        self.assertIsNotNone(login_form)
        self.assertEqual(login_form["data-analytics-placement"], "auth_modal_login")
        self.assertEqual(login_form["data-analytics-intent"], "log_in")
        login_button = login_form.select_one("button[data-auth-modal-submit]")
        self.assertIsNotNone(login_button)
        self.assertEqual(login_button["aria-busy"], "false")
        self.assertEqual(login_button.select_one("[data-auth-modal-submit-label]").get_text(strip=True), "Sign in")
        self.assertEqual(
            login_button.select_one("[data-auth-modal-submit-pending-label]").get_text(strip=True),
            "Signing in...",
        )
        self.assertIsNotNone(login_button.select_one("[data-auth-modal-submit-spinner]"))

        signup_response = self.client.get(
            reverse("account_signup_modal"),
            {"step": "password", "email": "saved@example.com", "lock_email": "1", "next": "/pricing/"},
            **self.MODAL_REQUEST_HEADER,
        )
        self.assertEqual(signup_response.status_code, 200)
        self.assertContains(signup_response, "cf-turnstile")
        self.assertNotContains(signup_response, "turnstile/v0/api.js")
        self.assertContains(signup_response, 'autocomplete="email"')
        self.assertContains(signup_response, 'autocomplete="new-password"')
        self.assertContains(
            signup_response,
            f'data-auth-modal-url="{reverse("account_signup_modal")}?next=%2Fpricing%2F&amp;email=saved%40example.com"',
        )
        signup_soup = BeautifulSoup(signup_response.content.decode("utf-8"), "html.parser")
        signup_back = signup_soup.select_one('button[data-analytics-cta-id="auth_modal_signup_back"]')
        self.assertIsNotNone(signup_back)
        self.assertEqual(signup_back["data-analytics-placement"], "auth_modal_signup")
        self.assertEqual(signup_back["data-analytics-intent"], "back_to_start")
        signup_form = signup_soup.select_one('form[data-analytics-cta-id="auth_modal_signup_submit"]')
        self.assertIsNotNone(signup_form)
        self.assertEqual(signup_form["data-analytics-placement"], "auth_modal_signup")
        self.assertEqual(signup_form["data-analytics-intent"], "sign_up")
        signup_button = signup_form.select_one("button[data-auth-modal-submit]")
        self.assertIsNotNone(signup_button)
        self.assertEqual(signup_button["aria-busy"], "false")
        self.assertEqual(
            signup_button.select_one("[data-auth-modal-submit-label]").get_text(strip=True),
            "Create account",
        )
        self.assertEqual(
            signup_button.select_one("[data-auth-modal-submit-pending-label]").get_text(strip=True),
            "Creating account...",
        )
        self.assertIsNotNone(signup_button.select_one("[data-auth-modal-submit-spinner]"))

    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_login_modal_invalid_post_preserves_tab_and_next(self, _mock_turnstile_validate):
        next_url = "/pricing/"
        response = self.client.post(
            reverse("account_login_modal"),
            {
                "login": "missing@example.com",
                "password": "wrong-password",
                "cf-turnstile-response": "stub-token",
                "next": next_url,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            **self.MODAL_REQUEST_HEADER,
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("html", payload)
        self.assertIn('data-auth-mode="modal"', payload["html"])
        self.assertIn(f'value="{next_url}"', payload["html"])
        self.assertIn(reverse("account_login_modal"), payload["html"])

    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_login_modal_valid_post_returns_next_location(self, _mock_turnstile_validate):
        user = get_user_model().objects.create_user(
            username="modal-login@example.com",
            email="modal-login@example.com",
            password="password123",
        )
        checkout_url = reverse("proprietary:startup_checkout")

        response = self.client.post(
            reverse("account_login_modal"),
            {
                "login": user.email,
                "password": "password123",
                "cf-turnstile-response": "stub-token",
                "next": checkout_url,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            **self.MODAL_REQUEST_HEADER,
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("location"), checkout_url)

    @override_settings(ACCOUNT_EMAIL_VERIFICATION="none")
    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_signup_modal_valid_post_returns_next_location(self, _mock_turnstile_validate):
        checkout_url = reverse("proprietary:startup_checkout")
        response = self.client.post(
            reverse("account_signup_modal"),
            {
                "email": "modal-signup@example.com",
                "password1": "password12345",
                "password2": "password12345",
                "cf-turnstile-response": "stub-token",
                "next": checkout_url,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            **self.MODAL_REQUEST_HEADER,
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("location"), checkout_url)

    def test_popup_complete_page_uses_auth_popup_template(self):
        response = self.client.get(reverse("account_auth_popup_complete"), {"auth_popup_state": "test-state"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertContains(response, '<meta name="robots" content="noindex, nofollow">')
        self.assertContains(response, "Completing sign in")
        self.assertContains(response, "js/account_auth_popup_complete.js")


@tag("batch_pages")
class LoginTurnstilePageTests(TestCase):
    def _login_request(self, *, ajax=False, user_agent="Chrome Mac Test/1.0"):
        request = RequestFactory().post(reverse("account_login"))
        request.META["HTTP_USER_AGENT"] = user_agent
        if ajax:
            request.META["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"
        return request

    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_invalid_login_post_logs_missing_password_diagnostics(self, _mock_turnstile_validate):
        from turnstile_signup import LoginFormWithTurnstile

        form = LoginFormWithTurnstile(
            data={
                "login": "john@example.com",
                "cf-turnstile-response": "stub-token",
            },
            request=self._login_request(ajax=True, user_agent="Mozilla/5.0 Macintosh"),
        )

        with self.assertLogs("turnstile_signup", level="WARNING") as captured:
            self.assertFalse(form.is_valid())

        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertIn("Invalid login POST", record.getMessage())
        self.assertIn("login=j***@example.com", record.getMessage())
        self.assertIn("error_fields=password", record.getMessage())
        self.assertIn("password_present=false", record.getMessage())
        self.assertIn("turnstile_token_present=true", record.getMessage())
        self.assertIn("ajax=true", record.getMessage())
        self.assertIn("user_agent=Mozilla/5.0 Macintosh", record.getMessage())
        self.assertFalse(record.password_present)
        self.assertTrue(record.turnstile_token_present)
        self.assertEqual(record.error_fields, "password")

    def test_invalid_login_post_logs_missing_turnstile_diagnostics(self):
        from turnstile_signup import LoginFormWithTurnstile

        form = LoginFormWithTurnstile(
            data={
                "login": "john@example.com",
                "password": "secret-password",
            },
            request=self._login_request(user_agent="Chrome Mac Test/1.0"),
        )

        with self.assertLogs("turnstile_signup", level="WARNING") as captured:
            self.assertFalse(form.is_valid())

        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertIn("Invalid login POST", record.getMessage())
        self.assertIn("error_fields=turnstile", record.getMessage())
        self.assertIn("password_present=true", record.getMessage())
        self.assertIn("turnstile_token_present=false", record.getMessage())
        self.assertIn("ajax=false", record.getMessage())
        self.assertTrue(record.password_present)
        self.assertFalse(record.turnstile_token_present)
        self.assertEqual(record.error_fields, "turnstile")

    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_invalid_login_post_logs_once_per_form_instance(self, _mock_turnstile_validate):
        from turnstile_signup import LoginFormWithTurnstile

        form = LoginFormWithTurnstile(
            data={
                "login": "john@example.com",
                "cf-turnstile-response": "stub-token",
            },
            request=self._login_request(),
        )

        with self.assertLogs("turnstile_signup", level="WARNING") as captured:
            self.assertFalse(form.is_valid())
            self.assertFalse(form.is_valid())

        self.assertEqual(len(captured.records), 1)

    @patch("turnstile.fields.TurnstileField.validate", return_value=None)
    def test_invalid_login_post_sanitizes_and_truncates_redacted_login(self, _mock_turnstile_validate):
        from turnstile_signup import LoginFormWithTurnstile

        crafted_login = f"john@example.com\nturnstile_token_present=true{'x' * 180}"
        form = LoginFormWithTurnstile(
            data={
                "login": crafted_login,
                "cf-turnstile-response": "stub-token",
            },
            request=self._login_request(),
        )

        with self.assertLogs("turnstile_signup", level="WARNING") as captured:
            self.assertFalse(form.is_valid())

        record = captured.records[0]
        self.assertLessEqual(len(record.login), 120)
        self.assertNotIn("\n", record.login)
        self.assertNotIn("turnstile_token_present=true", record.login)
        self.assertIn("login=j***@example.com_turnstile_token_present_true", record.getMessage())

    def test_login_turnstile_success_does_not_auto_submit(self):
        with open("static/js/account_auth_forms.js", encoding="utf-8") as js_file:
            script = js_file.read()

        success_callback_match = re.search(
            r"window\.gobiiLoginTurnstileSuccess\s*=\s*function\s*\(\)\s*\{(?P<body>.*?)^\s*\};",
            script,
            flags=re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(success_callback_match)
        success_callback = success_callback_match.group("body")

        self.assertNotIn("finalizeLoginSubmit", script)
        self.assertNotIn("requestSubmit", success_callback)
        self.assertNotIn("submitPending", success_callback)
        self.assertIn("Complete the verification, then sign in.", script)

    def test_modal_auth_navigation_fallback_uses_full_auth_pages(self):
        with open("static/js/account_auth_forms.js", encoding="utf-8") as js_file:
            script = js_file.read()

        self.assertIn('parsed.pathname === "/accounts/modal/signup/"', script)
        self.assertIn('parsed.pathname = "/accounts/signup/"', script)
        self.assertIn('parsed.pathname === "/accounts/modal/login/"', script)
        self.assertIn('parsed.pathname = "/accounts/login/"', script)
        self.assertIn("window.location.assign(getModalNavFallbackUrl(modalUrl));", script)
        self.assertNotIn("window.location.assign(modalUrl);", script)

    @modify_settings(INSTALLED_APPS={"append": "turnstile"})
    @override_settings(
        TURNSTILE_ENABLED=True,
        ACCOUNT_FORMS={
            "signup": "turnstile_signup.SignupFormWithTurnstile",
            "login": "turnstile_signup.LoginFormWithTurnstile",
        },
    )
    def test_login_page_disables_submit_until_fresh_turnstile_token(self):
        response = self.client.get(reverse("account_login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-turnstile-submit")
        self.assertContains(response, 'disabled aria-disabled="true"')
        self.assertContains(response, "data-turnstile-status")
        self.assertContains(response, "js/account_auth_forms.js")
        self.assertContains(response, "turnstile/v0/api.js?render=explicit")
        self.assertEqual(response.content.decode("utf-8").count("turnstile/v0/api.js"), 1)
        self.assertContains(response, 'data-expired-callback="gobiiLoginTurnstileExpired"')
        self.assertContains(response, 'data-timeout-callback="gobiiLoginTurnstileExpired"')
        self.assertContains(response, 'data-error-callback="gobiiLoginTurnstileError"')
        self.assertContains(response, 'data-callback="gobiiLoginTurnstileSuccess"')

    @modify_settings(INSTALLED_APPS={"append": "turnstile"})
    @override_settings(
        TURNSTILE_ENABLED=True,
        ACCOUNT_FORMS={
            "signup": "turnstile_signup.SignupFormWithTurnstile",
            "login": "turnstile_signup.LoginFormWithTurnstile",
        },
    )
    def test_signup_page_uses_single_explicit_turnstile_api_script(self):
        response = self.client.get(reverse("account_signup"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cf-turnstile")
        self.assertContains(response, "turnstile/v0/api.js?render=explicit")
        self.assertEqual(response.content.decode("utf-8").count("turnstile/v0/api.js"), 1)
@tag("batch_pages")
class MarketingMetaTests(TestCase):
    def test_terms_meta_description(self):
        response = self.client.get("/tos/")
        self.assertContains(
            response,
            "<meta name=\"description\" content=\"Review Gobii's Terms of Service covering usage policies, billing, and compliance for our pretrained worker platform.\">",
        )

    def test_privacy_meta_description(self):
        response = self.client.get("/privacy/")
        self.assertContains(
            response,
            "<meta name=\"description\" content=\"Understand how Gobii collects, uses, and safeguards data across our pretrained worker platform.\">",
        )



    def test_careers_meta_description(self):
        response = self.client.get("/careers/")
        self.assertContains(
            response,
            "<meta name=\"description\" content=\"Join Gobii to build AI coworkers that browse, research, and automate the web for organizations worldwide.\">",
        )


    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.get_existing_individual_subscriptions")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    @override_settings(
        CAPI_LTV_MULTIPLE=5.0,
        CAPI_START_TRIAL_CONV_RATE=0.322,
        CAPI_START_TRIAL_SCALE_CONV_RATE=0.22,
    )
    def test_switching_from_startup_redirects_to_billing(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        mock_existing_subs,
        mock_ensure,
        _,
    ):
        user = get_user_model().objects.create_user(email="scale@test.com", password="pw", username="scale_user")
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            scale_price_id="price_scale",
            scale_additional_task_price_id="price_scale_meter",
        )
        mock_customer.return_value = SimpleNamespace(id="cus_scale")
        mock_price_get.return_value = MagicMock(unit_amount=25000, currency="usd")
        mock_session_create.return_value = MagicMock(url="https://stripe.test/checkout-scale")
        mock_ensure.return_value = ({"id": "sub_updated"}, "updated")

        mock_existing_subs.return_value = [
            {
                "id": "sub_startup",
                "items": {"data": [{"price": {"id": "price_startup", "usage_type": "licensed"}}]},
            }
        ]

        resp = self.client.get("/subscribe/scale/")

        self.assertEqual(resp.status_code, 302)
        parsed = urlparse(resp["Location"])
        params = parse_qs(parsed.query)

        self.assertEqual(parsed.path, "/app/billing")
        self.assertEqual(params.get("subscribe_success"), ["1"])
        self.assertEqual(params.get("p"), ["275.00"])
        self.assertTrue(params.get("eid"))
        self.assertTrue(params["eid"][0].startswith("scale-sub-"))
        mock_ensure.assert_called_once()
        ensure_kwargs = mock_ensure.call_args.kwargs
        self.assertNotIn("metered_price_id", ensure_kwargs)
        mock_session_create.assert_not_called()


@tag("batch_pages")
class SubscriptionPriceParsingTests(TestCase):
    def test_get_price_info_from_item_handles_dict(self):
        item = {"price": {"id": "price_123", "usage_type": "licensed"}}
        price_id, usage = page_views._get_price_info_from_item(item)
        self.assertEqual(price_id, "price_123")
        self.assertEqual(usage, "licensed")

    def test_get_price_info_from_item_handles_string(self):
        item = {"price": "price_string"}
        price_id, usage = page_views._get_price_info_from_item(item)
        self.assertEqual(price_id, "price_string")
        self.assertEqual(usage, "")

    def test_subscription_contains_price_ignores_metered(self):
        sub = {
            "items": {
                "data": [
                    {"price": {"id": "price_meter", "usage_type": "metered"}},
                    {"price": {"id": "price_target", "usage_type": "licensed"}},
                ]
            }
        }
        self.assertTrue(page_views._subscription_contains_price(sub, "price_target"))
        self.assertFalse(page_views._subscription_contains_price(sub, "price_meter"))

    def test_subscription_contains_meter_price_only_metered(self):
        sub = {
            "items": {
                "data": [
                    {"price": {"id": "price_meter", "usage_type": "metered"}},
                    {"price": {"id": "price_meter", "usage_type": "licensed"}},
                ]
            }
        }
        self.assertTrue(page_views._subscription_contains_meter_price(sub, "price_meter"))
        self.assertFalse(page_views._subscription_contains_meter_price(sub, "price_missing"))

    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.get_existing_individual_subscriptions")
    @patch("pages.views.stripe.checkout.Session.create")
    @patch("pages.views.Price.objects.get")
    @patch("pages.views.get_or_create_stripe_customer")
    @patch("pages.views.get_stripe_settings")
    @override_settings(
        CAPI_LTV_MULTIPLE=5.0,
        CAPI_START_TRIAL_CONV_RATE=0.322,
        CAPI_START_TRIAL_SCALE_CONV_RATE=0.22,
    )
    def test_existing_scale_subscription_short_circuits_checkout(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        mock_existing_subs,
        mock_ensure,
        _,
    ):
        user = get_user_model().objects.create_user(email="scale2@test.com", password="pw", username="scale_user_2")
        self.client.force_login(user)

        mock_stripe_settings.return_value = SimpleNamespace(
            scale_price_id="price_scale",
            scale_additional_task_price_id=None,
        )
        mock_customer.return_value = SimpleNamespace(id="cus_scale")
        mock_price_get.return_value = MagicMock(unit_amount=25000, currency="usd")
        mock_session_create.return_value = MagicMock(url="https://stripe.test/checkout-scale")
        mock_ensure.return_value = ({"id": "sub_updated"}, "updated")

        mock_existing_subs.return_value = [
            {
                "id": "sub_scale",
                "items": {"data": [{"price": {"id": "price_scale", "usage_type": "licensed"}}]},
            }
        ]

        resp = self.client.get("/subscribe/scale/")

        self.assertEqual(resp.status_code, 302)
        parsed = urlparse(resp["Location"])
        params = parse_qs(parsed.query)

        self.assertEqual(parsed.path, "/app/billing")
        self.assertEqual(params.get("subscribe_success"), ["1"])
        self.assertEqual(params.get("p"), ["275.00"])
        self.assertTrue(params.get("eid"))
        self.assertTrue(params["eid"][0].startswith("scale-sub-"))
        mock_ensure.assert_called_once()
        ensure_kwargs = mock_ensure.call_args.kwargs
        self.assertNotIn("metered_price_id", ensure_kwargs)
        mock_session_create.assert_not_called()
