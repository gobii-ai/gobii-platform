import json
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from waffle.testutils import override_switch

from api.agent.core.event_processing import (
    _PreparedToolExecution,
    _ToolExecutionOutcome,
    _execute_tool_call_runtime,
    _finalize_tool_batch,
    _prepare_tool_batch,
    _resolve_tool_for_execution,
)
from api.agent.system_skills.defaults import GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.service import prepare_google_sheets_native_handoff
from api.agent.tools.mcp_manager import MCPToolInfo, MCPToolManager
from api.agent.tools.sqlite_skills import format_recent_skills_for_prompt
from api.agent.tools.tool_runtime import execute_runtime_tool_call
from api.agent.tools.tool_manager import (
    BUILTIN_TOOL_REGISTRY,
    ToolCatalogEntry,
    execute_enabled_tool,
)
from api.agent.tools.tracked_runtime import execute_tracked_runtime_tool_call
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentStep,
    PersistentAgentSystemSkillState,
    PersistentAgentToolCall,
)
from constants.feature_flags import PIPEDREAM_GOOGLE_SHEETS_GUARD
from util.analytics import AnalyticsEvent, AnalyticsSource


@tag("batch_mcp_tools")
@override_switch(PIPEDREAM_GOOGLE_SHEETS_GUARD, active=True)
class PipedreamGoogleSheetsExecutionGuardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(
            username="pipedream-sheets-guard@example.com",
            email="pipedream-sheets-guard@example.com",
            password="secret",
        )
        cls.agent_a = PersistentAgent.objects.create(
            user=user,
            name="Sheets Guard Agent A",
            charter="Test the Google Sheets provider guard.",
            browser_use_agent=BrowserUseAgent.objects.create(
                user=user,
                name="Guarded Agent Browser",
            ),
        )
        cls.agent_b = PersistentAgent.objects.create(
            user=user,
            name="Sheets Guard Agent B",
            charter="Also use the universal Google Sheets provider guard.",
            browser_use_agent=BrowserUseAgent.objects.create(
                user=user,
                name="Control Agent Browser",
            ),
        )

    @staticmethod
    def _mcp_entry(
        tool_name: str,
        *,
        server_name: str = "pipedream",
        app_slug: str = "",
    ) -> ToolCatalogEntry:
        tool_info = MCPToolInfo(
            config_id=f"{server_name}-config",
            full_name=tool_name,
            server_name=server_name,
            tool_name=tool_name,
            description="Test MCP tool",
            parameters={"type": "object", "properties": {}},
            app_slug=app_slug,
        )
        return ToolCatalogEntry(
            provider="mcp",
            full_name=tool_name,
            description=tool_info.description,
            parameters=tool_info.parameters,
            tool_server=server_name,
            tool_name=tool_name,
            server_config_id=tool_info.config_id,
            mcp_info=tool_info,
        )

    @staticmethod
    def _enable(agent: PersistentAgent, entry: ToolCatalogEntry) -> None:
        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name=entry.full_name,
            tool_server=entry.tool_server,
            tool_name=entry.tool_name,
        )

    def test_pipedream_google_sheets_fallback_match_is_blocked_for_every_agent(self):
        entry = self._mcp_entry("google_sheets-read-rows")
        self._enable(self.agent_a, entry)
        self._enable(self.agent_b, entry)

        with patch("api.agent.tools.tool_manager.execute_mcp_tool") as executor:
            result_a = execute_enabled_tool(
                self.agent_a,
                entry.full_name,
                {},
                resolved_entry=entry,
            )
            result_b = execute_enabled_tool(
                self.agent_b,
                entry.full_name,
                {},
                resolved_entry=entry,
            )

        self.assertEqual(result_a["error_code"], "deprecated_provider_blocked")
        self.assertEqual(result_b["error_code"], "deprecated_provider_blocked")
        executor.assert_not_called()

    def test_guarded_agent_direct_pipedream_google_sheets_call_is_blocked(self):
        entry = self._mcp_entry(
            "component-proxy",
            app_slug="google_sheets",
        )
        self._enable(self.agent_a, entry)

        with patch("api.agent.tools.tool_manager.execute_mcp_tool") as executor, patch(
            "api.services.deprecated_provider_guard.Analytics.track_event"
        ) as track_event:
            result = execute_enabled_tool(
                self.agent_a,
                entry.full_name,
                {},
                resolved_entry=entry,
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        self.assertEqual(result["provider"], "pipedream")
        self.assertEqual(result["integration"], "google_sheets")
        self.assertEqual(result["replacement"], "google_sheets_native")
        self.assertEqual(result["handoff_status"], "ready")
        self.assertEqual(result["next_action"], "continue_with_native_google_sheets")
        self.assertIs(result["retryable"], False)
        executor.assert_not_called()
        track_event.assert_called_once()
        analytics_call = track_event.call_args.kwargs
        self.assertEqual(analytics_call["user_id"], self.agent_a.user_id)
        self.assertEqual(
            analytics_call["event"],
            AnalyticsEvent.PIPEDREAM_GOOGLE_SHEETS_EXECUTION_BLOCKED,
        )
        self.assertEqual(analytics_call["source"], AnalyticsSource.AGENT)
        self.assertEqual(
            analytics_call["properties"],
            {
                "agent_id": str(self.agent_a.id),
                "tool_name": entry.full_name,
                "provider": "pipedream",
                "app_slug": "google_sheets",
                "integration": "google_sheets",
                "replacement": "google_sheets_native",
                "invocation_scope": "top_level",
                "handoff_status": "ready",
                "error_code": "deprecated_provider_blocked",
                "organization": False,
            },
        )
        self.assertTrue(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent_a,
                skill_key=GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY,
                is_enabled=True,
            ).exists()
        )
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent_a,
                tool_full_name="http_request",
            ).exists()
        )
        native_prompt = format_recent_skills_for_prompt(self.agent_a, limit=3)
        self.assertIn("System Skill: Google Sheets", native_prompt)
        self.assertIn("Google Drive is not connected", native_prompt)
        self.assertIn("/app/integrations", native_prompt)
        self.assertIn("Park this work until the native connection event wakes you", native_prompt)
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent_a,
                tool_full_name=entry.full_name,
            ).exists()
        )

    def test_eval_mock_cannot_bypass_guard(self):
        entry = self._mcp_entry("google_sheets-read-rows")
        self._enable(self.agent_a, entry)

        with patch(
            "api.agent.core.event_processing._resolve_eval_mock_result",
            return_value={"status": "ok", "rows": [["mocked"]]},
        ), patch(
            "api.agent.core.event_processing.get_agent_tools",
            return_value=[],
        ), patch(
            "api.agent.tools.tool_manager.execute_mcp_tool",
        ) as executor:
            result, _updated_tools = _execute_tool_call_runtime(
                self.agent_a,
                tool_name=entry.full_name,
                exec_params={},
                budget_ctx=None,
                eval_run_id="guard-eval",
                resolved_entry=entry,
            )

        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        executor.assert_not_called()

    def test_explicitly_disabled_native_skill_is_not_reactivated(self):
        entry = self._mcp_entry("google_sheets-read-rows")
        self._enable(self.agent_a, entry)
        state = PersistentAgentSystemSkillState.objects.create(
            agent=self.agent_a,
            skill_key=GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY,
            is_enabled=False,
        )

        result = execute_enabled_tool(
            self.agent_a,
            entry.full_name,
            {},
            resolved_entry=entry,
        )

        state.refresh_from_db()
        self.assertIs(state.is_enabled, False)
        self.assertEqual(result["handoff_status"], "explicitly_disabled")
        self.assertEqual(result["next_action"], "ask_user_to_enable_native_google_sheets")
        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent_a,
                tool_full_name="http_request",
            ).exists()
        )

    def test_native_handoff_failure_preserves_typed_block_and_recovery_hint(self):
        entry = self._mcp_entry("google_sheets-read-rows")
        self._enable(self.agent_a, entry)

        with patch(
            "api.agent.tools.tool_manager.mark_tool_enabled_without_discovery",
            return_value={"status": "error", "message": "unavailable"},
        ), patch(
            "api.services.deprecated_provider_guard.Analytics.track_event",
            side_effect=RuntimeError("analytics unavailable"),
        ):
            result = execute_enabled_tool(
                self.agent_a,
                entry.full_name,
                {},
                resolved_entry=entry,
            )

        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        self.assertEqual(result["handoff_status"], "unavailable")
        self.assertEqual(result["next_action"], "enable_native_google_sheets")
        self.assertIn("search_tools", result["message"])

    def test_top_level_handoff_refreshes_tools_for_the_same_agent_turn(self):
        entry = self._mcp_entry("google_sheets-read-rows")
        sibling_entry = self._mcp_entry("google_sheets-add-row")
        internal_entry = self._mcp_entry(
            "google_sheets-internal-read",
            server_name="internal_spreadsheets",
        )
        other_app_entry = self._mcp_entry(
            "google_sheets-looking-name",
            app_slug="trello",
        )
        self._enable(self.agent_a, entry)
        refreshed_tools = [
            {"type": "function", "function": {"name": entry.full_name}},
            {"type": "function", "function": {"name": sibling_entry.full_name}},
            {"type": "function", "function": {"name": internal_entry.full_name}},
            {"type": "function", "function": {"name": other_app_entry.full_name}},
            {"type": "function", "function": {"name": "trello-create-card"}},
            {"type": "function", "function": {"name": "http_request"}},
        ]
        resolved_entries = {
            sibling_entry.full_name: sibling_entry,
            internal_entry.full_name: internal_entry,
            other_app_entry.full_name: other_app_entry,
        }

        with patch(
            "api.agent.core.event_processing.get_agent_tools",
            return_value=refreshed_tools,
        ) as refresh, patch(
            "api.services.deprecated_provider_guard._resolve_tool_entry",
            side_effect=lambda _agent, tool_name: resolved_entries.get(tool_name),
        ):
            result, updated_tools = _execute_tool_call_runtime(
                self.agent_a,
                tool_name=entry.full_name,
                exec_params={},
                budget_ctx=None,
                eval_run_id=None,
                resolved_entry=entry,
            )

        self.assertEqual(result["handoff_status"], "ready")
        self.assertEqual(
            updated_tools,
            [
                {"type": "function", "function": {"name": internal_entry.full_name}},
                {"type": "function", "function": {"name": other_app_entry.full_name}},
                {"type": "function", "function": {"name": "trello-create-card"}},
                {"type": "function", "function": {"name": "http_request"}},
            ],
        )
        refresh.assert_called_once_with(self.agent_a)

    def test_nested_handoff_filters_blocked_tool_from_same_turn_roster(self):
        entry = self._mcp_entry("google_sheets-read-rows")
        sibling_entry = self._mcp_entry("google_sheets-add-row")
        self._enable(self.agent_a, entry)
        refreshed_tools = [
            {"type": "function", "function": {"name": entry.full_name}},
            {"type": "function", "function": {"name": sibling_entry.full_name}},
            {"type": "function", "function": {"name": "trello-create-card"}},
            {"type": "function", "function": {"name": "http_request"}},
        ]

        with patch(
            "api.agent.tools.tool_manager.resolve_tool_entry",
            side_effect=lambda _agent, tool_name: (
                entry if tool_name == entry.full_name else sibling_entry
            ),
        ), patch(
            "api.agent.tools.tool_runtime._refresh_agent_tools",
            return_value=refreshed_tools,
        ):
            result, updated_tools = execute_runtime_tool_call(
                self.agent_a,
                tool_name=entry.full_name,
                exec_params={},
            )

        self.assertEqual(result["handoff_status"], "ready")
        self.assertEqual(
            updated_tools,
            [
                {"type": "function", "function": {"name": "trello-create-card"}},
                {"type": "function", "function": {"name": "http_request"}},
            ],
        )

    def test_top_level_refresh_failure_preserves_actionable_block(self):
        entry = self._mcp_entry("google_sheets-read-rows")
        self._enable(self.agent_a, entry)

        with patch(
            "api.agent.core.event_processing.get_agent_tools",
            side_effect=RuntimeError("refresh failed"),
        ):
            result, updated_tools = _execute_tool_call_runtime(
                self.agent_a,
                tool_name=entry.full_name,
                exec_params={},
                budget_ctx=None,
                eval_run_id=None,
                resolved_entry=entry,
            )

        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        self.assertIn("native Google Sheets skill is enabled", result["message"])
        self.assertIn("Do not retry Pipedream", result["message"])
        self.assertIsNone(updated_tools)

    def test_nested_refresh_failure_preserves_actionable_block(self):
        entry = self._mcp_entry("google_sheets-read-rows")
        self._enable(self.agent_a, entry)

        with patch(
            "api.agent.tools.tool_manager.resolve_tool_entry",
            return_value=entry,
        ), patch(
            "api.agent.tools.tool_runtime._refresh_agent_tools",
            side_effect=RuntimeError("refresh failed"),
        ):
            result, updated_tools = execute_tracked_runtime_tool_call(
                self.agent_a,
                tool_name=entry.full_name,
                exec_params={},
            )

        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        self.assertIn("native Google Sheets skill is enabled", result["message"])
        self.assertIn("Do not retry Pipedream", result["message"])
        self.assertIsNone(updated_tools)

    @override_switch(PIPEDREAM_GOOGLE_SHEETS_GUARD, active=False)
    def test_global_rollback_switch_restores_pipedream_google_sheets_execution(self):
        entry = self._mcp_entry("google_sheets-read-rows")
        self._enable(self.agent_a, entry)

        with patch(
            "api.agent.tools.tool_manager.execute_mcp_tool",
            return_value={"status": "ok", "rows": []},
        ) as executor:
            result = execute_enabled_tool(
                self.agent_a,
                entry.full_name,
                {},
                resolved_entry=entry,
            )

        self.assertEqual(result["status"], "ok")
        executor.assert_called_once()

    def test_nested_custom_tool_call_is_blocked_at_shared_execution_boundary(self):
        entry = self._mcp_entry("google_sheets-add-rows")
        self._enable(self.agent_a, entry)
        parent_step = PersistentAgentStep.objects.create(
            agent=self.agent_a,
            description="Outer custom tool",
        )
        parent_call = PersistentAgentToolCall.objects.create(
            step=parent_step,
            tool_name="custom_sheet_writer",
            tool_params={},
            result=json.dumps({"status": "running"}),
            status=PersistentAgentToolCall.Status.PENDING,
        )

        with patch(
            "api.agent.core.event_processing._enforce_tool_rate_limit",
            return_value=True,
        ) as rate_limit, patch(
            "api.agent.core.event_processing._ensure_credit_for_tool",
        ) as credit_gate, patch(
            "api.agent.tools.tool_manager.resolve_tool_entry",
            return_value=entry,
        ) as resolve, patch(
            "api.agent.tools.tool_manager.execute_mcp_tool"
        ) as executor:
            result, updated_tools = execute_tracked_runtime_tool_call(
                self.agent_a,
                tool_name=entry.full_name,
                exec_params={},
                parent_step=parent_step,
            )

        child_call = PersistentAgentToolCall.objects.exclude(step=parent_step).get(
            step__agent=self.agent_a
        )
        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        self.assertEqual(child_call.parent_tool_call_id, parent_call.pk)
        self.assertEqual(child_call.status, PersistentAgentToolCall.Status.ERROR)
        self.assertIsNone(child_call.step.credits_cost)
        self.assertIsNone(child_call.step.task_credit_id)
        self.assertIn(
            "http_request",
            {
                tool["function"]["name"]
                for tool in updated_tools
                if isinstance(tool.get("function"), dict)
            },
        )
        executor.assert_not_called()
        resolve.assert_called_once_with(self.agent_a, entry.full_name)
        rate_limit.assert_not_called()
        credit_gate.assert_not_called()

    def test_pipedream_sheets_tools_hide_only_after_native_handoff_is_ready(self):
        prefixed_entry = self._mcp_entry("google_sheets-read-rows")
        metadata_entry = self._mcp_entry(
            "component-proxy",
            app_slug="google_sheets",
        )
        other_app_entry = self._mcp_entry(
            "trello-create-card",
            app_slug="trello",
        )
        for entry in (prefixed_entry, metadata_entry, other_app_entry):
            self._enable(self.agent_a, entry)

        manager = MCPToolManager()
        with patch.object(
            manager,
            "get_tools_for_agent",
            return_value=[
                prefixed_entry.mcp_info,
                metadata_entry.mcp_info,
                other_app_entry.mcp_info,
            ],
        ), patch.object(manager, "_backfill_enabled_tool_metadata"):
            before_handoff = manager.get_enabled_tools_definitions(self.agent_a)
            handoff_status = prepare_google_sheets_native_handoff(self.agent_a)
            after_handoff = manager.get_enabled_tools_definitions(self.agent_a)
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent_a,
                tool_full_name="http_request",
            ).delete()
            after_native_tool_eviction = manager.get_enabled_tools_definitions(self.agent_a)
            prepare_google_sheets_native_handoff(self.agent_a)
            with patch(
                "api.services.tool_blacklist.is_tool_blacklisted_for_agent",
                return_value=True,
            ):
                after_native_tool_blacklist = manager.get_enabled_tools_definitions(self.agent_a)

        before_names = {
            definition["function"]["name"]
            for definition in before_handoff
        }
        after_names = {
            definition["function"]["name"]
            for definition in after_handoff
        }
        after_eviction_names = {
            definition["function"]["name"]
            for definition in after_native_tool_eviction
        }
        after_blacklist_names = {
            definition["function"]["name"]
            for definition in after_native_tool_blacklist
        }
        self.assertEqual(handoff_status, "ready")
        self.assertIn(prefixed_entry.full_name, before_names)
        self.assertIn(metadata_entry.full_name, before_names)
        self.assertIn(other_app_entry.full_name, before_names)
        self.assertNotIn(prefixed_entry.full_name, after_names)
        self.assertNotIn(metadata_entry.full_name, after_names)
        self.assertIn(other_app_entry.full_name, after_names)
        self.assertIn(prefixed_entry.full_name, after_eviction_names)
        self.assertIn(metadata_entry.full_name, after_eviction_names)
        self.assertIn(other_app_entry.full_name, after_eviction_names)
        self.assertIn(prefixed_entry.full_name, after_blacklist_names)
        self.assertIn(metadata_entry.full_name, after_blacklist_names)
        self.assertIn(other_app_entry.full_name, after_blacklist_names)

    def test_metadata_only_pipedream_tool_cold_resolution_uses_effective_app_shards(self):
        entry = self._mcp_entry(
            "component-proxy",
            app_slug="google_sheets",
        )
        self._enable(self.agent_a, entry)
        manager = MCPToolManager()

        with patch(
            "api.agent.tools.mcp_manager.get_effective_pipedream_app_slugs_for_agent",
            return_value=["google_sheets", "trello"],
        ), patch.object(
            manager,
            "get_tools_for_agent",
            return_value=[entry.mcp_info],
        ) as get_tools, patch.object(manager, "_backfill_enabled_tool_metadata"):
            resolved = manager.prepare_tool_for_agent(self.agent_a, entry.full_name)

        self.assertIs(resolved, entry.mcp_info)
        self.assertEqual(resolved.app_slug, "google_sheets")
        get_tools.assert_called_once_with(
            self.agent_a,
            allowed_server_names={"pipedream"},
            pipedream_app_slugs={"google_sheets", "trello"},
        )

    def test_metadata_only_pipedream_tool_refuses_metadata_less_fallback(self):
        entry = self._mcp_entry("component-proxy")
        self._enable(self.agent_a, entry)
        manager = MCPToolManager()
        manager._server_cache[entry.server_config_id] = Mock(
            config_id=entry.server_config_id,
            name="pipedream",
            display_name="Pipedream",
        )

        with patch(
            "api.agent.tools.mcp_manager.get_effective_pipedream_app_slugs_for_agent",
            return_value=["google_sheets"],
        ), patch.object(
            manager,
            "get_tools_for_agent",
            return_value=[],
        ), patch.object(
            manager,
            "_sandbox_required_runtime_available",
            return_value=True,
        ):
            resolved = manager.prepare_tool_for_agent(self.agent_a, entry.full_name)

        self.assertIsNone(resolved)

    def test_nested_runtime_reuses_failed_catalog_resolution(self):
        with patch(
            "api.agent.tools.tool_runtime.tool_manager_service.resolve_tool_entry"
        ) as resolve, patch(
            "api.agent.tools.tool_runtime.execute_enabled_tool"
        ) as executor:
            result, updated_tools = execute_runtime_tool_call(
                self.agent_a,
                tool_name="missing-pipedream-tool",
                exec_params={},
                resolved_entry=None,
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("not available", result["message"])
        self.assertIsNone(updated_tools)
        resolve.assert_not_called()
        executor.assert_not_called()

    def test_unresolved_stale_sheets_name_returns_native_handoff_error(self):
        with patch(
            "api.agent.tools.tool_runtime._refresh_agent_tools",
            return_value=[],
        ), patch(
            "api.agent.tools.tool_manager.execute_mcp_tool",
        ) as executor:
            result, _updated_tools = execute_runtime_tool_call(
                self.agent_a,
                tool_name="google_sheets-stale-action",
                exec_params={},
                resolved_entry=None,
            )

        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        self.assertEqual(result["integration"], "google_sheets")
        self.assertNotIn("not available", result["message"])
        executor.assert_not_called()

    def test_stale_sheets_name_absent_from_latest_roster_reaches_guard(self):
        tool_call = {
            "id": "call-stale-sheets",
            "function": {
                "name": "google_sheets-stale-action",
                "arguments": "{}",
            },
        }

        with patch(
            "api.agent.core.event_processing.resolve_tool_entry",
            return_value=None,
        ), patch(
            "api.agent.core.event_processing._enforce_tool_rate_limit"
        ) as rate_limit, patch(
            "api.agent.core.event_processing._ensure_credit_for_tool"
        ) as credit_gate:
            prepared_batch = _prepare_tool_batch(
                self.agent_a,
                tool_calls=[tool_call],
                allowed_tool_names={"send_chat_message"},
                budget_ctx=None,
                eval_run_id=None,
                heartbeat=None,
                lock_extender=None,
                credit_snapshot={"available": None, "daily_state": {}},
                allow_inferred_message_continue=True,
                has_non_sleep_calls=True,
                has_user_facing_message=False,
                attach_completion=lambda kwargs: None,
                attach_prompt_archive=lambda step: None,
            )

        self.assertEqual(len(prepared_batch.prepared_calls), 1)
        prepared = prepared_batch.prepared_calls[0]
        self.assertEqual(
            prepared.deprecated_provider_integration,
            "google_sheets",
        )
        self.assertIsNone(prepared.resolved_entry)
        rate_limit.assert_not_called()
        credit_gate.assert_not_called()

    def test_nested_direct_runtime_tool_skips_catalog_resolution(self):
        with patch(
            "api.agent.core.event_processing._enforce_tool_rate_limit",
            return_value=True,
        ), patch(
            "api.agent.core.event_processing._ensure_credit_for_tool",
            return_value={"cost": None, "credit": None},
        ), patch(
            "api.agent.tools.tool_manager.resolve_tool_entry",
        ) as resolve, patch(
            "api.agent.tools.tracked_runtime.execute_runtime_tool_call",
            return_value=({"status": "ok"}, None),
        ) as runtime:
            result, updated_tools = execute_tracked_runtime_tool_call(
                self.agent_a,
                tool_name="send_email",
                exec_params={"to_address": "test@example.com", "body": "hello"},
            )

        self.assertEqual(result, {"status": "ok"})
        self.assertIsNone(updated_tools)
        resolve.assert_not_called()
        runtime.assert_called_once_with(
            self.agent_a,
            tool_name="send_email",
            exec_params={"to_address": "test@example.com", "body": "hello"},
            isolated_mcp=False,
            resolved_entry=None,
            deprecated_provider_integration=None,
        )

    def test_nested_guard_decision_is_stable_for_execution_and_billing(self):
        entry = self._mcp_entry("google_sheets-read-rows")
        self._enable(self.agent_a, entry)

        with patch(
            "api.services.deprecated_provider_guard.pipedream_google_sheets_guard_enabled",
            side_effect=[True, AssertionError("guard decision was evaluated twice")],
        ) as guard_enabled, patch(
            "api.agent.core.event_processing._enforce_tool_rate_limit",
        ) as rate_limit, patch(
            "api.agent.core.event_processing._ensure_credit_for_tool",
        ) as credit_gate, patch(
            "api.agent.tools.tool_manager.resolve_tool_entry",
            return_value=entry,
        ), patch(
            "api.agent.tools.tool_manager.execute_mcp_tool",
        ) as executor, patch(
            "api.agent.tools.tool_runtime._refresh_agent_tools",
            return_value=[],
        ):
            result, _updated_tools = execute_tracked_runtime_tool_call(
                self.agent_a,
                tool_name=entry.full_name,
                exec_params={},
            )

        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        guard_enabled.assert_called_once_with()
        rate_limit.assert_not_called()
        credit_gate.assert_not_called()
        executor.assert_not_called()

    @override_switch(PIPEDREAM_GOOGLE_SHEETS_GUARD, active=False)
    def test_guard_rollback_restores_connected_sheets_tools_to_future_rosters(self):
        entry = self._mcp_entry(
            "component-proxy",
            app_slug="google_sheets",
        )
        self._enable(self.agent_a, entry)
        self.assertEqual(prepare_google_sheets_native_handoff(self.agent_a), "ready")
        manager = MCPToolManager()

        with patch.object(
            manager,
            "get_tools_for_agent",
            return_value=[entry.mcp_info],
        ), patch.object(manager, "_backfill_enabled_tool_metadata"):
            definitions = manager.get_enabled_tools_definitions(self.agent_a)

        self.assertEqual(
            [definition["function"]["name"] for definition in definitions],
            [entry.full_name],
        )

    def test_top_level_blocked_call_bypasses_credit_message_only_mode(self):
        entry = self._mcp_entry("google_sheets-read-rows")
        self._enable(self.agent_a, entry)
        tool_call = {
            "id": "call-1",
            "function": {
                "name": entry.full_name,
                "arguments": json.dumps({"spreadsheetId": "sheet-1"}),
            },
        }

        with patch(
            "api.agent.core.event_processing.is_credit_message_only_mode",
            return_value=True,
        ), patch(
            "api.agent.core.event_processing.resolve_tool_entry",
            return_value=entry,
        ), patch(
            "api.agent.core.event_processing._enforce_tool_rate_limit"
        ) as rate_limit, patch(
            "api.agent.core.event_processing._ensure_credit_for_tool"
        ) as credit_gate:
            prepared_batch = _prepare_tool_batch(
                self.agent_a,
                tool_calls=[tool_call],
                budget_ctx=None,
                eval_run_id=None,
                heartbeat=None,
                lock_extender=None,
                credit_snapshot={"available": Decimal("0"), "daily_state": {}},
                allow_inferred_message_continue=True,
                has_non_sleep_calls=True,
                has_user_facing_message=False,
                attach_completion=lambda kwargs: None,
                attach_prompt_archive=lambda step: None,
            )

        self.assertEqual(len(prepared_batch.prepared_calls), 1)
        prepared = prepared_batch.prepared_calls[0]
        self.assertIs(prepared.resolved_entry, entry)
        self.assertIsNone(prepared.credits_consumed)
        self.assertIsNone(prepared.consumed_credit)
        rate_limit.assert_not_called()
        credit_gate.assert_not_called()

    def test_top_level_blocked_result_forces_credit_refund(self):
        entry = self._mcp_entry("google_sheets-read-rows")
        prepared = _PreparedToolExecution(
            idx=1,
            tool_name=entry.full_name,
            tool_params={},
            exec_params={},
            pending_step=None,
            credits_consumed=Decimal("0.4"),
            consumed_credit=Mock(),
            call_id="call-1",
            explicit_continue=None,
            inferred_continue=False,
            parallel_safe=False,
            parallel_ineligible_reason="test",
            resolved_entry=entry,
            deprecated_provider_integration="google_sheets",
        )
        outcome = _ToolExecutionOutcome(
            prepared=prepared,
            result={
                "status": "error",
                "error_code": "deprecated_provider_blocked",
                "message": "Use the native Google Sheets integration.",
                "retryable": False,
            },
            duration_ms=1,
            updated_tools=None,
            variable_map={},
        )
        persisted_step = Mock()

        with patch(
            "api.agent.core.event_processing._persist_tool_call_step",
            return_value=persisted_step,
        ), patch(
            "api.agent.core.event_processing._refund_tool_credit_on_error_if_configured"
        ) as refund:
            _finalize_tool_batch(
                self.agent_a,
                [outcome],
                attach_completion=lambda kwargs: None,
                attach_prompt_archive=lambda step: None,
            )

        refund.assert_called_once_with(
            agent=self.agent_a,
            tool_name=prepared.tool_name,
            step=persisted_step,
            credits_consumed=Decimal("0.4"),
            consumed_credit=prepared.consumed_credit,
            force=True,
        )

    def test_top_level_spoofed_blocked_error_does_not_force_credit_refund(self):
        entry = self._mcp_entry("trello-create-card")
        prepared = _PreparedToolExecution(
            idx=1,
            tool_name=entry.full_name,
            tool_params={},
            exec_params={},
            pending_step=None,
            credits_consumed=Decimal("0.4"),
            consumed_credit=Mock(),
            call_id="call-1",
            explicit_continue=None,
            inferred_continue=False,
            parallel_safe=False,
            parallel_ineligible_reason="test",
            resolved_entry=entry,
        )
        outcome = _ToolExecutionOutcome(
            prepared=prepared,
            result={
                "status": "error",
                "error_code": "deprecated_provider_blocked",
                "message": "Provider-controlled payload.",
            },
            duration_ms=1,
            updated_tools=None,
            variable_map={},
        )
        persisted_step = Mock()

        with patch(
            "api.agent.core.event_processing._persist_tool_call_step",
            return_value=persisted_step,
        ), patch(
            "api.agent.core.event_processing._refund_tool_credit_on_error_if_configured"
        ) as refund:
            _finalize_tool_batch(
                self.agent_a,
                [outcome],
                attach_completion=lambda kwargs: None,
                attach_prompt_archive=lambda step: None,
            )

        refund.assert_called_once_with(
            agent=self.agent_a,
            tool_name=prepared.tool_name,
            step=persisted_step,
            credits_consumed=Decimal("0.4"),
            consumed_credit=prepared.consumed_credit,
            force=False,
        )

    def test_nested_spoofed_blocked_error_does_not_force_credit_refund(self):
        entry = self._mcp_entry("trello-create-card")

        with patch(
            "api.agent.core.event_processing._enforce_tool_rate_limit",
            return_value=True,
        ), patch(
            "api.agent.core.event_processing._ensure_credit_for_tool",
            return_value={"cost": Decimal("0.4"), "credit": None},
        ), patch(
            "api.agent.tools.tool_manager.resolve_tool_entry",
            return_value=entry,
        ), patch(
            "api.agent.tools.tracked_runtime.execute_runtime_tool_call",
            return_value=(
                {
                    "status": "error",
                    "error_code": "deprecated_provider_blocked",
                    "message": "Provider-controlled payload.",
                },
                None,
            ),
        ), patch(
            "api.agent.core.event_processing._refund_tool_credit_on_error_if_configured"
        ) as refund:
            result, _updated_tools = execute_tracked_runtime_tool_call(
                self.agent_a,
                tool_name=entry.full_name,
                exec_params={},
            )

        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        refund.assert_not_called()

    def test_spoofed_blocked_result_does_not_replace_top_level_roster(self):
        entry = self._mcp_entry("trello-create-card")
        spoofed_result = {
            "status": "error",
            "error_code": "deprecated_provider_blocked",
            "message": "Provider-controlled payload.",
        }

        with patch(
            "api.agent.core.event_processing.execute_enabled_tool",
            return_value=spoofed_result,
        ), patch(
            "api.agent.core.event_processing.get_agent_tools",
        ) as refresh:
            result, updated_tools = _execute_tool_call_runtime(
                self.agent_a,
                tool_name=entry.full_name,
                exec_params={},
                budget_ctx=None,
                eval_run_id=None,
                resolved_entry=entry,
            )

        self.assertEqual(result, spoofed_result)
        self.assertIsNone(updated_tools)
        refresh.assert_not_called()

    def test_spoofed_blocked_result_does_not_replace_nested_roster(self):
        entry = self._mcp_entry("trello-create-card")
        spoofed_result = {
            "status": "error",
            "error_code": "deprecated_provider_blocked",
            "message": "Provider-controlled payload.",
        }

        with patch(
            "api.agent.tools.tool_manager.resolve_tool_entry",
            return_value=entry,
        ), patch(
            "api.agent.tools.tool_runtime.execute_enabled_tool",
            return_value=spoofed_result,
        ), patch(
            "api.agent.tools.tool_runtime._refresh_agent_tools",
        ) as refresh:
            result, updated_tools = execute_runtime_tool_call(
                self.agent_a,
                tool_name=entry.full_name,
                exec_params={},
            )

        self.assertEqual(result, spoofed_result)
        self.assertIsNone(updated_tools)
        refresh.assert_not_called()

    def test_known_local_tools_skip_catalog_resolution(self):
        with patch("api.agent.core.event_processing.resolve_tool_entry") as resolve:
            direct_name, direct_entry = _resolve_tool_for_execution(
                self.agent_a,
                "send_email",
            )
            builtin_name, builtin_entry = _resolve_tool_for_execution(
                self.agent_a,
                "http_request",
            )

        self.assertEqual(direct_name, "send_email")
        self.assertIsNone(direct_entry)
        self.assertEqual(builtin_name, "http_request")
        self.assertIsNone(builtin_entry)
        resolve.assert_not_called()

    def test_pipedream_google_sheets_metadata_match_is_blocked_for_every_agent(self):
        entry = self._mcp_entry(
            "component-proxy",
            app_slug="google_sheets",
        )
        self._enable(self.agent_b, entry)

        with patch("api.agent.tools.tool_manager.execute_mcp_tool") as executor:
            result = execute_enabled_tool(
                self.agent_b,
                entry.full_name,
                {},
                resolved_entry=entry,
            )

        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        executor.assert_not_called()

    def test_generic_pipedream_component_tools_targeting_google_sheets_are_blocked(self):
        component_params = {
            "retrieve_options": {"componentKey": "google_sheets-add-single-row"},
            "configure_component": {"component_key": "google_sheets-update-row"},
        }

        for tool_name, params in component_params.items():
            with self.subTest(tool_name=tool_name):
                entry = self._mcp_entry(tool_name)
                self._enable(self.agent_a, entry)

                with patch(
                    "api.agent.tools.tool_manager.execute_mcp_tool"
                ) as shared_executor, patch(
                    "api.agent.tools.tool_manager.execute_mcp_tool_isolated"
                ) as isolated_executor:
                    result = execute_enabled_tool(
                        self.agent_a,
                        entry.full_name,
                        params,
                        isolated_mcp=True,
                        resolved_entry=entry,
                    )

                self.assertEqual(result["error_code"], "deprecated_provider_blocked")
                shared_executor.assert_not_called()
                isolated_executor.assert_not_called()

    def test_generic_pipedream_component_tool_targeting_other_app_remains_allowed(self):
        entry = self._mcp_entry("retrieve_options")
        self._enable(self.agent_a, entry)

        with patch(
            "api.agent.tools.tool_manager.execute_mcp_tool",
            return_value={"status": "ok", "options": []},
        ) as executor:
            result = execute_enabled_tool(
                self.agent_a,
                entry.full_name,
                {"componentKey": "trello-create-card"},
                resolved_entry=entry,
            )

        self.assertEqual(result["status"], "ok")
        executor.assert_called_once()

    def test_other_pipedream_application_remains_allowed(self):
        entry = self._mcp_entry(
            "google_sheets-looking-name",
            app_slug="trello",
        )
        self._enable(self.agent_a, entry)

        with patch(
            "api.agent.tools.tool_manager.execute_mcp_tool",
            return_value={"status": "ok", "card_id": "card-1"},
        ) as executor:
            result = execute_enabled_tool(
                self.agent_a,
                entry.full_name,
                {},
                resolved_entry=entry,
            )

        self.assertEqual(result["status"], "ok")
        executor.assert_called_once()

    def test_native_google_sheets_http_request_remains_allowed(self):
        entry = ToolCatalogEntry(
            provider="builtin",
            full_name="http_request",
            description="Native OAuth HTTP request",
            parameters={"type": "object", "properties": {}},
            tool_server="builtin",
            tool_name="http_request",
        )
        self._enable(self.agent_a, entry)
        native_executor = Mock(return_value={"status": "ok", "spreadsheetId": "sheet-1"})
        registry_entry = {
            **BUILTIN_TOOL_REGISTRY["http_request"],
            "executor": native_executor,
        }

        with patch.dict(
            BUILTIN_TOOL_REGISTRY,
            {"http_request": registry_entry},
        ):
            result = execute_enabled_tool(
                self.agent_a,
                entry.full_name,
                {
                    "method": "GET",
                    "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-1",
                },
                resolved_entry=entry,
            )

        self.assertEqual(result["status"], "ok")
        native_executor.assert_called_once()

    def test_google_sheets_prefix_on_non_pipedream_server_is_not_blocked(self):
        entry = self._mcp_entry(
            "google_sheets-read-rows",
            server_name="internal_spreadsheets",
        )
        self._enable(self.agent_a, entry)

        with patch(
            "api.agent.tools.tool_manager.execute_mcp_tool",
            return_value={"status": "ok", "rows": []},
        ) as executor:
            result = execute_enabled_tool(
                self.agent_a,
                entry.full_name,
                {},
                resolved_entry=entry,
            )

        self.assertEqual(result["status"], "ok")
        executor.assert_called_once()

    def test_blocked_call_is_persisted_as_typed_error(self):
        entry = self._mcp_entry("google_sheets-update-multiple-rows")
        self._enable(self.agent_a, entry)

        with patch(
            "api.agent.core.event_processing._enforce_tool_rate_limit",
            return_value=True,
        ), patch(
            "api.agent.core.event_processing._ensure_credit_for_tool",
            return_value={"cost": None, "credit": None},
        ), patch(
            "api.agent.tools.tool_manager.resolve_tool_entry",
            return_value=entry,
        ):
            result, _updated_tools = execute_tracked_runtime_tool_call(
                self.agent_a,
                tool_name=entry.full_name,
                exec_params={"rows": [{"row": 2}]},
            )

        persisted_call = PersistentAgentToolCall.objects.get(step__agent=self.agent_a)
        persisted_result = json.loads(persisted_call.result)
        self.assertEqual(persisted_call.status, PersistentAgentToolCall.Status.ERROR)
        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        self.assertEqual(result["handoff_status"], "ready")
        self.assertEqual(result["next_action"], "continue_with_native_google_sheets")
        self.assertEqual(result["setup_url"], "/app/integrations")
        self.assertEqual(persisted_result["error_code"], "deprecated_provider_blocked")
        self.assertEqual(persisted_result["handoff_status"], "ready")
        self.assertEqual(
            persisted_result["next_action"],
            "continue_with_native_google_sheets",
        )
        self.assertEqual(persisted_result["setup_url"], "/app/integrations")
        self.assertIs(persisted_result["retryable"], False)

    def test_provider_executor_is_never_invoked_for_blocked_fallback_match(self):
        entry = self._mcp_entry("google_sheets-add-single-row")
        self._enable(self.agent_a, entry)

        with patch(
            "api.agent.tools.tool_manager.execute_mcp_tool"
        ) as shared_executor, patch(
            "api.agent.tools.tool_manager.execute_mcp_tool_isolated"
        ) as isolated_executor:
            result = execute_enabled_tool(
                self.agent_a,
                entry.full_name,
                {"row": ["secret-free-test-value"]},
                isolated_mcp=True,
                resolved_entry=entry,
            )

        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        shared_executor.assert_not_called()
        isolated_executor.assert_not_called()
