import hashlib

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse


User = get_user_model()


@tag("batch_pages_signals")
class ClearSignupTrackingViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ga-pages-user",
            email="ga-pages@example.com",
            password="pw",
        )

    @override_settings(
        GA_MEASUREMENT_ID="G-TEST123",
        REDDIT_PIXEL_ID="reddit-123",
        TIKTOK_PIXEL_ID="tiktok-123",
        META_PIXEL_ID="meta-123",
        LINKEDIN_SIGNUP_CONVERSION_ID="123456",
        CAPI_REGISTRATION_VALUE=12.5,
    )
    def test_returns_tracking_payload_and_clears_session(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["show_signup_tracking"] = True
        session["signup_event_id"] = "evt-123"
        session["signup_user_id"] = str(self.user.id)
        session["signup_email_hash"] = "unused-when-authenticated"
        session["signup_auth_method"] = "social"
        session["signup_auth_provider"] = "linkedin"
        session.save()

        response = self.client.get(reverse("pages:clear_signup_tracking"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertTrue(payload["tracking"])
        self.assertEqual(payload["eventId"], "evt-123")
        self.assertEqual(payload["userId"], str(self.user.id))
        self.assertEqual(
            payload["emailHash"],
            hashlib.sha256(self.user.email.strip().lower().encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            payload["idHash"],
            hashlib.sha256(str(self.user.id).encode("utf-8")).hexdigest(),
        )
        self.assertEqual(payload["authMethod"], "social")
        self.assertEqual(payload["authProvider"], "linkedin")
        self.assertEqual(payload["registrationValue"], 12.5)
        self.assertEqual(payload["pixels"]["ga"], "G-TEST123")
        self.assertEqual(payload["pixels"]["reddit"], "reddit-123")
        self.assertEqual(payload["pixels"]["tiktok"], "tiktok-123")
        self.assertEqual(payload["pixels"]["meta"], "meta-123")
        self.assertEqual(payload["pixels"]["linkedin"], "123456")

        session = self.client.session
        self.assertNotIn("show_signup_tracking", session)
        self.assertNotIn("signup_event_id", session)
        self.assertNotIn("signup_user_id", session)
        self.assertNotIn("signup_email_hash", session)
        self.assertNotIn("signup_auth_method", session)
        self.assertNotIn("signup_auth_provider", session)

    def test_returns_false_when_no_tracking_flag(self):
        response = self.client.get(reverse("pages:clear_signup_tracking"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"tracking": False})


@tag("batch_pages_signals")
class GoogleAnalyticsRenderingTests(TestCase):
    @override_settings(DEBUG=False, GA_MEASUREMENT_ID="G-TEST123")
    def test_base_template_uses_page_meta_title_in_ga_config(self):
        response = self.client.get(reverse("pages:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'let gaPageTitle = "Marketing - Home";')
        self.assertContains(response, "gtag('config', 'G-TEST123', gtagConfig);")

    @override_settings(DEBUG=False, GA_MEASUREMENT_ID="G-TEST123", GOBII_PROPRIETARY_MODE=True)
    def test_teams_page_uses_teams_page_meta_for_analytics(self):
        response = self.client.get("/teams/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'let gaPageTitle = "Marketing - Teams";')
        self.assertContains(response, 'let pageCategory = "Marketing";')
        self.assertContains(response, 'let pageName = "Teams";')

    @override_settings(
        DEBUG=True,
        SEGMENT_WEB_WRITE_KEY="segment-web-test",
        SEGMENT_WEB_ENABLE_IN_DEBUG=True,
    )
    def test_base_template_loads_segment_when_debug_override_enabled(self):
        response = self.client.get(reverse("pages:home"))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode("utf-8")
        self.assertIn('src="/static/js/segment_bootstrap.js"', content)
        self.assertIn('writeKey: "segment\\u002Dweb\\u002Dtest"', content)
        self.assertIn("enabled: true,", content)
        self.assertNotIn('analytics.load("segment-web-test");', content)

    @override_settings(
        DEBUG=True,
        SEGMENT_WEB_WRITE_KEY="segment-web-test",
        SEGMENT_WEB_ENABLE_IN_DEBUG=False,
    )
    def test_base_template_uses_stub_when_debug_override_disabled(self):
        response = self.client.get(reverse("pages:home"))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode("utf-8")
        self.assertIn('src="/static/js/segment_bootstrap.js"', content)
        self.assertIn('writeKey: "segment\\u002Dweb\\u002Dtest"', content)
        self.assertIn("enabled: false,", content)
        self.assertNotIn('analytics.load("segment-web-test");', content)

    @override_settings(DEBUG=False, GA_MEASUREMENT_ID="G-TEST123", GOBII_PROPRIETARY_MODE=True)
    def test_app_shell_includes_shared_tracking_helpers(self):
        response = self.client.get("/app")
        self.assertEqual(response.status_code, 200)

        content = response.content.decode("utf-8")
        self.assertIn('src="/static/js/segment_bootstrap.js"', content)
        self.assertIn('src="/static/js/gobii_analytics.js"', content)
        self.assertIn('src="/static/js/signup_tracking.js"', content)
        self.assertIn("window.GobiiSignupTracking.fetchAndFire", content)
        self.assertIn("source: 'app_shell'", content)
        self.assertIn("send_page_view: false", content)

    @override_settings(
        DEBUG=True,
        SEGMENT_WEB_WRITE_KEY="segment-web-test",
        SEGMENT_WEB_ENABLE_IN_DEBUG=True,
    )
    def test_app_shell_enables_segment_when_debug_override_enabled(self):
        response = self.client.get("/app")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('src="/static/js/segment_bootstrap.js"', content)
        self.assertIn('writeKey: "segment-web-test"', content)
        self.assertIn("enabled: true,", content)

    @override_settings(
        DEBUG=True,
        SEGMENT_WEB_WRITE_KEY="segment-web-test",
        SEGMENT_WEB_ENABLE_IN_DEBUG=False,
    )
    def test_app_shell_disables_segment_when_debug_override_disabled(self):
        response = self.client.get("/app")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('src="/static/js/segment_bootstrap.js"', content)
        self.assertIn('writeKey: "segment-web-test"', content)
        self.assertIn("enabled: false,", content)

    @tag("batch_pages_signals")
    def test_base_template_uses_fish_assets(self):
        response = self.client.get(reverse("pages:home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('/static/images/gobii_fish_favicon.ico?v=5', content)
        self.assertIn('/static/images/gobii_fish_favicon_16.png?v=5', content)
        self.assertIn('/static/images/gobii_fish_favicon_32.png?v=5', content)
        self.assertIn('/static/images/gobii_fish_apple_touch_180.png?v=5', content)

    @tag("batch_pages_signals")
    @override_settings(DEBUG=False, GA_MEASUREMENT_ID="G-TEST123", GOBII_PROPRIETARY_MODE=True)
    def test_app_shell_uses_fish_icon(self):
        response = self.client.get("/app")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('href="/static/images/gobii_fish.png"', content)


@tag("batch_pages_signals")
class WebManifestRenderingTests(TestCase):
    def test_manifest_is_publicly_cacheable_without_tracking_cookies(self):
        response = self.client.get(reverse("pages:web_manifest"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "public, max-age=3600")
        self.assertFalse(response.has_header("Vary"))
        self.assertNotIn(settings.FBP_COOKIE_NAME, response.cookies)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)

    def test_manifest_cache_headers_do_not_depend_on_existing_cookies(self):
        user = User.objects.create_user(
            username="manifest-user",
            email="manifest@example.com",
            password="pw",
        )
        self.client.force_login(user)
        self.client.cookies[settings.FBP_COOKIE_NAME] = "fb.1.1780155105587.5000808142"

        response = self.client.get(reverse("pages:web_manifest"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "public, max-age=3600")
        self.assertFalse(response.has_header("Vary"))
        self.assertNotIn(settings.FBP_COOKIE_NAME, response.cookies)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)

    def test_manifest_uses_fish_icons(self):
        response = self.client.get(reverse("pages:web_manifest"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        payload = response.json()
        self.assertEqual(payload["icons"][0]["src"], "/static/images/gobii_fish_favicon_16.png")
        self.assertEqual(payload["icons"][1]["src"], "/static/images/gobii_fish_favicon_32.png")
        self.assertEqual(payload["icons"][2]["src"], "/static/images/gobii_fish_icon_192.png")
        self.assertEqual(payload["icons"][3]["src"], "/static/images/gobii_fish_icon_512.png")
