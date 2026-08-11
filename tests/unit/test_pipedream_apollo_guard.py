import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from waffle.testutils import override_switch

from api.agent.core.event_processing import (
    _execute_tool_call_runtime,
    _prepare_tool_batch,
)
from api.agent.system_skills.defaults import APOLLO_NATIVE_SYSTEM_SKILL_KEY
from api.agent.tools.mcp_manager import MCPToolInfo, MCPToolManager
from api.agent.tools.tool_manager import ToolCatalogEntry, execute_enabled_tool
from api.agent.tools.tracked_runtime import execute_tracked_runtime_tool_call
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentSystemSkillState,
    PersistentAgentToolCall,
)
from api.services.native_integrations import (
    APOLLO_PROVIDER,
    native_integration_is_connected,
    save_native_integration_credentials,
)
from constants.feature_flags import PIPEDREAM_APOLLO_GUARD
from util.analytics import AnalyticsEvent, AnalyticsSource


@tag("batch_mcp_tools")
@override_switch(PIPEDREAM_APOLLO_GUARD, active=True)
class PipedreamApolloExecutionGuardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(
            username="pipedream-apollo-guard@example.com",
            email="pipedream-apollo-guard@example.com",
            password="secret",
        )
        cls.agent = PersistentAgent.objects.create(
            user=user,
            name="Apollo Guard Agent",
            charter="Test the Apollo provider guard.",
            browser_use_agent=BrowserUseAgent.objects.create(
                user=user,
                name="Apollo Guard Browser",
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

    def test_apollo_prefixes_metadata_and_generic_components_are_blocked(self):
        cases = (
            (self._mcp_entry("apollo_io-search-people"), {}),
            (self._mcp_entry("apollo_io_oauth-search-people"), {}),
            (self._mcp_entry("component-proxy", app_slug="apollo_io"), {}),
            (self._mcp_entry("component-oauth-proxy", app_slug="apollo_io_oauth"), {}),
            (
                self._mcp_entry("retrieve_options"),
                {"componentKey": "apollo_io-search-people"},
            ),
            (
                self._mcp_entry("configure_component"),
                {"component_key": "apollo_io_oauth-enrich-person"},
            ),
        )

        for entry, params in cases:
            with self.subTest(tool_name=entry.full_name, app_slug=entry.mcp_info.app_slug):
                with patch("api.agent.tools.tool_manager.execute_mcp_tool") as shared, patch(
                    "api.agent.tools.tool_manager.execute_mcp_tool_isolated"
                ) as isolated:
                    result = execute_enabled_tool(
                        self.agent,
                        entry.full_name,
                        params,
                        isolated_mcp=True,
                        resolved_entry=entry,
                    )

                self.assertEqual(result["error_code"], "deprecated_provider_blocked")
                self.assertEqual(result["integration"], "apollo")
                self.assertEqual(result["replacement"], "apollo_native")
                self.assertEqual(result["setup_url"], "/app/integrations?provider=apollo")
                self.assertIs(result["retryable"], False)
                shared.assert_not_called()
                isolated.assert_not_called()

    def test_ready_handoff_enables_native_apollo_and_emits_analytics(self):
        entry = self._mcp_entry("apollo_io-search-people")
        save_native_integration_credentials(
            APOLLO_PROVIDER,
            self.agent.user,
            None,
            {"provider_key": APOLLO_PROVIDER.key, "access_token": "apollo-access-token"},
        )
        self.assertTrue(
            native_integration_is_connected(
                APOLLO_PROVIDER.key,
                self.agent.user,
                None,
            )
        )

        with patch(
            "api.services.deprecated_provider_guard.Analytics.track_event"
        ) as track_event:
            result = execute_enabled_tool(
                self.agent,
                entry.full_name,
                {},
                resolved_entry=entry,
            )

        self.assertEqual(result["handoff_status"], "ready")
        self.assertEqual(result["next_action"], "continue_with_native_apollo")
        self.assertIn("using http_request", result["message"])
        self.assertIn("Do not retry Pipedream", result["message"])
        self.assertTrue(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key=APOLLO_NATIVE_SYSTEM_SKILL_KEY,
                is_enabled=True,
            ).exists()
        )
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent,
                tool_full_name="http_request",
            ).exists()
        )
        analytics_call = track_event.call_args.kwargs
        self.assertEqual(
            analytics_call["event"],
            AnalyticsEvent.PIPEDREAM_APOLLO_EXECUTION_BLOCKED,
        )
        self.assertEqual(analytics_call["source"], AnalyticsSource.AGENT)
        self.assertEqual(analytics_call["properties"]["tool_name"], entry.full_name)
        self.assertEqual(analytics_call["properties"]["app_slug"], "apollo_io")
        self.assertEqual(analytics_call["properties"]["invocation_scope"], "top_level")
        self.assertEqual(analytics_call["properties"]["handoff_status"], "ready")
        self.assertEqual(analytics_call["properties"]["agent_id"], str(self.agent.id))
        self.assertIn("organization", analytics_call["properties"])

    def test_explicitly_disabled_native_apollo_is_not_reactivated(self):
        entry = self._mcp_entry("apollo_io-search-people")
        state = PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key=APOLLO_NATIVE_SYSTEM_SKILL_KEY,
            is_enabled=False,
        )

        result = execute_enabled_tool(
            self.agent,
            entry.full_name,
            {},
            resolved_entry=entry,
        )

        state.refresh_from_db()
        self.assertIs(state.is_enabled, False)
        self.assertEqual(result["handoff_status"], "explicitly_disabled")
        self.assertEqual(result["next_action"], "ask_user_to_enable_native_apollo")
        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent,
                tool_full_name="http_request",
            ).exists()
        )

    def test_unavailable_native_handoff_remains_typed_and_actionable(self):
        entry = self._mcp_entry("apollo_io_oauth-enrich-person")

        with patch(
            "api.agent.tools.tool_manager.mark_tool_enabled_without_discovery",
            return_value={"status": "error", "message": "unavailable"},
        ):
            result = execute_enabled_tool(
                self.agent,
                entry.full_name,
                {},
                resolved_entry=entry,
            )

        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        self.assertEqual(result["handoff_status"], "unavailable")
        self.assertEqual(result["next_action"], "enable_native_apollo")
        self.assertIn("search_tools", result["message"])

    def test_unresolved_stale_call_is_blocked_before_limits_and_persisted(self):
        tool_name = "apollo_io-search-people"

        with patch(
            "api.agent.tools.tool_manager.resolve_tool_entry",
            return_value=None,
        ), patch(
            "api.agent.core.event_processing._enforce_tool_rate_limit"
        ) as rate_limit, patch(
            "api.agent.core.event_processing._ensure_credit_for_tool"
        ) as credit_gate, patch(
            "api.agent.tools.tool_manager.execute_mcp_tool"
        ) as executor, patch(
            "api.agent.tools.tool_runtime._refresh_agent_tools",
            return_value=[],
        ):
            result, _updated_tools = execute_tracked_runtime_tool_call(
                self.agent,
                tool_name=tool_name,
                exec_params={"q_keywords": "founder"},
            )

        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        self.assertNotIn("not available", result["message"])
        persisted = PersistentAgentToolCall.objects.get(tool_name=tool_name)
        persisted_result = json.loads(persisted.result)
        self.assertEqual(persisted.status, PersistentAgentToolCall.Status.ERROR)
        self.assertEqual(persisted_result["error_code"], "deprecated_provider_blocked")
        self.assertEqual(persisted_result["integration"], "apollo")
        self.assertEqual(persisted_result["status_code"], "deprecated_provider_blocked")
        rate_limit.assert_not_called()
        credit_gate.assert_not_called()
        executor.assert_not_called()

    def test_stale_generic_call_absent_from_latest_roster_reaches_guard(self):
        tool_call = {
            "id": "call-apollo",
            "function": {
                "name": "retrieve_options",
                "arguments": json.dumps(
                    {"componentKey": "apollo_io_oauth-search-people"}
                ),
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
                self.agent,
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
        self.assertEqual(prepared.deprecated_provider_integration.key, "apollo")
        self.assertIsNone(prepared.resolved_entry)
        rate_limit.assert_not_called()
        credit_gate.assert_not_called()

    def test_same_turn_refresh_removes_only_apollo_pipedream_tools(self):
        entry = self._mcp_entry("apollo_io-search-people")
        sibling = self._mcp_entry("apollo_io_oauth-enrich-person")
        sheets = self._mcp_entry("google_sheets-read-rows")
        internal = self._mcp_entry(
            "apollo_io-internal-lookup",
            server_name="internal_crm",
        )
        unresolved_sibling = "apollo_io-stale-sibling"
        refreshed_tools = [
            {"type": "function", "function": {"name": candidate}}
            for candidate in (
                entry.full_name,
                sibling.full_name,
                unresolved_sibling,
                sheets.full_name,
                internal.full_name,
                "trello-create-card",
                "http_request",
            )
        ]
        resolved_entries = {
            sibling.full_name: sibling,
            sheets.full_name: sheets,
            internal.full_name: internal,
        }

        with patch(
            "api.agent.core.event_processing.get_agent_tools",
            return_value=refreshed_tools,
        ), patch(
            "api.services.deprecated_provider_guard._resolve_tool_entry",
            side_effect=lambda _agent, tool_name: resolved_entries.get(tool_name),
        ):
            result, updated_tools = _execute_tool_call_runtime(
                self.agent,
                tool_name=entry.full_name,
                exec_params={},
                budget_ctx=None,
                eval_run_id=None,
                resolved_entry=entry,
            )

        self.assertEqual(result["handoff_status"], "ready")
        updated_names = {
            definition["function"]["name"] for definition in updated_tools
        }
        self.assertNotIn(entry.full_name, updated_names)
        self.assertNotIn(sibling.full_name, updated_names)
        self.assertIn(unresolved_sibling, updated_names)
        self.assertIn(sheets.full_name, updated_names)
        self.assertIn(internal.full_name, updated_names)
        self.assertIn("trello-create-card", updated_names)
        self.assertIn("http_request", updated_names)

    def test_apollo_tools_hide_from_future_rosters_after_native_handoff(self):
        apollo = self._mcp_entry("apollo_io-search-people")
        apollo_oauth = self._mcp_entry("apollo_io_oauth-enrich-person")
        sheets = self._mcp_entry("google_sheets-read-rows")
        for entry in (apollo, apollo_oauth, sheets):
            PersistentAgentEnabledTool.objects.create(
                agent=self.agent,
                tool_full_name=entry.full_name,
                tool_server=entry.tool_server,
                tool_name=entry.tool_name,
            )

        manager = MCPToolManager()
        with patch.object(
            manager,
            "get_tools_for_agent",
            return_value=[apollo.mcp_info, apollo_oauth.mcp_info, sheets.mcp_info],
        ), patch.object(manager, "_backfill_enabled_tool_metadata"):
            before_handoff = manager.get_enabled_tools_definitions(self.agent)
            handoff = execute_enabled_tool(
                self.agent,
                apollo.full_name,
                {},
                resolved_entry=apollo,
            )
            after_handoff = manager.get_enabled_tools_definitions(self.agent)

        before_names = {definition["function"]["name"] for definition in before_handoff}
        after_names = {definition["function"]["name"] for definition in after_handoff}
        self.assertEqual(handoff["handoff_status"], "ready")
        self.assertEqual(
            before_names,
            {apollo.full_name, apollo_oauth.full_name, sheets.full_name},
        )
        self.assertNotIn(apollo.full_name, after_names)
        self.assertNotIn(apollo_oauth.full_name, after_names)
        self.assertIn(sheets.full_name, after_names)

    def test_credit_preflight_does_not_resolve_spoofed_prefix_twice(self):
        entry = self._mcp_entry(
            "apollo_io-internal-lookup",
            server_name="internal_crm",
        )
        tool_call = {
            "id": "call-internal-apollo",
            "function": {"name": entry.full_name, "arguments": "{}"},
        }

        with patch(
            "api.agent.core.event_processing.is_credit_message_only_mode",
            return_value=True,
        ), patch(
            "api.agent.core.event_processing.resolve_tool_entry",
            return_value=entry,
        ) as resolve:
            prepared_batch = _prepare_tool_batch(
                self.agent,
                tool_calls=[tool_call],
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

        self.assertEqual(prepared_batch.prepared_calls, [])
        resolve.assert_called_once_with(self.agent, entry.full_name)

    def test_eval_mock_cannot_bypass_apollo_guard(self):
        entry = self._mcp_entry("apollo_io-search-people")

        with patch(
            "api.agent.core.event_processing._resolve_eval_mock_result",
            return_value={"status": "ok", "people": [{"id": "mock"}]},
        ), patch(
            "api.agent.core.event_processing.get_agent_tools",
            return_value=[],
        ), patch(
            "api.agent.tools.tool_manager.execute_mcp_tool",
        ) as executor:
            result, _updated_tools = _execute_tool_call_runtime(
                self.agent,
                tool_name=entry.full_name,
                exec_params={},
                budget_ctx=None,
                eval_run_id="apollo-guard-eval",
                resolved_entry=entry,
            )

        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        executor.assert_not_called()

    def test_parallel_executor_cannot_bypass_apollo_guard(self):
        entry = self._mcp_entry("apollo_io_oauth-enrich-person")

        with patch(
            "api.agent.core.event_processing.get_agent_tools",
            return_value=[],
        ), patch(
            "api.agent.tools.tool_manager.execute_mcp_tool",
        ) as shared, patch(
            "api.agent.tools.tool_manager.execute_mcp_tool_isolated",
        ) as isolated:
            result, _updated_tools = _execute_tool_call_runtime(
                self.agent,
                tool_name=entry.full_name,
                exec_params={},
                budget_ctx=None,
                eval_run_id=None,
                parallel_safe=True,
                resolved_entry=entry,
            )

        self.assertEqual(result["error_code"], "deprecated_provider_blocked")
        shared.assert_not_called()
        isolated.assert_not_called()

    def test_non_pipedream_prefix_match_remains_allowed(self):
        entry = self._mcp_entry(
            "apollo_io-internal-lookup",
            server_name="internal_crm",
        )

        with patch(
            "api.agent.tools.tool_manager.execute_mcp_tool",
            return_value={"status": "ok", "person": {}},
        ) as executor:
            result = execute_enabled_tool(
                self.agent,
                entry.full_name,
                {},
                resolved_entry=entry,
            )

        self.assertEqual(result["status"], "ok")
        executor.assert_called_once()

    @override_switch(PIPEDREAM_APOLLO_GUARD, active=False)
    def test_switch_off_restores_execution_and_unavailable_behavior(self):
        entry = self._mcp_entry("apollo_io-search-people")

        with patch(
            "api.agent.tools.tool_manager.execute_mcp_tool",
            return_value={"status": "ok", "people": []},
        ) as executor:
            resolved_result = execute_enabled_tool(
                self.agent,
                entry.full_name,
                {},
                resolved_entry=entry,
            )
        with patch(
            "api.agent.tools.tool_manager.resolve_tool_entry",
            return_value=None,
        ):
            unresolved_result, _updated_tools = _execute_tool_call_runtime(
                self.agent,
                tool_name="apollo_io-stale-tool",
                exec_params={},
                budget_ctx=None,
                eval_run_id=None,
                resolved_entry=None,
            )

        self.assertEqual(resolved_result["status"], "ok")
        executor.assert_called_once()
        self.assertIn("not available", unresolved_result["message"])
