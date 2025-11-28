import json
import shutil
import tempfile
from datetime import timedelta
from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.test import RequestFactory, TestCase, tag, override_settings
from django.utils import timezone
from unittest.mock import patch, MagicMock

import zstandard as zstd

from api.agent.core.event_processing import (
    build_prompt_context,
    _get_completed_process_run_count,
    _run_agent_loop,
)
from api.agent.core.prompt_context import (
    get_prompt_token_budget,
    message_history_limit,
    tool_call_history_limit,
)
from api.admin import PersistentAgentPromptArchiveAdmin
from api.agent.tools.schedule_updater import execute_update_schedule as _execute_update_schedule
from api.agent.tools.search_web import execute_search_web as _execute_search_web
from api.agent.tools.http_request import execute_http_request as _execute_http_request
from api.agent.tools.tool_manager import enable_tools
from api.agent.tasks.process_events import process_agent_cron_trigger_task, _remove_orphaned_celery_beat_task
from api.models import (
    BrowserUseAgent,
    MCPServerConfig,
    ProxyServer,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentCronTrigger,
    PersistentAgentSecret,
    PersistentAgentPromptArchive,
    PersistentAgentCompletion,
    PersistentAgentSystemStep,
    PersistentAgentSystemMessage,
    PromptConfig,
)
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNamesChoices
from api.agent.core.llm_config import AgentLLMTier
from api.services.prompt_settings import invalidate_prompt_settings_cache

User = get_user_model()


