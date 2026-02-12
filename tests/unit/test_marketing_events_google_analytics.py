from unittest.mock import patch

from django.test import SimpleTestCase, override_settings, tag

from marketing_events.providers import get_providers
from marketing_events.providers.google_analytics import GoogleAnalyticsMP


@tag("batch_marketing_events")
class GoogleAnalyticsMPTests(SimpleTestCase):
    def test_send_start_trial_payload(self):
        provider = GoogleAnalyticsMP(measurement_id="G-TEST123", api_secret="secret-123")
        evt = {
            "event_name": "StartTrial",
            "event_time": 1_700_000_000,
            "event_id": "evt-123",
            "properties": {
                "plan": "startup",
                "subscription_id": "sub_123",
                "invalid.param": "drop-me",
                "empty_value": "",
            },
            "ids": {
                "external_id": "hashed-user-id",
                "em": "hashed-email",
                "ph": "hashed-phone",
            },
            "network": {
                "ga_client_id": "GA1.2.111.222",
            },
            "utm": {},
            "consent": True,
        }

        with patch("marketing_events.providers.google_analytics.post_json") as mock_post:
            mock_post.return_value = {}
            provider.send(evt)

        mock_post.assert_called_once()
        url = mock_post.call_args.args[0]
        kwargs = mock_post.call_args.kwargs

        self.assertEqual(url, "https://www.google-analytics.com/mp/collect")
        self.assertEqual(kwargs["params"]["measurement_id"], "G-TEST123")
        self.assertEqual(kwargs["params"]["api_secret"], "secret-123")
        self.assertEqual(kwargs["headers"], {"Content-Type": "application/json"})

        body = kwargs["json"]
        self.assertEqual(body["client_id"], "GA1.2.111.222")
        self.assertEqual(body["user_id"], "hashed-user-id")
        self.assertEqual(body["timestamp_micros"], 1_700_000_000_000_000)
        self.assertEqual(body["events"][0]["name"], "start_trial")
        self.assertEqual(body["events"][0]["params"]["plan"], "startup")
        self.assertEqual(body["events"][0]["params"]["subscription_id"], "sub_123")
        self.assertEqual(body["events"][0]["params"]["event_id"], "evt-123")
        self.assertEqual(body["events"][0]["params"]["engagement_time_msec"], 1)
        self.assertNotIn("invalid.param", body["events"][0]["params"])
        self.assertNotIn("empty_value", body["events"][0]["params"])

    def test_send_ignores_unmapped_event(self):
        provider = GoogleAnalyticsMP(measurement_id="G-TEST123", api_secret="secret-123")
        evt = {
            "event_name": "CompleteRegistration",
            "event_time": 1_700_000_000,
            "event_id": "evt-123",
            "properties": {},
            "ids": {"external_id": "hashed-user-id"},
            "network": {},
            "utm": {},
            "consent": True,
        }

        with patch("marketing_events.providers.google_analytics.post_json") as mock_post:
            provider.send(evt)

        mock_post.assert_not_called()

    def test_send_honors_consent(self):
        provider = GoogleAnalyticsMP(measurement_id="G-TEST123", api_secret="secret-123")
        evt = {
            "event_name": "StartTrial",
            "event_time": 1_700_000_000,
            "event_id": "evt-123",
            "properties": {},
            "ids": {"external_id": "hashed-user-id"},
            "network": {"ga_client_id": "GA1.2.111.222"},
            "utm": {},
            "consent": False,
        }

        with patch("marketing_events.providers.google_analytics.post_json") as mock_post:
            provider.send(evt)

        mock_post.assert_not_called()

    def test_send_falls_back_to_external_id_for_client_id(self):
        provider = GoogleAnalyticsMP(measurement_id="G-TEST123", api_secret="secret-123")
        evt = {
            "event_name": "StartTrial",
            "event_time": 1_700_000_000,
            "event_id": "evt-123",
            "properties": {},
            "ids": {"external_id": "hashed-user-id"},
            "network": {},
            "utm": {},
            "consent": True,
        }

        with patch("marketing_events.providers.google_analytics.post_json") as mock_post:
            mock_post.return_value = {}
            provider.send(evt)

        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["client_id"], "hashed-user-id")


@tag("batch_marketing_events")
class MarketingProvidersRegistrationTests(SimpleTestCase):
    @override_settings(
        GA_MEASUREMENT_ID="G-TEST123",
        GA_MEASUREMENT_API_SECRET="secret-123",
        FACEBOOK_ACCESS_TOKEN="",
        META_PIXEL_ID="",
        REDDIT_ACCESS_TOKEN="",
        REDDIT_ADVERTISER_ID="",
        TIKTOK_ACCESS_TOKEN="",
        TIKTOK_PIXEL_ID="",
    )
    def test_get_providers_includes_google_analytics_when_configured(self):
        providers = get_providers()
        self.assertEqual(len(providers), 1)
        self.assertIsInstance(providers[0], GoogleAnalyticsMP)

    @override_settings(
        GA_MEASUREMENT_ID="G-TEST123",
        GA_MEASUREMENT_API_SECRET="",
        FACEBOOK_ACCESS_TOKEN="",
        META_PIXEL_ID="",
        REDDIT_ACCESS_TOKEN="",
        REDDIT_ADVERTISER_ID="",
        TIKTOK_ACCESS_TOKEN="",
        TIKTOK_PIXEL_ID="",
    )
    def test_get_providers_skips_google_analytics_without_secret(self):
        providers = get_providers()
        self.assertEqual(providers, [])
