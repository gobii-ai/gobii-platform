import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from api.agent.system_skills.registry import get_system_skill_definition, shortlist_system_skills
from api.agent.system_skills.service import enable_system_skills
from api.agent.tools.pipedream_trigger_subscriptions import execute_pipedream_trigger_subscriptions
from api.agent.tools.tool_manager import ToolCatalogEntry, _record_pipedream_tool_side_effects
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentEnabledTool,
    PersistentAgentMessage,
    PersistentAgentPipedreamTriggerSubscription,
    PipedreamAppSelection,
)
from api.services.pipedream_trigger_subscriptions import (
    DISCORD_MESSAGE_EVENT_TYPE,
    PipedreamTriggerSubscriptionError,
    disable_subscription,
    discover_targets,
    ensure_subscriptions,
    record_discord_outbound_send,
)


def _response(payload=None, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload or {}
    response.raise_for_status.return_value = None
    return response


def _signature(signing_key: str, body: bytes) -> str:
    timestamp = str(int(time.time()))
    digest = hmac.new(
        signing_key.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


@override_settings(PIPEDREAM_PROJECT_ID="proj_test", PIPEDREAM_ENVIRONMENT="development")
class PipedreamTriggerSubscriptionServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="pd-trigger-owner",
            email="pd-trigger@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="PD Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="PD Receiver",
            charter="Receive connected app events",
            browser_use_agent=cls.browser_agent,
        )

    def _patch_token(self, mock_get_manager):
        manager = MagicMock()
        manager.get_pipedream_access_token.return_value = "pd-token"
        mock_get_manager.return_value = manager

    @tag("batch_agent_webhooks")
    @patch("api.services.pipedream_trigger_subscriptions.requests.post")
    @patch("api.services.pipedream_trigger_subscriptions.requests.get")
    @patch("api.services.pipedream_trigger_subscriptions.get_mcp_manager")
    def test_ensure_creates_and_reuses_subscription(self, mock_get_manager, mock_get, mock_post):
        self._patch_token(mock_get_manager)
        mock_get.return_value = _response(
            {
                "data": [
                    {
                        "id": "apn_123",
                        "healthy": True,
                        "dead": False,
                        "app": {"name_slug": "discord"},
                    }
                ]
            }
        )
        mock_post.return_value = _response(
            {
                "data": {
                    "id": "dc_trigger_123",
                    "webhook_signing_key": "signing-secret",
                }
            }
        )

        results = ensure_subscriptions(
            self.agent,
            app_slug="discord",
            event_type=DISCORD_MESSAGE_EVENT_TYPE,
            channel_ids=["1492138162066034751"],
            channel_names={"1492138162066034751": "general"},
        )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].created)
        subscription = results[0].subscription
        self.assertIsNotNone(subscription)
        self.assertEqual(subscription.platform_channel, "1492138162066034751")
        self.assertEqual(subscription.platform_channel_name, "general")
        self.assertEqual(subscription.deployed_trigger_id, "dc_trigger_123")
        self.assertEqual(subscription.signing_key, "signing-secret")
        mock_post.assert_called_once()
        self.assertEqual(
            mock_post.call_args.kwargs["json"]["configured_props"],
            {
                "discord": {"authProvisionId": "apn_123"},
                "channels": ["1492138162066034751"],
            },
        )

        reused = ensure_subscriptions(
            self.agent,
            app_slug="discord",
            event_type=DISCORD_MESSAGE_EVENT_TYPE,
            channel_ids=["1492138162066034751"],
        )
        self.assertEqual(len(reused), 1)
        self.assertTrue(reused[0].reused)
        self.assertEqual(mock_post.call_count, 1)

    @tag("batch_agent_webhooks")
    @patch("api.services.pipedream_trigger_subscriptions.create_connect_session")
    @patch("api.services.pipedream_trigger_subscriptions.requests.get")
    @patch("api.services.pipedream_trigger_subscriptions.get_mcp_manager")
    def test_ensure_returns_action_required_when_discord_not_connected(
        self,
        mock_get_manager,
        mock_get,
        mock_create_connect_session,
    ):
        self._patch_token(mock_get_manager)
        mock_get.return_value = _response({"data": []})
        mock_create_connect_session.return_value = (MagicMock(), "https://connect.example/discord")

        result = execute_pipedream_trigger_subscriptions(
            self.agent,
            {
                "action": "ensure",
                "app_slug": "discord",
                "event_type": DISCORD_MESSAGE_EVENT_TYPE,
                "channel_ids": ["1492138162066034751"],
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "action_required")
        self.assertEqual(result["connect_url"], "https://connect.example/discord")

    @tag("batch_agent_webhooks")
    @patch("api.services.pipedream_trigger_subscriptions.requests.post")
    @patch("api.services.pipedream_trigger_subscriptions.requests.get")
    @patch("api.services.pipedream_trigger_subscriptions.get_mcp_manager")
    def test_discover_targets_returns_discord_channel_options(self, mock_get_manager, mock_get, mock_post):
        self._patch_token(mock_get_manager)
        mock_get.side_effect = [
            _response(
                {
                    "data": [
                        {
                            "id": "apn_123",
                            "healthy": True,
                            "dead": False,
                            "app": {"name_slug": "discord"},
                        }
                    ]
                }
            ),
            _response(
                {
                    "data": {
                        "key": "discord-new-message",
                        "version": "1.0.3",
                        "configurable_props": [
                            {"name": "discord", "type": "app", "app": "discord"},
                            {"name": "channels", "type": "string[]", "remoteOptions": True},
                        ],
                    }
                }
            ),
        ]
        mock_post.return_value = _response(
            {
                "options": [
                    {"label": "#general", "value": "1492138162066034751"},
                    {"label": "#alerts", "value": "1492138162066034752"},
                ],
                "context": {},
            }
        )

        result = discover_targets(
            self.agent,
            app_slug="discord",
            event_type=DISCORD_MESSAGE_EVENT_TYPE,
        )

        self.assertFalse(result.action_required)
        self.assertEqual(
            [(target.label, target.value) for target in result.targets],
            [
                ("#general", "1492138162066034751"),
                ("#alerts", "1492138162066034752"),
            ],
        )
        self.assertEqual(
            mock_post.call_args.kwargs["json"]["configured_props"],
            {"discord": {"authProvisionId": "apn_123"}},
        )
        self.assertEqual(mock_post.call_args.kwargs["json"]["prop_name"], "channels")

    @tag("batch_agent_webhooks")
    @patch("api.services.pipedream_trigger_subscriptions.requests.post")
    @patch("api.services.pipedream_trigger_subscriptions.requests.get")
    @patch("api.services.pipedream_trigger_subscriptions.get_mcp_manager")
    def test_discover_targets_tool_action_returns_channel_choices(self, mock_get_manager, mock_get, mock_post):
        self._patch_token(mock_get_manager)
        mock_get.side_effect = [
            _response(
                {
                    "data": [
                        {
                            "id": "apn_123",
                            "healthy": True,
                            "dead": False,
                            "app": {"name_slug": "discord"},
                        }
                    ]
                }
            ),
            _response(
                {
                    "data": {
                        "configurable_props": [
                            {"name": "discord", "type": "app", "app": "discord"},
                            {"name": "channels", "type": "string[]", "remoteOptions": True},
                        ],
                    }
                }
            ),
        ]
        mock_post.return_value = _response(
            {"options": [{"label": "#general", "value": "1492138162066034751"}]}
        )

        result = execute_pipedream_trigger_subscriptions(
            self.agent,
            {
                "action": "discover_targets",
                "app_slug": "discord",
                "event_type": DISCORD_MESSAGE_EVENT_TYPE,
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["target_type"], "channel")
        self.assertEqual(
            result["targets"],
            [{"label": "#general", "value": "1492138162066034751"}],
        )

    @tag("batch_agent_webhooks")
    @patch("api.services.pipedream_trigger_subscriptions.create_connect_session")
    @patch("api.services.pipedream_trigger_subscriptions.requests.get")
    @patch("api.services.pipedream_trigger_subscriptions.get_mcp_manager")
    def test_discover_targets_requires_discord_connection(
        self,
        mock_get_manager,
        mock_get,
        mock_create_connect_session,
    ):
        self._patch_token(mock_get_manager)
        mock_get.return_value = _response({"data": []})
        mock_create_connect_session.return_value = (MagicMock(), "https://connect.example/discord")

        result = execute_pipedream_trigger_subscriptions(
            self.agent,
            {
                "action": "discover_targets",
                "app_slug": "discord",
                "event_type": DISCORD_MESSAGE_EVENT_TYPE,
                "will_continue_work": True,
            },
        )

        self.assertEqual(result["status"], "action_required")
        self.assertEqual(result["connect_url"], "https://connect.example/discord")
        self.assertEqual(result["targets"], [])

    @tag("batch_agent_webhooks")
    def test_ensure_rejects_placeholder_channel_ids(self):
        with self.assertRaises(PipedreamTriggerSubscriptionError) as ctx:
            ensure_subscriptions(
                self.agent,
                app_slug="discord",
                event_type=DISCORD_MESSAGE_EVENT_TYPE,
                channel_ids=["<<<DISCORD_CHANNEL_ID>>>"],
            )

        self.assertIn("numeric Discord snowflakes", str(ctx.exception))
        self.assertFalse(
            PersistentAgentPipedreamTriggerSubscription.objects.filter(agent=self.agent).exists()
        )

    @tag("batch_agent_webhooks")
    @patch("api.services.pipedream_trigger_subscriptions.requests.delete")
    @patch("api.services.pipedream_trigger_subscriptions.get_mcp_manager")
    def test_disable_subscription_marks_disabled_and_deletes_deployed_trigger(self, mock_get_manager, mock_delete):
        self._patch_token(mock_get_manager)
        mock_delete.return_value = _response(status_code=204)
        subscription = PersistentAgentPipedreamTriggerSubscription.objects.create(
            agent=self.agent,
            app_slug="discord",
            event_type=DISCORD_MESSAGE_EVENT_TYPE,
            platform_channel="12345",
            platform_channel_name="general",
            trigger_key="discord-new-message",
            trigger_version="1.0.3",
            external_user_id=str(self.agent.id),
            deployed_trigger_id="dc_trigger_123",
            configured_props={"channels": ["12345"]},
        )
        subscription.signing_key = "signing-secret"
        subscription.save()

        payload = disable_subscription(self.agent, str(subscription.id))

        subscription.refresh_from_db()
        self.assertEqual(subscription.status, PersistentAgentPipedreamTriggerSubscription.Status.DISABLED)
        self.assertEqual(payload["status"], "disabled")
        mock_delete.assert_called_once()
        self.assertEqual(mock_delete.call_args.kwargs["params"]["external_user_id"], str(self.agent.id))


@override_settings(PIPEDREAM_PROJECT_ID="proj_test", PIPEDREAM_ENVIRONMENT="development")
class PipedreamTriggerSubscriptionWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="pd-webhook-owner",
            email="pd-webhook@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="PD Webhook Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="PD Webhook Receiver",
            charter="Receive Discord events",
            browser_use_agent=cls.browser_agent,
        )

    def setUp(self):
        self.subscription = PersistentAgentPipedreamTriggerSubscription.objects.create(
            agent=self.agent,
            app_slug="discord",
            event_type=DISCORD_MESSAGE_EVENT_TYPE,
            platform_channel="12345",
            platform_channel_name="general",
            trigger_key="discord-new-message",
            trigger_version="1.0.3",
            external_user_id=str(self.agent.id),
            deployed_trigger_id="dc_trigger_123",
            configured_props={"channels": ["12345"]},
        )
        self.subscription.signing_key = "signing-secret"
        self.subscription.save()

    @tag("batch_agent_webhooks")
    def test_webhook_rejects_missing_or_invalid_secret(self):
        url = reverse("api:pipedream_trigger_subscription_webhook", args=[self.subscription.id])
        response = self.client.post(url, data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 400)

        response = self.client.post(f"{url}?t=wrong", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)

    @tag("batch_agent_webhooks")
    def test_webhook_rejects_invalid_signature(self):
        url = f"{reverse('api:pipedream_trigger_subscription_webhook', args=[self.subscription.id])}?t={self.subscription.webhook_secret}"
        response = self.client.post(
            url,
            data='{"event":{"id":"m1","channelID":"12345","content":"hi"}}',
            content_type="application/json",
            HTTP_X_PD_SIGNATURE="t=1,v1=bad",
        )
        self.assertEqual(response.status_code, 403)

    @tag("batch_agent_webhooks")
    def test_webhook_rejects_non_matching_discord_channel_without_disabling_subscription(self):
        body = json.dumps(
            {
                "event": {
                    "id": "m1",
                    "channelID": "other-channel",
                    "content": "wrong channel",
                }
            }
        ).encode("utf-8")
        url = f"{reverse('api:pipedream_trigger_subscription_webhook', args=[self.subscription.id])}?t={self.subscription.webhook_secret}"

        response = self.client.post(
            url,
            data=body,
            content_type="application/json",
            HTTP_X_PD_SIGNATURE=_signature("signing-secret", body),
        )

        self.assertEqual(response.status_code, 400)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, PersistentAgentPipedreamTriggerSubscription.Status.ACTIVE)
        self.assertIn("does not match", self.subscription.last_error)

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_webhook_accepts_discord_message_event(self, mock_delay):
        body = json.dumps(
            {
                "event": {
                    "id": "m1",
                    "guildID": "g1",
                    "guildName": "Gobii",
                    "channelID": "12345",
                    "channelName": "general",
                    "content": "hello from discord",
                    "author": {"id": "u1", "username": "matt"},
                    "attachments": [{"url": "https://cdn.example/file.png"}],
                }
            }
        ).encode("utf-8")
        url = f"{reverse('api:pipedream_trigger_subscription_webhook', args=[self.subscription.id])}?t={self.subscription.webhook_secret}"

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                data=body,
                content_type="application/json",
                HTTP_X_PD_SIGNATURE=_signature("signing-secret", body),
            )

        self.assertEqual(response.status_code, 202, response.content)
        payload = response.json()
        message = PersistentAgentMessage.objects.get(id=payload["messageId"])
        self.assertEqual(message.owner_agent_id, self.agent.id)
        self.assertEqual(message.conversation.channel, CommsChannel.DISCORD)
        self.assertEqual(
            message.conversation.address,
            f"discord://agent/{self.agent.id}/guild/g1/channel/12345",
        )
        self.assertEqual(message.from_endpoint.address, "discord://guild/g1/channel/12345")
        self.assertEqual(message.conversation.display_name, "#general")
        self.assertEqual(message.body, "hello from discord")
        self.assertEqual(message.raw_payload["source_kind"], "discord")
        self.assertEqual(message.raw_payload["source_label"], "matt in #general")
        self.assertEqual(message.raw_payload["discord_author_id"], "u1")
        self.assertEqual(message.raw_payload["discord_attachments"], [{"url": "https://cdn.example/file.png"}])
        mock_delay.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_webhook_accepts_flat_pipedream_discord_payload_author(self, mock_delay):
        body = json.dumps(
            {
                "id": "1502283018652451047",
                "guildID": "1492138161625759834",
                "channelID": "12345",
                "channel": "general",
                "content": "looking good!",
                "author": "_the_juicer_",
                "authorID": "177593384389705729",
                "author_metadata": {"bot": False, "avatar": "avatar-hash"},
            }
        ).encode("utf-8")
        url = f"{reverse('api:pipedream_trigger_subscription_webhook', args=[self.subscription.id])}?t={self.subscription.webhook_secret}"

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                data=body,
                content_type="application/json",
                HTTP_X_PD_SIGNATURE=_signature("signing-secret", body),
            )

        self.assertEqual(response.status_code, 202, response.content)
        payload = response.json()
        message = PersistentAgentMessage.objects.get(id=payload["messageId"])
        self.assertEqual(message.body, "looking good!")
        self.assertEqual(message.raw_payload["source_label"], "_the_juicer_ in #general")
        self.assertEqual(message.raw_payload["discord_author_id"], "177593384389705729")
        self.assertEqual(message.raw_payload["discord_author_name"], "_the_juicer_")
        mock_delay.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_discord_conversations_are_scoped_per_agent_channel_subscription(self, mock_delay):
        second_browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Second PD Browser")
        second_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Second PD Receiver",
            charter="Receive the same Discord channel",
            browser_use_agent=second_browser_agent,
        )
        second_subscription = PersistentAgentPipedreamTriggerSubscription.objects.create(
            agent=second_agent,
            app_slug="discord",
            event_type=DISCORD_MESSAGE_EVENT_TYPE,
            platform_channel="12345",
            platform_channel_name="general",
            trigger_key="discord-new-message",
            trigger_version="1.0.3",
            external_user_id=str(second_agent.id),
            deployed_trigger_id="dc_trigger_456",
            configured_props={"channels": ["12345"]},
        )
        second_subscription.signing_key = "second-signing-secret"
        second_subscription.save()
        body = json.dumps(
            {
                "event": {
                    "id": "shared-channel-message",
                    "guildID": "g1",
                    "channelID": "12345",
                    "channelName": "general",
                    "content": "hello both agents",
                    "author": {"id": "u1", "username": "matt"},
                }
            }
        ).encode("utf-8")

        first_url = f"{reverse('api:pipedream_trigger_subscription_webhook', args=[self.subscription.id])}?t={self.subscription.webhook_secret}"
        second_url = f"{reverse('api:pipedream_trigger_subscription_webhook', args=[second_subscription.id])}?t={second_subscription.webhook_secret}"
        with self.captureOnCommitCallbacks(execute=True):
            first_response = self.client.post(
                first_url,
                data=body,
                content_type="application/json",
                HTTP_X_PD_SIGNATURE=_signature("signing-secret", body),
            )
            second_response = self.client.post(
                second_url,
                data=body,
                content_type="application/json",
                HTTP_X_PD_SIGNATURE=_signature("second-signing-secret", body),
            )

        self.assertEqual(first_response.status_code, 202, first_response.content)
        self.assertEqual(second_response.status_code, 202, second_response.content)
        first_message = PersistentAgentMessage.objects.get(id=first_response.json()["messageId"])
        second_message = PersistentAgentMessage.objects.get(id=second_response.json()["messageId"])
        self.assertNotEqual(first_message.conversation_id, second_message.conversation_id)
        self.assertEqual(
            first_message.conversation.address,
            f"discord://agent/{self.agent.id}/guild/g1/channel/12345",
        )
        self.assertEqual(
            second_message.conversation.address,
            f"discord://agent/{second_agent.id}/guild/g1/channel/12345",
        )
        self.assertEqual(first_message.from_endpoint_id, second_message.from_endpoint_id)
        self.assertEqual(mock_delay.call_count, 2)

    @tag("batch_agent_webhooks")
    def test_record_discord_outbound_send_creates_visible_outbound_message(self):
        message = record_discord_outbound_send(
            self.agent,
            tool_name="discord-send-message",
            params={
                "channel": "1492138162066034751",
                "message": "hello from the agent",
                "username": self.agent.name,
            },
            result={"status": "success"},
        )

        self.assertIsNotNone(message)
        self.assertTrue(message.is_outbound)
        self.assertEqual(message.body, "hello from the agent")
        self.assertEqual(message.conversation.channel, CommsChannel.DISCORD)
        self.assertEqual(message.raw_payload["source"], "pipedream_tool")
        self.assertEqual(message.raw_payload["discord_channel_id"], "1492138162066034751")

    @tag("batch_agent_webhooks")
    def test_pipedream_mcp_success_hook_records_discord_outbound_message(self):
        entry = ToolCatalogEntry(
            provider="mcp",
            full_name="discord-send-message",
            description="Send Discord message",
            parameters={},
            tool_server="pipedream",
            tool_name="discord-send-message",
        )

        _record_pipedream_tool_side_effects(
            self.agent,
            entry,
            {
                "channel": "1492138162066034751",
                "message": "hello from the provider hook",
                "username": self.agent.name,
            },
            {"status": "success"},
        )

        message = PersistentAgentMessage.objects.get(
            owner_agent=self.agent,
            is_outbound=True,
            conversation__channel=CommsChannel.DISCORD,
        )
        self.assertEqual(message.body, "hello from the provider hook")
        self.assertEqual(message.raw_payload["source"], "pipedream_tool")
        self.assertEqual(message.raw_payload["pipedream_tool_name"], "discord-send-message")

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_webhook_self_echo_updates_outbound_message_without_reprocessing(self, mock_delay):
        from_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.DISCORD,
            address=f"discord://agent/{self.agent.id}",
            is_primary=True,
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.DISCORD,
            address=f"discord://agent/{self.agent.id}/guild/unknown/channel/12345",
            display_name="#general",
        )
        outbound = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=from_endpoint,
            conversation=conversation,
            is_outbound=True,
            body="hello from the agent",
            raw_payload={
                "source": "pipedream_tool",
                "source_kind": "discord",
                "discord_channel_id": "12345",
            },
        )
        body = json.dumps(
            {
                "id": "m-self",
                "guildID": "g1",
                "channelID": "12345",
                "channel": "general",
                "content": "hello from the agent",
                "author": self.agent.name,
                "authorID": "bot-user",
                "webhookId": "bot-user",
                "author_metadata": {"bot": True},
            }
        ).encode("utf-8")
        url = f"{reverse('api:pipedream_trigger_subscription_webhook', args=[self.subscription.id])}?t={self.subscription.webhook_secret}"

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                data=body,
                content_type="application/json",
                HTTP_X_PD_SIGNATURE=_signature("signing-secret", body),
            )

        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(PersistentAgentMessage.objects.filter(owner_agent=self.agent).count(), 1)
        outbound.refresh_from_db()
        self.assertTrue(outbound.is_outbound)
        self.assertEqual(
            outbound.conversation.address,
            f"discord://agent/{self.agent.id}/guild/g1/channel/12345",
        )
        self.assertEqual(outbound.raw_payload["discord_message_id"], "m-self")
        self.assertEqual(outbound.raw_payload["discord_author_name"], self.agent.name)
        self.assertTrue(
            outbound.conversation.participants.filter(
                endpoint__address="discord://guild/g1/channel/12345",
                role="external",
            ).exists()
        )
        mock_delay.assert_not_called()


