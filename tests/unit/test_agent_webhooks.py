from unittest.mock import MagicMock, patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from requests import RequestException

from api.agent.tools.webhook_sender import execute_send_webhook_event
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentWebhook, ProxyServer


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
