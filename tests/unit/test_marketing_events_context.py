from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase, tag

from api.models import UserAttribution
from marketing_events.context import build_marketing_context_from_user, extract_click_context


@tag("batch_marketing_events")
class ExtractClickContextTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch("marketing_events.context.time.time", return_value=1_700_000_000.123)
    @patch("marketing_events.context.record_fbc_synthesized")
    @patch("marketing_events.context.Analytics.get_client_ip", return_value="198.51.100.24")
    def test_synthesizes_fbc_with_millisecond_timestamp_from_fbclid(
        self, _mock_client_ip, mock_record_fbc_synthesized, _mock_time
    ):
        request = self.factory.get("/pricing", {"fbclid": "fbclid-123"})

        context = extract_click_context(request)

        self.assertEqual(context["click_ids"]["fbclid"], "fbclid-123")
        self.assertEqual(context["click_ids"]["fbc"], "fb.1.1700000000123.fbclid-123")
        mock_record_fbc_synthesized.assert_called_once_with(
            source="marketing_events.context.extract_click_context"
        )

    @patch("marketing_events.context.record_fbc_synthesized")
    @patch("marketing_events.context.Analytics.get_client_ip", return_value="198.51.100.24")
    def test_preserves_existing_fbc_cookie_when_fbclid_present(self, _mock_client_ip, mock_record_fbc_synthesized):
        request = self.factory.get("/pricing", {"fbclid": "fbclid-123"})
        request.COOKIES["_fbc"] = "fb.1.1111111111111.existing"

        context = extract_click_context(request)

        self.assertEqual(context["click_ids"]["fbclid"], "fbclid-123")
        self.assertEqual(context["click_ids"]["fbc"], "fb.1.1111111111111.existing")
        mock_record_fbc_synthesized.assert_not_called()

    @patch("marketing_events.context.Analytics.get_client_ip", return_value="198.51.100.24")
    def test_reads_reddit_click_id_alias_from_query(self, _mock_client_ip):
        request = self.factory.get("/pricing", {"rdt_click_id": "reddit-click-123"})

        context = extract_click_context(request)

        self.assertEqual(context["click_ids"]["rdt_cid"], "reddit-click-123")

    @patch("marketing_events.context.Analytics.get_client_ip", return_value="198.51.100.24")
    def test_uses_reddit_click_id_cookie_when_query_missing(self, _mock_client_ip):
        request = self.factory.get("/pricing")
        request.COOKIES["rdt_cid"] = "reddit-cookie-123"

        context = extract_click_context(request)

        self.assertEqual(context["click_ids"]["rdt_cid"], "reddit-cookie-123")

    @patch("marketing_events.context.Analytics.get_client_ip", return_value="198.51.100.24")
    def test_reads_linkedin_first_party_ads_tracking_id(self, _mock_client_ip):
        request = self.factory.get("/pricing", {"li_fat_id": "linkedin-click-123"})

        context = extract_click_context(request)

        self.assertEqual(context["click_ids"]["li_fat_id"], "linkedin-click-123")


@tag("batch_marketing_events")
class BuildMarketingContextFromUserTests(TestCase):
    def test_build_marketing_context_from_user_includes_persisted_tiktok_click_id(self):
        user = get_user_model().objects.create_user(
            username="marketing-context-user",
            email="marketing-context@example.com",
            password="password123",
        )
        UserAttribution.objects.create(
            user=user,
            ttclid_first="ttclid-first",
            ttclid_last="ttclid-last",
        )

        context = build_marketing_context_from_user(user)

        self.assertEqual(context["click_ids"]["ttclid"], "ttclid-last")

    def test_build_marketing_context_from_user_includes_persisted_linkedin_click_id(self):
        user = get_user_model().objects.create_user(
            username="marketing-context-linkedin-user",
            email="marketing-context-linkedin@example.com",
            password="password123",
        )
        UserAttribution.objects.create(
            user=user,
            li_fat_id_first="li-first",
            li_fat_id_last="li-last",
        )

        context = build_marketing_context_from_user(user)

        self.assertEqual(context["click_ids"]["li_fat_id"], "li-last")
