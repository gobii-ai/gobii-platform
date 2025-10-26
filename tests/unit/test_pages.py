
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, override_settings, tag
from pages.models import LandingPage
from agents.services import PretrainedWorkerTemplateService


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
            '<meta name="description" content="Deploy always-on pretrained workers that handle outreach, research, and operations for you. Spawn a Gobii agent in minutes and keep work moving around the clock.">',
        )

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
        self.assertNotContains(response, "Disallow: /")

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
    def test_about_meta_description(self):
        response = self.client.get("/about/")
        self.assertContains(
            response,
            "<meta name=\"description\" content=\"Meet the team building Gobii's pretrained workers and learn how we automate web workflows for modern teams.\">",
        )

    @tag("batch_pages")
    def test_careers_meta_description(self):
        response = self.client.get("/careers/")
        self.assertContains(
            response,
            "<meta name=\"description\" content=\"Join Gobii to build AI coworkers that browse, research, and automate the web for organizations worldwide.\">",
        )
