from unittest.mock import patch

from django.test import SimpleTestCase, tag

from marketing_events.providers.reddit import RedditCAPI


@tag("batch_marketing_events")
class RedditPayloadTests(SimpleTestCase):
    def test_payload_matches_latest_schema(self):
        provider = RedditCAPI(pixel_id="acct123", token="token456")
        evt = {
            "event_name": "CompleteRegistration",
            "event_time": 1_700_000_000,
            "event_id": "evt-123",
            "properties": {
                "value": 99.99,
                "currency": "USD",
                "item_count": 1,
                "test_mode": True,
                "event_id": "should-remove",
                "products": [
                    {"id": "product123", "category": "products", "name": "Product 123"}
                ],
                "extra": "should-drop",
            },
            "ids": {
                "external_id": "hash-external",
                "em": "hash-email",
                "ph": "hash-phone",
            },
            "network": {
                "client_ip": "203.0.113.5",
                "user_agent": "pytest-agent",
                "page_url": "https://example.com",
                "fbp": None,
                "fbc": None,
                "fbclid": None,
                "rdt_cid": "rdt-123",
            },
            "utm": {},
            "consent": True,
        }

        with patch("marketing_events.providers.reddit.post_json") as mock_post:
            mock_post.return_value = {}
            provider.send(evt)

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["headers"],
            {
                "Authorization": "Bearer token456",
                "Content-Type": "application/json",
            },
        )
        body = kwargs["json"]

        self.assertIn("data", body)
        events = body["data"]["events"]
        self.assertEqual(len(events), 1)
        event = events[0]

        self.assertEqual(event["event_at"], 1_700_000_000_000)
        self.assertEqual(event["action_source"], "WEBSITE")
        self.assertEqual(event["type"], {"tracking_type": "SIGN_UP"})

        user = event["user"]
        self.assertEqual(user["email"], "hash-email")
        self.assertEqual(user["phone"], "hash-phone")
        self.assertEqual(user["external_id"], "hash-external")
        self.assertEqual(user["ip_address"], "203.0.113.5")
        self.assertEqual(user["user_agent"], "pytest-agent")

        self.assertEqual(event["click_id"], "rdt-123")

        metadata = event["metadata"]
        self.assertEqual(
            metadata,
            {
                "conversion_id": "evt-123",
                "currency": "USD",
                "item_count": 1,
                "products": [{"id": "product123", "category": "products"}],
                "value": 99.99,
            },
        )

    def test_start_trial_maps_to_lead(self):
        provider = RedditCAPI(pixel_id="acct123", token="token456")
        evt = {
            "event_name": "StartTrial",
            "event_time": 1_700_000_000,
            "event_id": "evt-trial-1",
            "properties": {},
            "ids": {},
            "network": {},
            "utm": {},
            "consent": True,
        }

        with patch("marketing_events.providers.reddit.post_json") as mock_post:
            mock_post.return_value = {}
            provider.send(evt)

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        event = kwargs["json"]["data"]["events"][0]
        self.assertEqual(event["type"], {"tracking_type": "LEAD"})
