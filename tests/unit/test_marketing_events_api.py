from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings, tag

from marketing_events.api import capi, capi_delay_subscription_guarded
from marketing_events.custom_events import (
    ConfiguredCustomEvent,
    _is_first_workspace_agent_creation,
    emit_configured_custom_capi_event,
)
from api.models import BrowserUseAgent, Organization, PersistentAgent


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
    @patch("marketing_events.custom_events.is_fast_cancel_owner", return_value=False)
    @patch("marketing_events.custom_events.is_owner_currently_in_trial", return_value=True)
    @patch("marketing_events.custom_events.get_owner_plan", return_value={"id": "startup"})
    def test_emit_configured_custom_capi_event_uses_plan_value_and_ad_targets(
        self,
        _mock_get_owner_plan,
        _mock_is_owner_currently_in_trial,
        _mock_is_fast_cancel_owner,
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
    @patch("marketing_events.custom_events.is_fast_cancel_owner", return_value=False)
    @patch("marketing_events.custom_events.is_owner_currently_in_trial", return_value=True)
    @patch("marketing_events.custom_events.get_owner_plan", return_value={"id": "free"})
    def test_emit_configured_custom_capi_event_omits_value_for_free_plan(
        self,
        _mock_get_owner_plan,
        _mock_is_owner_currently_in_trial,
        _mock_is_fast_cancel_owner,
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
    @patch("marketing_events.custom_events.is_owner_currently_in_trial", return_value=False)
    def test_emit_configured_custom_capi_event_skips_when_user_not_in_trial(
        self,
        _mock_is_owner_currently_in_trial,
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
    @patch("marketing_events.custom_events.is_fast_cancel_owner", return_value=True)
    @patch("marketing_events.custom_events.is_owner_currently_in_trial", return_value=True)
    def test_emit_configured_custom_capi_event_skips_for_fast_cancel_user(
        self,
        _mock_is_owner_currently_in_trial,
        _mock_is_fast_cancel_owner,
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
    @patch("marketing_events.custom_events.is_fast_cancel_owner", return_value=False)
    @patch("marketing_events.custom_events.is_owner_currently_in_trial", return_value=True)
    @patch("marketing_events.custom_events.get_owner_plan", return_value={"id": "startup"})
    def test_emit_configured_custom_capi_event_sends_inbound_message_for_first_message(
        self,
        _mock_get_owner_plan,
        _mock_is_owner_currently_in_trial,
        _mock_is_fast_cancel_owner,
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
    @patch("marketing_events.custom_events.is_fast_cancel_owner", return_value=False)
    @patch("marketing_events.custom_events.is_owner_currently_in_trial", return_value=True)
    @patch("marketing_events.custom_events.get_owner_plan", return_value={"id": "startup"})
    def test_emit_configured_custom_capi_event_resolves_inbound_message_count_once(
        self,
        _mock_get_owner_plan,
        _mock_is_owner_currently_in_trial,
        _mock_is_fast_cancel_owner,
        mock_count_messages_sent_to_gobii,
        _mock_get_custom_capi_event_delay_seconds,
        _mock_get_active_subscription,
        _mock_capi_delay_subscription_guarded,
    ):
        user = SimpleNamespace(id=42, email="test@example.com", phone="+15555550123")

        emit_configured_custom_capi_event(
            user=user,
            event_name=ConfiguredCustomEvent.INBOUND_MESSAGE,
            plan_owner=user,
            properties={"agent_id": "agent-1"},
        )

        mock_count_messages_sent_to_gobii.assert_called_once_with(user)

    @patch("marketing_events.custom_events.capi_delay_subscription_guarded")
    @patch("marketing_events.custom_events.count_messages_sent_to_gobii", return_value=2)
    @patch("marketing_events.custom_events.is_fast_cancel_owner", return_value=False)
    @patch("marketing_events.custom_events.is_owner_currently_in_trial", return_value=True)
    def test_emit_configured_custom_capi_event_skips_inbound_message_outside_thresholds(
        self,
        _mock_is_owner_currently_in_trial,
        _mock_is_fast_cancel_owner,
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
    @patch("marketing_events.custom_events.is_fast_cancel_owner", return_value=False)
    @patch("marketing_events.custom_events.is_owner_currently_in_trial", return_value=True)
    def test_emit_configured_custom_capi_event_skips_agent_created_after_first_workspace_agent(
        self,
        _mock_is_owner_currently_in_trial,
        _mock_is_fast_cancel_owner,
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

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        CAPI_CUSTOM_EVENT_CURRENCY="USD",
        CAPI_CUSTOM_EVENT_VALUES_BY_PLAN={
            "pro": {"IntegrationAdded": 9.45},
            "scale": {"IntegrationAdded": 47.25},
            "org_team": {"IntegrationAdded": 9.45},
        },
    )
    @patch("marketing_events.custom_events.capi_delay_subscription_guarded")
    @patch("marketing_events.custom_events.get_active_subscription", return_value=SimpleNamespace(id="sub_org_123"))
    @patch("marketing_events.custom_events.get_custom_capi_event_delay_seconds", return_value=7200)
    @patch("marketing_events.custom_events.is_fast_cancel_owner", return_value=False)
    @patch("marketing_events.custom_events.is_owner_currently_in_trial", return_value=True)
    @patch("marketing_events.custom_events.get_owner_plan", return_value={"id": "org_team"})
    def test_emit_configured_custom_capi_event_uses_plan_owner_for_trial_gate_delay_and_subscription_guard(
        self,
        _mock_get_owner_plan,
        mock_is_owner_currently_in_trial,
        mock_is_fast_cancel_owner,
        mock_get_custom_capi_event_delay_seconds,
        mock_get_active_subscription,
        mock_capi_delay_subscription_guarded,
    ):
        user = SimpleNamespace(id=42, email="member@example.com", phone="+15555550123")
        org_owner = SimpleNamespace(id="org-123")

        emit_configured_custom_capi_event(
            user=user,
            event_name=ConfiguredCustomEvent.INTEGRATION_ADDED,
            plan_owner=org_owner,
            properties={"agent_id": "agent-1"},
        )

        mock_is_owner_currently_in_trial.assert_called_once_with(org_owner)
        mock_is_fast_cancel_owner.assert_called_once_with(org_owner)
        mock_get_custom_capi_event_delay_seconds.assert_called_once_with(org_owner)
        mock_get_active_subscription.assert_called_once_with(org_owner)
        mock_capi_delay_subscription_guarded.assert_called_once_with(
            user=user,
            event_name="IntegrationAdded",
            countdown_seconds=7200,
            subscription_guard_id="sub_org_123",
            properties={
                "agent_id": "agent-1",
                "value": 9.45,
                "currency": "USD",
            },
            request=None,
            context={"consent": True},
            provider_targets=["meta", "reddit", "tiktok"],
        )


@tag("batch_marketing_events")
class ConfiguredCustomEventHelperTests(TestCase):
    def _create_agent(self, *, user, name: str, organization=None) -> PersistentAgent:
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            if organization is not None:
                with patch.object(PersistentAgent, "_validate_org_seats", return_value=None):
                    browser = BrowserUseAgent.objects.create(user=user, name=f"{name}-browser")
                    return PersistentAgent.objects.create(
                        user=user,
                        organization=organization,
                        name=name,
                        charter="",
                        browser_use_agent=browser,
                    )

            browser = BrowserUseAgent.objects.create(user=user, name=f"{name}-browser")
            return PersistentAgent.objects.create(
                user=user,
                organization=None,
                name=name,
                charter="",
                browser_use_agent=browser,
            )

    def test_is_first_workspace_agent_creation_matches_first_personal_agent_id(self):
        user = get_user_model().objects.create_user(
            username="owner",
            email="owner@example.com",
            password="password123",
        )
        first_agent = self._create_agent(user=user, name="First")
        second_agent = self._create_agent(user=user, name="Second")

        self.assertTrue(
            _is_first_workspace_agent_creation(user, {"agent_id": str(first_agent.id)})
        )
        self.assertFalse(
            _is_first_workspace_agent_creation(user, {"agent_id": str(second_agent.id)})
        )

    def test_is_first_workspace_agent_creation_matches_first_org_agent_id(self):
        user = get_user_model().objects.create_user(
            username="org-owner",
            email="org-owner@example.com",
            password="password123",
        )
        organization = Organization.objects.create(name="Acme", slug="acme", created_by=user)
        first_agent = self._create_agent(user=user, organization=organization, name="First Org")
        second_agent = self._create_agent(user=user, organization=organization, name="Second Org")

        self.assertTrue(
            _is_first_workspace_agent_creation(organization, {"agent_id": str(first_agent.id)})
        )
        self.assertFalse(
            _is_first_workspace_agent_creation(organization, {"agent_id": str(second_agent.id)})
        )
