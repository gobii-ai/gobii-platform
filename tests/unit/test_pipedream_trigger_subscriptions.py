import hashlib
import hmac
import json
import time
from unittest.mock import ANY, MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from api.agent.system_skills.registry import get_system_skill_definition, shortlist_system_skills
from api.agent.system_skills.service import enable_system_skills
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentEnabledTool,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    PersistentAgentPipedreamTriggerSubscription,
)
from api.services.pipedream_trigger_subscriptions import (
    DISCORD_MESSAGE_EVENT_TYPE,
    PipedreamTriggerSubscriptionError,
    disable_subscription,
    discover_targets,
    ensure_subscriptions,
    schedule_discord_inbound_processing,
)
from api.services.discord_messages import (
    DISCORD_TYPING_INDICATOR_TIMEOUT_SECONDS,
    process_discord_inbound_debounce,
)


def _response(payload=None, status_code=200, content=b"", headers=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload or {}
    response.raise_for_status.return_value = None
    response.content = content
    response.headers = headers or {}
    return response


def _signature(signing_key: str, body: bytes) -> str:
    timestamp = str(int(time.time()))
    digest = hmac.new(
        signing_key.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


class FakeRedis:
    def __init__(self):
        self.values = {}

    class Pipeline:
        def __init__(self, redis_client):
            self.redis_client = redis_client
            self.commands = []

        def set(self, *args, **kwargs):
            self.commands.append(("set", args, kwargs))
            return self

        def execute(self):
            results = []
            for command, args, kwargs in self.commands:
                if command == "set":
                    results.append(self.redis_client.set(*args, **kwargs))
            return results

    def pipeline(self, transaction=True):
        return self.Pipeline(self)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def expire(self, key, ttl):
        return key in self.values

    def delete(self, *keys):
        deleted = 0
        for key in keys:
            if key in self.values:
                deleted += 1
                del self.values[key]
        return deleted


@override_settings(
    PIPEDREAM_PROJECT_ID="proj_test",
    PIPEDREAM_ENVIRONMENT="development",
    PUBLIC_SITE_URL="https://app.example.test",
)
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
        self.assertEqual(
            mock_post.call_args.kwargs["json"]["webhook_url"],
            (
                f"https://app.example.test"
                f"{reverse('api:pipedream_trigger_subscription_webhook', args=[subscription.id])}"
                f"?t={subscription.webhook_secret}"
            ),
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

        results = ensure_subscriptions(
            self.agent,
            app_slug="discord",
            event_type=DISCORD_MESSAGE_EVENT_TYPE,
            channel_ids=["1492138162066034751"],
        )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].action_required)
        self.assertEqual(results[0].connect_url, "https://connect.example/discord")

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

        result = discover_targets(
            self.agent,
            app_slug="discord",
            event_type=DISCORD_MESSAGE_EVENT_TYPE,
        )

        self.assertTrue(result.action_required)
        self.assertEqual(result.connect_url, "https://connect.example/discord")
        self.assertEqual(result.targets, [])

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
            configured_props={"discord": {"authProvisionId": "apn_discord"}, "channels": ["12345"]},
        )
        subscription.signing_key = "signing-secret"
        subscription.save()

        payload = disable_subscription(self.agent, str(subscription.id))

        subscription.refresh_from_db()
        self.assertEqual(subscription.status, PersistentAgentPipedreamTriggerSubscription.Status.DISABLED)
        self.assertEqual(payload["status"], "disabled")
        mock_delete.assert_called_once()
        self.assertEqual(mock_delete.call_args.kwargs["params"]["external_user_id"], str(self.agent.id))


