import asyncio
import json
from datetime import timedelta
from unittest.mock import Mock, patch

import httpx
from kombu.exceptions import OperationalError as KombuOperationalError
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings, tag
from django.utils import timezone

from api.agent.tools.mcp_task_protocol import (
    MCPCreateTaskResult,
    MCPDetailedTaskResult,
    MCPTaskHTTPClient,
    MCPTaskMalformedResponse,
    MCPTaskProtocolError,
)
from api.agent.tools.mcp_manager import MCPServerRuntime, MCPToolInfo, MCPToolManager
from api.agent.core.prompt_context import _build_mcp_task_tool_result_record
from api.models import (
    BrowserUseAgent,
    MCPServerConfig,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentMCPTask,
)
from api.tasks.mcp_tasks import poll_mcp_task


class FakeMCPHTTPServer:
    def __init__(self):
        self.requests = []
        self.tool_result = {
            "resultType": "task",
            "taskId": "remote-123",
            "status": "working",
            "statusMessage": "Queued",
            "createdAt": "2026-07-29T12:00:00Z",
            "lastUpdatedAt": "2026-07-29T12:00:00Z",
            "ttlMs": 3_600_000,
            "pollIntervalMs": 5_000,
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.requests.append((request, payload))
        method = payload["method"]
        if method == "server/discover":
            result = {
                "resultType": "complete",
                "supportedVersions": ["2026-07-28"],
                "capabilities": {
                    "tools": {},
                    "extensions": {"io.modelcontextprotocol/tasks": {}},
                },
                "serverInfo": {"name": "fake", "version": "1"},
            }
        elif method == "tools/call":
            result = self.tool_result
        elif method == "tasks/get":
            result = {
                "resultType": "complete",
                "taskId": "remote-123",
                "status": "completed",
                "createdAt": "2026-07-29T12:00:00Z",
                "lastUpdatedAt": "2026-07-29T12:01:00Z",
                "ttlMs": 3_600_000,
                "result": {"content": [{"type": "text", "text": "done"}], "isError": False},
            }
        elif method == "tasks/cancel":
            result = {"resultType": "complete"}
        else:
            result = {"resultType": "complete", "tools": []}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    def client_factory(self, **kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle), **kwargs)


@tag("mcp_async_tasks_batch")
class MCPTaskHTTPClientTests(SimpleTestCase):
    def setUp(self):
        self.server = FakeMCPHTTPServer()
        self.client = MCPTaskHTTPClient(
            url="https://mcp.example.test/mcp",
            headers={"Authorization": "Bearer secret"},
            httpx_client_factory=self.server.client_factory,
            timeout_seconds=5,
        )

    def test_discovers_extension_and_typed_task_result(self):
        discovery = asyncio.run(self.client.discover())
        result = asyncio.run(
            self.client.call_tool("long_job", {"value": 1}, advertise_tasks=True)
        )

        self.assertTrue(discovery.supports_tasks)
        self.assertIsInstance(result, MCPCreateTaskResult)
        self.assertEqual(result.task_id, "remote-123")
        self.assertIsNotNone(result.created_at.tzinfo)
        call_request, call_payload = self.server.requests[-1]
        self.assertEqual(call_request.headers["mcp-method"], "tools/call")
        self.assertEqual(call_request.headers["mcp-name"], "long_job")
        self.assertEqual(
            call_payload["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"],
            {"extensions": {"io.modelcontextprotocol/tasks": {}}},
        )

    def test_normal_tool_result_is_not_inferred_from_task_id_json(self):
        self.server.tool_result = {
            "content": [{"type": "text", "text": '{"task_id":"not-a-protocol-task"}'}],
            "structuredContent": {"task_id": "also-not-a-protocol-task"},
            "isError": False,
        }

        result = asyncio.run(
            self.client.call_tool("normal_job", {}, advertise_tasks=True)
        )

        self.assertNotIsInstance(result, MCPCreateTaskResult)
        self.assertEqual(result.structuredContent["task_id"], "also-not-a-protocol-task")

    def test_poll_and_cancel_include_task_routing_headers(self):
        result = asyncio.run(self.client.get_task("remote-123"))
        asyncio.run(self.client.cancel_task("remote-123"))

        self.assertEqual(result.status, "completed")
        get_request = self.server.requests[-2][0]
        cancel_request = self.server.requests[-1][0]
        self.assertEqual(get_request.headers["mcp-method"], "tasks/get")
        self.assertEqual(get_request.headers["mcp-name"], "remote-123")
        self.assertEqual(cancel_request.headers["mcp-method"], "tasks/cancel")
        self.assertEqual(cancel_request.headers["mcp-name"], "remote-123")

    def test_rejects_malformed_task_result(self):
        self.server.tool_result = {"resultType": "task", "taskId": "missing-fields"}
        with self.assertRaises(MCPTaskMalformedResponse):
            asyncio.run(self.client.call_tool("bad", {}, advertise_tasks=True))


