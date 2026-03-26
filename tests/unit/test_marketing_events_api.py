from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings, tag

from marketing_events.api import capi, capi_delay_subscription_guarded
from marketing_events.custom_events import ConfiguredCustomEvent, emit_configured_custom_capi_event


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

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("marketing_events.api.time.time", return_value=1_700_000_000)
    @patch("marketing_events.api.enqueue_delayed_subscription_guarded_marketing_event.apply_async")
    def test_capi_delay_subscription_guarded_preserves_event_time_and_subscription_guard(
        self,
        mock_apply_async,
        _mock_time,
    ):
        user = SimpleNamespace(id=42, email="test@example.com", phone="+15555550123")

        capi_delay_subscription_guarded(
            user=user,
            event_name="AgentCreated",
            countdown_seconds=3660.2,
            subscription_guard_id="sub_guard_123",
            properties={"agent_id": "agent-1"},
            request=None,
            context={"consent": True},
            provider_targets=["meta"],
        )

        mock_apply_async.assert_called_once()
        call_kwargs = mock_apply_async.call_args.kwargs
        self.assertEqual(call_kwargs["countdown"], 3661)

        payload = call_kwargs["args"][0]
        self.assertEqual(payload["event_name"], "AgentCreated")
        self.assertEqual(payload["subscription_guard_id"], "sub_guard_123")
        self.assertEqual(payload["properties"]["agent_id"], "agent-1")
        self.assertEqual(payload["properties"]["event_time"], 1_700_000_000)
        self.assertEqual(payload["provider_targets"], ["meta"])

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        CAPI_CUSTOM_EVENT_CURRENCY="USD",
        CAPI_CUSTOM_EVENT_VALUES_BY_PLAN={
            "pro": {"AgentCreated": 12.5},
            "scale": {"AgentCreated": 22.5},
            "org_team": {"AgentCreated": 12.5},
        },
    )
    @patch("marketing_events.custom_events.capi_delay_subscription_guarded")
    @patch("marketing_events.custom_events.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("marketing_events.custom_events.get_custom_capi_event_delay_seconds", return_value=3600)
    @patch("marketing_events.custom_events._is_first_workspace_agent_creation", return_value=True)
    @patch("marketing_events.custom_events.is_fast_cancel_user", return_value=False)
    @patch("marketing_events.custom_events.is_user_currently_in_trial", return_value=True)
    @patch("marketing_events.custom_events.get_owner_plan", return_value={"id": "startup"})
    def test_emit_configured_custom_capi_event_uses_plan_value_and_ad_targets(
        self,
        _mock_get_owner_plan,
        _mock_is_user_currently_in_trial,
        _mock_is_fast_cancel_user,
        _mock_is_first_workspace_agent_creation,
        _mock_get_custom_capi_event_delay_seconds,
        _mock_get_active_subscription,
        mock_capi_delay_subscription_guarded,
    ):
        user = SimpleNamespace(id=42, email="test@example.com", phone="+15555550123")

        emit_configured_custom_capi_event(
            user=user,
            event_name=ConfiguredCustomEvent.AGENT_CREATED,
            plan_owner=user,
            properties={"agent_id": "agent-1"},
        )

        mock_capi_delay_subscription_guarded.assert_called_once_with(
            user=user,
            event_name="AgentCreated",
            countdown_seconds=3600,
            subscription_guard_id="sub_123",
            properties={
                "value": 12.5,
                "currency": "USD",
                "agent_id": "agent-1",
            },
            request=None,
            context={"consent": True},
            provider_targets=["meta", "reddit", "tiktok"],
        )

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        CAPI_CUSTOM_EVENT_CURRENCY="USD",
        CAPI_CUSTOM_EVENT_VALUES_BY_PLAN={
            "pro": {"AgentCreated": 12.5},
            "scale": {"AgentCreated": 22.5},
            "org_team": {"AgentCreated": 12.5},
        },
    )
    @patch("marketing_events.custom_events.capi_delay_subscription_guarded")
    @patch("marketing_events.custom_events.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("marketing_events.custom_events.get_custom_capi_event_delay_seconds", return_value=3600)
    @patch("marketing_events.custom_events._is_first_workspace_agent_creation", return_value=True)
    @patch("marketing_events.custom_events.is_fast_cancel_user", return_value=False)
    @patch("marketing_events.custom_events.is_user_currently_in_trial", return_value=True)
    @patch("marketing_events.custom_events.get_owner_plan", return_value={"id": "free"})
    def test_emit_configured_custom_capi_event_omits_value_for_free_plan(
        self,
        _mock_get_owner_plan,
        _mock_is_user_currently_in_trial,
        _mock_is_fast_cancel_user,
        _mock_is_first_workspace_agent_creation,
        _mock_get_custom_capi_event_delay_seconds,
        _mock_get_active_subscription,
        mock_capi_delay_subscription_guarded,
    ):
        user = SimpleNamespace(id=42, email="test@example.com", phone="+15555550123")

        emit_configured_custom_capi_event(
            user=user,
            event_name=ConfiguredCustomEvent.AGENT_CREATED,
            plan_owner=user,
            properties={"agent_id": "agent-1"},
        )

        mock_capi_delay_subscription_guarded.assert_called_once_with(
            user=user,
            event_name="AgentCreated",
            countdown_seconds=3600,
            subscription_guard_id="sub_123",
            properties={"agent_id": "agent-1"},
            request=None,
            context={"consent": True},
            provider_targets=["meta", "reddit", "tiktok"],
        )

    @patch("marketing_events.custom_events.capi_delay_subscription_guarded")
    @patch("marketing_events.custom_events._is_first_workspace_agent_creation", return_value=True)
    @patch("marketing_events.custom_events.is_user_currently_in_trial", return_value=False)
    def test_emit_configured_custom_capi_event_skips_when_user_not_in_trial(
        self,
        _mock_is_user_currently_in_trial,
        _mock_is_first_workspace_agent_creation,
        mock_capi_delay_subscription_guarded,
    ):
        user = SimpleNamespace(id=42, email="test@example.com", phone="+15555550123")

        emit_configured_custom_capi_event(
            user=user,
            event_name=ConfiguredCustomEvent.AGENT_CREATED,
            plan_owner=user,
            properties={"agent_id": "agent-1"},
        )

        mock_capi_delay_subscription_guarded.assert_not_called()

    @patch("marketing_events.custom_events.capi_delay_subscription_guarded")
    @patch("marketing_events.custom_events._is_first_workspace_agent_creation", return_value=True)
    @patch("marketing_events.custom_events.is_fast_cancel_user", return_value=True)
    @patch("marketing_events.custom_events.is_user_currently_in_trial", return_value=True)
    def test_emit_configured_custom_capi_event_skips_for_fast_cancel_user(
        self,
        _mock_is_user_currently_in_trial,
        _mock_is_fast_cancel_user,
        _mock_is_first_workspace_agent_creation,
        mock_capi_delay_subscription_guarded,
    ):
        user = SimpleNamespace(id=42, email="test@example.com", phone="+15555550123")

        emit_configured_custom_capi_event(
            user=user,
            event_name=ConfiguredCustomEvent.AGENT_CREATED,
            plan_owner=user,
            properties={"agent_id": "agent-1"},
        )

        mock_capi_delay_subscription_guarded.assert_not_called()

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        CAPI_CUSTOM_EVENT_CURRENCY="USD",
        CAPI_CUSTOM_EVENT_VALUES_BY_PLAN={
            "pro": {"InboundMessage": {1: 2.1, 5: 4.2, 20: 8.4}},
            "scale": {"InboundMessage": {1: 10.5, 5: 21.0, 20: 42.0}},
            "org_team": {"InboundMessage": {1: 2.1, 5: 4.2, 20: 8.4}},
        },
    )
    @patch("marketing_events.custom_events.capi_delay_subscription_guarded")
    @patch("marketing_events.custom_events.get_active_subscription", return_value=SimpleNamespace(id="sub_123"))
    @patch("marketing_events.custom_events.get_custom_capi_event_delay_seconds", return_value=3600)
    @patch("marketing_events.custom_events.count_messages_sent_to_gobii", return_value=1)
    @patch("marketing_events.custom_events.is_fast_cancel_user", return_value=False)
    @patch("marketing_events.custom_events.is_user_currently_in_trial", return_value=True)
    @patch("marketing_events.custom_events.get_owner_plan", return_value={"id": "startup"})
    def test_emit_configured_custom_capi_event_sends_inbound_message_for_first_message(
        self,
        _mock_get_owner_plan,
        _mock_is_user_currently_in_trial,
        _mock_is_fast_cancel_user,
        _mock_count_messages_sent_to_gobii,
        _mock_get_custom_capi_event_delay_seconds,
        _mock_get_active_subscription,
        mock_capi_delay_subscription_guarded,
    ):
        user = SimpleNamespace(id=42, email="test@example.com", phone="+15555550123")

        emit_configured_custom_capi_event(
            user=user,
            event_name=ConfiguredCustomEvent.INBOUND_MESSAGE,
            plan_owner=user,
            properties={"agent_id": "agent-1"},
        )

        mock_capi_delay_subscription_guarded.assert_called_once_with(
            user=user,
            event_name="InboundMessage",
            countdown_seconds=3600,
            subscription_guard_id="sub_123",
            properties={
                "agent_id": "agent-1",
                "message_count": 1,
                "value": 2.1,
                "currency": "USD",
            },
            request=None,
            context={"consent": True},
            provider_targets=["meta", "reddit", "tiktok"],
        )

    @patch("marketing_events.custom_events.capi_delay_subscription_guarded")
    @patch("marketing_events.custom_events.count_messages_sent_to_gobii", return_value=2)
    @patch("marketing_events.custom_events.is_fast_cancel_user", return_value=False)
    @patch("marketing_events.custom_events.is_user_currently_in_trial", return_value=True)
    def test_emit_configured_custom_capi_event_skips_inbound_message_outside_thresholds(
        self,
        _mock_is_user_currently_in_trial,
        _mock_is_fast_cancel_user,
        _mock_count_messages_sent_to_gobii,
        mock_capi_delay_subscription_guarded,
    ):
        user = SimpleNamespace(id=42, email="test@example.com", phone="+15555550123")

        emit_configured_custom_capi_event(
            user=user,
            event_name=ConfiguredCustomEvent.INBOUND_MESSAGE,
            plan_owner=user,
            properties={"agent_id": "agent-1"},
        )

        mock_capi_delay_subscription_guarded.assert_not_called()

    @patch("marketing_events.custom_events.capi_delay_subscription_guarded")
    @patch("marketing_events.custom_events._is_first_workspace_agent_creation", return_value=False)
    @patch("marketing_events.custom_events.is_fast_cancel_user", return_value=False)
    @patch("marketing_events.custom_events.is_user_currently_in_trial", return_value=True)
    def test_emit_configured_custom_capi_event_skips_agent_created_after_first_workspace_agent(
        self,
        _mock_is_user_currently_in_trial,
        _mock_is_fast_cancel_user,
        _mock_is_first_workspace_agent_creation,
        mock_capi_delay_subscription_guarded,
    ):
        user = SimpleNamespace(id=42, email="test@example.com", phone="+15555550123")

        emit_configured_custom_capi_event(
            user=user,
            event_name=ConfiguredCustomEvent.AGENT_CREATED,
            plan_owner=user,
            properties={"agent_id": "agent-2"},
        )

        mock_capi_delay_subscription_guarded.assert_not_called()