@override_settings(
    PIPEDREAM_PROJECT_ID="proj_test",
    PIPEDREAM_ENVIRONMENT="development",
    DISCORD_INBOUND_DEBOUNCE_SECONDS=0,
)
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
            configured_props={"discord": {"authProvisionId": "apn_discord"}, "channels": ["12345"]},
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
    @patch("api.webhooks.ingest_trigger_delivery")
    def test_webhook_rejects_agent_from_other_environment(self, mock_ingest):
        body = b'{"event":{"id":"m1","channelID":"12345","content":"hi"}}'
        url = f"{reverse('api:pipedream_trigger_subscription_webhook', args=[self.subscription.id])}?t={self.subscription.webhook_secret}"

        with patch("api.webhooks.settings.GOBII_RELEASE_ENV", "other-env"):
            response = self.client.post(
                url,
                data=body,
                content_type="application/json",
                HTTP_X_PD_SIGNATURE=_signature("signing-secret", body),
            )

        self.assertEqual(response.status_code, 404)
        mock_ingest.assert_not_called()

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
    @patch("api.agent.comms.message_service.get_max_file_size", return_value=None)
    @patch("api.agent.comms.message_service.requests.get")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_webhook_accepts_discord_message_event(
        self,
        mock_delay,
        mock_get,
        mock_max_file_size,
    ):
        mock_get.return_value = _response(content=b"file-bytes", headers={"Content-Type": "image/png"})
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
        mock_max_file_size.assert_called()
        mock_delay.assert_called_once_with(str(self.agent.id), inbound_generation=ANY)

    @tag("batch_agent_webhooks")
    @patch("api.agent.comms.message_service.get_max_file_size", return_value=None)
    @patch("api.agent.comms.message_service.requests.get")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_webhook_accepts_attachment_only_discord_message_event(
        self,
        mock_delay,
        mock_get,
        mock_max_file_size,
    ):
        mock_get.return_value = _response(content=b"image-bytes", headers={"Content-Type": "image/png"})
        body = json.dumps(
            {
                "event": {
                    "id": "m-attachment",
                    "guildID": "g1",
                    "channelID": "12345",
                    "channelName": "general",
                    "content": "",
                    "author": {"id": "u1", "username": "matt"},
                    "attachments": [
                        {
                            "id": "a1",
                            "url": "https://cdn.example/photo.png",
                            "filename": "photo.png",
                            "contentType": "image/png",
                        }
                    ],
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
        self.assertEqual(message.body, "")
        self.assertEqual(
            message.raw_payload["discord_attachments"],
            [
                {
                    "id": "a1",
                    "url": "https://cdn.example/photo.png",
                    "filename": "photo.png",
                    "contentType": "image/png",
                }
            ],
        )
        attachment = PersistentAgentMessageAttachment.objects.get(message=message)
        self.assertEqual(attachment.filename, "photo.png")
        self.assertEqual(attachment.content_type, "image/png")
        self.assertEqual(attachment.file_size, len(b"image-bytes"))
        mock_get.assert_called_once_with(
            "https://cdn.example/photo.png",
            timeout=30,
            allow_redirects=True,
            auth=None,
        )
        mock_max_file_size.assert_called()
        mock_delay.assert_called_once_with(str(self.agent.id), inbound_generation=ANY)

    @tag("batch_agent_webhooks")
    @patch("api.agent.comms.message_service.requests.get")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_webhook_records_id_only_discord_attachments_without_download(self, mock_delay, mock_get):
        body = json.dumps(
            {
                "id": "1504906483899568298",
                "guildID": "g1",
                "channelID": "12345",
                "channel": "general",
                "content": "can you see this file",
                "author": "_the_juicer_",
                "authorID": "177593384389705729",
                "attachments": ["1504906484096696551"],
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
        self.assertEqual(message.body, "can you see this file")
        self.assertEqual(message.raw_payload["discord_attachments"], ["1504906484096696551"])
        self.assertFalse(PersistentAgentMessageAttachment.objects.filter(message=message).exists())
        mock_get.assert_not_called()
        mock_delay.assert_called_once_with(str(self.agent.id), inbound_generation=ANY)

    @tag("batch_agent_webhooks")
    @override_settings(DISCORD_INBOUND_DEBOUNCE_SECONDS=15, CELERY_TASK_ALWAYS_EAGER=False)
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.tasks.process_events.process_discord_inbound_debounce_task.apply_async")
    @patch("api.services.discord_messages.get_redis_client")
    def test_webhook_debounces_discord_message_processing_when_enabled(
        self,
        mock_get_redis_client,
        mock_debounce_apply_async,
        mock_process_delay,
    ):
        fake_redis = FakeRedis()
        mock_get_redis_client.return_value = fake_redis
        body = json.dumps(
            {
                "event": {
                    "id": "m-debounce",
                    "guildID": "g1",
                    "channelID": "12345",
                    "channelName": "general",
                    "content": "first thought",
                    "author": {"id": "u1", "username": "matt"},
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
        self.assertTrue(payload["debounced"])
        self.assertEqual(payload["debounceSeconds"], 15)
        self.assertTrue(PersistentAgentMessage.objects.filter(id=payload["messageId"]).exists())
        mock_process_delay.assert_not_called()
        mock_debounce_apply_async.assert_called_once_with(args=[str(self.agent.id)], countdown=15)

    @tag("batch_agent_webhooks")
    @override_settings(DISCORD_INBOUND_DEBOUNCE_SECONDS=15, CELERY_TASK_ALWAYS_EAGER=False)
    @patch("api.agent.tasks.process_events.process_discord_inbound_debounce_task.apply_async")
    @patch("api.services.discord_messages.get_redis_client")
    def test_discord_inbound_debounce_scheduler_coalesces_burst(
        self,
        mock_get_redis_client,
        mock_debounce_apply_async,
    ):
        fake_redis = FakeRedis()
        mock_get_redis_client.return_value = fake_redis

        with patch("api.services.discord_messages.time.time", side_effect=[100.0, 105.0]):
            first = schedule_discord_inbound_processing(str(self.agent.id))
            second = schedule_discord_inbound_processing(str(self.agent.id))

        self.assertTrue(first["debounced"])
        self.assertTrue(first["scheduled"])
        self.assertTrue(second["debounced"])
        self.assertFalse(second["scheduled"])
        mock_debounce_apply_async.assert_called_once_with(args=[str(self.agent.id)], countdown=15)
        deadline_values = [
            value
            for key, value in fake_redis.values.items()
            if key.endswith(":deadline")
        ]
        self.assertEqual(deadline_values, ["120.000000"])

    @tag("batch_agent_webhooks")
    @override_settings(DISCORD_INBOUND_DEBOUNCE_SECONDS=15, CELERY_TASK_ALWAYS_EAGER=False)
    @patch("api.services.discord_messages.send_discord_typing_indicator")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.tasks.process_events.process_discord_inbound_debounce_task.apply_async")
    @patch("api.services.discord_messages.get_redis_client")
    def test_discord_inbound_debounce_sends_and_refreshes_typing_indicator(
        self,
        mock_get_redis_client,
        mock_debounce_apply_async,
        mock_process_delay,
        mock_typing,
    ):
        fake_redis = FakeRedis()
        mock_get_redis_client.return_value = fake_redis

        with patch("api.services.discord_messages.time.time", return_value=100.0):
            schedule_discord_inbound_processing(str(self.agent.id), typing_channel_id="12345")

        mock_typing.assert_called_once_with("12345")
        self.assertEqual(
            fake_redis.values[f"agent:discord-inbound-debounce:{self.agent.id}:typing-channel"],
            "12345",
        )
        mock_typing.reset_mock()
        mock_debounce_apply_async.reset_mock()

        with patch("api.services.discord_messages.time.time", return_value=116.0):
            process_discord_inbound_debounce(str(self.agent.id))

        mock_typing.assert_called_once_with("12345")
        mock_debounce_apply_async.assert_not_called()
        mock_process_delay.assert_called_once()
        queued_args, queued_kwargs = mock_process_delay.call_args
        self.assertEqual(queued_args, (str(self.agent.id),))
        self.assertGreater(queued_kwargs["inbound_generation"], 0)
        self.assertEqual(fake_redis.values, {})

    @tag("batch_agent_webhooks")
    @override_settings(DISCORD_BOT_TOKEN="bot-token")
    @patch("api.services.discord_messages.requests.post")
    def test_discord_typing_indicator_uses_short_timeout(self, mock_post):
        from api.services.discord_messages import send_discord_typing_indicator

        mock_post.return_value = _response()

        self.assertTrue(send_discord_typing_indicator("12345"))
        self.assertEqual(mock_post.call_args.kwargs["timeout"], DISCORD_TYPING_INDICATOR_TIMEOUT_SECONDS)

    @tag("batch_agent_webhooks")
    @override_settings(DISCORD_INBOUND_DEBOUNCE_SECONDS=15, CELERY_TASK_ALWAYS_EAGER=False)
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.tasks.process_events.process_discord_inbound_debounce_task.apply_async")
    @patch("api.services.discord_messages.get_redis_client")
    def test_discord_inbound_debounce_task_requeues_until_quiet(
        self,
        mock_get_redis_client,
        mock_debounce_apply_async,
        mock_process_delay,
    ):
        fake_redis = FakeRedis()
        mock_get_redis_client.return_value = fake_redis

        with patch("api.services.discord_messages.time.time", return_value=100.0):
            schedule_discord_inbound_processing(str(self.agent.id))
        mock_debounce_apply_async.reset_mock()

        with patch("api.services.discord_messages.time.time", return_value=110.0):
            process_discord_inbound_debounce(str(self.agent.id))

        mock_debounce_apply_async.assert_called_once_with(args=[str(self.agent.id)], countdown=5)
        mock_process_delay.assert_not_called()

    @tag("batch_agent_webhooks")
    @override_settings(DISCORD_INBOUND_DEBOUNCE_SECONDS=15, CELERY_TASK_ALWAYS_EAGER=False)
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.agent.tasks.process_events.process_discord_inbound_debounce_task.apply_async")
    @patch("api.services.discord_messages.get_redis_client")
    def test_discord_inbound_debounce_task_wakes_agent_after_quiet_period(
        self,
        mock_get_redis_client,
        mock_debounce_apply_async,
        mock_process_delay,
    ):
        fake_redis = FakeRedis()
        mock_get_redis_client.return_value = fake_redis

        with patch("api.services.discord_messages.time.time", return_value=100.0):
            schedule_discord_inbound_processing(str(self.agent.id))
        mock_debounce_apply_async.reset_mock()

        with patch("api.services.discord_messages.time.time", return_value=116.0):
            process_discord_inbound_debounce(str(self.agent.id))

        mock_debounce_apply_async.assert_not_called()
        mock_process_delay.assert_called_once()
        queued_args, queued_kwargs = mock_process_delay.call_args
        self.assertEqual(queued_args, (str(self.agent.id),))
        self.assertGreater(queued_kwargs["inbound_generation"], 0)
        self.assertEqual(fake_redis.values, {})

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
        mock_delay.assert_called_once_with(str(self.agent.id), inbound_generation=ANY)

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
    @override_settings(DISCORD_INBOUND_DEBOUNCE_SECONDS=15)
    @patch("api.services.pipedream_trigger_subscriptions.schedule_discord_inbound_processing")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_webhook_self_echo_updates_outbound_message_without_reprocessing(self, mock_delay, mock_schedule_debounce):
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
        mock_schedule_debounce.assert_not_called()


class ConnectedAppChannelsSystemSkillTests(TestCase):
    @tag("batch_agent_tools")
    def test_system_skill_search_and_enablement(self):
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
            available_tool_names={
                "discord_channel_subscriptions",
                "send_discord_message",
                "add_discord_reaction",
            },
        )
        self.assertEqual([match.skill_key for match in matches], ["discord_native"])
        integration_matches = shortlist_system_skills(
            "discord integration, discord bot, discord webhook, pipedream discord",
            available_tool_names={
                "discord_channel_subscriptions",
                "send_discord_message",
                "add_discord_reaction",
            },
        )
        self.assertEqual([match.skill_key for match in integration_matches], ["discord_native"])

        result = enable_system_skills(agent, ["discord_native"], available_skills=matches)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["pipedream_apps"]["enabled"], [])
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name="discord_channel_subscriptions",
            ).exists()
        )
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name="send_discord_message",
            ).exists()
        )
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name="add_discord_reaction",
            ).exists()
        )
        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name="pipedream_trigger_subscriptions",
            ).exists()
        )

    @tag("batch_agent_tools")
    def test_discord_native_skill_does_not_request_discord_ids_as_secrets(self):
        definition = get_system_skill_definition("discord_native")
        self.assertIsNotNone(definition)
        instructions = definition.prompt_instructions
        self.assertNotIn("secure_credentials_request", instructions)
        self.assertNotIn("DISCORD_SERVER_ID", instructions)
        self.assertNotIn("DISCORD_CHANNEL_ID", instructions)
        self.assertIn("action=\"discover_channels\"", instructions)
        self.assertIn("call `ensure` with the selected `guild_id`, `channel_id`, and `channel_name`", instructions)
        self.assertIn("Use `send_discord_message` for outbound Discord replies", instructions)
        self.assertIn("Use `add_discord_reaction`", instructions)
        self.assertNotIn("legacy fallback", instructions)
        self.assertNotIn("pipedream_trigger_subscriptions", instructions)
        self.assertNotIn("avatarURL=\"https://gobii.ai/static/images/gobii_fish.png\"", instructions)
        self.assertNotIn("`username` = this agent's name", instructions)
        self.assertNotIn("includeSentViaPipedream=false", instructions)
        self.assertIn("will_continue_work", instructions)
