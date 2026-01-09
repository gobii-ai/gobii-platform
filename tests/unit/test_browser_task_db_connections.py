from __future__ import annotations

import os
from unittest.mock import patch
from django.db.utils import OperationalError

from django.contrib.auth import get_user_model
from django.test import TestCase, tag, override_settings
from typing import Any

from api.models import (
    BrowserUseAgent,
    BrowserUseAgentTask,
    BrowserUseAgentTaskStep,
    BrowserLLMPolicy,
    BrowserLLMTier,
    BrowserTierEndpoint,
    BrowserModelEndpoint,
    LLMProvider,
    PersistentAgent,
    UserBilling,
)
from constants.plans import PlanNames
from tests.utils.llm_seed import get_intelligence_tier


def _provider_entry(provider_key: str, supports_vision: bool) -> dict[str, object]:
    return {
        "endpoint_key": f"{provider_key}-endpoint",
        "provider_key": provider_key,
        "weight": 1.0,
        "browser_model": None,
        "base_url": "",
        "backend": None,
        "supports_vision": supports_vision,
        "max_output_tokens": None,
        "api_key": "sk-test",
    }


@tag("batch_browser_task_db")
class BrowserTaskDbConnectionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="dbconn@example.com", email="dbconn@example.com", password="password123"
        )
        self.agent = BrowserUseAgent.objects.create(user=self.user, name="DBConn Agent")

    def test_close_old_connections_called_around_final_writes_success_path(self):
        # Create a task without output_schema to avoid dynamic model creation
        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="simple"
        )

        # Patch internals in the task module to simulate success quickly and avoid external deps
        with patch("api.tasks.browser_agent_tasks.LIBS_AVAILABLE", True), \
             patch("api.tasks.browser_agent_tasks.Controller") as MockController, \
             patch("api.tasks.browser_agent_tasks.select_proxy_for_task", return_value=None), \
             patch("api.tasks.browser_agent_tasks._execute_agent_with_failover", return_value=({"ok": True}, None)), \
             patch("api.tasks.browser_agent_tasks.close_old_connections") as mock_close:

            # Import inside the context to ensure patches are in effect
            from api.tasks.browser_agent_tasks import _process_browser_use_task_core

            _process_browser_use_task_core(str(task.id))

            # Ensure we refreshed DB connections before step creation and before final save
            self.assertGreaterEqual(mock_close.call_count, 2)

        # Verify task completed and result step was created
        task.refresh_from_db()
        self.assertEqual(task.status, BrowserUseAgentTask.StatusChoices.COMPLETED)
        self.assertTrue(
            BrowserUseAgentTaskStep.objects.filter(task=task, is_result=True).exists()
        )

    def test_step_creation_retry_is_idempotent_on_operational_error(self):
        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="simple"
        )

        with patch("api.tasks.browser_agent_tasks.LIBS_AVAILABLE", True), \
             patch("api.tasks.browser_agent_tasks.Controller") as MockController, \
             patch("api.tasks.browser_agent_tasks.select_proxy_for_task", return_value=None), \
             patch("api.tasks.browser_agent_tasks._execute_agent_with_failover", return_value=({"ok": True}, None)), \
             patch("api.tasks.browser_agent_tasks.close_old_connections") as mock_close, \
             patch("api.tasks.browser_agent_tasks.BrowserUseAgentTaskStep.objects.create") as mock_create:

            # First call raises OperationalError, second path will use update_or_create
            mock_create.side_effect = OperationalError("simulated closed connection")

            from api.tasks.browser_agent_tasks import _process_browser_use_task_core

            _process_browser_use_task_core(str(task.id))

            # We called close_old_connections at least once for retry
            self.assertGreaterEqual(mock_close.call_count, 2)

        # Verify only a single result step exists after retry path
        steps = BrowserUseAgentTaskStep.objects.filter(task=task, step_number=1)
        self.assertEqual(steps.count(), 1)
        self.assertTrue(steps.first().is_result)