@tag("batch_event_processing")
class PromptContextBuilderTests(TestCase):
    """Unit tests for `build_prompt_context`."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="prompt_tester@example.com",
            email="prompt_tester@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="PromptBA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="PromptAgent",
            charter="Test prompt context",
            browser_use_agent=self.browser_agent,
        )
        self.endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel="email",
            address="agent@example.com",
            is_primary=True,
        )
        self.external_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel="email",
            address="user@example.com",
        )
        self._storage_dir = tempfile.mkdtemp()
        self._storage = FileSystemStorage(location=self._storage_dir)
        self._storage_patch = patch('api.agent.core.prompt_context.default_storage', self._storage)
        self._admin_storage_patch = patch('api.admin.default_storage', self._storage)
        self._print_patch = patch('api.agent.core.prompt_context.print')
        self._storage_patch.start()
        self._admin_storage_patch.start()
        self._print_patch.start()
        self.addCleanup(self._storage_patch.stop)
        self.addCleanup(self._admin_storage_patch.stop)
        self.addCleanup(self._print_patch.stop)
        self.addCleanup(lambda: shutil.rmtree(self._storage_dir, ignore_errors=True))

    def test_message_metadata_in_prompt(self):
        """Test that message metadata (from, channel) is included in the prompt."""
        # Create a mock event window with one message
        msg = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=self.external_endpoint,
            to_endpoint=self.endpoint,
            is_outbound=False,
            body="Hello agent!",
            seq=f"TEST{int(timezone.now().timestamp() * 1_000_000):022d}"[:26],
        )
        # Build the prompt context
        with patch('api.agent.core.prompt_context.ensure_steps_compacted'), \
             patch('api.agent.core.prompt_context.ensure_comms_compacted'):
            context, _, _ = build_prompt_context(self.agent)

        # Find the user message in the context
        user_message = next((m for m in context if m['role'] == 'user'), None)

        self.assertIsNotNone(user_message)
        
        # Check that the content includes the structured format with message metadata
        content = user_message['content']
        
        # Verify the event block exists and contains message metadata
        self.assertIn('<event_', content)  # Event sections start with <event_
        self.assertIn('_message_inbound_email>', content)  # Contains message event type
        self.assertIn(f'On {self.external_endpoint.channel}, you received a message from {self.external_endpoint.address}:', content)
        self.assertIn('<body>', content)  # Updated to match current format
        self.assertIn('Hello agent!', content)
        self.assertIn('</body>', content)  # Updated to match current format

        # Verify other expected blocks are present
        self.assertIn('<charter>Test prompt context</charter>', content)
        self.assertIn('<schedule>No schedule configured</schedule>', content)
        self.assertIn('<current_datetime>', content)
        self.assertIn('</current_datetime>', content)

    def test_mcp_servers_listed_in_prompt(self):
        """Accessible MCP servers should be enumerated in the prompt context."""
        MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="test-sheets",
            display_name="Test Sheets",
            url="https://mcp.example.com",
        )

        with patch('api.agent.core.prompt_context.ensure_steps_compacted'), \
             patch('api.agent.core.prompt_context.ensure_comms_compacted'):
            context, _, _ = build_prompt_context(self.agent)

        user_message = next((m for m in context if m['role'] == 'user'), None)
        self.assertIsNotNone(user_message)
        content = user_message['content']
        self.assertIn("These are the MCP servers you have access to.", content)
        self.assertIn("Test Sheets", content)
        self.assertIn("search_tools", content)

    def test_admin_system_message_is_injected_once(self):
        """Admin-authored system directives should appear in the system prompt and be marked delivered."""
        directive = PersistentAgentSystemMessage.objects.create(
            agent=self.agent,
            body="Drop everything and update the quarterly results deck today.",
            created_by=self.user,
        )

        with patch('api.agent.core.prompt_context.ensure_steps_compacted'), \
             patch('api.agent.core.prompt_context.ensure_comms_compacted'):
            context, _, _ = build_prompt_context(self.agent)

        system_message = next((m for m in context if m['role'] == 'system'), None)
        self.assertIsNotNone(system_message)
        content = system_message['content']
        self.assertIn("SYSTEM NOTICE FROM GOBII OPERATIONS", content)
        self.assertIn("Drop everything and update the quarterly results deck today.", content)

        sys_steps = PersistentAgentSystemStep.objects.filter(
            code=PersistentAgentSystemStep.Code.SYSTEM_DIRECTIVE,
            step__agent=self.agent,
        )
        self.assertEqual(sys_steps.count(), 1)
        self.assertIn("Drop everything and update the quarterly results deck today.", sys_steps.first().step.description)

        directive.refresh_from_db()
        self.assertIsNotNone(directive.delivered_at)

        with patch('api.agent.core.prompt_context.ensure_steps_compacted'), \
             patch('api.agent.core.prompt_context.ensure_comms_compacted'):
            second_context, _, _ = build_prompt_context(self.agent)

        second_system = next((m for m in second_context if m['role'] == 'system'), None)
        self.assertIsNotNone(second_system)
        self.assertNotIn("Drop everything and update the quarterly results deck today.", second_system['content'])
        self.assertEqual(
            PersistentAgentSystemStep.objects.filter(
                code=PersistentAgentSystemStep.Code.SYSTEM_DIRECTIVE,
                step__agent=self.agent,
            ).count(),
            1,
        )

    def test_prompt_archive_saved_to_storage(self):
        """Prompt archives should be written to object storage as compressed JSON."""
        with patch('api.agent.core.prompt_context.ensure_steps_compacted'), \
             patch('api.agent.core.prompt_context.ensure_comms_compacted'):
            context, _, prompt_archive_id = build_prompt_context(self.agent)

        archive_dir = f"persistent_agents/{self.agent.id}/prompt_archives"
        _, files = self._storage.listdir(archive_dir)
        self.assertEqual(len(files), 1, "Expected a single prompt archive file")
        archive_path = f"{archive_dir}/{files[0]}"

        with self._storage.open(archive_path, "rb") as fh:
            compressed_bytes = fh.read()

        decompressed = zstd.ZstdDecompressor().decompress(compressed_bytes)
        payload = json.loads(decompressed.decode("utf-8"))

        self.assertEqual(payload["agent_id"], str(self.agent.id))
        self.assertEqual(payload["token_budget"], get_prompt_token_budget(self.agent))
        self.assertIn("system_prompt", payload)
        self.assertIn("user_prompt", payload)
        user_message = next((m for m in context if m["role"] == "user"), None)
        self.assertIsNotNone(user_message)
        self.assertEqual(payload["user_prompt"], user_message["content"])
        self.assertEqual(PersistentAgentPromptArchive.objects.count(), 1)
        archive_row = PersistentAgentPromptArchive.objects.get(agent=self.agent)
        self.assertEqual(archive_row.storage_key, archive_path)
        self.assertEqual(archive_row.raw_bytes, len(decompressed))
        self.assertEqual(archive_row.compressed_bytes, len(compressed_bytes))
        self.assertEqual(archive_row.tokens_before, payload["tokens_before"])
        self.assertEqual(archive_row.tokens_after, payload["tokens_after"])
        self.assertEqual(archive_row.tokens_saved, payload["tokens_saved"])

        admin_user = User.objects.create_superuser(
            username="prompt_archive_admin",
            email="prompt_archive_admin@example.com",
            password="secret",
        )
        request = RequestFactory().get("/")
        request.user = admin_user
        admin_view = PersistentAgentPromptArchiveAdmin(PersistentAgentPromptArchive, django_admin.site)
        response = admin_view.download_view(request, archive_row.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn(".json", response["Content-Disposition"])
        downloaded_bytes = b"".join(response.streaming_content)
        self.assertEqual(downloaded_bytes, decompressed)

    def test_prompt_archive_links_to_step(self):
        """Running the agent loop should attach the prompt archive to the first generated step."""
        response_message = MagicMock()
        response_message.tool_calls = None
        response_message.content = "Reasoning output"
        response_choice = MagicMock(message=response_message)
        response = MagicMock()
        response.choices = [response_choice]
        response.model_extra = {
            "usage": MagicMock(
                prompt_tokens=12,
                completion_tokens=6,
                total_tokens=18,
                prompt_tokens_details=MagicMock(cached_tokens=0),
            )
        }

        token_usage = {
            "prompt_tokens": 12,
            "completion_tokens": 6,
            "total_tokens": 18,
            "model": "mock-model",
            "provider": "mock-provider",
            "cached_tokens": 0,
        }

        with patch('api.agent.core.prompt_context.ensure_steps_compacted'), \
             patch('api.agent.core.prompt_context.ensure_comms_compacted'), \
             patch('api.agent.core.prompt_context.get_llm_config_with_failover', return_value=[("mock", "mock-model", {})]):
            with patch('api.agent.core.event_processing._completion_with_failover', return_value=(response, token_usage)):
                from api.agent.core import event_processing as ep
                with patch.object(ep, 'MAX_AGENT_LOOP_ITERATIONS', 1):
                    _run_agent_loop(self.agent, is_first_run=False)

        archive = PersistentAgentPromptArchive.objects.get(agent=self.agent)
        self.assertIsNotNone(archive.step, "Prompt archive should be linked to a step")
        linked_archive = PersistentAgentPromptArchive.objects.get(step=archive.step)
        self.assertEqual(linked_archive.id, archive.id)
        self.assertIsNotNone(archive.step.completion)
        self.assertEqual(archive.step.completion.prompt_tokens, token_usage["prompt_tokens"])

    def test_agent_loop_passes_preferred_provider(self):
        """Agent loop should forward the preferred provider returned by the helper."""
        response_message = MagicMock()
        response_message.tool_calls = None
        response_message.content = "Reasoning output"
        response_choice = MagicMock(message=response_message)
        response = MagicMock()
        response.choices = [response_choice]
        response.model_extra = {
            "usage": MagicMock(
                prompt_tokens=12,
                completion_tokens=6,
                total_tokens=18,
                prompt_tokens_details=MagicMock(cached_tokens=0),
            )
        }
        token_usage = {
            "prompt_tokens": 12,
            "completion_tokens": 6,
            "total_tokens": 18,
            "model": "mock-model",
            "provider": "mock-provider",
            "cached_tokens": 0,
        }
        with patch('api.agent.core.prompt_context.ensure_steps_compacted'), \
             patch('api.agent.core.prompt_context.ensure_comms_compacted'), \
             patch('api.agent.core.prompt_context.get_llm_config_with_failover', return_value=[("mock", "mock-model", {})]), \
             patch('api.agent.core.event_processing._get_recent_preferred_config', return_value=("mock", "mock-model")) as mock_helper, \
             patch('api.agent.core.event_processing._completion_with_failover', return_value=(response, token_usage)) as mock_completion:
            from api.agent.core import event_processing as ep
            with patch.object(ep, 'MAX_AGENT_LOOP_ITERATIONS', 1):
                _run_agent_loop(self.agent, is_first_run=False, run_sequence_number=3)

        mock_helper.assert_called_once_with(agent=self.agent, run_sequence_number=3)
        call_kwargs = mock_completion.call_args.kwargs
        self.assertEqual(call_kwargs["preferred_config"], ("mock", "mock-model"))

    def test_agent_loop_skips_preference_on_second_run(self):
        """Agent loop should not request preferred configs on the agent's second run."""
        response_message = MagicMock()
        response_message.tool_calls = None
        response_message.content = "Reasoning output"
        response_choice = MagicMock(message=response_message)
        response = MagicMock()
        response.choices = [response_choice]
        response.model_extra = {}
        token_usage = {
            "model": "mock-model",
            "provider": "mock-provider",
        }
        with patch('api.agent.core.prompt_context.ensure_steps_compacted'), \
             patch('api.agent.core.prompt_context.ensure_comms_compacted'), \
             patch('api.agent.core.prompt_context.get_llm_config_with_failover', return_value=[("mock", "mock-model", {})]), \
             patch('api.agent.core.event_processing._get_recent_preferred_config', return_value=None) as mock_helper, \
             patch('api.agent.core.event_processing._completion_with_failover', return_value=(response, token_usage)) as mock_completion:
            from api.agent.core import event_processing as ep
            with patch.object(ep, 'MAX_AGENT_LOOP_ITERATIONS', 1):
                _run_agent_loop(self.agent, is_first_run=False, run_sequence_number=2)

        mock_helper.assert_called_once_with(agent=self.agent, run_sequence_number=2)
        call_kwargs = mock_completion.call_args.kwargs
        self.assertIsNone(call_kwargs["preferred_config"])

    def test_completion_record_keeps_model_when_usage_missing(self):
        """PersistentAgentCompletion should store provider/model even if usage isn't provided."""
        response_message = MagicMock()
        response_message.tool_calls = None
        response_message.content = "Reasoning output"
        response_choice = MagicMock(message=response_message)
        response = MagicMock()
        response.choices = [response_choice]
        response.model_extra = {}

        token_usage = {
            "model": "mock-model",
            "provider": "mock-provider",
        }

        with patch('api.agent.core.prompt_context.ensure_steps_compacted'), \
             patch('api.agent.core.prompt_context.ensure_comms_compacted'), \
             patch('api.agent.core.prompt_context.get_llm_config_with_failover', return_value=[("mock", "mock-model", {})]), \
             patch('api.agent.core.event_processing._completion_with_failover', return_value=(response, token_usage)):
            from api.agent.core import event_processing as ep
            with patch.object(ep, 'MAX_AGENT_LOOP_ITERATIONS', 1):
                _run_agent_loop(self.agent, is_first_run=False)

        completion = PersistentAgentCompletion.objects.get(agent=self.agent)
        self.assertEqual(completion.llm_model, "mock-model")
        self.assertEqual(completion.llm_provider, "mock-provider")

    def test_agent_loop_filters_enable_database_from_tools(self):
        """LLM tool payload should hide enable_database once sqlite_batch is enabled."""
        enable_tools(self.agent, ["sqlite_batch"])

        response_message = MagicMock()
        response_message.tool_calls = None
        response_message.content = "Reasoning output"
        response_choice = MagicMock(message=response_message)
        response = MagicMock()
        response.choices = [response_choice]
        response.model_extra = {}
        token_usage = {"model": "mock-model", "provider": "mock-provider"}

        with patch('api.agent.core.event_processing.build_prompt_context', return_value=([{"role": "system", "content": "sys"}], 1000, None)), \
             patch('api.agent.core.event_processing.get_llm_config_with_failover', return_value=[("mock", "mock-model", {})]), \
             patch('api.agent.core.event_processing._completion_with_failover', return_value=(response, token_usage)) as mock_completion:
            from api.agent.core import event_processing as ep
            with patch.object(ep, 'MAX_AGENT_LOOP_ITERATIONS', 1):
                _run_agent_loop(self.agent, is_first_run=False)

        passed_tools = mock_completion.call_args.kwargs["tools"]
        tool_names = [
            entry.get("function", {}).get("name")
            for entry in passed_tools
            if isinstance(entry, dict)
        ]
        self.assertNotIn("enable_database", tool_names)
        self.assertIn("sqlite_batch", tool_names)


