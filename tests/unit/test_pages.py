
from urllib.parse import parse_qs, urlparse
import re
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from api.models import BrowserUseAgent, PersistentAgent
from pages import views as page_views
from pages.models import LandingPage
from agents.services import PretrainedWorkerTemplateService
from constants.plans import PlanNames


@tag("batch_pages")
class HomePageTests(TestCase):
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
            '<meta name="description" content="Create your own Gobii digital worker to automate prospecting, research, and repetitive web browsing tasks around the clock so you can focus on strategy.">',
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
        self.assertEqual(params.get("next"), [reverse("agent_quick_spawn")])
        self.assertEqual(params.get("utm_source"), ["newsletter"])

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
        session.save()

        response = self.client.post(
            reverse("pages:pretrained_worker_hire", kwargs={"slug": template.code})
        )
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_login"))

        params = parse_qs(parsed.query)
        self.assertEqual(params.get("next"), [reverse("agent_quick_spawn")])
        self.assertEqual(params.get("utm_medium"), ["ads"])

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

        session = self.client.session
        self.assertIsNone(session.get(page_views.POST_CHECKOUT_REDIRECT_SESSION_KEY))


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
