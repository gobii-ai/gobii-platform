from unittest.mock import patch

from django.test import SimpleTestCase, tag, override_settings

from marketing_events.providers.tiktok import TikTokCAPI


@tag("batch_marketing_events")
class TikTokPayloadTests(SimpleTestCase):
    @override_settings(TIKTOK_CAPI_TEST_MODE=True, TIKTOK_TEST_EVENT_CODE="TEST123")
    def test_payload_matches_expected_schema(self):
        provider = TikTokCAPI(pixel_id="pixel123", token="token456")
        evt = {
            "event_name": "CompleteRegistration",
            "event_time": 1_700_000_000,
            "event_id": "evt-123",
            "properties": {
                "currency": "USD",
                "value": 25,
                "event_id": "duplicate",
                "event_time": 123,
            },
            "ids": {
                "external_id": "hash-external",
                "em": "hash-email",
                "ph": "hash-phone",
            },
            "network": {
                "client_ip": "198.51.100.1",
                "user_agent": "pytest-agent",
                "page_url": "https://example.com/signup",
                "ttclid": "ttclid-123",
            },
            "utm": {},
            "consent": True,
        }

        with patch("marketing_events.providers.tiktok.post_json") as mock_post:
            mock_post.return_value = {}
            provider.send(evt)

        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        self.assertEqual(url, provider.url)
        kwargs = mock_post.call_args.kwargs
        self.assertEqual(
            kwargs["headers"],
            {"Access-Token": "token456", "Content-Type": "application/json"},
        )
        body = kwargs["json"]
        self.assertEqual(body["pixel_code"], "pixel123")
        self.assertEqual(body["event"], "CompleteRegistration")
        self.assertEqual(body["event_id"], "evt-123")
        self.assertEqual(body["timestamp"], "2023-11-14T22:13:20Z")
        self.assertEqual(body["event_source"], "web")
        self.assertEqual(body["event_channel"], "web")
        self.assertEqual(body["partner_name"], "gobii-platform")
        self.assertEqual(body["test_event_code"], "TEST123")

        context = body["context"]
        self.assertEqual(
            context,
            {
                "page": {"url": "https://example.com/signup"},
                "ip": "198.51.100.1",
                "user_agent": "pytest-agent",
                "ad": {"callback": "ttclid-123"},
            },
        )

        user = body["user"]
        self.assertEqual(
            user,
            {
                "external_id": ["hash-external"],
                "email": ["hash-email"],
                "phone_number": ["hash-phone"],
            },
        )

        properties = body["properties"]
        self.assertEqual(properties, {"currency": "USD", "value": 25})

    def test_send_honors_consent(self):
        provider = TikTokCAPI(pixel_id="pixel123", token="token456")
        evt = {
            "event_name": "CompleteRegistration",
            "event_time": 1_700_000_000,
            "event_id": "evt-123",
            "properties": {},
            "ids": {},
            "network": {},
            "utm": {},
            "consent": False,
        }

        with patch("marketing_events.providers.tiktok.post_json") as mock_post:
            provider.send(evt)

        mock_post.assert_not_called()
