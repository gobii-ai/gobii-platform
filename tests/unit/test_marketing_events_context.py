from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, tag

from marketing_events.context import extract_click_context


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
