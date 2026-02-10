
from urllib.parse import parse_qs, urlparse
import re
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from api.models import BrowserUseAgent, PersistentAgent
from config.socialaccount_adapter import OAUTH_ATTRIBUTION_COOKIE, OAUTH_CHARTER_COOKIE
from pages import views as page_views
from pages.models import LandingPage
from agents.services import PretrainedWorkerTemplateService
from constants.plans import PlanNames
from util.onboarding import (
    TRIAL_ONBOARDING_PENDING_SESSION_KEY,
    TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY,
    TRIAL_ONBOARDING_TARGET_AGENT_UI,
    TRIAL_ONBOARDING_TARGET_API_KEYS,
    TRIAL_ONBOARDING_TARGET_SESSION_KEY,
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
    def test_home_page_has_meta_description(self):
        response = self.client.get("/")
        self.assertContains(
            response,
            '<meta name="description" content="Gobii agents are virtual coworkers with their own identity, memory, and tools. Email them, text them — they browse the web, collect data, and deliver reports 24/7.">',
        )

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
    def test_landing_redirect_refreshes_fbc_when_fbclid_changes(self):
        lp = LandingPage.objects.create(charter="x")
        self.client.cookies["_fbc"] = "fb.1.1111111111111.old-click"

        resp = self.client.get(f"/g/{lp.code}/", {"fbclid": "new-click"})
        self.assertEqual(resp.status_code, 302)

        self.assertIn("_fbc", resp.cookies)
        self.assertIn("fbclid", resp.cookies)
        self.assertTrue(resp.cookies["_fbc"].value.startswith("fb.1."))
        self.assertTrue(resp.cookies["_fbc"].value.endswith(".new-click"))
        self.assertEqual(resp.cookies["fbclid"].value, "new-click")

    @tag("batch_pages")
    def test_landing_redirect_does_not_rotate_fbc_for_same_fbclid(self):
        lp = LandingPage.objects.create(charter="x")
        self.client.cookies["_fbc"] = "fb.1.1111111111111.same-click"

        resp = self.client.get(f"/g/{lp.code}/", {"fbclid": "same-click"})
        self.assertEqual(resp.status_code, 302)

        self.assertNotIn("_fbc", resp.cookies)
        self.assertIn("fbclid", resp.cookies)
        self.assertEqual(resp.cookies["fbclid"].value, "same-click")


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

        charter_payload = signing.loads(response.cookies[OAUTH_CHARTER_COOKIE].value, max_age=3600)
        self.assertEqual(charter_payload.get("agent_charter"), template.charter)
        self.assertNotIn("utm_first_touch", charter_payload)
        self.assertNotIn("utm_last_touch", charter_payload)

        attribution_payload = signing.loads(response.cookies[OAUTH_ATTRIBUTION_COOKIE].value, max_age=3600)
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

        cookie_payload = signing.loads(response.cookies[OAUTH_CHARTER_COOKIE].value, max_age=3600)
        self.assertTrue(cookie_payload.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))
        self.assertEqual(
            cookie_payload.get(TRIAL_ONBOARDING_TARGET_SESSION_KEY),
            TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )
        self.assertFalse(cookie_payload.get(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY, False))


@tag("batch_pages")
class SolutionCtaCopyTests(TestCase):
    @staticmethod
    def _normalized_button_text(button) -> str:
        return " ".join(
            segment for segment in button.stripped_strings if segment and segment != "→"
        ).strip()

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
        self.assertEqual(payload.get("onboarding_target"), TRIAL_ONBOARDING_TARGET_AGENT_UI)
        self.assertTrue(payload.get("requires_plan_selection"))

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
        self.assertEqual(payload.get("onboarding_target"), TRIAL_ONBOARDING_TARGET_AGENT_UI)
        self.assertTrue(payload.get("requires_plan_selection"))


@tag("batch_pages")
class CheckoutRedirectTests(TestCase):
    @tag("batch_pages")
    @patch("pages.views.get_user_plan")
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
    @patch("pages.views.get_user_plan")
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
    @patch("pages.views.customer_has_any_individual_subscription")
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
        mock_has_history,
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
        mock_has_history.return_value = False

        resp = self.client.get(reverse("proprietary:pro_checkout"))

        self.assertEqual(resp.status_code, 302)
        parsed = urlparse(resp["Location"])
        self.assertEqual(parsed.path, reverse("agent_quick_spawn"))

        session = self.client.session
        self.assertIsNone(session.get(page_views.POST_CHECKOUT_REDIRECT_SESSION_KEY))

    @tag("batch_pages")
    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views.customer_has_any_individual_subscription")
    @patch("pages.views.ensure_single_individual_subscription")
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
        mock_ensure,
        mock_has_history,
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
        mock_session_create.return_value = MagicMock(url="https://stripe.test/checkout-startup")
        mock_ensure.return_value = (None, "absent")
        mock_has_history.return_value = False

        resp = self.client.get(reverse("proprietary:pro_checkout"))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-startup")

        kwargs = mock_session_create.call_args.kwargs
        self.assertEqual(kwargs["subscription_data"]["trial_period_days"], 7)

    @tag("batch_pages")
    @patch("pages.views._prepare_stripe_or_404")
    @patch("pages.views.customer_has_any_individual_subscription")
    @patch("pages.views.ensure_single_individual_subscription")
    @patch("pages.views.get_existing_individual_subscriptions")
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
        mock_existing_subs,
        mock_ensure,
        mock_has_history,
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
        mock_session_create.return_value = MagicMock(url="https://stripe.test/checkout-scale")
        mock_existing_subs.return_value = []
        mock_ensure.return_value = (None, "absent")
        mock_has_history.return_value = True

        resp = self.client.get("/subscribe/scale/")

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://stripe.test/checkout-scale")

        kwargs = mock_session_create.call_args.kwargs
        self.assertNotIn("trial_period_days", kwargs["subscription_data"])


@tag("batch_pages")
class AuthLinkTests(TestCase):
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
        self.assertEqual(params.get("p"), ["1250.00"])
        self.assertTrue(params.get("eid"))
        self.assertTrue(params["eid"][0].startswith("scale-sub-"))
        mock_ensure.assert_called_once()
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
        self.assertEqual(params.get("p"), ["1250.00"])
        self.assertTrue(params.get("eid"))
        self.assertTrue(params["eid"][0].startswith("scale-sub-"))
        mock_ensure.assert_called_once()
        mock_session_create.assert_not_called()