@tag("batch_event_processing")
class AgentRunSequenceHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sequence_tester@example.com",
            email="sequence_tester@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="SeqBA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="SeqAgent",
            charter="Test run sequence helper",
            browser_use_agent=self.browser_agent,
        )

    def test_completed_run_count_ignores_non_processing_steps(self):
        """Helper should ignore credit gate PROCESS_EVENTS system steps."""
        skipped_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Skipped due to credits",
        )
        PersistentAgentSystemStep.objects.create(
            step=skipped_step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            notes="credit_insufficient",
        )

        self.assertEqual(_get_completed_process_run_count(self.agent), 0)

        run_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Process events",
        )
        PersistentAgentSystemStep.objects.create(
            step=run_step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            notes="simplified",
        )

        self.assertEqual(_get_completed_process_run_count(self.agent), 1)

@tag("batch_event_processing")
class CronTriggerTaskTests(TestCase):
    """Unit tests for the cron trigger task."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="cron_tester@example.com",
            email="cron_tester@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="CronBA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="CronAgent",
            charter="cron test agent",
            browser_use_agent=self.browser_agent,
        )

    @patch('api.agent.tasks.process_events.process_agent_events')
    def test_cron_trigger_task_creates_trigger_record(self, mock_process_events):
        """Test that process_agent_cron_trigger_task creates the cron trigger record."""
        cron_expression = "@daily"
        
        # Verify no cron triggers exist initially
        self.assertEqual(PersistentAgentCronTrigger.objects.count(), 0)
        
        # Call the cron trigger task
        process_agent_cron_trigger_task(str(self.agent.id), cron_expression)
        
        # Verify cron trigger was created
        self.assertEqual(PersistentAgentCronTrigger.objects.count(), 1)
        
        cron_trigger = PersistentAgentCronTrigger.objects.first()
        self.assertEqual(cron_trigger.cron_expression, cron_expression)
        self.assertEqual(cron_trigger.step.agent, self.agent)
        self.assertEqual(cron_trigger.step.description, f"Cron trigger: {cron_expression}")
        
        # Verify process_agent_events was called
        mock_process_events.assert_called_once_with(str(self.agent.id))

    @patch('api.agent.tasks.process_events.process_agent_events')
    def test_cron_trigger_task_with_complex_expression(self, mock_process_events):
        """Test that cron trigger task works with complex cron expressions."""
        cron_expression = "0 9 * * 1-5"  # Weekdays at 9am
        
        # Call the cron trigger task
        process_agent_cron_trigger_task(str(self.agent.id), cron_expression)
        
        # Verify cron trigger was created with correct expression
        cron_trigger = PersistentAgentCronTrigger.objects.first()
        self.assertEqual(cron_trigger.cron_expression, cron_expression)
        
        # Verify process_agent_events was called
        mock_process_events.assert_called_once_with(str(self.agent.id))

    @patch('api.agent.tasks.process_events.logger')
    @patch('api.agent.tasks.process_events.process_agent_events')
    def test_cron_trigger_task_logs_quota_validation_as_info(self, mock_process_events, mock_logger):
        """Quota ValidationErrors should be logged as info instead of failing the task."""
        quota_error = ValidationError(
            {"quota": ["Task quota exceeded. You have no remaining task credits and no active subscription."]}
        )
        mock_process_events.side_effect = quota_error
        cron_expression = "@hourly"

        process_agent_cron_trigger_task(str(self.agent.id), cron_expression)

        self.assertEqual(PersistentAgentCronTrigger.objects.count(), 1)
        mock_process_events.assert_called_once_with(str(self.agent.id))
        mock_logger.info.assert_any_call(
            "Skipping cron trigger for agent %s due to task quota: %s",
            str(self.agent.id),
            quota_error,
        )

    @patch('api.agent.tasks.process_events._remove_orphaned_celery_beat_task')
    @patch('api.agent.tasks.process_events.process_agent_events')
    def test_cron_trigger_task_handles_nonexistent_agent(self, mock_process_events, mock_remove_beat_task):
        """Test that cron trigger task handles non-existent agents by removing orphaned beat tasks."""
        # Use a non-existent agent ID
        nonexistent_agent_id = "00000000-0000-0000-0000-000000000000"
        cron_expression = "@daily"
        
        # Verify no cron triggers exist initially
        initial_count = PersistentAgentCronTrigger.objects.count()
        
        # Call the cron trigger task with non-existent agent ID
        process_agent_cron_trigger_task(nonexistent_agent_id, cron_expression)
        
        # Verify no cron trigger was created
        self.assertEqual(PersistentAgentCronTrigger.objects.count(), initial_count)
        
        # Verify process_agent_events was NOT called
        mock_process_events.assert_not_called()
        
        # Verify orphaned beat task removal was called
        mock_remove_beat_task.assert_called_once_with(nonexistent_agent_id)

    @patch('redbeat.RedBeatSchedulerEntry.from_key')
    @patch('celery.current_app')
    def test_remove_orphaned_celery_beat_task_success(self, mock_celery_app, mock_from_key):
        """Test successful removal of orphaned Celery beat task."""
        # Setup mocks
        mock_entry = mock_from_key.return_value
        
        agent_id = "test-agent-id"
        expected_task_name = f"persistent-agent-schedule:{agent_id}"
        expected_key = f"redbeat:{expected_task_name}"
        
        # Call the function
        with patch('api.agent.tasks.process_events.logger') as mock_logger:
            _remove_orphaned_celery_beat_task(agent_id)
        
        # Verify RedBeatSchedulerEntry.from_key was called with correct parameters
        mock_from_key.assert_called_once_with(expected_key, app=mock_celery_app)
        
        # Verify entry.delete() was called
        mock_entry.delete.assert_called_once()
        
        # Verify success was logged
        mock_logger.info.assert_called_once_with(
            "Removed orphaned Celery Beat task for non-existent agent %s", agent_id
        )

    @patch('redbeat.RedBeatSchedulerEntry.from_key')
    @patch('celery.current_app')
    def test_remove_orphaned_celery_beat_task_key_error(self, mock_celery_app, mock_from_key):
        """Test handling of KeyError when beat task doesn't exist."""
        # Setup mocks - simulate KeyError when task doesn't exist
        mock_from_key.side_effect = KeyError("Task not found")
        
        agent_id = "test-agent-id"
        
        # Call the function
        with patch('api.agent.tasks.process_events.logger') as mock_logger:
            _remove_orphaned_celery_beat_task(agent_id)
        
        # Verify appropriate message was logged
        mock_logger.info.assert_called_once_with(
            "No Celery Beat task found for non-existent agent %s", agent_id
        )

    @patch('redbeat.RedBeatSchedulerEntry.from_key')
    @patch('celery.current_app')
    def test_remove_orphaned_celery_beat_task_general_error(self, mock_celery_app, mock_from_key):
        """Test handling of general exceptions during beat task removal."""
        # Setup mocks - simulate general exception
        mock_from_key.side_effect = Exception("Redis connection failed")
        
        agent_id = "test-agent-id"
        
        # Call the function
        with patch('api.agent.tasks.process_events.logger') as mock_logger:
            _remove_orphaned_celery_beat_task(agent_id)
        
        # Verify error was logged
        mock_logger.error.assert_called_once_with(
            "Error removing orphaned Celery Beat task for agent %s: %s", 
            agent_id, 
            mock_from_key.side_effect
        ) 


