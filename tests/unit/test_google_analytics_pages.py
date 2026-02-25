import hashlib

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from waffle.testutils import override_switch


User = get_user_model()


@tag("batch_pages")
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

    def test_returns_false_when_no_tracking_flag(self):
        response = self.client.get(reverse("pages:clear_signup_tracking"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"tracking": False})


@tag("batch_pages")
class GoogleAnalyticsRenderingTests(TestCase):
    @override_settings(DEBUG=False, GA_MEASUREMENT_ID="G-TEST123")
    def test_base_template_uses_page_meta_title_in_ga_config(self):
        response = self.client.get(reverse("pages:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'let gaPageTitle = "Marketing - Home";')
        self.assertContains(response, "gtag('config', 'G-TEST123', gtagConfig);")

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

    @tag("batch_pages")
    def test_base_template_uses_legacy_collateral_when_switch_is_off(self):
        with override_switch("fish_collateral", active=False):
            response = self.client.get(reverse("pages:home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('/static/images/favicon.ico?v=4', content)
        self.assertIn('/static/images/favicon-16x16.png?v=4', content)
        self.assertIn('/static/images/favicon-32x32.png?v=4', content)
        self.assertIn('/static/images/apple-touch-icon.png?v=4', content)
        self.assertIn('rel="manifest" href="/manifest.json"', content)

    @tag("batch_pages")
    def test_base_template_uses_fish_collateral_when_switch_is_on(self):
        with override_switch("fish_collateral", active=True):
            response = self.client.get(reverse("pages:home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('/static/images/gobii_fish_favicon.ico?v=5', content)
        self.assertIn('/static/images/gobii_fish_favicon_16.png?v=5', content)
        self.assertIn('/static/images/gobii_fish_favicon_32.png?v=5', content)
        self.assertIn('/static/images/gobii_fish_apple_touch_180.png?v=5', content)

    @tag("batch_pages")
    @override_settings(DEBUG=False, GA_MEASUREMENT_ID="G-TEST123", GOBII_PROPRIETARY_MODE=True)
    def test_app_shell_uses_legacy_icon_when_switch_is_off(self):
        with override_switch("fish_collateral", active=False):
            response = self.client.get("/app")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('href="/static/images/noBgBlue.png"', content)
        self.assertIn('data-fish-collateral-enabled="false"', content)

    @tag("batch_pages")
    @override_settings(DEBUG=False, GA_MEASUREMENT_ID="G-TEST123", GOBII_PROPRIETARY_MODE=True)
    def test_app_shell_uses_fish_icon_when_switch_is_on(self):
        with override_switch("fish_collateral", active=True):
            response = self.client.get("/app")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('href="/static/images/gobii_fish.png"', content)
        self.assertIn('data-fish-collateral-enabled="true"', content)


@tag("batch_pages")
class WebManifestRenderingTests(TestCase):
    def test_manifest_uses_legacy_icons_when_switch_is_off(self):
        with override_switch("fish_collateral", active=False):
            response = self.client.get(reverse("pages:web_manifest"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        payload = response.json()
        self.assertEqual(payload["icons"][0]["src"], "/static/images/favicon-16x16.png")
        self.assertEqual(payload["icons"][1]["src"], "/static/images/favicon-32x32.png")
        self.assertEqual(payload["icons"][2]["src"], "/static/images/favicon-192x192.png")
        self.assertEqual(payload["icons"][3]["src"], "/static/images/gobii_swoosh_white_on_blue_512.png")

    def test_manifest_uses_fish_icons_when_switch_is_on(self):
        with override_switch("fish_collateral", active=True):
            response = self.client.get(reverse("pages:web_manifest"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        payload = response.json()
        self.assertEqual(payload["icons"][0]["src"], "/static/images/gobii_fish_favicon_16.png")
        self.assertEqual(payload["icons"][1]["src"], "/static/images/gobii_fish_favicon_32.png")
        self.assertEqual(payload["icons"][2]["src"], "/static/images/gobii_fish_icon_192.png")
        self.assertEqual(payload["icons"][3]["src"], "/static/images/gobii_fish_icon_512.png")
