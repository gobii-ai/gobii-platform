
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, tag
from pages.models import LandingPage


@tag("batch_pages")
class HomePageTests(TestCase):
    @tag("batch_pages")
    def test_home_page_renders(self):
        """Basic smoke test for home page."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)


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