@tag("batch_browser_task_db")
@override_settings(GOBII_PROPRIETARY_MODE=True)
class BrowserTaskPremiumTierTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="premium@example.com",
            email="premium@example.com",
            password="password123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Premium Agent")
        self.persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Persistent Premium Agent",
            browser_use_agent=self.browser_agent,
            preferred_llm_tier=get_intelligence_tier("premium"),
        )
        UserBilling.objects.update_or_create(
            user=self.user,
            defaults={"subscription": PlanNames.STARTUP},
        )

    def _create_task(self):
        return BrowserUseAgentTask.objects.create(
            agent=self.browser_agent,
            user=self.user,
            prompt="browse something",
        )

    def test_premium_tier_selected_when_available(self):
        provider = LLMProvider.objects.create(
            key="openai_premium",
            display_name="OpenAI",
            enabled=True,
            env_var_name="OPENAI_PREMIUM_API_KEY",
            browser_backend="OPENAI",
        )
        policy = BrowserLLMPolicy.objects.create(name="default", is_active=True)

        premium_endpoint = BrowserModelEndpoint.objects.create(
            key="openai_browser_premium",
            provider=provider,
            enabled=True,
            browser_model="openai/gpt-4.1-premium",
        )
        standard_endpoint = BrowserModelEndpoint.objects.create(
            key="openai_browser_standard",
            provider=provider,
            enabled=True,
            browser_model="openai/gpt-4o-mini",
        )

        premium_tier = BrowserLLMTier.objects.create(
            policy=policy,
            order=1,
            description="Premium",
            intelligence_tier=get_intelligence_tier("premium"),
        )
        standard_tier = BrowserLLMTier.objects.create(
            policy=policy,
            order=2,
            description="Standard",
            intelligence_tier=get_intelligence_tier("standard"),
        )

        BrowserTierEndpoint.objects.create(tier=premium_tier, endpoint=premium_endpoint, weight=1.0)
        BrowserTierEndpoint.objects.create(tier=standard_tier, endpoint=standard_endpoint, weight=1.0)

        task = self._create_task()
        captured: dict[str, Any] = {}

        def fake_execute(*, provider_priority=None, **kwargs):
            captured["priority"] = provider_priority
            return {"ok": True}, None

        with patch.dict(os.environ, {"OPENAI_PREMIUM_API_KEY": "sk-premium"}, clear=True), \
             patch("api.tasks.browser_agent_tasks.LIBS_AVAILABLE", True), \
             patch("api.tasks.browser_agent_tasks.Controller"), \
             patch("api.tasks.browser_agent_tasks.select_proxy_for_task", return_value=None), \
             patch("api.tasks.browser_agent_tasks.close_old_connections"), \
             patch("api.tasks.browser_agent_tasks._execute_agent_with_failover", side_effect=fake_execute):
            from api.tasks.browser_agent_tasks import _process_browser_use_task_core

            _process_browser_use_task_core(
                str(task.id),
                persistent_agent_id=str(self.persistent_agent.id),
            )

        priority = captured.get("priority")
        self.assertIsNotNone(priority)
        self.assertTrue(priority, "Expected provider priority to be populated")
        first_tier_keys = {entry["endpoint_key"] for entry in priority[0]}
        self.assertEqual(first_tier_keys, {"openai_browser_premium"})
        self.assertTrue(all(entry.get("intelligence_tier") == "premium" for entry in priority[0]))

    def test_falls_back_to_standard_when_no_premium_available(self):
        premium_provider = LLMProvider.objects.create(
            key="anthropic_premium",
            display_name="Anthropic",
            enabled=True,
            env_var_name="ANTHROPIC_PREMIUM_API_KEY",
            browser_backend="ANTHROPIC",
        )
        standard_provider = LLMProvider.objects.create(
            key="openai_standard",
            display_name="OpenAI",
            enabled=True,
            env_var_name="OPENAI_STANDARD_API_KEY",
            browser_backend="OPENAI",
        )
        policy = BrowserLLMPolicy.objects.create(name="fallback", is_active=True)

        premium_endpoint = BrowserModelEndpoint.objects.create(
            key="anthropic_browser_premium",
            provider=premium_provider,
            enabled=True,
            browser_model="anthropic/claude-premium",
        )
        standard_endpoint = BrowserModelEndpoint.objects.create(
            key="openai_browser_standard",
            provider=standard_provider,
            enabled=True,
            browser_model="openai/gpt-4o-mini",
        )

        premium_tier = BrowserLLMTier.objects.create(
            policy=policy,
            order=1,
            description="Premium",
            intelligence_tier=get_intelligence_tier("premium"),
        )
        standard_tier = BrowserLLMTier.objects.create(
            policy=policy,
            order=2,
            description="Standard",
            intelligence_tier=get_intelligence_tier("standard"),
        )

        BrowserTierEndpoint.objects.create(tier=premium_tier, endpoint=premium_endpoint, weight=1.0)
        BrowserTierEndpoint.objects.create(tier=standard_tier, endpoint=standard_endpoint, weight=1.0)

        task = BrowserUseAgentTask.objects.create(
            agent=self.browser_agent,
            user=self.user,
            prompt="fallback scenario",
        )

        captured: dict[str, Any] = {}

        def fake_execute(*, provider_priority=None, **kwargs):
            captured["priority"] = provider_priority
            return {"ok": True}, None

        # Only provide standard provider key; premium lacks credentials
        with patch.dict(os.environ, {"OPENAI_STANDARD_API_KEY": "sk-standard"}, clear=True), \
             patch("api.tasks.browser_agent_tasks.LIBS_AVAILABLE", True), \
             patch("api.tasks.browser_agent_tasks.Controller"), \
             patch("api.tasks.browser_agent_tasks.select_proxy_for_task", return_value=None), \
             patch("api.tasks.browser_agent_tasks.close_old_connections"), \
             patch("api.tasks.browser_agent_tasks._execute_agent_with_failover", side_effect=fake_execute):
            from api.tasks.browser_agent_tasks import _process_browser_use_task_core

            _process_browser_use_task_core(
                str(task.id),
                persistent_agent_id=str(self.persistent_agent.id),
            )

        priority = captured.get("priority")
        self.assertIsNotNone(priority)
        self.assertTrue(priority, "Expected provider priority to be populated")
        first_tier_keys = {entry["endpoint_key"] for entry in priority[0]}
        self.assertEqual(first_tier_keys, {"openai_browser_standard"})
        self.assertTrue(all(entry.get("intelligence_tier") == "standard" for entry in priority[0]))