class ConnectedAppChannelsSystemSkillTests(TestCase):
    @tag("batch_agent_tools")
    @override_settings(PIPEDREAM_PREFETCH_APPS="")
    @patch("api.agent.tools.mcp_manager.get_mcp_manager")
    def test_system_skill_search_and_enablement(self, mock_get_mcp_manager):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="connected-app-skill",
            email="connected-app-skill@example.com",
            password="password123",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Skill Browser")
        agent = PersistentAgent.objects.create(
            user=user,
            name="Skill Receiver",
            charter="Receive Discord events",
            browser_use_agent=browser_agent,
        )

        matches = shortlist_system_skills(
            "listen to discord channel messages",
            available_tool_names={"pipedream_trigger_subscriptions"},
        )
        self.assertEqual([match.skill_key for match in matches], ["connected_app_channels"])
        integration_matches = shortlist_system_skills(
            "discord integration, discord bot, discord webhook, pipedream discord",
            available_tool_names={"pipedream_trigger_subscriptions"},
        )
        self.assertEqual([match.skill_key for match in integration_matches], ["connected_app_channels"])

        result = enable_system_skills(agent, ["connected_app_channels"], available_skills=matches)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["pipedream_apps"]["enabled"], ["discord"])
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name="pipedream_trigger_subscriptions",
            ).exists()
        )
        selection = PipedreamAppSelection.objects.get(user=user)
        self.assertEqual(selection.selected_app_slugs, ["discord"])
        manager = mock_get_mcp_manager.return_value
        manager.invalidate_pipedream_owner_cache.assert_called_once_with("user", str(user.id))
        manager.prewarm_pipedream_owner_cache.assert_called_once_with(
            "user",
            str(user.id),
            app_slugs=["discord"],
        )

    @tag("batch_agent_tools")
    def test_connected_app_channels_skill_does_not_request_discord_ids_as_secrets(self):
        definition = get_system_skill_definition("connected_app_channels")
        self.assertIsNotNone(definition)
        instructions = definition.prompt_instructions
        self.assertNotIn("secure_credentials_request", instructions)
        self.assertNotIn("DISCORD_SERVER_ID", instructions)
        self.assertNotIn("DISCORD_CHANNEL_ID", instructions)
        self.assertIn("action=\"discover_targets\"", instructions)
        self.assertIn("ask the user to choose by channel name", instructions)
        self.assertIn("Do not request Discord server IDs or channel IDs as secrets.", instructions)
        self.assertIn("Server ID is not required for v1 setup.", instructions)
        self.assertIn("When calling Discord send-message tools, use this parameter pattern", instructions)
        self.assertIn("`channel` = the selected Discord channel ID", instructions)
        self.assertIn("`message` = the text to send", instructions)
        self.assertIn("avatarURL=\"https://gobii.ai/static/images/gobii_fish.png\"", instructions)
        self.assertIn("`username` = this agent's name", instructions)
        self.assertIn("includeSentViaPipedream=false", instructions)
        self.assertIn("will_continue_work", instructions)
