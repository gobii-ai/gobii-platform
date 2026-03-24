from unittest.mock import MagicMock, patch
import json

from allauth.account.models import EmailAddress
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import RequestFactory
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone
from django.utils.datastructures import MultiValueDict
from requests import RequestException

from api.agent.tools.webhook_sender import execute_send_webhook_event
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentInboundWebhook,
    PersistentAgentMessage,
    PersistentAgentWebhook,
    ProxyServer,
)
from api.webhooks import _parse_inbound_agent_webhook_request
from console.views import AgentDetailView
from util.analytics import AnalyticsEvent


class AgentWebhookToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="webhook-owner",
            email="owner@example.com",
            password="password123",
        )
        # Email verification is required for webhook sending
        EmailAddress.objects.create(
            user=cls.user,
            email=cls.user.email,
            verified=True,
            primary=True,
        )
        cls.proxy = ProxyServer.objects.create(
            name="Webhook Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="proxy.example.com",
            port=8080,
        )
        cls.browser_agent = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Browser Agent",
            preferred_proxy=cls.proxy,
        )
        agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Webhook Tester",
            charter="Test webhook delivery",
            browser_use_agent=cls.browser_agent,
        )
        webhook = PersistentAgentWebhook.objects.create(
            agent=agent,
            name="Status Hook",
            url="https://example.com/hook",
        )
        cls.agent_id = agent.id
        cls.webhook_id = webhook.id

    def setUp(self):
        self.agent = PersistentAgent.objects.get(pk=self.agent_id)
        self.webhook = PersistentAgentWebhook.objects.get(pk=self.webhook_id)
        self.proxy = type(self).proxy

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_success(self):
        with patch("api.agent.tools.webhook_sender.requests.post") as mock_post:
            mock_response = MagicMock(status_code=204, text="")
            mock_post.return_value = mock_response

            payload = {"status": "ok"}
            result = execute_send_webhook_event(
                self.agent,
                {"webhook_id": str(self.webhook.id), "payload": payload},
            )

            self.assertEqual(result.get("status"), "success")
            self.assertEqual(result.get("webhook_id"), str(self.webhook.id))
            self.assertEqual(result.get("response_status"), 204)

            self.webhook.refresh_from_db()
            self.assertIsNotNone(self.webhook.last_triggered_at)
            self.assertEqual(self.webhook.last_response_status, 204)
            self.assertEqual(self.webhook.last_error_message, "")

            called_kwargs = mock_post.call_args.kwargs
            self.assertEqual(called_kwargs["json"], payload)
            self.assertEqual(called_kwargs["headers"]["User-Agent"], "Gobii-AgentWebhook/1.0")
            self.assertEqual(
                called_kwargs["proxies"],
                {"http": self.proxy.proxy_url, "https": self.proxy.proxy_url},
            )

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_http_error(self):
        with patch("api.agent.tools.webhook_sender.requests.post") as mock_post:
            mock_response = MagicMock(status_code=500, text="boom")
            mock_post.return_value = mock_response

            result = execute_send_webhook_event(
                self.agent,
                {"webhook_id": str(self.webhook.id), "payload": {"value": 1}},
            )

            self.assertEqual(result.get("status"), "error")
            self.assertEqual(result.get("response_status"), 500)

            self.webhook.refresh_from_db()
            self.assertEqual(self.webhook.last_response_status, 500)
            self.assertIn("boom", self.webhook.last_error_message)

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_request_exception(self):
        with patch("api.agent.tools.webhook_sender.requests.post") as mock_post:
            mock_post.side_effect = RequestException("timeout")

            result = execute_send_webhook_event(
                self.agent,
                {"webhook_id": str(self.webhook.id), "payload": {"value": 1}},
            )

            self.assertEqual(result.get("status"), "error")
            self.assertIn("timeout", result.get("message", ""))

            self.webhook.refresh_from_db()
            self.assertIsNone(self.webhook.last_response_status)
            self.assertIn("timeout", self.webhook.last_error_message)

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_requires_proxy(self):
        with patch(
            "api.agent.tools.webhook_sender.select_proxy_for_persistent_agent",
            return_value=None,
        ) as mock_select, patch("api.agent.tools.webhook_sender.requests.post") as mock_post:
            result = execute_send_webhook_event(
                self.agent,
                {"webhook_id": str(self.webhook.id), "payload": {"value": 1}},
            )

        mock_select.assert_called_once_with(self.agent, allow_no_proxy_in_debug=False)
        mock_post.assert_not_called()
        self.assertEqual(result.get("status"), "error")
        self.assertIn("proxy", result.get("message", ""))

        self.webhook.refresh_from_db()
        self.assertIsNone(self.webhook.last_response_status)
        self.assertIn("proxy", self.webhook.last_error_message)

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_requires_json_object(self):
        result = execute_send_webhook_event(
            self.agent,
            {"webhook_id": str(self.webhook.id), "payload": "not-a-dict"},
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("Payload must be a JSON object", result.get("message", ""))

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_supports_socks5_proxy(self):
        socks_proxy = ProxyServer.objects.create(
            name="Webhook SOCKS Proxy",
            proxy_type=ProxyServer.ProxyType.SOCKS5,
            host="proxy.example.com",
            port=1080,
        )
        with patch(
            "api.agent.tools.webhook_sender.select_proxy_for_persistent_agent",
            return_value=socks_proxy,
        ), patch("api.agent.tools.webhook_sender.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=204, text="")

            result = execute_send_webhook_event(
                self.agent,
                {"webhook_id": str(self.webhook.id), "payload": {"status": "ok"}},
            )

        self.assertEqual(result.get("status"), "success")
        self.assertEqual(
            mock_post.call_args.kwargs["proxies"],
            {"http": socks_proxy.proxy_url, "https": socks_proxy.proxy_url},
        )

    @tag("batch_agent_webhooks")
    def test_execute_send_webhook_event_unknown_webhook(self):
        result = execute_send_webhook_event(
            self.agent,
            {"webhook_id": "00000000-0000-0000-0000-000000000000", "payload": {}},
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("Webhook not found", result.get("message", ""))


class AgentWebhookConsoleViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="console-owner",
            email="console@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Browser Agent")
        agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Console Tester",
            charter="Manage webhooks",
            browser_use_agent=cls.browser_agent,
        )
        cls.agent_id = agent.id

    def setUp(self):
        self.user = type(self).user
        self.client.force_login(self.user)
        self.agent = PersistentAgent.objects.get(pk=self.agent_id)
        self.factory = RequestFactory()

    @tag("batch_agent_webhooks")
    def test_console_creates_webhook(self):
        response = self.client.post(
            reverse("agent_detail", args=[self.agent_id]),
            {
                "webhook_action": "create",
                "webhook_name": "CI Hook",
                "webhook_url": "https://example.com/ci",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            PersistentAgentWebhook.objects.filter(agent=self.agent, name="CI Hook").exists()
        )

    @tag("batch_agent_webhooks")
    def test_console_updates_webhook(self):
        webhook = PersistentAgentWebhook.objects.create(
            agent=self.agent,
            name="Original",
            url="https://example.com/old",
        )
        response = self.client.post(
            reverse("agent_detail", args=[self.agent_id]),
            {
                "webhook_action": "update",
                "webhook_id": str(webhook.id),
                "webhook_name": "Updated",
                "webhook_url": "https://example.com/new",
            },
        )
        self.assertEqual(response.status_code, 302)
        webhook.refresh_from_db()
        self.assertEqual(webhook.name, "Updated")
        self.assertEqual(webhook.url, "https://example.com/new")

    @tag("batch_agent_webhooks")
    def test_console_deletes_webhook(self):
        webhook = PersistentAgentWebhook.objects.create(
            agent=self.agent,
            name="To Delete",
            url="https://example.com/delete",
        )
        response = self.client.post(
            reverse("agent_detail", args=[self.agent_id]),
            {
                "webhook_action": "delete",
                "webhook_id": str(webhook.id),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            PersistentAgentWebhook.objects.filter(pk=webhook.pk).exists()
        )

    @tag("batch_agent_webhooks")
    def test_console_creates_inbound_webhook(self):
        response = self.client.post(
            reverse("agent_detail", args=[self.agent_id]),
            {
                "inbound_webhook_action": "create",
                "inbound_webhook_name": "Build Trigger",
                "inbound_webhook_is_active": "true",
            },
        )
        self.assertEqual(response.status_code, 302)
        webhook = PersistentAgentInboundWebhook.objects.get(agent=self.agent, name="Build Trigger")
        self.assertTrue(webhook.is_active)
        self.assertTrue(webhook.secret)

    @tag("batch_agent_webhooks")
    def test_console_updates_inbound_webhook(self):
        webhook = PersistentAgentInboundWebhook.objects.create(
            agent=self.agent,
            name="Inbound Original",
            is_active=True,
        )
        response = self.client.post(
            reverse("agent_detail", args=[self.agent_id]),
            {
                "inbound_webhook_action": "update",
                "inbound_webhook_id": str(webhook.id),
                "inbound_webhook_name": "Inbound Updated",
                "inbound_webhook_is_active": "false",
            },
        )
        self.assertEqual(response.status_code, 302)
        webhook.refresh_from_db()
        self.assertEqual(webhook.name, "Inbound Updated")
        self.assertFalse(webhook.is_active)

    @tag("batch_agent_webhooks")
    def test_console_rotates_inbound_webhook_secret(self):
        webhook = PersistentAgentInboundWebhook.objects.create(
            agent=self.agent,
            name="Rotate Me",
        )
        old_secret = webhook.secret
        response = self.client.post(
            reverse("agent_detail", args=[self.agent_id]),
            {
                "inbound_webhook_action": "rotate_secret",
                "inbound_webhook_id": str(webhook.id),
            },
        )
        self.assertEqual(response.status_code, 302)
        webhook.refresh_from_db()
        self.assertNotEqual(webhook.secret, old_secret)

    @tag("batch_agent_webhooks")
    def test_console_deletes_inbound_webhook(self):
        webhook = PersistentAgentInboundWebhook.objects.create(
            agent=self.agent,
            name="Inbound Delete",
        )
        response = self.client.post(
            reverse("agent_detail", args=[self.agent_id]),
            {
                "inbound_webhook_action": "delete",
                "inbound_webhook_id": str(webhook.id),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            PersistentAgentInboundWebhook.objects.filter(pk=webhook.pk).exists()
        )

    @tag("batch_agent_webhooks")
    @patch("console.views.Analytics.track_event")
    def test_inbound_webhook_actions_emit_analytics(self, mock_track_event):
        view = AgentDetailView()

        create_request = self.factory.post(
            "/console/agents/test/",
            {
                "inbound_webhook_name": "Build Trigger",
                "inbound_webhook_is_active": "true",
            },
        )
        create_request.user = self.user
        with self.captureOnCommitCallbacks(execute=True):
            create_response = view._handle_inbound_webhook_action(
                create_request,
                self.agent,
                "create",
                ajax=True,
            )

        self.assertEqual(create_response.status_code, 200)
        webhook = PersistentAgentInboundWebhook.objects.get(agent=self.agent, name="Build Trigger")

        update_request = self.factory.post(
            "/console/agents/test/",
            {
                "inbound_webhook_id": str(webhook.id),
                "inbound_webhook_name": "Build Trigger Updated",
                "inbound_webhook_is_active": "false",
            },
        )
        update_request.user = self.user
        with self.captureOnCommitCallbacks(execute=True):
            update_response = view._handle_inbound_webhook_action(
                update_request,
                self.agent,
                "update",
                ajax=True,
            )

        self.assertEqual(update_response.status_code, 200)
        webhook.refresh_from_db()

        rotate_request = self.factory.post(
            "/console/agents/test/",
            {"inbound_webhook_id": str(webhook.id)},
        )
        rotate_request.user = self.user
        with self.captureOnCommitCallbacks(execute=True):
            rotate_response = view._handle_inbound_webhook_action(
                rotate_request,
                self.agent,
                "rotate_secret",
                ajax=True,
            )

        self.assertEqual(rotate_response.status_code, 200)

        delete_request = self.factory.post(
            "/console/agents/test/",
            {"inbound_webhook_id": str(webhook.id)},
        )
        delete_request.user = self.user
        with self.captureOnCommitCallbacks(execute=True):
            delete_response = view._handle_inbound_webhook_action(
                delete_request,
                self.agent,
                "delete",
                ajax=True,
            )

        self.assertEqual(delete_response.status_code, 200)

        self.assertEqual(
            [call.kwargs["event"] for call in mock_track_event.call_args_list],
            [
                AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_ADDED,
                AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_UPDATED,
                AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_SECRET_ROTATED,
                AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_DELETED,
            ],
        )
        create_props = mock_track_event.call_args_list[0].kwargs["properties"]
        self.assertEqual(create_props["agent_id"], str(self.agent.id))
        self.assertEqual(create_props["webhook_id"], str(webhook.id))
        self.assertEqual(create_props["webhook_name"], "Build Trigger")
        self.assertTrue(create_props["is_active"])

        update_props = mock_track_event.call_args_list[1].kwargs["properties"]
        self.assertEqual(update_props["webhook_name"], "Build Trigger Updated")
        self.assertFalse(update_props["is_active"])


class InboundAgentWebhookEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="inbound-owner",
            email="inbound@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=cls.user,
            email=cls.user.email,
            verified=True,
            primary=True,
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Inbound Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Inbound Receiver",
            charter="Receive inbound webhook events",
            browser_use_agent=cls.browser_agent,
        )
        cls.webhook = PersistentAgentInboundWebhook.objects.create(
            agent=cls.agent,
            name="Deploy Hook",
        )

    @tag("batch_agent_webhooks")
    @patch("api.webhooks.Analytics.track_event")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_inbound_webhook_emits_analytics(self, mock_delay, mock_track_event):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={self.webhook.secret}",
                data='{"status":"ok","build_id":42}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 202, response.content)
        mock_delay.assert_called_once_with(str(self.agent.id))
        mock_track_event.assert_called_once()
        self.assertEqual(
            mock_track_event.call_args.kwargs["event"],
            AnalyticsEvent.PERSISTENT_AGENT_INBOUND_WEBHOOK_TRIGGERED,
        )
        props = mock_track_event.call_args.kwargs["properties"]
        self.assertEqual(props["agent_id"], str(self.agent.id))
        self.assertEqual(props["webhook_id"], str(self.webhook.id))
        self.assertEqual(props["webhook_name"], self.webhook.name)
        self.assertEqual(props["payload_kind"], "json")
        self.assertEqual(props["attachment_count"], 0)
        self.assertTrue(props["message_id"])

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_inbound_webhook_accepts_json_payload(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={self.webhook.secret}",
                data='{"status":"ok","build_id":42}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 202, response.content)
        payload = response.json()
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["webhookId"], str(self.webhook.id))

        message = PersistentAgentMessage.objects.get(id=payload["messageId"])
        self.assertEqual(message.owner_agent_id, self.agent.id)
        self.assertEqual(message.conversation.channel, "other")
        self.assertEqual(message.conversation.display_name, self.webhook.name)
        self.assertEqual(message.raw_payload["source_kind"], "webhook")
        self.assertEqual(message.raw_payload["webhook_name"], self.webhook.name)
        self.assertEqual(message.raw_payload["payload_kind"], "json")
        self.assertEqual(message.body, json.dumps({"build_id": 42, "status": "ok"}, indent=2, sort_keys=True))
        mock_delay.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_inbound_webhook_accepts_multipart_payload_and_attachments(self, mock_delay):
        upload = SimpleUploadedFile("deploy.json", b'{"ok": true}', content_type="application/json")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={self.webhook.secret}",
                data={
                    "environment": "prod",
                    "build_id": "123",
                    "artifact": upload,
                },
            )

        self.assertEqual(response.status_code, 202, response.content)
        message = PersistentAgentMessage.objects.get(id=response.json()["messageId"])
        self.assertEqual(message.attachments.count(), 1)
        self.assertEqual(message.raw_payload["payload_kind"], "form")
        self.assertEqual(
            message.body,
            json.dumps({"build_id": "123", "environment": "prod"}, indent=2, sort_keys=True),
        )
        mock_delay.assert_called_once_with(str(self.agent.id))

    @tag("batch_agent_webhooks")
    def test_inbound_webhook_rejects_invalid_secret(self):
        response = self.client.post(
            f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t=wrong-secret",
            data='{"status":"ok"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    @tag("batch_agent_webhooks")
    def test_inbound_webhook_rejects_inactive_webhook(self):
        self.webhook.is_active = False
        self.webhook.save(update_fields=["is_active"])

        response = self.client.post(
            f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={self.webhook.secret}",
            data='{"status":"ok"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)

    @tag("batch_agent_webhooks")
    @patch("api.agent.comms.message_service.send_billing_pause_auto_reply")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_inbound_webhook_skips_processing_when_owner_billing_paused(self, mock_delay, mock_auto_reply):
        billing = self.user.billing
        billing.execution_paused = True
        billing.execution_pause_reason = "billing_delinquency"
        billing.execution_paused_at = timezone.now()
        billing.save(
            update_fields=[
                "execution_paused",
                "execution_pause_reason",
                "execution_paused_at",
            ]
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={self.webhook.secret}",
                data='{"status":"paused"}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 202, response.content)
        payload = response.json()
        self.assertTrue(payload["accepted"])
        self.assertTrue(PersistentAgentMessage.objects.filter(id=payload["messageId"]).exists())
        mock_delay.assert_not_called()
        mock_auto_reply.assert_not_called()

        self.webhook.refresh_from_db()
        self.assertIsNotNone(self.webhook.last_triggered_at)

    @tag("batch_agent_webhooks")
    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_rotated_secret_invalidates_previous_url(self, mock_delay):
        old_secret = self.webhook.secret
        self.webhook.rotate_secret()
        self.webhook.refresh_from_db()

        old_response = self.client.post(
            f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={old_secret}",
            data='{"status":"stale"}',
            content_type="application/json",
        )
        self.assertEqual(old_response.status_code, 403)

        with self.captureOnCommitCallbacks(execute=True):
            new_response = self.client.post(
                f"{reverse('api:inbound_agent_webhook', args=[self.webhook.id])}?t={self.webhook.secret}",
                data='{"status":"fresh"}',
                content_type="application/json",
            )
        self.assertEqual(new_response.status_code, 202, new_response.content)
        mock_delay.assert_called_once_with(str(self.agent.id))


class InboundAgentWebhookParsingTests(TestCase):
    @tag("batch_agent_webhooks")
    def test_parse_multipart_request_does_not_access_raw_body(self):
        upload = SimpleUploadedFile("deploy.json", b'{"ok": true}', content_type="application/json")
        post_data = QueryDict("", mutable=True)
        post_data["environment"] = "prod"
        post_data["build_id"] = "123"

        class MultipartRequest:
            content_type = "multipart/form-data; boundary=test-boundary"
            encoding = "utf-8"
            method = "POST"
            path = "/api/webhooks/inbound/test/"
            POST = post_data
            FILES = MultiValueDict({"artifact": [upload]})
            GET = QueryDict("t=secret&source=ci")

            @property
            def body(self):
                raise AssertionError("multipart webhook parsing should not read request.body")

        body, raw_payload, attachments = _parse_inbound_agent_webhook_request(MultipartRequest())

        self.assertEqual(
            body,
            json.dumps({"build_id": "123", "environment": "prod"}, indent=2, sort_keys=True),
        )
        self.assertEqual(raw_payload["payload_kind"], "form")
        self.assertEqual(raw_payload["query_params"], {"source": "ci"})
        self.assertEqual(raw_payload["attachments"][0]["filename"], "deploy.json")
        self.assertEqual(attachments, [upload])