@tag("batch_event_processing")
class UpdateScheduleMinimumIntervalTests(TestCase):
    """Unit tests for _execute_update_schedule minimum interval validation."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="schedule_tester@example.com",
            email="schedule_tester@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="ScheduleBA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="ScheduleAgent",
            charter="test schedule updates",
            browser_use_agent=self.browser_agent,
            schedule="@daily",  # Start with a valid schedule
        )

    def test_valid_schedules_accepted(self):
        """Test that schedules meeting minimum interval are accepted."""
        valid_schedules = [
            "@daily",          # Once per day
            "@hourly",         # Once per hour
            "@every 30m",      # Exactly 30 minutes
            "@every 1h",       # 1 hour
            "@every 2h",       # 2 hours
            "0 */2 * * *",     # Every 2 hours (cron)
            "0 0 * * *",       # Daily at midnight (cron)
            "0 8,20 * * *",    # Twice daily, 12 hours apart (cron)
        ]
        
        for schedule in valid_schedules:
            with self.subTest(schedule=schedule):
                original_schedule = self.agent.schedule
                result = _execute_update_schedule(self.agent, {"new_schedule": schedule})
                
                self.assertEqual(result["status"], "ok")
                self.agent.refresh_from_db()
                self.assertEqual(self.agent.schedule, schedule)
                
                # Reset for next test
                self.agent.schedule = original_schedule
                self.agent.save()

    def test_too_frequent_interval_schedules_rejected(self):
        """Test that interval schedules more frequent than 30 minutes are rejected."""
        too_frequent_schedules = [
            "@every 29m",      # 29 minutes - just under limit
            "@every 15m",      # 15 minutes
            "@every 5m",       # 5 minutes
            "@every 1m",       # 1 minute
            "@every 30s",      # 30 seconds
            "@every 1h 29m",   # 1 hour 29 minutes - just under 90 minutes (1.5 hours), but this is > 30m so should be OK
        ]
        
        # Note: "@every 1h 29m" is actually 89 minutes, which is > 30 minutes, so it should be accepted
        # Let me correct the test cases
        actually_too_frequent = [
            "@every 29m",      # 29 minutes - just under limit
            "@every 15m",      # 15 minutes
            "@every 5m",       # 5 minutes
            "@every 1m",       # 1 minute
            "@every 30s",      # 30 seconds
        ]
        
        for schedule in actually_too_frequent:
            with self.subTest(schedule=schedule):
                original_schedule = self.agent.schedule
                result = _execute_update_schedule(self.agent, {"new_schedule": schedule})
                
                self.assertEqual(result["status"], "error")
                self.assertIn("too frequent", result["message"])
                self.assertIn("1800 seconds", result["message"])  # 30 minutes in seconds
                
                # Verify schedule wasn't changed
                self.agent.refresh_from_db()
                self.assertEqual(self.agent.schedule, original_schedule)

    def test_too_frequent_cron_schedules_rejected(self):
        """Test that cron schedules running more than twice per hour are rejected."""
        too_frequent_cron_schedules = [
            "*/10 * * * *",    # Every 10 minutes (6 times per hour)
            "*/15 * * * *",    # Every 15 minutes (4 times per hour)
            "*/20 * * * *",    # Every 20 minutes (3 times per hour)
            "0,20,40 * * * *", # At 0, 20, 40 minutes (3 times per hour)
            "0,15,30,45 * * * *", # Every 15 minutes (4 times per hour)
        ]
        
        for schedule in too_frequent_cron_schedules:
            with self.subTest(schedule=schedule):
                original_schedule = self.agent.schedule
                result = _execute_update_schedule(self.agent, {"new_schedule": schedule})
                
                self.assertEqual(result["status"], "error")
                self.assertIn("too frequent", result["message"])
                
                # Verify schedule wasn't changed
                self.agent.refresh_from_db()
                self.assertEqual(self.agent.schedule, original_schedule)

    def test_edge_case_cron_schedules(self):
        """Test edge cases for cron schedule validation."""
        # Test exactly 2 executions per hour with various intervals
        edge_cases = [
            ("0,30 * * * *", True),    # Every 30 minutes (2 times per hour) - should be accepted
            ("0,31 * * * *", False),   # At 0 and 31 minutes (29 minute gap from 31 to 0) - should be rejected
            ("15,45 * * * *", True),   # At 15 and 45 minutes (30 minute gap) - should be accepted
            ("10,35 * * * *", False),  # At 10 and 35 minutes (25 minute gap) - should be rejected
            ("5,40 * * * *", False),   # At 5 and 40 minutes (35 minute gap first, then 25 minute gap) - should be rejected
        ]
        
        for schedule, should_be_valid in edge_cases:
            with self.subTest(schedule=schedule, should_be_valid=should_be_valid):
                original_schedule = self.agent.schedule
                result = _execute_update_schedule(self.agent, {"new_schedule": schedule})
                
                if should_be_valid:
                    self.assertEqual(result["status"], "ok")
                    self.agent.refresh_from_db()
                    self.assertEqual(self.agent.schedule, schedule)
                    # Reset for next test
                    self.agent.schedule = original_schedule
                    self.agent.save()
                else:
                    self.assertEqual(result["status"], "error")
                    self.assertIn("too frequent", result["message"])
                    # Verify schedule wasn't changed
                    self.agent.refresh_from_db()
                    self.assertEqual(self.agent.schedule, original_schedule)

    def test_empty_and_null_schedules(self):
        """Test that empty and null schedules are accepted (disables scheduling)."""
        empty_schedules = [
            None,
            "",
            "   ",  # Whitespace only
        ]
        
        for schedule in empty_schedules:
            with self.subTest(schedule=schedule):
                result = _execute_update_schedule(self.agent, {"new_schedule": schedule})
                
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["message"], "Schedule has been disabled.")
                
                self.agent.refresh_from_db()
                self.assertIsNone(self.agent.schedule)
                
                # Reset for next test
                self.agent.schedule = "@daily"
                self.agent.save()

    def test_invalid_schedule_format_rejected(self):
        """Test that invalid schedule formats are rejected without affecting the agent."""
        invalid_schedules = [
            "invalid schedule",
            "@reboot",
            "@every 5x",
            "60 * * * *",      # Invalid minute value
            "* 25 * * *",      # Invalid hour value
        ]
        
        for schedule in invalid_schedules:
            with self.subTest(schedule=schedule):
                original_schedule = self.agent.schedule
                result = _execute_update_schedule(self.agent, {"new_schedule": schedule})
                
                self.assertEqual(result["status"], "error")
                self.assertIn("Invalid schedule format", result["message"])
                
                # Verify schedule wasn't changed
                self.agent.refresh_from_db()
                self.assertEqual(self.agent.schedule, original_schedule)

    def test_boundary_30_minute_interval(self):
        """Test that exactly 30 minute intervals are accepted."""
        result = _execute_update_schedule(self.agent, {"new_schedule": "@every 30m"})
        
        self.assertEqual(result["status"], "ok")
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.schedule, "@every 30m")

    def test_just_under_30_minute_interval(self):
        """Test that intervals just under 30 minutes are rejected."""
        result = _execute_update_schedule(self.agent, {"new_schedule": "@every 29m 59s"})
        
        self.assertEqual(result["status"], "error")
        self.assertIn("too frequent", result["message"])
        
        # Verify schedule wasn't changed
        original_schedule = self.agent.schedule
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.schedule, original_schedule)

    def test_complex_interval_combinations(self):
        """Test complex interval combinations that should be valid."""
        valid_complex_intervals = [
            "@every 1h 30m",   # 90 minutes
            "@every 2h 15m",   # 135 minutes  
            "@every 30m 30s",  # 30.5 minutes - should be valid
        ]
        
        for schedule in valid_complex_intervals:
            with self.subTest(schedule=schedule):
                original_schedule = self.agent.schedule
                result = _execute_update_schedule(self.agent, {"new_schedule": schedule})
                
                self.assertEqual(result["status"], "ok")
                self.agent.refresh_from_db()
                self.assertEqual(self.agent.schedule, schedule)
                
                # Reset for next test
                self.agent.schedule = original_schedule
                self.agent.save()


@tag("batch_event_processing")
class SearchWebCreditConsumptionTests(TestCase):
    """Unit-tests for search_web tool credit consumption."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="tester@example.com",
            email="tester@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="BA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Search-Agent",
            charter="Search things",
            browser_use_agent=self.browser_agent,
            created_at=timezone.now(),
        )

    def _create_task_credit(self, credits=10, credits_used=0, additional_task=False):
        """Helper to create a TaskCredit for testing."""
        from api.models import TaskCredit
        return TaskCredit.objects.create(
            user=self.user,
            credits=credits,
            credits_used=credits_used,
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=30),
            additional_task=additional_task,
            plan=PlanNamesChoices.FREE,
            grant_type=GrantTypeChoices.PROMO
        )

    @patch('api.agent.core.event_processing.settings.EXA_SEARCH_API_KEY', 'test_key')
    @patch('exa_py.Exa')
    def test_search_web_consumes_credit_when_available(self, mock_exa):
        """Test that search_web consumes a task credit when credits are available."""
        # Clear any existing credits and create a specific one
        from api.models import TaskCredit
        TaskCredit.objects.filter(user=self.user).delete()
        credit = self._create_task_credit(credits=5, credits_used=2)
        
        # Mock Exa search response
        mock_search_result = type('SearchResult', (), {
            'results': [
                type('Result', (), {
                    'title': 'Test Result',
                    'url': 'https://example.com',
                    'published_date': '2024-01-01',
                    'text': 'Test content'
                })()
            ]
        })()
        
        mock_exa.return_value.search_and_contents.return_value = mock_search_result
        
        result = _execute_search_web(self.agent, {"query": "test search"})
        
        # Verify search succeeded
        self.assertEqual(result["status"], "ok")
        self.assertIn("<title>Test Result</title>", result["result"])
        self.assertIn("<url>https://example.com</url>", result["result"])
        self.assertIn("<content>", result["result"])
        
        # Credit consumption is currently disabled; ensure credits remain unchanged
        credit.refresh_from_db()
        self.assertEqual(credit.credits_used, 2)

    @patch('api.agent.core.event_processing.settings.EXA_SEARCH_API_KEY', 'test_key')
    @patch('exa_py.Exa')
    @patch('util.subscription_helper.get_active_subscription')
    @patch('util.subscription_helper.allow_and_has_extra_tasks')
    def test_search_web_consumes_additional_credit_for_paid_plan(self, mock_extra_tasks, mock_subscription, mock_exa):
        """Test that search_web consumes additional credit for paid plans when regular credits exhausted."""
        # Clear existing credits and create fully used ones
        from api.models import TaskCredit
        TaskCredit.objects.filter(user=self.user).delete()
        self._create_task_credit(credits=1, credits_used=1)  # Fully used
        
        # Mock paid subscription and extra tasks allowed
        mock_subscription.return_value = type('Subscription', (), {
            'id': 'sub_123',
            'stripe_data': {'id': 'sub_123', 'plan': {'product': {'name': 'Pro Plan'}}}
        })()
        mock_extra_tasks.return_value = 5  # Allow extra tasks
        
        # Mock Exa search response
        mock_search_result = type('SearchResult', (), {
            'results': [
                type('Result', (), {
                    'title': 'Test Result',
                    'url': 'https://example.com',
                    'published_date': '2024-01-01',
                    'text': 'Test content'
                })()
            ]
        })()
        
        mock_exa.return_value.search_and_contents.return_value = mock_search_result
        
        result = _execute_search_web(self.agent, {"query": "test search"})
        
        # Credit checks disabled, search should succeed even when extra tasks exhausted
        self.assertEqual(result["status"], "ok")
        
        # Additional credit consumption is currently disabled; ensure none created
        additional_credits = TaskCredit.objects.filter(user=self.user, additional_task=True)
        self.assertEqual(additional_credits.count(), 0)

    @patch('api.agent.core.event_processing.settings.EXA_SEARCH_API_KEY', 'test_key')
    @patch('exa_py.Exa')
    @patch('util.subscription_helper.get_active_subscription')
    def test_search_web_fails_without_credits_or_subscription(self, mock_subscription, mock_exa):
        """Test that search_web succeeds when credit checks disabled, even without credits or subscription."""
        from api.models import TaskCredit
        TaskCredit.objects.filter(user=self.user).delete()


        mock_subscription.return_value = None

        mock_search_result = type('SearchResult', (), {
            'results': [
                type('Result', (), {
                    'title': 'Test Result',
                    'url': 'https://example.com',
                    'published_date': '2024-01-01',
                    'text': 'Test content'
                })()
            ]
        })()
        mock_exa.return_value.search_and_contents.return_value = mock_search_result

        result = _execute_search_web(self.agent, {"query": "test search"})
        self.assertEqual(result["status"], "ok")
        self.assertIn("<title>Test Result</title>", result["result"])
        self.assertIn("<query>test search</query>", result["result"])

    @patch('api.agent.core.event_processing.settings.EXA_SEARCH_API_KEY', 'test_key')
    @patch('exa_py.Exa')
    @patch('util.subscription_helper.get_active_subscription')
    @patch('util.subscription_helper.allow_and_has_extra_tasks')
    def test_search_web_fails_without_credits_and_extra_tasks_exhausted(self, mock_extra_tasks, mock_subscription, mock_exa):
        """Test that search_web succeeds when extra tasks exhausted but credit checks disabled."""
        from api.models import TaskCredit
        TaskCredit.objects.filter(user=self.user).delete()
        self._create_task_credit(credits=1, credits_used=1)

        mock_subscription.return_value = type('Subscription', (), {
            'id': 'sub_123',
            'stripe_data': {'id': 'sub_123', 'plan': {'product': {'name': 'Pro Plan'}}}
        })()
        mock_extra_tasks.return_value = 0

        mock_search_result = type('SearchResult', (), {
            'results': [
                type('Result', (), {
                    'title': 'Test Result',
                    'url': 'https://example.com',
                    'published_date': '2024-01-01',
                    'text': 'Test content'
                })()
            ]
        })()
        mock_exa.return_value.search_and_contents.return_value = mock_search_result

        result = _execute_search_web(self.agent, {"query": "test search"})
        self.assertEqual(result["status"], "ok")
        self.assertIn("<title>Test Result</title>", result["result"])
        self.assertIn("<search_results>", result["result"])

    def test_search_web_fails_without_query(self):
        """Test that search_web fails when no query is provided."""
        result = _execute_search_web(self.agent, {})
        
        self.assertEqual(result["status"], "error")
        self.assertIn("Missing required parameter: query", result["message"]) 


