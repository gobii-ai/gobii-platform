from unittest.mock import patch

from django.test import SimpleTestCase, tag

from marketing_events.providers.reddit import RedditCAPI


@tag("batch_marketing_events")
class RedditPayloadTests(SimpleTestCase):
    def test_payload_matches_latest_schema(self):
        provider = RedditCAPI(ad_account="acct123", token="token456")
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

        self.assertEqual(body["event_name"], "SignUp")
        self.assertEqual(body["conversion_id"], "evt-123")
        self.assertTrue(body["event_at"].endswith("Z"))
        self.assertTrue(body["test_mode"])

        match_keys = body["match_keys"]
        self.assertEqual(match_keys["email"], "hash-email")
        self.assertEqual(match_keys["phone"], "hash-phone")
        self.assertEqual(match_keys["external_id"], "hash-external")
        self.assertEqual(match_keys["rdt_click_id"], "rdt-123")
        self.assertEqual(match_keys["ip_address"], "203.0.113.5")

        metadata = body["event_metadata"]
        self.assertEqual(metadata["value"], 99.99)
        self.assertEqual(metadata["currency"], "USD")
        self.assertEqual(metadata["item_count"], 1)
        self.assertNotIn("event_id", metadata)