@tag("batch_browser_task_db")
class BrowserTaskVisionRoutingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="vision@example.com",
            email="vision@example.com",
            password="password123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Vision Agent")
        self.persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Persistent Vision Agent",
            browser_use_agent=self.browser_agent,
        )

    def test_requires_vision_filters_out_non_vision_endpoints(self):
        task = BrowserUseAgentTask.objects.create(
            agent=self.browser_agent,
            user=self.user,
            prompt="vision required",
            requires_vision=True,
        )

        provider_priority = [[
            _provider_entry("vision", True),
            _provider_entry("text-only", False),
        ]]

        with patch("api.tasks.browser_agent_tasks.LIBS_AVAILABLE", True), \
             patch("api.tasks.browser_agent_tasks.Controller") as MockController, \
             patch("api.tasks.browser_agent_tasks.select_proxy_for_task", return_value=None), \
             patch("api.tasks.browser_agent_tasks._resolve_browser_provider_priority_from_db", return_value=provider_priority), \
             patch("api.tasks.browser_agent_tasks._execute_agent_with_failover", return_value=({"ok": True}, None)) as mock_execute, \
             patch("api.tasks.browser_agent_tasks.close_old_connections") as mock_close:

            from api.tasks.browser_agent_tasks import _process_browser_use_task_core

            _process_browser_use_task_core(
                str(task.id),
                persistent_agent_id=str(self.persistent_agent.id),
            )

            mock_execute.assert_called_once()
            filtered_priority = mock_execute.call_args.kwargs.get("provider_priority")
            self.assertEqual(len(filtered_priority), 1)
            self.assertEqual(len(filtered_priority[0]), 1)
            self.assertTrue(filtered_priority[0][0]["supports_vision"])
            self.assertGreaterEqual(mock_close.call_count, 1)

        task.refresh_from_db()
        self.assertEqual(task.status, BrowserUseAgentTask.StatusChoices.COMPLETED)

    def test_requires_vision_without_matching_endpoints_fails_task(self):
        task = BrowserUseAgentTask.objects.create(
            agent=self.browser_agent,
            user=self.user,
            prompt="vision required",
            requires_vision=True,
        )

        provider_priority = [[_provider_entry("text-only", False)]]

        with patch("api.tasks.browser_agent_tasks.LIBS_AVAILABLE", True), \
             patch("api.tasks.browser_agent_tasks.Controller") as MockController, \
             patch("api.tasks.browser_agent_tasks.select_proxy_for_task", return_value=None), \
             patch("api.tasks.browser_agent_tasks._resolve_browser_provider_priority_from_db", return_value=provider_priority), \
             patch("api.tasks.browser_agent_tasks._execute_agent_with_failover") as mock_execute, \
             patch("api.tasks.browser_agent_tasks.close_old_connections") as mock_close:

            from api.tasks.browser_agent_tasks import _process_browser_use_task_core

            _process_browser_use_task_core(
                str(task.id),
                persistent_agent_id=str(self.persistent_agent.id),
            )

            mock_execute.assert_not_called()
            self.assertGreaterEqual(mock_close.call_count, 1)

        task.refresh_from_db()
        self.assertEqual(task.status, BrowserUseAgentTask.StatusChoices.FAILED)
        self.assertIn("No vision-capable", task.error_message or "")
