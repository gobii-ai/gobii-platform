from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone
from requests import RequestException

from api.models import BrowserUseAgent, BrowserUseAgentTask, BrowserUseAgentTaskStep, ProxyServer
from api.services.task_webhooks import trigger_task_webhook, WEBHOOK_TIMEOUT_SECONDS


@tag("batch_api_tasks")
class TaskWebhookServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="webhook-user",
            email="webhooks@example.com",
            password="password123",
        )
        cls.proxy = ProxyServer.objects.create(
            name="Task Webhook Proxy",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="proxy.tasks.example.com",
            port=8080,
        )
        cls.browser_agent = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Task Agent",
            preferred_proxy=cls.proxy,
        )

    def setUp(self):
        self.browser_agent = type(self).browser_agent
        self.proxy = type(self).proxy

    def _create_completed_task(self, webhook_url="https://example.com/webhook"):
        task = BrowserUseAgentTask.objects.create(
            prompt={'detail': 'done'},
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            agent=self.browser_agent,
            user=self.browser_agent.user,
            webhook_url=webhook_url,
        )
        BrowserUseAgentTaskStep.objects.create(
            task=task,
            step_number=1,
            description="result",
            is_result=True,
            result_value={'foo': 'bar'},
        )
        return task

    @patch("api.services.task_webhooks.requests.post")
    def test_trigger_task_webhook_success(self, mock_post: Mock):
        mock_post.return_value = Mock(status_code=202, text="")
        task = self._create_completed_task()

        trigger_task_webhook(task)

        mock_post.assert_called_once()
        called_args, called_kwargs = mock_post.call_args
        self.assertEqual(called_args[0], task.webhook_url)
        self.assertEqual(called_kwargs["timeout"], WEBHOOK_TIMEOUT_SECONDS)
        self.assertEqual(
            called_kwargs["json"],
            {
                "id": str(task.id),
                "status": BrowserUseAgentTask.StatusChoices.COMPLETED,
                "agent_id": str(self.browser_agent.id),
                "result": {'foo': 'bar'},
            },
        )
        self.assertEqual(called_kwargs["headers"]["User-Agent"], "Gobii-AgentWebhook/1.0")
        self.assertEqual(called_kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(
            called_kwargs["proxies"],
            {"http": self.proxy.proxy_url, "https": self.proxy.proxy_url},
        )
        task.refresh_from_db()
        self.assertIsNotNone(task.webhook_last_called_at)
        self.assertEqual(task.webhook_last_status_code, 202)
        self.assertIsNone(task.webhook_last_error)

    @patch("api.services.task_webhooks.requests.post", side_effect=RequestException("boom"))
    def test_trigger_task_webhook_records_error(self, mock_post: Mock):
        task = self._create_completed_task()

        trigger_task_webhook(task)

        mock_post.assert_called_once()
        called_kwargs = mock_post.call_args.kwargs
        self.assertEqual(
            called_kwargs["proxies"],
            {"http": self.proxy.proxy_url, "https": self.proxy.proxy_url},
        )
        task.refresh_from_db()
        self.assertIsNotNone(task.webhook_last_called_at)
        self.assertIsNone(task.webhook_last_status_code)
        self.assertIsNone(task.webhook_last_error)

    def test_trigger_task_webhook_requires_proxy(self):
        task = self._create_completed_task()
        with patch(
            "api.services.task_webhooks.select_proxy_for_browser_task",
            return_value=None,
        ) as mock_select, patch("api.services.task_webhooks.requests.post") as mock_post:
            trigger_task_webhook(task)

        mock_select.assert_called_once_with(task, allow_no_proxy_in_debug=False)
        mock_post.assert_not_called()

        task.refresh_from_db()
        self.assertIsNotNone(task.webhook_last_called_at)
        self.assertIsNone(task.webhook_last_status_code)
        self.assertIn("proxy", task.webhook_last_error or "")

    @patch("api.services.task_webhooks.requests.post")
    def test_trigger_task_webhook_skips_when_missing_url(self, mock_post: Mock):
        task = self._create_completed_task(webhook_url=None)
        mock_post.reset_mock()

        trigger_task_webhook(task)

        mock_post.assert_not_called()

    @patch("api.services.task_webhooks.requests.post")
    def test_trigger_task_webhook_skips_non_terminal_status(self, mock_post: Mock):
        task = BrowserUseAgentTask.objects.create(
            prompt={'detail': 'pending'},
            status=BrowserUseAgentTask.StatusChoices.PENDING,
            agent=self.browser_agent,
            user=self.browser_agent.user,
            webhook_url="https://example.com/webhook",
        )

        trigger_task_webhook(task)

        mock_post.assert_not_called()

    @patch("api.services.task_webhooks.requests.post")
    def test_trigger_task_webhook_skips_when_already_attempted(self, mock_post: Mock):
        task = self._create_completed_task()
        BrowserUseAgentTask.objects.filter(pk=task.pk).update(webhook_last_called_at=timezone.now())
        task.webhook_last_called_at = timezone.now()

        trigger_task_webhook(task)

        mock_post.assert_not_called()