@tag("batch_event_processing")
class HttpRequestSecretPlaceholderTests(TestCase):
    """Unit tests for http_request tool secret placeholder substitution."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="http_tester@example.com", 
            email="http_tester@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="HttpBA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="HttpAgent",
            charter="HTTP test agent",
            browser_use_agent=self.browser_agent,
        )

    def _create_secret(self, key, value, domain="*", name=None):
        """Helper to create a secret for the agent."""
        secret = PersistentAgentSecret(
            agent=self.agent,
            domain_pattern=domain,
            name=name or key,
            key=key
        )
        secret.set_value(value)
        secret.save()
        return secret

    @patch('requests.request')
    @patch('api.agent.tools.http_request.select_proxy_for_persistent_agent')
    def test_secret_substitution_in_headers(self, mock_proxy, mock_request):
        """Test that secret placeholders in headers are properly substituted."""
        # Create a test secret
        self._create_secret("api_key", "secret-api-key-value")
        
        # Mock proxy and response
        mock_proxy.return_value = type('ProxyServer', (), {'proxy_url': 'http://proxy:8080'})()
        mock_response = type('Response', (), {
            'status_code': 200,
            'headers': {'Content-Type': 'application/json'},
            'iter_content': lambda self, chunk_size: [b'{"success": true}'],
            'close': lambda self: None
        })()
        mock_request.return_value = mock_response
        
        # Test with placeholder in headers
        params = {
            "method": "GET",
            "url": "https://api.example.com/data",
            "headers": {
                "Authorization": "Bearer <<<api_key>>>",
                "X-API-Key": "<<<api_key>>>"
            }
        }
        
        result = _execute_http_request(self.agent, params)
        
        # Verify request was made
        self.assertEqual(result["status"], "ok")
        mock_request.assert_called_once()
        
        # Verify headers were substituted correctly
        call_args = mock_request.call_args
        headers = call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer secret-api-key-value")
        self.assertEqual(headers["X-API-Key"], "secret-api-key-value")

    @patch('requests.request')
    @patch('api.agent.tools.http_request.select_proxy_for_persistent_agent')
    def test_secret_substitution_in_url(self, mock_proxy, mock_request):
        """Test that secret placeholders in URL are properly substituted."""
        # Create test secrets
        self._create_secret("base_url", "https://api.secret.com")
        self._create_secret("api_version", "v2")
        
        # Mock proxy and response
        mock_proxy.return_value = type('ProxyServer', (), {'proxy_url': 'http://proxy:8080'})()
        mock_response = type('Response', (), {
            'status_code': 200,
            'headers': {'Content-Type': 'text/plain'},
            'iter_content': lambda self, chunk_size: [b'success'],
            'close': lambda self: None
        })()
        mock_request.return_value = mock_response
        
        # Test with placeholder in URL
        params = {
            "method": "GET",
            "url": "<<<base_url>>>/<<<api_version>>>/endpoint"
        }
        
        result = _execute_http_request(self.agent, params)
        
        # Verify request was made with substituted URL
        self.assertEqual(result["status"], "ok")
        mock_request.assert_called_once()
        
        call_args = mock_request.call_args
        self.assertEqual(call_args[0][1], "https://api.secret.com/v2/endpoint")

    @patch('requests.request')
    @patch('api.agent.tools.http_request.select_proxy_for_persistent_agent')
    def test_http_request_uses_agent_preferred_proxy(self, mock_proxy_selector, mock_request):
        """Preferred proxies assigned to the browser agent should be reused for HTTP requests."""
        proxy = ProxyServer.objects.create(
            name="Dedicated",
            proxy_type=ProxyServer.ProxyType.HTTP,
            host="dedicated.proxy",
            port=8080,
            username="user",
            password="pass",
            is_active=True,
        )
        self.browser_agent.preferred_proxy = proxy
        self.browser_agent.save(update_fields=["preferred_proxy"])

        def _selector(agent, *args, **kwargs):
            self.assertEqual(agent.preferred_proxy, proxy)
            return proxy

        mock_proxy_selector.side_effect = _selector
        mock_response = type('Response', (), {
            'status_code': 200,
            'headers': {'Content-Type': 'text/plain'},
            'iter_content': lambda self, chunk_size: [b'ok'],
            'close': lambda self: None,
        })()
        mock_request.return_value = mock_response

        params = {
            "method": "GET",
            "url": "https://api.example.com/health",
        }

        result = _execute_http_request(self.agent, params)

        self.assertEqual(result["status"], "ok")
        mock_proxy_selector.assert_called_once()
        proxies = mock_request.call_args[1]["proxies"]
        self.assertEqual(proxies, {"http": proxy.proxy_url, "https": proxy.proxy_url})

    @patch('requests.request')
    @patch('api.agent.tools.http_request.select_proxy_for_persistent_agent')
    def test_secret_substitution_in_body_string(self, mock_proxy, mock_request):
        """Test that secret placeholders in body string are properly substituted."""
        # Create test secrets
        self._create_secret("username", "test_user")
        self._create_secret("password", "secret_pass")
        
        # Mock proxy and response
        mock_proxy.return_value = type('ProxyServer', (), {'proxy_url': 'http://proxy:8080'})()
        mock_response = type('Response', (), {
            'status_code': 200,
            'headers': {'Content-Type': 'application/json'},
            'iter_content': lambda self, chunk_size: [b'{"login": "success"}'],
            'close': lambda self: None
        })()
        mock_request.return_value = mock_response
        
        # Test with placeholders in body
        params = {
            "method": "POST",
            "url": "https://api.example.com/login",
            "body": '{"username": "<<<username>>>", "password": "<<<password>>>"}'
        }
        
        result = _execute_http_request(self.agent, params)
        
        # Verify request was made with substituted body
        self.assertEqual(result["status"], "ok")
        mock_request.assert_called_once()
        
        call_args = mock_request.call_args
        expected_body = '{"username": "test_user", "password": "secret_pass"}'
        self.assertEqual(call_args[1]["data"], expected_body)

    @patch('requests.request')
    @patch('api.agent.tools.http_request.select_proxy_for_persistent_agent')
    def test_secret_substitution_in_body_dict(self, mock_proxy, mock_request):
        """Test that secret placeholders in body dict are properly substituted and JSON-encoded."""
        # Create test secret
        self._create_secret("client_secret", "super-secret-value")
        
        # Mock proxy and response
        mock_proxy.return_value = type('ProxyServer', (), {'proxy_url': 'http://proxy:8080'})()
        mock_response = type('Response', (), {
            'status_code': 200,
            'headers': {'Content-Type': 'application/json'},
            'iter_content': lambda self, chunk_size: [b'{"token": "abc123"}'],
            'close': lambda self: None
        })()
        mock_request.return_value = mock_response
        
        # Test with dict body containing placeholders
        params = {
            "method": "POST",
            "url": "https://oauth.example.com/token",
            "body": {
                "grant_type": "client_credentials",
                "client_secret": "<<<client_secret>>>",
                "scope": "read"
            }
        }
        
        result = _execute_http_request(self.agent, params)
        
        # Verify request was made
        self.assertEqual(result["status"], "ok")
        mock_request.assert_called_once()
        
        # Verify body was substituted and JSON-encoded
        call_args = mock_request.call_args
        import json
        body_data = json.loads(call_args[1]["data"])
        self.assertEqual(body_data["client_secret"], "super-secret-value")
        self.assertEqual(body_data["grant_type"], "client_credentials")
        self.assertEqual(body_data["scope"], "read")

    @patch('requests.request')
    @patch('api.agent.tools.http_request.select_proxy_for_persistent_agent')
    def test_secret_substitution_with_whitespace(self, mock_proxy, mock_request):
        """Test that secret placeholders with whitespace are properly handled."""
        # Create test secret
        self._create_secret("api_token", "token-with-spaces")
        
        # Mock proxy and response
        mock_proxy.return_value = type('ProxyServer', (), {'proxy_url': 'http://proxy:8080'})()
        mock_response = type('Response', (), {
            'status_code': 200,
            'headers': {'Content-Type': 'text/plain'},
            'iter_content': lambda self, chunk_size: [b'ok'],
            'close': lambda self: None
        })()
        mock_request.return_value = mock_response
        
        # Test with whitespace in placeholders
        params = {
            "method": "GET",
            "url": "https://api.example.com/data",
            "headers": {
                "Token": "<<<  api_token  >>>",  # Extra whitespace
                "Authorization": "Bearer <<<api_token>>>"  # No whitespace
            }
        }
        
        result = _execute_http_request(self.agent, params)
        
        # Verify both placeholders were substituted correctly
        self.assertEqual(result["status"], "ok")
        call_args = mock_request.call_args
        headers = call_args[1]["headers"]
        self.assertEqual(headers["Token"], "token-with-spaces")
        self.assertEqual(headers["Authorization"], "Bearer token-with-spaces")

    @patch('requests.request')
    @patch('api.agent.tools.http_request.select_proxy_for_persistent_agent')
    def test_nonexistent_secret_placeholder_unchanged(self, mock_proxy, mock_request):
        """Test that placeholders for nonexistent secrets are left unchanged."""
        # Create one secret but reference a different one
        self._create_secret("real_secret", "real_value")
        
        # Mock proxy and response
        mock_proxy.return_value = type('ProxyServer', (), {'proxy_url': 'http://proxy:8080'})()
        mock_response = type('Response', (), {
            'status_code': 200,
            'headers': {'Content-Type': 'text/plain'},
            'iter_content': lambda self, chunk_size: [b'ok'],
            'close': lambda self: None
        })()
        mock_request.return_value = mock_response
        
        # Test with nonexistent secret placeholder
        params = {
            "method": "GET",
            "url": "https://api.example.com/data",
            "headers": {
                "Real-Key": "<<<real_secret>>>",  # This should be replaced
                "Fake-Key": "<<<fake_secret>>>"   # This should remain unchanged
            }
        }
        
        result = _execute_http_request(self.agent, params)
        
        # Verify only real secret was substituted
        self.assertEqual(result["status"], "ok")
        call_args = mock_request.call_args
        headers = call_args[1]["headers"]
        self.assertEqual(headers["Real-Key"], "real_value")
        self.assertEqual(headers["Fake-Key"], "<<<fake_secret>>>")  # Unchanged

    @patch('requests.request')
    @patch('api.agent.tools.http_request.select_proxy_for_persistent_agent')
    def test_no_secrets_no_substitution(self, mock_proxy, mock_request):
        """Test that when agent has no secrets, placeholders remain unchanged."""
        # Don't create any secrets
        
        # Mock proxy and response
        mock_proxy.return_value = type('ProxyServer', (), {'proxy_url': 'http://proxy:8080'})()
        mock_response = type('Response', (), {
            'status_code': 200,
            'headers': {'Content-Type': 'text/plain'},
            'iter_content': lambda self, chunk_size: [b'ok'],
            'close': lambda self: None
        })()
        mock_request.return_value = mock_response
        
        # Test with placeholder when no secrets exist
        params = {
            "method": "GET",
            "url": "https://api.example.com/data",
            "headers": {
                "Authorization": "Bearer <<<api_key>>>"
            }
        }
        
        result = _execute_http_request(self.agent, params)
        
        # Verify placeholder remains unchanged
        self.assertEqual(result["status"], "ok")
        call_args = mock_request.call_args
        headers = call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer <<<api_key>>>")

    @patch('requests.request')
    @patch('api.agent.tools.http_request.select_proxy_for_persistent_agent')
    def test_complex_nested_substitution(self, mock_proxy, mock_request):
        """Test secret substitution in complex nested data structures."""
        # Create test secrets
        self._create_secret("auth_token", "nested-auth-token")
        self._create_secret("api_endpoint", "https://nested-api.com")
        
        # Mock proxy and response
        mock_proxy.return_value = type('ProxyServer', (), {'proxy_url': 'http://proxy:8080'})()
        mock_response = type('Response', (), {
            'status_code': 200,
            'headers': {'Content-Type': 'application/json'},
            'iter_content': lambda self, chunk_size: [b'{"result": "success"}'],
            'close': lambda self: None
        })()
        mock_request.return_value = mock_response
        
        # Test with complex nested body structure
        params = {
            "method": "POST",
            "url": "<<<api_endpoint>>>/webhook",
            "headers": {
                "Authorization": "Bearer <<<auth_token>>>",
                "Content-Type": "application/json"
            },
            "body": {
                "webhook": {
                    "url": "<<<api_endpoint>>>/callback",
                    "auth": {
                        "type": "bearer",
                        "token": "<<<auth_token>>>"
                    },
                    "events": ["user.created", "user.updated"]
                }
            }
        }
        
        result = _execute_http_request(self.agent, params)
        
        # Verify all substitutions worked correctly
        self.assertEqual(result["status"], "ok")
        call_args = mock_request.call_args
        
        # Check URL substitution
        self.assertEqual(call_args[0][1], "https://nested-api.com/webhook")
        
        # Check header substitution
        headers = call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer nested-auth-token")
        
        # Check body substitution (should be JSON-encoded)
        import json
        body_data = json.loads(call_args[1]["data"])
        self.assertEqual(body_data["webhook"]["url"], "https://nested-api.com/callback")
        self.assertEqual(body_data["webhook"]["auth"]["token"], "nested-auth-token")
        self.assertEqual(body_data["webhook"]["events"], ["user.created", "user.updated"])

    @patch('requests.request')
    @patch('api.agent.tools.http_request.select_proxy_for_persistent_agent')
    def test_whole_string_secret_key_substitution(self, mock_proxy, mock_request):
        """Test that a header value that exactly matches a secret key is substituted."""
        # Create test secret
        self._create_secret("bearer_token", "whole-string-token-value")
        
        # Mock proxy and response
        mock_proxy.return_value = type('ProxyServer', (), {'proxy_url': 'http://proxy:8080'})()
        mock_response = type('Response', (), {
            'status_code': 200,
            'headers': {'Content-Type': 'text/plain'},
            'iter_content': lambda self, chunk_size: [b'ok'],
            'close': lambda self: None
        })()
        mock_request.return_value = mock_response
        
        # Test with header value that exactly matches secret key
        params = {
            "method": "GET",
            "url": "https://api.example.com/data",
            "headers": {
                "Authorization": "bearer_token",  # Exact match to secret key
                "X-Token": "Bearer <<<bearer_token>>>"  # Regular placeholder
            }
        }
        
        result = _execute_http_request(self.agent, params)
        
        # Verify both substitutions worked
        self.assertEqual(result["status"], "ok")
        call_args = mock_request.call_args
        headers = call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], "whole-string-token-value")  # Whole string match
        self.assertEqual(headers["X-Token"], "Bearer whole-string-token-value")  # Placeholder match 

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    @patch('requests.request')
    @patch('api.agent.tools.http_request.select_proxy_for_persistent_agent')
    def test_allows_direct_http_request_without_proxy_in_community_mode(self, mock_proxy, mock_request):
        """Community mode should fall back to direct requests when no proxy exists."""
        mock_proxy.side_effect = RuntimeError("No proxies configured")

        mock_response = type('Response', (), {
            'status_code': 200,
            'headers': {'Content-Type': 'text/plain'},
            'iter_content': lambda self, chunk_size: [b'ok'],
            'close': lambda self: None
        })()
        mock_request.return_value = mock_response

        params = {
            "method": "GET",
            "url": "https://api.example.com/community",
        }

        result = _execute_http_request(self.agent, params)

        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["proxy_used"])

        call_args = mock_request.call_args
        self.assertNotIn("proxies", call_args[1])

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch('requests.request')
    @patch('api.agent.tools.http_request.select_proxy_for_persistent_agent')
    def test_requires_proxy_in_proprietary_mode(self, mock_proxy, mock_request):
        """Proprietary mode must fail if no proxy is available."""
        mock_proxy.side_effect = RuntimeError("No proxies configured")

        params = {
            "method": "GET",
            "url": "https://api.example.com/proprietary",
        }

        result = _execute_http_request(self.agent, params)

        self.assertEqual(result["status"], "error")
        self.assertIn("No proxy server available", result["message"])
        mock_request.assert_not_called()

@tag("batch_event_processing")
class PromptConfigFunctionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="prompt_limit_user@example.com",
            email="prompt_limit_user@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="PromptConfigBA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="PromptConfigAgent",
            charter="",
            browser_use_agent=self.browser_agent,
        )

    def _configure_limits(self):
        config, _ = PromptConfig.objects.get_or_create(singleton_id=1)
        config.standard_prompt_token_budget = 500
        config.premium_prompt_token_budget = 1000
        config.max_prompt_token_budget = 1500
        config.standard_message_history_limit = 3
        config.premium_message_history_limit = 7
        config.max_message_history_limit = 9
        config.standard_tool_call_history_limit = 4
        config.premium_tool_call_history_limit = 8
        config.max_tool_call_history_limit = 10
        config.save()
        invalidate_prompt_settings_cache()
        return config

    def test_limits_follow_configuration(self):
        config = self._configure_limits()

        with patch("api.agent.core.prompt_context.get_agent_llm_tier", return_value=AgentLLMTier.STANDARD):
            self.assertEqual(get_prompt_token_budget(self.agent), config.standard_prompt_token_budget)
            self.assertEqual(message_history_limit(self.agent), config.standard_message_history_limit)
            self.assertEqual(tool_call_history_limit(self.agent), config.standard_tool_call_history_limit)

        with patch("api.agent.core.prompt_context.get_agent_llm_tier", return_value=AgentLLMTier.PREMIUM):
            self.assertEqual(get_prompt_token_budget(self.agent), config.premium_prompt_token_budget)
            self.assertEqual(message_history_limit(self.agent), config.premium_message_history_limit)
            self.assertEqual(tool_call_history_limit(self.agent), config.premium_tool_call_history_limit)

        with patch("api.agent.core.prompt_context.get_agent_llm_tier", return_value=AgentLLMTier.MAX):
            self.assertEqual(get_prompt_token_budget(self.agent), config.max_prompt_token_budget)
            self.assertEqual(message_history_limit(self.agent), config.max_message_history_limit)
            self.assertEqual(tool_call_history_limit(self.agent), config.max_tool_call_history_limit)
