from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings, tag

from marketing_events.api import capi


@tag("batch_marketing_events")
class MarketingEventsApiTests(SimpleTestCase):
    @override_settings(GOBII_PROPRIETARY_MODE=True, CAPI_START_TRIAL_DELAY_MINUTES=60)
    @patch("marketing_events.api.time.time", return_value=1_700_000_000)
    @patch("marketing_events.api.enqueue_start_trial_marketing_event.apply_async")
    def test_capi_start_trial_delays_with_original_event_time(
        self,
        mock_apply_async,
        _mock_time,
    ):
        user = SimpleNamespace(id=42, email="test@example.com", phone="+15555550123")

        capi(
            user=user,
            event_name="StartTrial",
            properties={"subscription_id": "sub_123"},
            request=None,
            context={"consent": True},
        )

        mock_apply_async.assert_called_once()
        call_kwargs = mock_apply_async.call_args.kwargs
        self.assertEqual(call_kwargs["countdown"], 3600)

        payload = call_kwargs["args"][0]
        self.assertEqual(payload["event_name"], "StartTrial")
        self.assertEqual(payload["properties"]["subscription_id"], "sub_123")
        self.assertEqual(payload["properties"]["event_time"], 1_700_000_000)

    @override_settings(GOBII_PROPRIETARY_MODE=True, CAPI_START_TRIAL_DELAY_MINUTES=60)
    @patch("marketing_events.api.enqueue_marketing_event.delay")
    @patch("marketing_events.api.enqueue_start_trial_marketing_event.apply_async")
    def test_capi_non_start_trial_uses_existing_enqueue_path(
        self,
        mock_start_trial_apply_async,
        mock_enqueue_delay,
    ):
        user = SimpleNamespace(id=42, email="test@example.com", phone="+15555550123")

        capi(
            user=user,
            event_name="CompleteRegistration",
            properties={"event_id": "evt-123"},
            request=None,
            context={"consent": True},
        )

        mock_start_trial_apply_async.assert_not_called()
        mock_enqueue_delay.assert_called_once()
