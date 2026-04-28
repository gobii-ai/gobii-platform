from unittest.mock import patch

from django.test import SimpleTestCase, override_settings, tag

from marketing_events.providers import get_providers
from marketing_events.providers.linkedin import LinkedInCAPI


@tag("batch_marketing_events")
class LinkedInPayloadTests(SimpleTestCase):
    def test_payload_matches_expected_schema(self):
        provider = LinkedInCAPI(
            token="token-123",
            conversion_ids={"StartTrial": "27222626"},
            api_version="202601",
        )
        evt = {
            "event_name": "StartTrial",
            "event_time": 1_700_000_000,
            "event_id": "evt-123",
            "properties": {
                "value": 25,
                "currency": "USD",
            },
            "ids": {
                "external_id": "hash-external",
                "em": "hash-email",
                "ph": "hash-phone",
            },
            "network": {
                "li_fat_id": "li-click-123",
            },
            "utm": {},
            "consent": True,
        }

        with patch("marketing_events.providers.linkedin.post_json") as mock_post:
            mock_post.return_value = {}
            provider.send(evt)

        mock_post.assert_called_once()
        url = mock_post.call_args.args[0]
        kwargs = mock_post.call_args.kwargs

        self.assertEqual(url, provider.url)
        self.assertEqual(
            kwargs["headers"],
            {
                "Authorization": "Bearer token-123",
                "Content-Type": "application/json",
                "Linkedin-Version": "202601",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )

        body = kwargs["json"]
        self.assertEqual(body["conversion"], "urn:lla:llaPartnerConversion:27222626")
        self.assertEqual(body["conversionHappenedAt"], 1_700_000_000_000)
        self.assertEqual(body["eventId"], "evt-123")
        self.assertEqual(body["conversionValue"], {"currencyCode": "USD", "amount": "25"})
        self.assertEqual(
            body["user"]["userIds"],
            [
                {"idType": "SHA256_EMAIL", "idValue": "hash-email"},
                {
                    "idType": "LINKEDIN_FIRST_PARTY_ADS_TRACKING_UUID",
                    "idValue": "li-click-123",
                },
            ],
        )

    def test_accepts_full_conversion_urn(self):
        provider = LinkedInCAPI(
            token="token-123",
            conversion_ids={"Activated": "urn:lla:llaPartnerConversion:27222634"},
            api_version="202601",
        )
        evt = {
            "event_name": "Activated",
            "event_time": 1_700_000_000,
            "event_id": "evt-activated",
            "properties": {},
            "ids": {"em": "hash-email"},
            "network": {},
            "utm": {},
            "consent": True,
        }

        with patch("marketing_events.providers.linkedin.post_json") as mock_post:
            mock_post.return_value = {}
            provider.send(evt)

        self.assertEqual(
            mock_post.call_args.kwargs["json"]["conversion"],
            "urn:lla:llaPartnerConversion:27222634",
        )

    def test_subscribe_prefers_transaction_value(self):
        provider = LinkedInCAPI(
            token="token-123",
            conversion_ids={"Subscribe": "27222618"},
            api_version="202601",
        )
        evt = {
            "event_name": "Subscribe",
            "event_time": 1_700_000_000,
            "event_id": "evt-subscribe",
            "properties": {
                "value": 150.0,
                "transaction_value": 30.0,
                "currency": "USD",
            },
            "ids": {"em": "hash-email"},
            "network": {},
            "utm": {},
            "consent": True,
        }

        with patch("marketing_events.providers.linkedin.post_json") as mock_post:
            mock_post.return_value = {}
            provider.send(evt)

        self.assertEqual(
            mock_post.call_args.kwargs["json"]["conversionValue"],
            {"currencyCode": "USD", "amount": "30.0"},
        )

    def test_send_honors_consent(self):
        provider = LinkedInCAPI(
            token="token-123",
            conversion_ids={"StartTrial": "27222626"},
            api_version="202601",
        )
        evt = {
            "event_name": "StartTrial",
            "event_time": 1_700_000_000,
            "event_id": "evt-123",
            "properties": {},
            "ids": {"em": "hash-email"},
            "network": {},
            "utm": {},
            "consent": False,
        }

        with patch("marketing_events.providers.linkedin.post_json") as mock_post:
            provider.send(evt)

        mock_post.assert_not_called()

    def test_send_skips_when_event_has_no_configured_conversion(self):
        provider = LinkedInCAPI(
            token="token-123",
            conversion_ids={"StartTrial": "27222626"},
            api_version="202601",
        )
        evt = {
            "event_name": "UnconfiguredEvent",
            "event_time": 1_700_000_000,
            "event_id": "evt-123",
            "properties": {},
            "ids": {"em": "hash-email"},
            "network": {},
            "utm": {},
            "consent": True,
        }

        with patch("marketing_events.providers.linkedin.post_json") as mock_post:
            provider.send(evt)

        mock_post.assert_not_called()

    def test_send_skips_when_user_has_no_linkedin_identifier(self):
        provider = LinkedInCAPI(
            token="token-123",
            conversion_ids={"StartTrial": "27222626"},
            api_version="202601",
        )
        evt = {
            "event_name": "StartTrial",
            "event_time": 1_700_000_000,
            "event_id": "evt-123",
            "properties": {},
            "ids": {},
            "network": {},
            "utm": {},
            "consent": True,
        }

        with patch("marketing_events.providers.linkedin.post_json") as mock_post:
            provider.send(evt)

        mock_post.assert_not_called()


@tag("batch_marketing_events")
class LinkedInProviderRegistrationTests(SimpleTestCase):
    @override_settings(
        GA_MEASUREMENT_ID="",
        GA_MEASUREMENT_API_SECRET="",
        FACEBOOK_ACCESS_TOKEN="",
        META_PIXEL_ID="",
        REDDIT_ACCESS_TOKEN="",
        REDDIT_ADVERTISER_ID="",
        TIKTOK_ACCESS_TOKEN="",
        TIKTOK_PIXEL_ID="",
        LINKEDIN_CAPI_ACCESS_TOKEN="token-123",
        LINKEDIN_CAPI_CONVERSION_IDS={"StartTrial": "27222626"},
        LINKEDIN_CAPI_VERSION="202601",
    )
    def test_get_providers_includes_linkedin_when_configured(self):
        providers = get_providers()

        self.assertEqual(len(providers), 1)
        self.assertIsInstance(providers[0], LinkedInCAPI)

    @override_settings(
        GA_MEASUREMENT_ID="",
        GA_MEASUREMENT_API_SECRET="",
        FACEBOOK_ACCESS_TOKEN="",
        META_PIXEL_ID="",
        REDDIT_ACCESS_TOKEN="",
        REDDIT_ADVERTISER_ID="",
        TIKTOK_ACCESS_TOKEN="",
        TIKTOK_PIXEL_ID="",
        LINKEDIN_CAPI_ACCESS_TOKEN="token-123",
        LINKEDIN_CAPI_CONVERSION_IDS={},
        LINKEDIN_CAPI_VERSION="202601",
    )
    def test_get_providers_skips_linkedin_without_conversion_ids(self):
        providers = get_providers()

        self.assertEqual(providers, [])
