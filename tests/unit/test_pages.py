from urllib.parse import parse_qs, urlparse
import re
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from bs4 import BeautifulSoup
from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sites.models import Site
from django.core import signing
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, modify_settings, override_settings, tag
from django.urls import reverse
from waffle.testutils import override_flag
from api.models import BrowserUseAgent, MCPServerConfig, PersistentAgent, UserBilling, UserFlags
from config.socialaccount_adapter import (
    OAUTH_ATTRIBUTION_COOKIE,
    OAUTH_CHARTER_COOKIE,
    OAUTH_CHARTER_SERVER_SIDE_TOKEN_KEY,
    build_oauth_charter_stash_cache_key,
)
from pages import views as page_views
from pages.models import LandingPage
from agents.services import PretrainedWorkerTemplateService
from config.redis_client import get_redis_client
from billing.checkout_metadata import (
    STRIPE_CHECKOUT_CUSTOMER_CURRENCY_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_PLAN_LABEL_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_PLAN_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_SOURCE_URL_META_KEY,
    STRIPE_CHECKOUT_CUSTOMER_VALUE_META_KEY,
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


@tag("batch_pages")
class HomePageTests(TestCase):
    @staticmethod
    def _normalized_button_text(button) -> str:
        return " ".join(
            segment for segment in button.stripped_strings if segment and segment != "→"
        ).strip()

    @tag("batch_pages")
    def test_home_page_renders(self):
        """Basic smoke test for home page."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    @tag("batch_pages")
    def test_home_page_shows_fish_in_both_modes(self):
        """The Gobii fish mascot should render in both proprietary and community modes."""
        for proprietary_mode in (False, True):
            with self.subTest(proprietary_mode=proprietary_mode):
                with override_settings(GOBII_PROPRIETARY_MODE=proprietary_mode):
                    response = self.client.get("/")
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, 'data-gobii-fish-cursor')

    @tag("batch_pages")
    def test_home_page_has_meta_description(self):
        response = self.client.get("/")
        self.assertContains(
            response,
            '<meta name="description" content="Gobii agents are virtual coworkers with their own identity, memory, and tools. Email them, text them — they browse the web, collect data, and deliver reports 24/7.">',
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @tag("batch_pages")
    def test_home_page_uses_legacy_hero_illustration_when_fish_homepage_is_off(self):
        with override_flag("fish_homepage", active=False):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        legacy_hero_image = soup.find("img", {"src": "/static/images/undraw/texting.svg"})
        self.assertIsNotNone(legacy_hero_image)
        self.assertIsNone(soup.select_one("[data-gobii-fish-cursor]"))

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @tag("batch_pages")
    def test_home_page_uses_fish_hero_animation_when_fish_homepage_is_on(self):
        with override_flag("fish_homepage", active=True):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        self.assertIsNotNone(soup.select_one("[data-gobii-fish-cursor]"))
        self.assertIsNone(soup.find("img", {"src": "/static/images/undraw/texting.svg"}))

    @tag("batch_pages")
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

    @tag("batch_pages")
    def test_home_page_exposes_all_pretrained_workers(self):
        templates = PretrainedWorkerTemplateService.get_active_templates()
        response = self.client.get("/")
        workers = response.context.get("homepage_pretrained_workers")

        self.assertIsNotNone(workers)
        self.assertEqual(len(workers), len(templates))
        self.assertEqual(response.context.get("homepage_pretrained_total"), len(templates))
        self.assertEqual(response.context.get("homepage_pretrained_filtered_count"), len(templates))

    @tag("batch_pages")
    def test_home_page_filters_by_category(self):
        templates = PretrainedWorkerTemplateService.get_active_templates()
        category = None
        for template in templates:
            if template.category:
                category = template.category
                break

        if not category:
            self.skipTest("No pretrained worker templates expose a category for filtering")

        expected = [template for template in templates if template.category == category]

        response = self.client.get("/", {"pretrained_category": category})
        workers = response.context.get("homepage_pretrained_workers")

        self.assertEqual(len(workers), len(expected))
        self.assertTrue(all(worker.category == category for worker in workers))
        self.assertEqual(response.context.get("homepage_pretrained_filtered_count"), len(expected))
        self.assertEqual(response.context.get("homepage_pretrained_total"), len(templates))

    @tag("batch_pages")
    def test_home_page_filters_by_search(self):
        templates = PretrainedWorkerTemplateService.get_active_templates()
        self.assertGreater(len(templates), 0)
        target = templates[0]
        search_term = target.display_name

        expected = [
            template
            for template in templates
            if search_term.lower() in template.display_name.lower()
            or search_term.lower() in template.tagline.lower()
            or search_term.lower() in template.description.lower()
        ]

        response = self.client.get("/", {"pretrained_search": search_term})
        workers = response.context.get("homepage_pretrained_workers")

        self.assertEqual(len(workers), len(expected))
        self.assertEqual(response.context.get("homepage_pretrained_filtered_count"), len(expected))

    @patch("pages.views.get_homepage_integrations_payload", return_value={"enabled": False, "builtins": []})
    @tag("batch_pages")
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
    @tag("batch_pages")
    def test_home_page_hides_integrations_when_pipedream_server_exists_but_config_is_missing(self):
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
        self.assertFalse(response.context.get("homepage_integrations_enabled"))
        self.assertNotContains(response, "Search more integrations")

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
    @tag("batch_pages")
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
                "selectedFieldsContainerId": "homepage-integrations-selected-fields",
            },
        )
        self.assertEqual(
            [app["slug"] for app in response.context.get("homepage_integrations_inline_builtins")],
            ["linkedin", "google_sheets", "trello", "slack"],
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
    @tag("batch_pages")
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
    @tag("batch_pages")
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
    @tag("batch_pages")
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
    @tag("batch_pages")
    def test_home_cta_text_changes_for_authenticated_users(self):
        unauth_response = self.client.get("/")
        self.assertEqual(unauth_response.status_code, 200)
        unauth_soup = BeautifulSoup(unauth_response.content, "html.parser")
        unauth_hero_form = unauth_soup.find("form", {"id": "create-agent-form"})
        self.assertIsNotNone(unauth_hero_form)
        unauth_hero_button = unauth_hero_form.find("button", {"type": "submit"})
        self.assertIsNotNone(unauth_hero_button)
        self.assertEqual(self._normalized_button_text(unauth_hero_button), "Start Free Trial")

        unauth_card_source = unauth_soup.find(
            "input",
            {"name": "source_page", "value": "home_pretrained_workers"},
        )
        self.assertIsNotNone(unauth_card_source)
        unauth_card_form = unauth_card_source.find_parent("form")
        self.assertIsNotNone(unauth_card_form)
        unauth_card_button = unauth_card_form.find("button", {"type": "submit"})
        self.assertIsNotNone(unauth_card_button)
        self.assertEqual(self._normalized_button_text(unauth_card_button), "Start Free Trial")

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

        auth_card_source = auth_soup.find(
            "input",
            {"name": "source_page", "value": "home_pretrained_workers"},
        )
        self.assertIsNotNone(auth_card_source)
        auth_card_form = auth_card_source.find_parent("form")
        self.assertIsNotNone(auth_card_form)
        auth_card_button = auth_card_form.find("button", {"type": "submit"})
        self.assertIsNotNone(auth_card_button)
        self.assertEqual(self._normalized_button_text(auth_card_button), "Spawn This Worker")

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @tag("batch_pages")
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

        card_source = soup.find(
            "input",
            {"name": "source_page", "value": "home_pretrained_workers"},
        )
        self.assertIsNotNone(card_source)
        card_form = card_source.find_parent("form")
        self.assertIsNotNone(card_form)
        card_button = card_form.find("button", {"type": "submit"})
        self.assertIsNotNone(card_button)
        self.assertEqual(self._normalized_button_text(card_button), "Start Free Trial")

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @tag("batch_pages")
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

        card_source = soup.find(
            "input",
            {"name": "source_page", "value": "home_pretrained_workers"},
        )
        self.assertIsNotNone(card_source)
        card_form = card_source.find_parent("form")
        self.assertIsNotNone(card_form)
        card_button = card_form.find("button", {"type": "submit"})
        self.assertIsNotNone(card_button)
        self.assertEqual(self._normalized_button_text(card_button), "Spawn This Worker")

    @tag("batch_pages")
    def test_home_pretrained_worker_cards_include_trial_onboarding_fields(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        soup = BeautifulSoup(response.content, "html.parser")
        card_forms = []
        for form in soup.find_all("form"):
            hidden_source = form.find("input", {"name": "source_page", "value": "home_pretrained_workers"})
            if hidden_source is not None:
                card_forms.append(form)

        self.assertGreater(len(card_forms), 0)
        for form in card_forms:
            self.assertIsNotNone(form.find("input", {"name": "trial_onboarding", "value": "1"}))
            self.assertIsNotNone(
                form.find(
                    "input",
                    {"name": "trial_onboarding_target", "value": TRIAL_ONBOARDING_TARGET_AGENT_UI},
                )
            )

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
    def test_home_spawn_redirect_stashes_oauth_fallback_cookie(self):
        session = self.client.session
        session["utm_querystring"] = "utm_source=newsletter"
        session.save()

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

    @tag("batch_pages")
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

    @tag("batch_pages")
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
    @tag("batch_pages")
    def test_home_page_uses_session_selected_pipedream_apps_in_modal_props(self, _mock_integrations):
        session = self.client.session
        session[page_views.AGENT_SELECTED_PIPEDREAM_APP_SLUGS_SESSION_KEY] = ["slack", "trello"]
        session.save()

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context.get("homepage_integrations_modal_props"),
            {
                "builtins": [],
                "initialSearchTerm": "",
                "initialSelectedAppSlugs": ["slack", "trello"],
                "searchUrl": reverse("pages:homepage_integrations_search"),
                "selectedFieldsContainerId": "homepage-integrations-selected-fields",
            },
        )

    @patch(
        "pages.views.get_owner_selected_app_slugs",
        return_value=["notion", "slack"],
    )
    @patch(
        "pages.views.get_homepage_integrations_payload",
        return_value={"enabled": True, "builtins": []},
    )
    @tag("batch_pages")
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
    @tag("batch_pages")
    def test_landing_redirect(self):
        """Landing page shortlink redirects to marketing page."""
        lp = LandingPage.objects.create(charter="x")

        resp = self.client.get(f"/g/{lp.code}/")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].endswith(f"?g={lp.code}"))

    @tag("batch_pages")
    def test_disabled_landing_returns_404(self):
        lp = LandingPage.objects.create(charter="x", disabled=True)

        resp = self.client.get(f"/g/{lp.code}/")
        self.assertEqual(resp.status_code, 404)

    @tag("batch_pages")
    def test_landing_redirect_increments_hits(self):
        lp = LandingPage.objects.create(charter="x", hits=0)
        self.client.get(f"/g/{lp.code}/")
        lp.refresh_from_db()
        self.assertEqual(lp.hits, 1)

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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
    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
    def test_disabled_landing_launch_returns_404(self):
        landing = LandingPage.objects.create(charter="x", disabled=True)

        response = self.client.get(reverse("pages:landing_launch", kwargs={"code": landing.code}))
        self.assertEqual(response.status_code, 404)


@tag("batch_pages")
class RobotsTxtTests(TestCase):
    @tag("batch_pages")
    @override_settings(GOBII_RELEASE_ENV="prod")
    def test_production_allows_indexing(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Allow: /")
        self.assertContains(response, "Sitemap:")
        lines = [line.strip() for line in response.content.decode().splitlines() if line.strip()]
        self.assertIn("Disallow: /console/agents/", lines)
        self.assertNotIn("Disallow: /", lines)

    @tag("batch_pages")
    @override_settings(GOBII_RELEASE_ENV="staging")
    def test_non_production_blocks_indexing(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Disallow: /")
        self.assertNotContains(response, "Allow: /")
        self.assertNotContains(response, "Sitemap:")


@tag("batch_pages")
class CanonicalLinkTests(TestCase):
    @tag("batch_pages")
    @override_settings(GOBII_RELEASE_ENV="prod", GOBII_PROPRIETARY_MODE=True)
    def test_canonical_present_in_production_proprietary(self):
        response = self.client.get("/")
        self.assertContains(response, '<link rel="canonical" href="http://testserver/">')

    @tag("batch_pages")
    @override_settings(GOBII_RELEASE_ENV="prod", GOBII_PROPRIETARY_MODE=False)
    def test_canonical_absent_when_not_proprietary(self):
        response = self.client.get("/")
        self.assertNotContains(response, 'rel="canonical"')

    @tag("batch_pages")
    @override_settings(GOBII_RELEASE_ENV="staging", GOBII_PROPRIETARY_MODE=True)
    def test_canonical_absent_when_not_production(self):
        response = self.client.get("/")
        self.assertNotContains(response, 'rel="canonical"')


@tag("batch_pages")
class SitemapTests(TestCase):
    @tag("batch_pages")
    def test_pretrained_worker_detail_urls_included(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        template = PretrainedWorkerTemplateService.get_active_templates()[0]
        self.assertIn(
            f"http://example.com/pretrained-workers/{template.code}/",
            response.content.decode(),
        )


@tag("batch_pages")
class PretrainedWorkerDirectoryTests(TestCase):
    @tag("batch_pages")
    def test_directory_redirects_to_home_section(self):
        response = self.client.get("/pretrained-workers/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith("#pretrained-workers"))

    @tag("batch_pages")
    def test_directory_redirect_preserves_filters(self):
        response = self.client.get(
            "/pretrained-workers/",
            {"q": "ops", "category": "Team Ops", "foo": "bar"},
        )
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertIn("pretrained_search=ops", location)
        self.assertIn("pretrained_category=Team+Ops", location)
        self.assertIn("foo=bar", location)
        self.assertTrue(location.endswith("#pretrained-workers"))

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    @tag("batch_pages")
    def test_pretrained_worker_detail_omits_trial_onboarding_fields_in_community_mode(self):
        template = PretrainedWorkerTemplateService.get_active_templates()[0]

        response = self.client.get(
            reverse("pages:pretrained_worker_detail", kwargs={"slug": template.code})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="trial_onboarding" value="1"')
        self.assertNotContains(
            response,
            f'name="trial_onboarding_target" value="{TRIAL_ONBOARDING_TARGET_AGENT_UI}"',
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @tag("batch_pages")
    def test_pretrained_worker_detail_includes_trial_onboarding_fields_in_proprietary_mode(self):
        template = PretrainedWorkerTemplateService.get_active_templates()[0]

        response = self.client.get(
            reverse("pages:pretrained_worker_detail", kwargs={"slug": template.code})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="trial_onboarding" value="1"')
        self.assertContains(
            response,
            f'name="trial_onboarding_target" value="{TRIAL_ONBOARDING_TARGET_AGENT_UI}"',
        )


@tag("batch_pages")
class PretrainedWorkerHireRedirectTests(TestCase):
    @tag("batch_pages")
    def test_hire_redirects_to_login(self):
        template = PretrainedWorkerTemplateService.get_active_templates()[0]

        session = self.client.session
        session["utm_querystring"] = "utm_medium=ads"
        session["utm_first_touch"] = {"utm_source": "meta", "utm_medium": "paid_social"}
        session["utm_last_touch"] = {"utm_source": "meta", "utm_campaign": "retargeting"}
        session["click_ids_first"] = {"gclid": "first-gclid"}
        session["click_ids_last"] = {"gclid": "last-gclid"}
        session["fbclid_first"] = "first-fbclid"
        session["fbclid_last"] = "last-fbclid"
        session.save()

        response = self.client.post(
            reverse("pages:pretrained_worker_hire", kwargs={"slug": template.code})
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

        self.assertIn(OAUTH_CHARTER_COOKIE, response.cookies)
        self.assertIn(OAUTH_ATTRIBUTION_COOKIE, response.cookies)

        charter_payload = signing.loads(response.cookies[OAUTH_CHARTER_COOKIE].value, max_age=7200)
        self.assertEqual(charter_payload.get("agent_charter"), template.charter)
        self.assertNotIn("utm_first_touch", charter_payload)
        self.assertNotIn("utm_last_touch", charter_payload)

        attribution_payload = signing.loads(response.cookies[OAUTH_ATTRIBUTION_COOKIE].value, max_age=7200)
        self.assertEqual(
            attribution_payload.get("utm_first_touch"),
            {"utm_source": "meta", "utm_medium": "paid_social"},
        )
        self.assertEqual(
            attribution_payload.get("utm_last_touch"),
            {"utm_source": "meta", "utm_campaign": "retargeting"},
        )
        self.assertEqual(attribution_payload.get("click_ids_first"), {"gclid": "first-gclid"})
        self.assertEqual(attribution_payload.get("click_ids_last"), {"gclid": "last-gclid"})
        self.assertEqual(attribution_payload.get("fbclid_first"), "first-fbclid")
        self.assertEqual(attribution_payload.get("fbclid_last"), "last-fbclid")
        self.assertEqual(attribution_payload.get("utm_querystring"), "utm_medium=ads")

    @tag("batch_pages")
    def test_hire_redirects_to_signup_when_cta_signup_first_enabled(self):
        template = PretrainedWorkerTemplateService.get_active_templates()[0]

        session = self.client.session
        session["utm_querystring"] = "utm_medium=ads"
        session["utm_first_touch"] = {"utm_source": "meta", "utm_medium": "paid_social"}
        session["utm_last_touch"] = {"utm_source": "meta", "utm_campaign": "retargeting"}
        session["click_ids_first"] = {"gclid": "first-gclid"}
        session["click_ids_last"] = {"gclid": "last-gclid"}
        session["fbclid_first"] = "first-fbclid"
        session["fbclid_last"] = "last-fbclid"
        session.save()

        with override_flag("cta_signup_first", active=True):
            response = self.client.post(
                reverse("pages:pretrained_worker_hire", kwargs={"slug": template.code})
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

        self.assertIn(OAUTH_CHARTER_COOKIE, response.cookies)
        self.assertIn(OAUTH_ATTRIBUTION_COOKIE, response.cookies)

    @tag("batch_pages")
    def test_hire_redirects_to_login_for_pro_flow(self):
        template = PretrainedWorkerTemplateService.get_active_templates()[0]

        session = self.client.session
        session["utm_querystring"] = "utm_medium=ads"
        session.save()

        response = self.client.post(
            reverse("pages:pretrained_worker_hire", kwargs={"slug": template.code}),
            {"flow": "pro"},
        )
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_login"))

        params = parse_qs(parsed.query)
        self.assertEqual(params.get("next"), [reverse("proprietary:pro_checkout")])
        self.assertEqual(params.get("utm_medium"), ["ads"])

        session = self.client.session
        self.assertEqual(
            session.get(page_views.POST_CHECKOUT_REDIRECT_SESSION_KEY),
            reverse("agent_quick_spawn"),
        )

    @tag("batch_pages")
    def test_hire_redirects_to_signup_for_pro_flow_when_cta_signup_first_enabled(self):
        template = PretrainedWorkerTemplateService.get_active_templates()[0]

        session = self.client.session
        session["utm_querystring"] = "utm_medium=ads"
        session.save()

        with override_flag("cta_signup_first", active=True):
            response = self.client.post(
                reverse("pages:pretrained_worker_hire", kwargs={"slug": template.code}),
                {"flow": "pro"},
            )
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_signup"))

        params = parse_qs(parsed.query)
        self.assertEqual(params.get("next"), [reverse("proprietary:pro_checkout")])
        self.assertEqual(params.get("utm_medium"), ["ads"])

        session = self.client.session
        self.assertEqual(
            session.get(page_views.POST_CHECKOUT_REDIRECT_SESSION_KEY),
            reverse("agent_quick_spawn"),
        )

    @tag("batch_pages")
    def test_hire_trial_onboarding_sets_session_intent(self):
        template = PretrainedWorkerTemplateService.get_active_templates()[0]

        response = self.client.post(
            reverse("pages:pretrained_worker_hire", kwargs={"slug": template.code}),
            {
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
        self.assertIn(OAUTH_CHARTER_COOKIE, response.cookies)
        self.assertIn(OAUTH_ATTRIBUTION_COOKIE, response.cookies)
        self.assertEqual(response.cookies[OAUTH_ATTRIBUTION_COOKIE].value, "")
        self.assertEqual(int(response.cookies[OAUTH_ATTRIBUTION_COOKIE]["max-age"]), 0)

        cookie_payload = signing.loads(response.cookies[OAUTH_CHARTER_COOKIE].value, max_age=7200)
        self.assertTrue(cookie_payload.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))
        self.assertEqual(
            cookie_payload.get(TRIAL_ONBOARDING_TARGET_SESSION_KEY),
            TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )
        self.assertFalse(cookie_payload.get(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY, False))


@tag("batch_pages")
class SolutionCtaCopyTests(TestCase):
    def setUp(self):
        self.request_factory = RequestFactory()

    @staticmethod
    def _normalized_button_text(button) -> str:
        return " ".join(
            segment for segment in button.stripped_strings if segment and segment != "→"
        ).strip()

    def _mini_header_logo_src(self) -> str | None:
        request = self.request_factory.get("/solutions/recruiting/")
        request.user = AnonymousUser()
        rendered = render_to_string("includes/_unified_header_nav_mini.html", {"request": request})
        soup = BeautifulSoup(rendered, "html.parser")
        logo = soup.select_one('header.hs-header a[href="/"] img')
        return logo.get("src") if logo else None

    @tag("batch_pages")
    def test_solution_header_uses_standard_logo_when_fish_upper_left_is_off(self):
        with override_flag("fish_upper_left", active=False):
            self.assertEqual(self._mini_header_logo_src(), "/static/images/noBgIndigo600.png")

    @tag("batch_pages")
    def test_solution_header_uses_fish_logo_when_fish_upper_left_is_on(self):
        with override_flag("fish_upper_left", active=True):
            self.assertEqual(self._mini_header_logo_src(), "/static/images/gobii_fish_with_text_purple_nav.png")

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False)
    @tag("batch_pages")
    def test_solution_cta_text_changes_for_authenticated_users(self):
        unauth_recruiting = self.client.get("/solutions/recruiting/")
        self.assertEqual(unauth_recruiting.status_code, 200)
        recruiting_soup = BeautifulSoup(unauth_recruiting.content, "html.parser")
        recruiting_source = recruiting_soup.find("input", {"name": "source_page", "value": "recruiting_hero"})
        self.assertIsNotNone(recruiting_source)
        recruiting_form = recruiting_source.find_parent("form")
        self.assertIsNotNone(recruiting_form)
        recruiting_button = recruiting_form.find("button", {"type": "submit"})
        self.assertIsNotNone(recruiting_button)
        self.assertEqual(self._normalized_button_text(recruiting_button), "Start Free Trial")

        unauth_sales = self.client.get("/solutions/sales/")
        self.assertEqual(unauth_sales.status_code, 200)
        sales_soup = BeautifulSoup(unauth_sales.content, "html.parser")
        sales_source = sales_soup.find("input", {"name": "source_page", "value": "sales_hero"})
        self.assertIsNotNone(sales_source)
        sales_form = sales_source.find_parent("form")
        self.assertIsNotNone(sales_form)
        sales_button = sales_form.find("button", {"type": "submit"})
        self.assertIsNotNone(sales_button)
        self.assertEqual(self._normalized_button_text(sales_button), "Start Free Trial")

        unauth_engineering = self.client.get("/solutions/engineering/")
        self.assertEqual(unauth_engineering.status_code, 200)
        engineering_soup = BeautifulSoup(unauth_engineering.content, "html.parser")
        engineering_form = engineering_soup.find("form", {"action": reverse("pages:engineering_pro_signup")})
        self.assertIsNotNone(engineering_form)
        engineering_button = engineering_form.find("button", {"type": "submit"})
        self.assertIsNotNone(engineering_button)
        self.assertEqual(self._normalized_button_text(engineering_button), "Start Free Trial")

        user = get_user_model().objects.create_user(
            username="solution_cta_auth@example.com",
            email="solution_cta_auth@example.com",
            password="password123",
        )
        self.client.force_login(user)

        auth_recruiting = self.client.get("/solutions/recruiting/")
        self.assertEqual(auth_recruiting.status_code, 200)
        auth_recruiting_soup = BeautifulSoup(auth_recruiting.content, "html.parser")
        auth_recruiting_source = auth_recruiting_soup.find(
            "input",
            {"name": "source_page", "value": "recruiting_hero"},
        )
        self.assertIsNotNone(auth_recruiting_source)
        auth_recruiting_form = auth_recruiting_source.find_parent("form")
        self.assertIsNotNone(auth_recruiting_form)
        auth_recruiting_button = auth_recruiting_form.find("button", {"type": "submit"})
        self.assertIsNotNone(auth_recruiting_button)
        self.assertEqual(self._normalized_button_text(auth_recruiting_button), "Spawn Agent")

        auth_sales = self.client.get("/solutions/sales/")
        self.assertEqual(auth_sales.status_code, 200)
        auth_sales_soup = BeautifulSoup(auth_sales.content, "html.parser")
        auth_sales_source = auth_sales_soup.find("input", {"name": "source_page", "value": "sales_hero"})
        self.assertIsNotNone(auth_sales_source)
        auth_sales_form = auth_sales_source.find_parent("form")
        self.assertIsNotNone(auth_sales_form)
        auth_sales_button = auth_sales_form.find("button", {"type": "submit"})
        self.assertIsNotNone(auth_sales_button)
        self.assertEqual(self._normalized_button_text(auth_sales_button), "Spawn Agent")

        auth_engineering = self.client.get("/solutions/engineering/")
        self.assertEqual(auth_engineering.status_code, 200)
        auth_engineering_soup = BeautifulSoup(auth_engineering.content, "html.parser")
        auth_engineering_form = auth_engineering_soup.find(
            "form",
            {"action": reverse("pages:engineering_pro_signup")},
        )
        self.assertIsNotNone(auth_engineering_form)
        auth_engineering_button = auth_engineering_form.find("button", {"type": "submit"})
        self.assertIsNotNone(auth_engineering_button)
        self.assertEqual(self._normalized_button_text(auth_engineering_button), "Get API Keys")

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @tag("batch_pages")
    def test_solution_cta_text_shows_trial_when_authenticated_user_requires_trial(self):
        user = get_user_model().objects.create_user(
            username="solution_cta_trial_required@example.com",
            email="solution_cta_trial_required@example.com",
            password="password123",
        )
        self.client.force_login(user)

        recruiting_response = self.client.get("/solutions/recruiting/")
        self.assertEqual(recruiting_response.status_code, 200)
        recruiting_soup = BeautifulSoup(recruiting_response.content, "html.parser")
        recruiting_source = recruiting_soup.find(
            "input",
            {"name": "source_page", "value": "recruiting_hero"},
        )
        self.assertIsNotNone(recruiting_source)
        recruiting_form = recruiting_source.find_parent("form")
        self.assertIsNotNone(recruiting_form)
        recruiting_button = recruiting_form.find("button", {"type": "submit"})
        self.assertIsNotNone(recruiting_button)
        self.assertEqual(self._normalized_button_text(recruiting_button), "Start Free Trial")

        sales_response = self.client.get("/solutions/sales/")
        self.assertEqual(sales_response.status_code, 200)
        sales_soup = BeautifulSoup(sales_response.content, "html.parser")
        sales_source = sales_soup.find("input", {"name": "source_page", "value": "sales_hero"})
        self.assertIsNotNone(sales_source)
        sales_form = sales_source.find_parent("form")
        self.assertIsNotNone(sales_form)
        sales_button = sales_form.find("button", {"type": "submit"})
        self.assertIsNotNone(sales_button)
        self.assertEqual(self._normalized_button_text(sales_button), "Start Free Trial")

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @tag("batch_pages")
    def test_solution_cta_text_stays_spawn_for_grandfathered_user(self):
        user = get_user_model().objects.create_user(
            username="solution_cta_grandfathered@example.com",
            email="solution_cta_grandfathered@example.com",
            password="password123",
        )
        UserFlags.objects.create(user=user, is_freemium_grandfathered=True)
        self.client.force_login(user)

        recruiting_response = self.client.get("/solutions/recruiting/")
        self.assertEqual(recruiting_response.status_code, 200)
        recruiting_soup = BeautifulSoup(recruiting_response.content, "html.parser")
        recruiting_source = recruiting_soup.find(
            "input",
            {"name": "source_page", "value": "recruiting_hero"},
        )
        self.assertIsNotNone(recruiting_source)
        recruiting_form = recruiting_source.find_parent("form")
        self.assertIsNotNone(recruiting_form)
        recruiting_button = recruiting_form.find("button", {"type": "submit"})
        self.assertIsNotNone(recruiting_button)
        self.assertEqual(self._normalized_button_text(recruiting_button), "Spawn Agent")

        sales_response = self.client.get("/solutions/sales/")
        self.assertEqual(sales_response.status_code, 200)
        sales_soup = BeautifulSoup(sales_response.content, "html.parser")
        sales_source = sales_soup.find("input", {"name": "source_page", "value": "sales_hero"})
        self.assertIsNotNone(sales_source)
        sales_form = sales_source.find_parent("form")
        self.assertIsNotNone(sales_form)
        sales_button = sales_form.find("button", {"type": "submit"})
        self.assertIsNotNone(sales_button)
        self.assertEqual(self._normalized_button_text(sales_button), "Spawn Agent")


@tag("batch_pages")
class EngineeringProSignupTests(TestCase):
    @tag("batch_pages")
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

    @tag("batch_pages")
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
            reverse("api_keys"),
        )

    @tag("batch_pages")
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

    @tag("batch_pages")
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
        self.assertEqual(parsed.path, reverse("api_keys"))


@tag("batch_pages")
class AgentSpawnIntentApiTests(TestCase):
    @tag("batch_pages")
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

    @tag("batch_pages")
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
    @tag("batch_pages")
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
    @tag("batch_pages")
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

    @tag("batch_pages")
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
    @tag("batch_pages")
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
        session[page_views.POST_CHECKOUT_REDIRECT_SESSION_KEY] = reverse("api_keys")
        session.save()

        mock_get_user_plan.return_value = {"id": PlanNames.SCALE}

        resp = self.client.get(reverse("proprietary:pro_checkout"))

        self.assertEqual(resp.status_code, 302)
        parsed = urlparse(resp["Location"])
        self.assertEqual(parsed.path, reverse("api_keys"))
        mock_prepare.assert_not_called()

        session = self.client.session
        self.assertIsNone(session.get(page_views.POST_CHECKOUT_REDIRECT_SESSION_KEY))

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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
        self.assertEqual(kwargs["subscription_data"]["trial_period_days"], 7)
        self.assertEqual(kwargs["billing_address_collection"], "required")
        self.assertEqual(
            kwargs["name_collection"],
            {
                "individual": {
                    "enabled": True,
                    "optional": False,
                }
            },
        )
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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
    def test_scale_checkout_applies_trial_checkout_fields_when_trial_configured(
        self,
        mock_stripe_settings,
        mock_customer,
        mock_price_get,
        mock_session_create,
        _mock_customer_modify,
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
        self.assertEqual(kwargs["billing_address_collection"], "required")
        self.assertEqual(
            kwargs["name_collection"],
            {
                "individual": {
                    "enabled": True,
                    "optional": False,
                }
            },
        )
        self.assertEqual(
            kwargs["custom_text"],
            {
                "after_submit": {
                    "message": "Prepaid cards are not eligible for a free trial. Subscriptions are automatically charged at the end of the trial period if not canceled."
                }
            },
        )

    @tag("batch_pages")
    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views._is_individual_trial_eligible", return_value=False)
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.get_existing_individual_subscriptions")
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
        self.assertNotIn("trial_period_days", kwargs["subscription_data"])
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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
            {
                STRIPE_CHECKOUT_CUSTOMER_CURRENCY_META_KEY: "",
                STRIPE_CHECKOUT_CUSTOMER_FLOW_TYPE_META_KEY: "",
                STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY: "",
                STRIPE_CHECKOUT_CUSTOMER_PLAN_LABEL_META_KEY: "",
                STRIPE_CHECKOUT_CUSTOMER_PLAN_META_KEY: "",
                STRIPE_CHECKOUT_CUSTOMER_SOURCE_URL_META_KEY: "",
                STRIPE_CHECKOUT_CUSTOMER_VALUE_META_KEY: "",
            },
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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
        self.assertContains(response, "data-password-signup-form")
        self.assertContains(response, "identitySignals.clearStagedFpjsCookies();")
        self.assertContains(response, "const fpjsTimeoutMs = 3000;")
        self.assertContains(response, "Promise.race([")
        self.assertContains(response, "signupForm.addEventListener('submit'")
        self.assertContains(response, "signupForm.submit()")

    @tag("batch_pages")
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
        self.assertContains(response, "identitySignals.clearStagedFpjsCookies();")
        self.assertContains(response, "const fpjsTimeoutMs = 3000;")
        self.assertContains(response, "Promise.race([")
        self.assertContains(response, "gobii_signup_fpjs_visitor_id")
        self.assertContains(response, "gobii_signup_fpjs_request_id")
        self.assertContains(response, "gobii_signup_ga_client_id")

    @tag("batch_pages")
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

    @tag("batch_pages")
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

    @tag("batch_pages")
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


@tag("batch_pages")
class LoginTurnstilePageTests(TestCase):
    @tag("batch_pages")
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
        self.assertContains(response, "window.gobiiLoginTurnstileSuccess = () => {")
        self.assertContains(response, "window.gobiiLoginTurnstileExpired = () => {")
        self.assertContains(response, "window.gobiiLoginTurnstileError = () => {")
        self.assertContains(response, "Verification expired. Please try again.")
        self.assertContains(response, "Verification failed. Please try again.")
        self.assertContains(response, "Completing verification...")
        self.assertContains(response, "let submitPending = false;")
        self.assertContains(response, "const submitWhenReady = () => {")
        self.assertContains(response, "const submitButton = () => loginForm?.querySelector('[data-turnstile-submit]');")
        self.assertContains(response, "const statusMessage = () => loginForm?.querySelector('[data-turnstile-status]');")
        self.assertContains(response, "const widget = () => loginForm?.querySelector('.cf-turnstile');")
        self.assertContains(response, "const button = submitButton();")
        self.assertContains(response, "const messageNode = statusMessage();")
        self.assertContains(response, "const turnstileWidget = widget();")
        self.assertContains(response, "loginForm.requestSubmit(submitButton() || undefined);")
        self.assertContains(response, "loginForm?.addEventListener('submit', (event) => {")
        self.assertContains(response, "if (hasFreshToken()) {")
        self.assertContains(response, "submitPending = true;")
        self.assertContains(response, "event.preventDefault();")
        self.assertContains(response, "window.turnstile.reset(turnstileWidget);")
        self.assertContains(response, 'data-expired-callback="gobiiLoginTurnstileExpired"')
        self.assertContains(response, 'data-timeout-callback="gobiiLoginTurnstileExpired"')
        self.assertContains(response, 'data-error-callback="gobiiLoginTurnstileError"')
        self.assertContains(response, 'data-callback="gobiiLoginTurnstileSuccess"')
@tag("batch_pages")
class MarketingMetaTests(TestCase):
    @tag("batch_pages")
    def test_terms_meta_description(self):
        response = self.client.get("/tos/")
        self.assertContains(
            response,
            "<meta name=\"description\" content=\"Review Gobii's Terms of Service covering usage policies, billing, and compliance for our pretrained worker platform.\">",
        )

    @tag("batch_pages")
    def test_privacy_meta_description(self):
        response = self.client.get("/privacy/")
        self.assertContains(
            response,
            "<meta name=\"description\" content=\"Understand how Gobii collects, uses, and safeguards data across our pretrained worker platform.\">",
        )



    @tag("batch_pages")
    def test_careers_meta_description(self):
        response = self.client.get("/careers/")
        self.assertContains(
            response,
            "<meta name=\"description\" content=\"Join Gobii to build AI coworkers that browse, research, and automate the web for organizations worldwide.\">",
        )


    @tag("batch_pages")
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

        self.assertEqual(parsed.path, "/console/billing/")
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

    @tag("batch_pages")
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

        self.assertEqual(parsed.path, "/console/billing/")
        self.assertEqual(params.get("subscribe_success"), ["1"])
        self.assertEqual(params.get("p"), ["275.00"])
        self.assertTrue(params.get("eid"))
        self.assertTrue(params["eid"][0].startswith("scale-sub-"))
        mock_ensure.assert_called_once()
        ensure_kwargs = mock_ensure.call_args.kwargs
        self.assertNotIn("metered_price_id", ensure_kwargs)
        mock_session_create.assert_not_called()
