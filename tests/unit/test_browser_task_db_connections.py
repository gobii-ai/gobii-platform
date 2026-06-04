from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from django.core.exceptions import ValidationError
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
    EvalRun,
    AgentFsNode,
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
        self.persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="DBConn Persistent Agent",
            charter="Test browser task pause handling",
            browser_use_agent=self.agent,
        )

    def test_task_creation_rejected_when_owner_execution_paused(self):
        UserBilling.objects.update_or_create(
            user=self.user,
            defaults={
                "execution_paused": True,
                "execution_pause_reason": "billing_delinquency",
            },
        )

        with self.assertRaises(ValidationError) as ctx:
            BrowserUseAgentTask.objects.create(
                agent=self.agent,
                user=self.user,
                prompt="simple",
            )

        self.assertIn("execution is paused", str(ctx.exception).lower())

    @override_settings(EVAL_BROWSER_TASK_SIMULATION_ENABLED=True)
    def test_disabled_browser_task_completes_eval_weather_simulation(self):
        from api.tasks.browser_agent_tasks import _finish_disabled_browser_task

        run = EvalRun.objects.create(
            scenario_slug="monitor_pollution",
            agent=self.persistent_agent,
            initiated_by=self.user,
            status=EvalRun.Status.RUNNING,
        )
        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            eval_run=run,
            prompt="Visit http://localhost:8000/eval/sim/weather/ and report the pollution index.",
        )

        with (
            patch("api.tasks.browser_agent_tasks.AgentBudgetManager.bump_branch_depth") as mock_bump_depth,
            patch("api.tasks.browser_agent_tasks._schedule_agent_follow_up") as mock_follow_up,
        ):
            _finish_disabled_browser_task(
                str(task.id),
                budget_id="budget-1",
                branch_id="branch-1",
                depth=2,
            )

        task.refresh_from_db()
        step = task.steps.get(step_number=1)
        self.assertEqual(task.status, BrowserUseAgentTask.StatusChoices.COMPLETED)
        self.assertTrue(step.is_result)
        self.assertEqual(step.result_value["pollution_index"], 55)
        mock_bump_depth.assert_called_once_with(
            agent_id=str(self.persistent_agent.id),
            branch_id="branch-1",
            delta=-1,
        )
        mock_follow_up.assert_called_once()

    @override_settings(EVAL_BROWSER_TASK_SIMULATION_ENABLED=True)
    def test_disabled_eval_browser_task_failure_does_not_schedule_follow_up(self):
        from api.tasks.browser_agent_tasks import _finish_disabled_browser_task

        run = EvalRun.objects.create(
            scenario_slug="job_listings_bundled_reply",
            agent=self.persistent_agent,
            initiated_by=self.user,
            status=EvalRun.Status.RUNNING,
        )
        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            eval_run=run,
            prompt="Find remote software engineer listings.",
        )

        with (
            patch("api.tasks.browser_agent_tasks.AgentBudgetManager.bump_branch_depth"),
            patch("api.tasks.browser_agent_tasks._schedule_agent_follow_up") as mock_follow_up,
        ):
            _finish_disabled_browser_task(
                str(task.id),
                budget_id="budget-1",
                branch_id="branch-1",
                depth=2,
            )

        task.refresh_from_db()
        self.assertEqual(task.status, BrowserUseAgentTask.StatusChoices.FAILED)
        mock_follow_up.assert_not_called()

    def test_task_is_cancelled_before_start_when_owner_execution_paused(self):
        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="simple",
        )
        UserBilling.objects.update_or_create(
            user=self.user,
            defaults={
                "execution_paused": True,
                "execution_pause_reason": "billing_delinquency",
            },
        )

        with patch("api.tasks.browser_agent_tasks._schedule_agent_follow_up") as mock_follow_up, \
             patch("api.tasks.browser_agent_tasks.trigger_task_webhook") as mock_webhook:
            from api.tasks.browser_agent_tasks import _process_browser_use_task_core

            _process_browser_use_task_core(str(task.id))

        task.refresh_from_db()
        self.assertEqual(task.status, BrowserUseAgentTask.StatusChoices.CANCELLED)
        self.assertIn("execution is paused", (task.error_message or "").lower())
        mock_follow_up.assert_not_called()
        mock_webhook.assert_called_once()

    def test_close_old_connections_called_around_final_writes_success_path(self):
        # Create a task without output_schema to avoid dynamic model creation
        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="simple"
        )

        # Patch internals in the task module to simulate success quickly and avoid external deps
        with patch("api.tasks.browser_agent_tasks.LIBS_AVAILABLE", True), \
             patch("api.tasks.browser_agent_tasks.Controller"), \
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
             patch("api.tasks.browser_agent_tasks.Controller"), \
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

    def test_browser_task_artifacts_persist_unique_existing_attachments(self):
        from api.agent.browser_actions.artifacts import persist_browser_task_artifacts_sync

        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="save files",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path = os.path.join(tmpdir, "summary.md")
            pdf_path = os.path.join(tmpdir, "page.pdf")
            missing_path = os.path.join(tmpdir, "missing.txt")
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write("summary")
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4\n")

            history = SimpleNamespace(
                action_results=lambda: [
                    SimpleNamespace(attachments=[summary_path, pdf_path, summary_path, missing_path]),
                    SimpleNamespace(attachments=None),
                ]
            )

            artifacts = persist_browser_task_artifacts_sync(
                history=history,
                persistent_agent_id=str(self.persistent_agent.id),
                task_id=str(task.id),
            )

        self.assertEqual(len(artifacts), 2)
        paths = {artifact["path"] for artifact in artifacts}
        self.assertEqual(
            paths,
            {
                f"/browser_tasks/{task.id}/summary.md",
                f"/browser_tasks/{task.id}/page.pdf",
            },
        )
        self.assertEqual(
            AgentFsNode.objects.filter(path__in=paths, node_type=AgentFsNode.NodeType.FILE).count(),
            2,
        )
        self.assertTrue(all(artifact.get("node_id") for artifact in artifacts))

    def test_browser_task_artifact_disappearing_before_checksum_is_skipped(self):
        from api.agent.browser_actions.artifacts import persist_browser_task_artifacts_sync

        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="save files",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path = os.path.join(tmpdir, "summary.md")
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write("summary")

            history = SimpleNamespace(action_results=lambda: [SimpleNamespace(attachments=[summary_path])])

            with patch("api.agent.browser_actions.artifacts._compute_sha256_file", side_effect=OSError("gone")):
                artifacts = persist_browser_task_artifacts_sync(
                    history=history,
                    persistent_agent_id=str(self.persistent_agent.id),
                    task_id=str(task.id),
                )

        self.assertEqual(artifacts, [])
        self.assertFalse(
            AgentFsNode.objects.filter(
                path=f"/browser_tasks/{task.id}/summary.md",
                node_type=AgentFsNode.NodeType.FILE,
            ).exists()
        )

    def test_browser_task_artifact_save_failure_deletes_stale_node(self):
        from api.agent.browser_actions.artifacts import persist_browser_task_artifacts_sync

        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="save files",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path = os.path.join(tmpdir, "summary.md")
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write("summary")

            history = SimpleNamespace(action_results=lambda: [SimpleNamespace(attachments=[summary_path])])

            with patch("django.db.models.fields.files.FieldFile.save", side_effect=RuntimeError("storage down")):
                artifacts = persist_browser_task_artifacts_sync(
                    history=history,
                    persistent_agent_id=str(self.persistent_agent.id),
                    task_id=str(task.id),
                )

        self.assertEqual(artifacts, [])
        self.assertFalse(
            AgentFsNode.objects.filter(
                path=f"/browser_tasks/{task.id}/summary.md",
                node_type=AgentFsNode.NodeType.FILE,
            ).exists()
        )

    def test_process_browser_task_saves_filespace_artifacts_from_provider_result(self):
        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="simple",
        )
        artifacts = [
            {
                "filename": "summary.md",
                "path": f"/browser_tasks/{task.id}/summary.md",
                "node_id": "node-1",
                "mime_type": "text/markdown",
                "size_bytes": 7,
            }
        ]

        with patch("api.tasks.browser_agent_tasks.LIBS_AVAILABLE", True), \
             patch("api.tasks.browser_agent_tasks.Controller"), \
             patch("api.tasks.browser_agent_tasks.select_proxy_for_task", return_value=None), \
             patch("api.tasks.browser_agent_tasks._execute_agent_with_failover", return_value=({"ok": True}, None, artifacts)), \
             patch("api.tasks.browser_agent_tasks.close_old_connections"):
            from api.tasks.browser_agent_tasks import _process_browser_use_task_core

            _process_browser_use_task_core(str(task.id))

        task.refresh_from_db()
        self.assertEqual(task.status, BrowserUseAgentTask.StatusChoices.COMPLETED)
        self.assertEqual(task.filespace_artifacts, artifacts)

    def test_browser_task_result_payload_includes_filespace_artifact_files(self):
        from api.agent.core.prompt_context import _build_browser_task_result_payload

        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="simple",
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            filespace_artifacts=[
                {
                    "filename": "summary.md",
                    "path": "/browser_tasks/task-1/summary.md",
                    "node_id": "node-1",
                    "mime_type": "text/markdown",
                    "size_bytes": 7,
                }
            ],
        )
        step = BrowserUseAgentTaskStep.objects.create(
            task=task,
            step_number=1,
            description="done",
            is_result=True,
            result_value={"ok": True},
        )

        payload = _build_browser_task_result_payload(task, step)

        self.assertEqual(payload["result"], {"ok": True})
        self.assertEqual(
            payload["files"],
            [{"path": "/browser_tasks/task-1/summary.md", "filename": "summary.md"}],
        )

    def test_browser_task_result_payload_omits_files_when_no_artifacts(self):
        from api.agent.core.prompt_context import _build_browser_task_result_payload

        task = BrowserUseAgentTask.objects.create(
            agent=self.agent,
            user=self.user,
            prompt="simple",
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
        )
        step = BrowserUseAgentTaskStep.objects.create(
            task=task,
            step_number=1,
            description="done",
            is_result=True,
            result_value={"ok": True},
        )

        payload = _build_browser_task_result_payload(task, step)

        self.assertEqual(payload["result"], {"ok": True})
        self.assertNotIn("files", payload)


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
             patch("api.tasks.browser_agent_tasks.Controller"), \
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
             patch("api.tasks.browser_agent_tasks.Controller"), \
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
