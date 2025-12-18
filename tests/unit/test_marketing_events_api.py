from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings, tag

from marketing_events.api import capi


@tag("batch_marketing_events")
class CapiEnvironmentGateTests(TestCase):
    @override_settings(GOBII_PROPRIETARY_MODE=True, GOBII_RELEASE_ENV="prod")
    @patch("marketing_events.api.enqueue_marketing_event.delay")
    def test_emits_in_production(self, mock_delay):
        user = SimpleNamespace(id=123, email="user@example.com", phone=None)

        capi(user=user, event_name="TestEvent", properties={"foo": "bar"}, request=None, context={"consent": True})

        mock_delay.assert_called_once()
        payload = mock_delay.call_args.args[0]
        self.assertEqual(payload["event_name"], "TestEvent")
        self.assertEqual(payload["properties"]["foo"], "bar")
        self.assertEqual(payload["user"]["id"], "123")

    @override_settings(GOBII_PROPRIETARY_MODE=True, GOBII_RELEASE_ENV="staging")
    @patch("marketing_events.api.enqueue_marketing_event.delay")
    def test_skips_when_not_production(self, mock_delay):
        user = SimpleNamespace(id=123, email="user@example.com", phone=None)

        capi(user=user, event_name="TestEvent", properties={"foo": "bar"}, request=None, context={"consent": True})

        mock_delay.assert_not_called()