@tag("mcp_async_tasks_batch")
@override_settings(
    MCP_ASYNC_TASK_LEASE_SECONDS=120,
    MCP_ASYNC_TASK_MIN_POLL_INTERVAL_SECONDS=2,
    MCP_ASYNC_TASK_MAX_POLL_INTERVAL_SECONDS=60,
)
class MCPTaskPollingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="mcp-task-user")
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="MCP task browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="MCP task agent",
            charter="Test MCP tasks.",
            browser_use_agent=browser_agent,
        )
        self.server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="async-test",
            display_name="Async test",
            url="https://mcp.example.test/mcp",
        )

    def _task(self):
        now = timezone.now()
        return PersistentAgentMCPTask.objects.create(
            agent=self.agent,
            server_config=self.server,
            server_name=self.server.name,
            remote_task_id="remote-123",
            tool_name="mcp_async-test_long_job",
            remote_tool_name="long_job",
            tool_arguments={"value": 1},
            protocol_version="2026-07-28",
            status=PersistentAgentMCPTask.Status.WORKING,
            poll_interval_ms=2_000,
            next_poll_at=now - timedelta(seconds=1),
            deadline_at=now + timedelta(hours=1),
        )

    def _completed_state(self):
        now_text = timezone.now().isoformat()
        return MCPDetailedTaskResult(
            task_id="remote-123",
            status="completed",
            status_message="Done",
            created_at=now_text,
            last_updated_at=now_text,
            ttl_ms=3_600_000,
            poll_interval_ms=2_000,
            input_requests=None,
            result={"content": [{"type": "text", "text": "final"}], "isError": False},
            error=None,
        )

    def test_completed_task_is_normalized_and_wakes_exactly_once(self):
        task = self._task()
        manager = Mock()
        manager.get_mcp_task_state.return_value = self._completed_state()
        manager.normalize_mcp_task_result.return_value = {
            "status": "success",
            "result": "final",
        }

        with patch("api.tasks.mcp_tasks.get_mcp_manager", return_value=manager), patch(
            "api.tasks.mcp_tasks._schedule_follow_up"
        ) as wake:
            with self.captureOnCommitCallbacks(execute=True):
                poll_mcp_task.run(str(task.id))
            with self.captureOnCommitCallbacks(execute=True):
                poll_mcp_task.run(str(task.id))

        task.refresh_from_db()
        self.assertEqual(task.status, PersistentAgentMCPTask.Status.COMPLETED)
        self.assertEqual(task.result["result"], "final")
        self.assertIsNotNone(task.wake_enqueued_at)
        self.assertEqual(manager.get_mcp_task_state.call_count, 1)
        wake.assert_called_once()

    def test_working_then_completed_reschedules_without_early_wake(self):
        task = self._task()
        task.attempts = 3
        task.save(update_fields=["attempts"])
        now_text = timezone.now().isoformat()
        working = MCPDetailedTaskResult(
            task_id="remote-123",
            status="working",
            status_message="Halfway",
            created_at=now_text,
            last_updated_at=now_text,
            ttl_ms=3_600_000,
            poll_interval_ms=2_000,
            input_requests=None,
            result=None,
            error=None,
        )
        manager = Mock()
        manager._clamp_mcp_task_poll_interval_ms.return_value = 2_000
        manager.get_mcp_task_state.side_effect = [working, self._completed_state()]
        manager.normalize_mcp_task_result.return_value = {
            "status": "success",
            "result": "final",
        }

        with patch("api.tasks.mcp_tasks.get_mcp_manager", return_value=manager), patch(
            "api.tasks.mcp_tasks.poll_mcp_task.apply_async"
        ) as enqueue, patch("api.tasks.mcp_tasks._schedule_follow_up") as wake:
            with self.captureOnCommitCallbacks(execute=True):
                poll_mcp_task.run(str(task.id))
            task.refresh_from_db()
            self.assertEqual(task.status, PersistentAgentMCPTask.Status.WORKING)
            self.assertEqual(task.attempts, 0)
            self.assertIsNone(task.wake_enqueued_at)
            enqueue.assert_called_once()
            wake.assert_not_called()

            task.next_poll_at = timezone.now() - timedelta(seconds=1)
            task.save(update_fields=["next_poll_at"])
            with self.captureOnCommitCallbacks(execute=True):
                poll_mcp_task.run(str(task.id))

        task.refresh_from_db()
        self.assertEqual(task.status, PersistentAgentMCPTask.Status.COMPLETED)
        wake.assert_called_once()

    def test_input_required_stops_polling_and_wakes_with_requests(self):
        task = self._task()
        now_text = timezone.now().isoformat()
        manager = Mock()
        manager._clamp_mcp_task_poll_interval_ms.return_value = 2_000
        manager.get_mcp_task_state.return_value = MCPDetailedTaskResult(
            task_id="remote-123",
            status="input_required",
            status_message="Choose a region",
            created_at=now_text,
            last_updated_at=now_text,
            ttl_ms=3_600_000,
            poll_interval_ms=2_000,
            input_requests={"region": {"method": "elicitation/create", "params": {}}},
            result=None,
            error=None,
        )

        with patch("api.tasks.mcp_tasks.get_mcp_manager", return_value=manager), patch(
            "api.tasks.mcp_tasks._schedule_follow_up"
        ) as wake:
            with self.captureOnCommitCallbacks(execute=True):
                poll_mcp_task.run(str(task.id))
            with self.captureOnCommitCallbacks(execute=True):
                poll_mcp_task.run(str(task.id))

        task.refresh_from_db()
        self.assertEqual(task.status, PersistentAgentMCPTask.Status.INPUT_REQUIRED)
        self.assertIsNone(task.next_poll_at)
        self.assertIn("region", task.input_requests)
        self.assertEqual(manager.get_mcp_task_state.call_count, 1)
        wake.assert_called_once()

    def test_transient_timeout_retries_without_waking(self):
        task = self._task()
        manager = Mock()
        manager.get_mcp_task_state.side_effect = httpx.ReadTimeout("later")

        with patch("api.tasks.mcp_tasks.get_mcp_manager", return_value=manager), patch(
            "api.tasks.mcp_tasks.poll_mcp_task.apply_async"
        ) as enqueue, patch("api.tasks.mcp_tasks.random.uniform", return_value=1):
            with self.captureOnCommitCallbacks(execute=True):
                poll_mcp_task.run(str(task.id))

        task.refresh_from_db()
        self.assertEqual(task.status, PersistentAgentMCPTask.Status.WORKING)
        self.assertEqual(task.attempts, 1)
        self.assertIsNone(task.lease_expires_at)
        self.assertIsNone(task.wake_enqueued_at)
        enqueue.assert_called_once()

    def test_missing_task_or_oauth_reconnect_error_is_terminal(self):
        for message in ("Unknown task ID", "Reconnect this MCP integration"):
            with self.subTest(message=message):
                task = self._task()
                task.remote_task_id = f"remote-{message[:5]}"
                task.save(update_fields=["remote_task_id"])
                manager = Mock()
                manager.get_mcp_task_state.side_effect = MCPTaskProtocolError(message)

                with patch("api.tasks.mcp_tasks.get_mcp_manager", return_value=manager), patch(
                    "api.tasks.mcp_tasks._schedule_follow_up"
                ) as wake:
                    with self.captureOnCommitCallbacks(execute=True):
                        poll_mcp_task.run(str(task.id))

                task.refresh_from_db()
                self.assertEqual(task.status, PersistentAgentMCPTask.Status.FAILED)
                self.assertEqual(task.error["message"], message)
                wake.assert_called_once()

    def test_failed_and_cancelled_are_terminal_and_each_wake_once(self):
        for status in ("failed", "cancelled"):
            with self.subTest(status=status):
                task = self._task()
                task.remote_task_id = f"remote-{status}"
                task.save(update_fields=["remote_task_id"])
                now_text = timezone.now().isoformat()
                manager = Mock()
                manager._clamp_mcp_task_poll_interval_ms.return_value = 2_000
                manager.get_mcp_task_state.return_value = MCPDetailedTaskResult(
                    task_id=task.remote_task_id,
                    status=status,
                    status_message=f"Remote {status}",
                    created_at=now_text,
                    last_updated_at=now_text,
                    ttl_ms=3_600_000,
                    poll_interval_ms=2_000,
                    input_requests=None,
                    result=None,
                    error={"code": -32000, "message": "boom"} if status == "failed" else None,
                )

                with patch("api.tasks.mcp_tasks.get_mcp_manager", return_value=manager), patch(
                    "api.tasks.mcp_tasks._schedule_follow_up"
                ) as wake:
                    with self.captureOnCommitCallbacks(execute=True):
                        poll_mcp_task.run(str(task.id))

                task.refresh_from_db()
                self.assertEqual(task.status, status)
                self.assertIsNotNone(task.terminal_at)
                wake.assert_called_once()

    @override_settings(
        MCP_ASYNC_TASK_MAX_LIFETIME_SECONDS=3600,
        MCP_ASYNC_TASK_MIN_POLL_INTERVAL_SECONDS=2,
        MCP_ASYNC_TASK_MAX_POLL_INTERVAL_SECONDS=60,
    )
    def test_creation_clamps_poll_interval_and_lifetime_and_returns_pending(self):
        manager = MCPToolManager()
        runtime = MCPServerRuntime(
            config_id=str(self.server.id),
            name=self.server.name,
            display_name=self.server.display_name,
            description="",
            command=None,
            args=[],
            url=self.server.url,
            auth_method=MCPServerConfig.AuthMethod.NONE,
            env={},
            headers={},
            prefetch_apps=[],
            scope=self.server.scope,
            organization_id=None,
            user_id=str(self.user.id),
            updated_at=self.server.updated_at,
        )
        now = timezone.now()
        create_result = MCPCreateTaskResult(
            task_id="creation-remote",
            status="working",
            status_message="Started",
            created_at=now.isoformat(),
            last_updated_at=now.isoformat(),
            ttl_ms=7_200_000,
            poll_interval_ms=1,
        )

        with patch(
            "api.tasks.mcp_tasks.poll_mcp_task.apply_async",
            side_effect=KombuOperationalError("broker unavailable"),
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                response = manager._persist_mcp_task(
                    agent=self.agent,
                    runtime=runtime,
                    full_tool_name="mcp_async-test_long_job",
                    remote_tool_name="long_job",
                    arguments={"value": 1},
                    create_result=create_result,
                    protocol_version="2026-07-28",
                )

        task = PersistentAgentMCPTask.objects.get(remote_task_id="creation-remote")
        self.assertEqual(response["status"], "pending")
        self.assertTrue(response["auto_sleep_ok"])
        self.assertEqual(response["task_id"], str(task.id))
        self.assertEqual(task.poll_interval_ms, 2_000)
        self.assertLessEqual(task.deadline_at, now + timedelta(seconds=3601))
        enqueue.assert_called_once()

    @override_settings(MCP_ASYNC_TASK_MAX_ACTIVE_PER_AGENT=1)
    def test_active_task_limit_prevents_task_capable_call(self):
        self._task()
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="mcp_async-test_long_job",
        )
        manager = MCPToolManager()
        runtime = MCPServerRuntime(
            config_id=str(self.server.id),
            name=self.server.name,
            display_name=self.server.display_name,
            description="",
            command=None,
            args=[],
            url=self.server.url,
            auth_method=MCPServerConfig.AuthMethod.NONE,
            env={},
            headers={},
            prefetch_apps=[],
            scope=self.server.scope,
            organization_id=None,
            user_id=str(self.user.id),
            updated_at=self.server.updated_at,
        )
        info = MCPToolInfo(
            config_id=str(self.server.id),
            full_name="mcp_async-test_long_job",
            server_name=self.server.name,
            tool_name="long_job",
            description="",
            parameters={"type": "object"},
        )
        manager._server_cache[str(self.server.id)] = runtime
        manager._modern_http_protocols[str(self.server.id)] = "2026-07-28"
        manager._task_capable_http_configs.add(str(self.server.id))

        with patch.object(manager, "_ensure_runtime_registered", return_value=True), patch.object(
            manager, "_select_agent_proxy_url", return_value=(None, None)
        ), patch.object(manager, "_run_coroutine_sync") as execute:
            response = manager.execute_mcp_tool(
                self.agent,
                info.full_name,
                {},
                tool_info=info,
            )

        self.assertEqual(response["status"], "error")
        self.assertIn("maximum number", response["message"])
        execute.assert_not_called()

    def test_terminal_task_projects_under_original_tool_name_and_local_id(self):
        task = self._task()
        task.status = PersistentAgentMCPTask.Status.COMPLETED
        task.result = {"status": "success", "result": "final"}
        task.terminal_at = timezone.now()
        task.save(update_fields=["status", "result", "terminal_at", "updated_at"])

        record = _build_mcp_task_tool_result_record(task)
        payload = json.loads(record.result_text)

        self.assertEqual(record.tool_name, task.tool_name)
        self.assertEqual(record.result_id, str(task.id))
        self.assertEqual(payload["task_id"], str(task.id))
        self.assertEqual(payload["result"]["result"], "final")

    def test_unexpired_lease_suppresses_duplicate_poll_delivery(self):
        task = self._task()
        task.lease_expires_at = timezone.now() + timedelta(minutes=1)
        task.save(update_fields=["lease_expires_at"])
        manager = Mock()

        with patch("api.tasks.mcp_tasks.get_mcp_manager", return_value=manager):
            poll_mcp_task.run(str(task.id))

        manager.get_mcp_task_state.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.attempts, 0)

    def test_lifetime_expiry_cancels_remote_and_wakes(self):
        task = self._task()
        task.deadline_at = timezone.now() - timedelta(seconds=1)
        task.save(update_fields=["deadline_at"])
        manager = Mock()

        with patch("api.tasks.mcp_tasks.get_mcp_manager", return_value=manager), patch(
            "api.tasks.mcp_tasks._schedule_follow_up"
        ) as wake:
            with self.captureOnCommitCallbacks(execute=True):
                poll_mcp_task.run(str(task.id))

        task.refresh_from_db()
        self.assertEqual(task.status, PersistentAgentMCPTask.Status.EXPIRED)
        manager.cancel_mcp_task_remote.assert_called_once()
        manager.get_mcp_task_state.assert_not_called()
        wake.assert_called_once()

    def test_disabling_server_cancels_active_task_while_config_is_available(self):
        task = self._task()
        manager = Mock()

        with patch("api.tasks.mcp_tasks.get_mcp_manager", return_value=manager), patch(
            "api.tasks.mcp_tasks._schedule_follow_up"
        ) as wake:
            self.server.is_active = False
            with self.captureOnCommitCallbacks(execute=True):
                self.server.save(update_fields=["is_active", "updated_at"])

        task.refresh_from_db()
        self.assertEqual(task.status, PersistentAgentMCPTask.Status.CANCELLED)
        manager.cancel_mcp_task_remote.assert_called_once()
        wake.assert_called_once()
