import json
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.models import (
    BrowserUseAgent,
    BrowserUseAgentTask,
    PersistentAgent,
    PersistentAgentWebSession,
    ProxyHealthCheckResult,
    ProxyHealthCheckSpec,
    ProxyServer,
    TaskCredit,
)
from config.redis_client import _FakeRedis


@tag("batch_console_api")
class SystemStatusAPITests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff = user_model.objects.create_user(
            username="status-staff",
            email="status-staff@example.com",
            password="pass123",
            is_staff=True,
        )
        self.owner = user_model.objects.create_user(
            username="status-owner",
            email="status-owner@example.com",
            password="pass123",
        )
        self.client.force_login(self.staff)
        self.url = reverse("console-api-status")

    def _create_agent(self, name, *, execution_environment=None):
        browser_agent = BrowserUseAgent.objects.create(user=self.owner, name=f"{name} Browser")
        return PersistentAgent.objects.create(
            user=self.owner,
            name=name,
            charter=f"{name} charter",
            browser_use_agent=browser_agent,
            execution_environment=execution_environment or settings.GOBII_RELEASE_ENV,
        )

    @patch("console.system_status.get_redis_client")
    def test_status_api_returns_snapshot_shape_and_celery_counts(self, mock_get_redis_client):
        redis_client = _FakeRedis()
        redis_client.rpush("celery", "job-a")
        redis_client.rpush("celery", "job-b")
        redis_client.rpush("celery", "job-c")
        redis_client.rpush("celery.single_instance", "job-d")
        redis_client.rpush("celery.single_instance", "job-e")
        mock_get_redis_client.return_value = redis_client

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("meta", payload)
        self.assertIn("overview", payload)
        self.assertIn("sections", payload)

        celery_section = payload["sections"]["celery"]
        self.assertTrue(celery_section["available"])
        self.assertEqual(celery_section["summary"]["totalPending"], 5)
        self.assertEqual(celery_section["summary"]["queueCounts"]["celery"], 3)
        self.assertEqual(celery_section["summary"]["queueCounts"]["celery.single_instance"], 2)

    def test_status_api_requires_staff(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    @patch("console.system_status.get_redis_client")
    def test_agent_processing_section_dedupes_redis_signals_and_filters_environment(self, mock_get_redis_client):
        current_agent = self._create_agent("Current Agent")
        self._create_agent("Other Env Agent", execution_environment="staging")

        redis_client = _FakeRedis()
        heartbeat_payload = json.dumps({"stage": "tool_call", "last_seen": timezone.now().timestamp()})
        redis_client.set(f"agent-event-processing:heartbeat:{current_agent.id}", heartbeat_payload)
        redis_client.set(f"agent-event-processing:queued:{current_agent.id}", "1")
        redis_client.sadd("agent-event-processing:pending", str(current_agent.id))
        redis_client.set(f"redlock:agent-event-processing:{current_agent.id}", "1")
        redis_client.set("agent-event-processing:heartbeat:not-a-uuid", heartbeat_payload)
        mock_get_redis_client.return_value = redis_client

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        section = response.json()["sections"]["agents"]
        self.assertEqual(section["summary"]["activeAgentCount"], 1)
        self.assertEqual(section["summary"]["queuedCount"], 1)
        self.assertEqual(section["summary"]["pendingCount"], 1)
        self.assertEqual(section["summary"]["lockedCount"], 1)
        self.assertEqual(section["summary"]["heartbeatCount"], 1)
        self.assertEqual(section["summary"]["queuedOrPendingCount"], 1)
        self.assertEqual(len(section["rows"]), 1)
        self.assertEqual(section["rows"][0]["agentName"], "Current Agent")
        self.assertEqual(section["rows"][0]["stage"], "tool_call")

    @patch("console.system_status.get_redis_client")
    def test_web_session_section_only_counts_live_sessions_for_current_environment(self, mock_get_redis_client):
        current_agent = self._create_agent("Live Agent")
        stale_agent = self._create_agent("Stale Agent")
        other_env_agent = self._create_agent("Other Env Live", execution_environment="staging")

        now = timezone.now()
        PersistentAgentWebSession.objects.create(
            agent=current_agent,
            user=self.owner,
            started_at=now,
            last_seen_at=now,
        )
        stale_session = PersistentAgentWebSession.objects.create(
            agent=stale_agent,
            user=get_user_model().objects.create_user(
                username="stale-session-user",
                email="stale-session-user@example.com",
                password="pass123",
            ),
            started_at=now - timedelta(minutes=1),
            last_seen_at=now - timedelta(minutes=5),
        )
        PersistentAgentWebSession.objects.create(
            agent=other_env_agent,
            user=self.owner,
            started_at=now,
            last_seen_at=now,
        )

        mock_get_redis_client.return_value = _FakeRedis()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        section = response.json()["sections"]["webSessions"]
        stale_session.refresh_from_db()
        self.assertEqual(section["summary"]["liveCount"], 1)
        self.assertEqual(len(section["rows"]), 1)
        self.assertEqual(section["rows"][0]["agentName"], "Live Agent")
        self.assertIsNotNone(stale_session.ended_at)

    @patch("console.system_status.get_redis_client")
    def test_proxy_section_classifies_healthy_degraded_stale_and_inactive(self, mock_get_redis_client):
        mock_get_redis_client.return_value = _FakeRedis()
        spec = ProxyHealthCheckSpec.objects.create(name="Proxy check", prompt="Check")
        now = timezone.now()

        healthy_proxy = ProxyServer.objects.create(name="Healthy", host="healthy.local", port=8001, is_active=True)
        degraded_proxy = ProxyServer.objects.create(name="Degraded", host="degraded.local", port=8002, is_active=True)
        stale_proxy = ProxyServer.objects.create(name="Stale", host="stale.local", port=8003, is_active=True)
        ProxyServer.objects.create(name="Inactive", host="inactive.local", port=8004, is_active=False)

        ProxyHealthCheckResult.objects.create(
            proxy_server=healthy_proxy,
            health_check_spec=spec,
            status=ProxyHealthCheckResult.Status.PASSED,
            checked_at=now - timedelta(minutes=10),
            response_time_ms=120,
        )
        ProxyHealthCheckResult.objects.create(
            proxy_server=degraded_proxy,
            health_check_spec=spec,
            status=ProxyHealthCheckResult.Status.FAILED,
            checked_at=now - timedelta(minutes=5),
            response_time_ms=600,
        )
        ProxyHealthCheckResult.objects.create(
            proxy_server=stale_proxy,
            health_check_spec=spec,
            status=ProxyHealthCheckResult.Status.PASSED,
            checked_at=now - timedelta(days=2),
            response_time_ms=140,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        section = response.json()["sections"]["proxies"]
        self.assertEqual(section["summary"]["activeCount"], 3)
        self.assertEqual(section["summary"]["healthyCount"], 1)
        self.assertEqual(section["summary"]["degradedCount"], 1)
        self.assertEqual(section["summary"]["staleCount"], 1)
        self.assertEqual(section["summary"]["inactiveCount"], 1)
        self.assertEqual(section["status"], "critical")

    @patch("console.system_status._collect_proxy_section", side_effect=RuntimeError("proxy collector down"))
    @patch("console.system_status.get_redis_client")
    def test_status_api_marks_single_section_unavailable_when_collector_fails(self, mock_get_redis_client, _mock_proxy):
        mock_get_redis_client.return_value = _FakeRedis()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["sections"]["celery"]["available"])
        self.assertFalse(payload["sections"]["proxies"]["available"])
        self.assertEqual(payload["sections"]["proxies"]["status"], "critical")
        self.assertEqual(payload["sections"]["proxies"]["error"], "Temporarily unavailable.")

    @patch("console.system_status.get_redis_client")
    def test_browser_task_section_only_counts_tasks_for_current_environment(self, mock_get_redis_client):
        current_agent = self._create_agent("Current Browser Agent")
        other_env_agent = self._create_agent("Other Env Browser Agent", execution_environment="staging")
        loose_browser_agent = BrowserUseAgent.objects.create(user=self.owner, name="Loose Browser Agent")
        TaskCredit.objects.create(
            user=self.owner,
            credits=Decimal("10"),
            credits_used=Decimal("0"),
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=30),
            additional_task=True,
        )

        BrowserUseAgentTask.objects.create(
            agent=current_agent.browser_use_agent,
            user=self.owner,
            status=BrowserUseAgentTask.StatusChoices.PENDING,
        )
        BrowserUseAgentTask.objects.create(
            agent=other_env_agent.browser_use_agent,
            user=self.owner,
            status=BrowserUseAgentTask.StatusChoices.PENDING,
        )
        BrowserUseAgentTask.objects.create(
            agent=loose_browser_agent,
            user=self.owner,
            status=BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
        )
        BrowserUseAgentTask.objects.create(
            agent=None,
            user=self.owner,
            status=BrowserUseAgentTask.StatusChoices.FAILED,
        )
        mock_get_redis_client.return_value = _FakeRedis()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        section = response.json()["sections"]["browserTasks"]
        self.assertEqual(section["summary"]["pendingCount"], 1)
        self.assertEqual(section["summary"]["inProgressCount"], 0)
        self.assertEqual(section["summary"]["failedCount"], 0)
        self.assertEqual(section["summary"]["activeCount"], 1)
        self.assertEqual(len(section["rows"]), 1)
        self.assertEqual(section["rows"][0]["agentName"], "Current Browser Agent Browser")
