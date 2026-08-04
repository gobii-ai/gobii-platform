import json
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from api.agent.core.event_processing import (
    _PreparedToolExecution,
    _ToolExecutionOutcome,
    _finalize_tool_batch,
    _prepare_tool_batch,
)
from api.agent.tools.mcp_manager import MCPToolInfo
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
    PersistentAgentToolCall,
)


@tag("batch_mcp_tools")
@override_settings(PIPEDREAM_GOOGLE_SHEETS_GUARD_ENABLED=True)
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

        with patch("api.agent.tools.tool_manager.execute_mcp_tool") as executor:
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
        self.assertIs(result["retryable"], False)
        executor.assert_not_called()
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent_a,
                tool_full_name=entry.full_name,
            ).exists()
        )

    @override_settings(PIPEDREAM_GOOGLE_SHEETS_GUARD_ENABLED=False)
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
        ), patch(
            "api.agent.tools.tool_manager.execute_mcp_tool"
        ) as executor:
            result, _updated_tools = execute_tracked_runtime_tool_call(
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
        executor.assert_not_called()
        rate_limit.assert_not_called()
        credit_gate.assert_not_called()

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
        self.assertEqual(persisted_result["error_code"], "deprecated_provider_blocked")
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
