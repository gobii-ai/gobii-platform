from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, tag, override_settings

from marketing_events.providers.base import PermanentError
from marketing_events.tasks import _analytics_user_id, enqueue_marketing_event


@tag("batch_marketing_events")
class MarketingEventsTaskTests(SimpleTestCase):
    def test_analytics_user_id_prefers_numeric_raw_user_id(self):
        self.assertEqual(_analytics_user_id("123", "hashed-id"), 123)

    def test_analytics_user_id_falls_back_to_hashed_external_id(self):
        self.assertEqual(_analytics_user_id("", "hashed-id"), "hashed-id")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks.get_providers")
    def test_enqueue_tracks_success_with_raw_user_id(self, mock_get_providers, mock_track):
        provider = MagicMock()
        provider.__class__.__name__ = "MetaCAPI"
        provider.send.return_value = {}
        mock_get_providers.return_value = [provider]

        enqueue_marketing_event(
            {
                "event_name": "StartTrial",
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-123"},
                "user": {"id": "42", "email": "test@example.com"},
                "context": {},
            }
        )

        mock_track.assert_called_once()
        kwargs = mock_track.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 42)
        self.assertEqual(kwargs["event"], "CAPI Event Sent")
        self.assertEqual(kwargs["properties"]["provider"], "MetaCAPI")
        self.assertEqual(kwargs["properties"]["event_id"], "evt-123")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks.get_providers")
    def test_enqueue_tracks_permanent_failure_with_raw_user_id(self, mock_get_providers, mock_track):
        provider = MagicMock()
        provider.__class__.__name__ = "MetaCAPI"
        provider.send.side_effect = PermanentError("400: bad request")
        mock_get_providers.return_value = [provider]

        enqueue_marketing_event(
            {
                "event_name": "Subscribe",
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-456"},
                "user": {"id": "77", "email": "test@example.com"},
                "context": {},
            }
        )

        mock_track.assert_called_once()
        kwargs = mock_track.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 77)
        self.assertEqual(kwargs["event"], "CAPI Event Failed")
        self.assertEqual(kwargs["properties"]["provider"], "MetaCAPI")
        self.assertEqual(kwargs["properties"]["event_id"], "evt-456")
        self.assertEqual(kwargs["properties"]["error_type"], "permanent")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.tasks.Analytics.track")
    @patch("marketing_events.tasks.get_providers")
    def test_enqueue_respects_provider_targets(self, mock_get_providers, mock_track):
        meta_provider = MagicMock()
        meta_provider.__class__.__name__ = "MetaCAPI"
        meta_provider.send.return_value = {}

        ga_provider = MagicMock()
        ga_provider.__class__.__name__ = "GoogleAnalyticsMP"
        ga_provider.send.return_value = {}

        mock_get_providers.return_value = [meta_provider, ga_provider]

        enqueue_marketing_event(
            {
                "event_name": "Subscribe",
                "properties": {"event_time": 1_900_000_000, "event_id": "evt-789"},
                "user": {"id": "88", "email": "test@example.com"},
                "context": {},
                "provider_targets": ["google_analytics"],
            }
        )

        meta_provider.send.assert_not_called()
        ga_provider.send.assert_called_once()

        mock_track.assert_called_once()
        kwargs = mock_track.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 88)
        self.assertEqual(kwargs["event"], "CAPI Event Sent")
        self.assertEqual(kwargs["properties"]["provider"], "GoogleAnalyticsMP")
