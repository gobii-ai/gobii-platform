import asyncio
import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from django.test import SimpleTestCase, tag, override_settings
from django.utils import timezone
from pydantic import BaseModel

from api.agent.tools.mcp_manager import (
    MCPServerRuntime,
    MCPToolInfo,
    MCPToolManager,
    PipedreamToolCacheContext,
    SandboxToolCacheContext,
)
from api.agent.tools.tool_manager import ToolCatalogEntry, execute_enabled_tool
from api.services.mcp_tool_cache import (
    get_cached_mcp_tool_definitions,
    invalidate_mcp_tool_cache,
    set_cached_mcp_tool_definitions,
)


@tag("batch_mcp_tools")
@override_settings(CELERY_BROKER_URL="")
class MCPToolCacheTests(SimpleTestCase):
    def tearDown(self):
        invalidate_mcp_tool_cache("cache-test-id")

    def _runtime(self) -> MCPServerRuntime:
        return MCPServerRuntime(
            config_id="cache-test-id",
            name="example",
            display_name="Example",
            description="",
            command=None,
            args=[],
            url="https://example.com",
            auth_method="none",
            env={"API_KEY": "secret"},
            headers={"Authorization": "Bearer token"},
            prefetch_apps=[],
            scope="platform",
            organization_id=None,
            user_id=None,
            updated_at=timezone.now(),
        )

    def _tool(self, config_id: str, name: str) -> MCPToolInfo:
        return MCPToolInfo(
            config_id=config_id,
            full_name=name,
            server_name="example",
            tool_name=name.split("_")[-1],
            description="Test tool",
            parameters={"type": "object", "properties": {}},
        )

    def test_cache_roundtrip(self):
        manager = MCPToolManager()
        runtime = self._runtime()
        tools = [
            self._tool(runtime.config_id, "mcp_example_first"),
            self._tool(runtime.config_id, "mcp_example_second"),
        ]
        fingerprint = manager._build_tool_cache_fingerprint(runtime)
        payload = manager._serialize_tools_for_cache(tools)

        set_cached_mcp_tool_definitions(runtime.config_id, fingerprint, payload)
        cached_payload = get_cached_mcp_tool_definitions(runtime.config_id, fingerprint)

        self.assertEqual(payload, cached_payload)
        hydrated = manager._deserialize_tools_from_cache(runtime, cached_payload or [])
        self.assertEqual(
            [tool.full_name for tool in tools],
            [tool.full_name for tool in hydrated],
        )

    def test_fingerprint_changes_with_env_and_headers(self):
        manager = MCPToolManager()
        runtime = self._runtime()
        fingerprint = manager._build_tool_cache_fingerprint(runtime)

        updated_env = replace(runtime, env={"API_KEY": "updated"})
        updated_headers = replace(runtime, headers={"Authorization": "Bearer updated"})

        self.assertNotEqual(fingerprint, manager._build_tool_cache_fingerprint(updated_env))
        self.assertNotEqual(fingerprint, manager._build_tool_cache_fingerprint(updated_headers))

    def test_invalidate_cache_clears_latest(self):
        manager = MCPToolManager()
        runtime = self._runtime()
        tools = [self._tool(runtime.config_id, "mcp_example_first")]
        fingerprint = manager._build_tool_cache_fingerprint(runtime)
        payload = manager._serialize_tools_for_cache(tools)

        set_cached_mcp_tool_definitions(runtime.config_id, fingerprint, payload)
        invalidate_mcp_tool_cache(runtime.config_id)

        cached_payload = get_cached_mcp_tool_definitions(runtime.config_id, fingerprint)
        self.assertIsNone(cached_payload)

    @override_settings(PIPEDREAM_PREFETCH_APPS="alpha,beta")
    def test_fingerprint_includes_prefetch_apps(self):
        manager = MCPToolManager()
        runtime = replace(self._runtime(), name="pipedream")
        fallback_fingerprint = manager._build_tool_cache_fingerprint(runtime)

        runtime_with_prefetch = replace(runtime, prefetch_apps=["gamma"])
        custom_fingerprint = manager._build_tool_cache_fingerprint(runtime_with_prefetch)

        self.assertNotEqual(fallback_fingerprint, custom_fingerprint)

    def test_pipedream_fingerprint_is_owner_scoped(self):
        manager = MCPToolManager()
        runtime = replace(self._runtime(), name="pipedream")

        first = manager._build_tool_cache_fingerprint(
            runtime,
            PipedreamToolCacheContext(owner_cache_key="user:one", effective_app_slugs=["trello"]),
        )
        second = manager._build_tool_cache_fingerprint(
            runtime,
            PipedreamToolCacheContext(owner_cache_key="user:two", effective_app_slugs=["trello"]),
        )

        self.assertNotEqual(first, second)

    def test_sandbox_stdio_fingerprint_is_agent_scoped(self):
        manager = MCPToolManager()
        runtime = replace(self._runtime(), command="npx", args=["-y", "@dummy/server"], url=None, scope="user")

        first = manager._build_tool_cache_fingerprint(
            runtime,
            sandbox_context=SandboxToolCacheContext(agent_cache_key="agent-one"),
        )
        second = manager._build_tool_cache_fingerprint(
            runtime,
            sandbox_context=SandboxToolCacheContext(agent_cache_key="agent-two"),
        )

        self.assertNotEqual(first, second)

    def test_http_fingerprint_is_not_agent_scoped(self):
        manager = MCPToolManager()
        runtime = self._runtime()

        first = manager._build_tool_cache_fingerprint(
            runtime,
            sandbox_context=SandboxToolCacheContext(agent_cache_key="agent-one"),
        )
        second = manager._build_tool_cache_fingerprint(
            runtime,
            sandbox_context=SandboxToolCacheContext(agent_cache_key="agent-two"),
        )

        self.assertEqual(first, second)

    def test_ensure_runtime_registered_allows_pipedream_without_shared_client(self):
        manager = MCPToolManager()
        runtime = replace(self._runtime(), name="pipedream")
        manager._tools_cache[runtime.config_id] = [self._tool(runtime.config_id, "google_sheets-create-spreadsheet")]
        manager._tool_cache_fingerprints[runtime.config_id] = manager._build_tool_cache_fingerprint(runtime)

        with patch.object(manager, "_get_pipedream_access_token", return_value="token"):
            self.assertTrue(manager._ensure_runtime_registered(runtime, require_client=True))

    def test_ensure_runtime_registered_requires_pipedream_credentials(self):
        manager = MCPToolManager()
        runtime = replace(self._runtime(), name="pipedream")
        manager._tools_cache[runtime.config_id] = [self._tool(runtime.config_id, "google_sheets-create-spreadsheet")]
        manager._tool_cache_fingerprints[runtime.config_id] = manager._build_tool_cache_fingerprint(runtime)

        with patch.object(manager, "_get_pipedream_access_token", return_value=None):
            self.assertFalse(manager._ensure_runtime_registered(runtime, require_client=True))

    def test_ensure_runtime_registered_requires_shared_client_for_non_pipedream(self):
        manager = MCPToolManager()
        runtime = self._runtime()
        manager._tools_cache[runtime.config_id] = [self._tool(runtime.config_id, "mcp_example_first")]
        manager._tool_cache_fingerprints[runtime.config_id] = manager._build_tool_cache_fingerprint(runtime)

        with patch.object(manager, "_register_server") as register_mock:
            self.assertFalse(manager._ensure_runtime_registered(runtime, require_client=True))
        register_mock.assert_called_once()

    def test_ensure_runtime_registered_forces_local_register_when_shared_client_required(self):
        manager = MCPToolManager()
        runtime = self._runtime()

        def _fake_register(
            server,
            *,
            agent=None,
            force_local=False,
            prefer_cache=True,
            pipedream_context=None,
            sandbox_context=None,
        ):
            manager._tools_cache[server.config_id] = [self._tool(server.config_id, "mcp_example_first")]
            if force_local:
                manager._clients[server.config_id] = object()

        with patch.object(manager, "_register_server", side_effect=_fake_register) as register_mock:
            self.assertTrue(manager._ensure_runtime_registered(runtime, require_client=True))

        register_mock.assert_called_once()

    def test_get_tools_for_agent_uses_owner_specific_pipedream_slot(self):
        manager = MCPToolManager()
        runtime = replace(self._runtime(), name="pipedream", config_id="pd-config")
        manager._initialized = True
        manager._server_cache[runtime.config_id] = runtime

        owner_one_context = PipedreamToolCacheContext(
            owner_cache_key="user:one",
            effective_app_slugs=["trello"],
        )
        owner_two_context = PipedreamToolCacheContext(
            owner_cache_key="user:two",
            effective_app_slugs=["slack"],
        )
        manager._tools_cache[manager._tool_cache_slot_key(runtime, owner_one_context)] = [
            MCPToolInfo(
                config_id=runtime.config_id,
                full_name="trello-create-card",
                server_name="pipedream",
                tool_name="trello-create-card",
                description="Trello",
                parameters={},
            )
        ]
        manager._tool_cache_fingerprints[manager._tool_cache_slot_key(runtime, owner_one_context)] = (
            manager._build_tool_cache_fingerprint(runtime, owner_one_context)
        )
        manager._tools_cache[manager._tool_cache_slot_key(runtime, owner_two_context)] = [
            MCPToolInfo(
                config_id=runtime.config_id,
                full_name="slack-send-message",
                server_name="pipedream",
                tool_name="slack-send-message",
                description="Slack",
                parameters={},
            )
        ]
        manager._tool_cache_fingerprints[manager._tool_cache_slot_key(runtime, owner_two_context)] = (
            manager._build_tool_cache_fingerprint(runtime, owner_two_context)
        )

        with patch.object(manager, "_needs_refresh", return_value=False):
            with patch("api.agent.tools.mcp_manager.agent_accessible_server_configs", return_value=[SimpleNamespace(id=runtime.config_id)]):
                with patch.object(manager, "_ensure_runtime_registered", return_value=True):
                    with patch.object(
                        manager,
                        "_pipedream_cache_context_for_agent",
                        side_effect=[owner_one_context, owner_two_context],
                    ):
                        agent_one_tools = manager.get_tools_for_agent(SimpleNamespace())
                        agent_two_tools = manager.get_tools_for_agent(SimpleNamespace())

        self.assertEqual([tool.full_name for tool in agent_one_tools], ["trello-create-card"])
        self.assertEqual([tool.full_name for tool in agent_two_tools], ["slack-send-message"])

    def test_tool_cache_slot_key_is_agent_scoped_for_sandbox_stdio(self):
        manager = MCPToolManager()
        runtime = replace(self._runtime(), command="npx", url=None, scope="user")

        first = manager._tool_cache_slot_key(
            runtime,
            sandbox_context=SandboxToolCacheContext(agent_cache_key="agent-one"),
        )
        second = manager._tool_cache_slot_key(
            runtime,
            sandbox_context=SandboxToolCacheContext(agent_cache_key="agent-two"),
        )

        self.assertNotEqual(first, second)

    @override_settings(MCP_HTTP_REQUEST_TIMEOUT_SECONDS=9.0)
    def test_http_client_factory_does_not_load_dynamic_settings_in_async_context(self):
        manager = MCPToolManager()

        async def build_client():
            with patch(
                "api.agent.tools.mcp_manager.get_mcp_http_timeout_seconds",
                side_effect=AssertionError("dynamic timeout lookup should stay outside async transport setup"),
            ):
                client = manager._httpx_client_factory()
                try:
                    return client.timeout.connect
                finally:
                    await client.aclose()

        self.assertEqual(asyncio.run(build_client()), 9.0)

    @override_settings(MCP_HTTP_REQUEST_TIMEOUT_SECONDS=9.0)
    def test_http_client_factory_respects_context_var_timeout(self):
        from api.agent.tools.mcp_manager import _use_mcp_http_timeout

        manager = MCPToolManager()

        async def build_client():
            client = manager._httpx_client_factory()
            try:
                return client.timeout.connect
            finally:
                await client.aclose()

        with _use_mcp_http_timeout(12.34):
            self.assertEqual(asyncio.run(build_client()), 12.34)

    def test_ensure_runtime_registered_reregisters_when_pipedream_apps_change_for_same_owner(self):
        manager = MCPToolManager()
        runtime = replace(self._runtime(), name="pipedream", config_id="pd-config")
        original_context = PipedreamToolCacheContext(
            owner_cache_key="user:one",
            effective_app_slugs=["trello"],
        )
        updated_context = PipedreamToolCacheContext(
            owner_cache_key="user:one",
            effective_app_slugs=["slack"],
        )
        slot_key = manager._tool_cache_slot_key(runtime, original_context)
        manager._tools_cache[slot_key] = [
            MCPToolInfo(
                config_id=runtime.config_id,
                full_name="trello-create-card",
                server_name="pipedream",
                tool_name="trello-create-card",
                description="Trello",
                parameters={},
            )
        ]
        manager._tool_cache_fingerprints[slot_key] = manager._build_tool_cache_fingerprint(runtime, original_context)

        def _fake_register(
            server,
            *,
            agent=None,
            force_local=False,
            prefer_cache=True,
            pipedream_context=None,
            sandbox_context=None,
        ):
            new_slot_key = manager._tool_cache_slot_key(server, pipedream_context)
            manager._tools_cache[new_slot_key] = [
                MCPToolInfo(
                    config_id=server.config_id,
                    full_name="slack-send-message",
                    server_name="pipedream",
                    tool_name="slack-send-message",
                    description="Slack",
                    parameters={},
                )
            ]
            manager._tool_cache_fingerprints[new_slot_key] = manager._build_tool_cache_fingerprint(
                server,
                pipedream_context,
            )

        with patch.object(manager, "_register_server", side_effect=_fake_register) as register_mock:
            self.assertTrue(manager._ensure_runtime_registered(runtime, pipedream_context=updated_context))

        register_mock.assert_called_once()
        self.assertEqual(
            [tool.full_name for tool in manager._tools_cache[slot_key]],
            ["slack-send-message"],
        )
        self.assertEqual(
            manager._tool_cache_fingerprints[slot_key],
            manager._build_tool_cache_fingerprint(runtime, updated_context),
        )

    def test_invalidate_pipedream_owner_cache_removes_only_matching_slot(self):
        manager = MCPToolManager()
        runtime = replace(self._runtime(), name="pipedream", config_id="pd-config")
        keep_context = PipedreamToolCacheContext(owner_cache_key="user:keep", effective_app_slugs=["slack"])
        drop_context = PipedreamToolCacheContext(owner_cache_key="user:drop", effective_app_slugs=["trello"])
        keep_key = manager._tool_cache_slot_key(runtime, keep_context)
        drop_key = manager._tool_cache_slot_key(runtime, drop_context)
        manager._tools_cache[keep_key] = [self._tool(runtime.config_id, "slack-send-message")]
        manager._tools_cache[drop_key] = [self._tool(runtime.config_id, "trello-create-card")]
        manager._tool_cache_fingerprints[keep_key] = manager._build_tool_cache_fingerprint(runtime, keep_context)
        manager._tool_cache_fingerprints[drop_key] = manager._build_tool_cache_fingerprint(runtime, drop_context)

        manager.invalidate_pipedream_owner_cache("user", "drop")

        self.assertIn(keep_key, manager._tools_cache)
        self.assertNotIn(drop_key, manager._tools_cache)

    def test_fetch_server_tools_lists_pipedream_once_for_prefetched_apps(self):
        manager = MCPToolManager()
        runtime = replace(self._runtime(), name="pipedream")
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.list_tools.return_value = [
            SimpleNamespace(
                name="google_sheets-add-row",
                description="Add row",
                inputSchema={"type": "object", "properties": {}},
            )
        ]

        tools = asyncio.run(
            manager._fetch_server_tools(
                client,
                runtime,
                pipedream_context=PipedreamToolCacheContext(
                    owner_cache_key="user:test",
                    effective_app_slugs=["google_sheets", "trello"],
                ),
            )
        )

        client.list_tools.assert_awaited_once()
        self.assertEqual([tool.full_name for tool in tools], ["google_sheets-add-row"])


@tag("batch_mcp_tools")
class MCPToolResultDataNormalizationTests(SimpleTestCase):
    def test_pydantic_data_result_is_normalized_to_json_safe_primitives(self):
        class Project(BaseModel):
            description: str
            location: str
            name: str
            owner: str
            updated: str
            url: str

        class Root(BaseModel):
            count: int
            projects: list[Project]

        manager = MCPToolManager()
        raw_result = SimpleNamespace(
            data=Root(
                count=1,
                projects=[
                    Project(
                        description="",
                        location="",
                        name="Product Manager",
                        owner="",
                        updated="",
                        url="https://www.linkedin.com/talent/hire/2063754842/overview",
                    )
                ],
            ),
            content=[SimpleNamespace(text="fallback text should not be used")],
        )

        result = manager._finalize_mcp_result("linkedin-recruiter", "list-projects", raw_result)

        self.assertEqual(
            result["result"],
            {
                "count": 1,
                "projects": [
                    {
                        "description": "",
                        "location": "",
                        "name": "Product Manager",
                        "owner": "",
                        "updated": "",
                        "url": "https://www.linkedin.com/talent/hire/2063754842/overview",
                    }
                ],
            },
        )
        serialized = json.dumps(result)
        self.assertNotIn("Root(", serialized)
        self.assertNotIn("Project(", serialized)

    def test_nested_model_containers_are_normalized_recursively(self):
        class Item(BaseModel):
            name: str
            rank: int

        manager = MCPToolManager()
        raw_result = SimpleNamespace(
            data={
                "primary": Item(name="alpha", rank=1),
                "items": [Item(name="beta", rank=2)],
                "tuple_items": (Item(name="gamma", rank=3),),
                "set_items": {"delta"},
            },
        )

        result = manager._finalize_mcp_result("example", "nested-data", raw_result)

        self.assertEqual(result["result"]["primary"], {"name": "alpha", "rank": 1})
        self.assertEqual(result["result"]["items"], [{"name": "beta", "rank": 2}])
        self.assertEqual(result["result"]["tuple_items"], [{"name": "gamma", "rank": 3}])
        self.assertEqual(sorted(result["result"]["set_items"]), ["delta"])
        json.dumps(result)

    def test_standard_json_hostile_types_are_normalized(self):
        @dataclass
        class Event:
            id: UUID
            amount: Decimal
            created_at: datetime
            event_date: date
            event_time: time

        manager = MCPToolManager()
        raw_result = SimpleNamespace(
            data={
                "event": Event(
                    id=UUID("12345678-1234-5678-1234-567812345678"),
                    amount=Decimal("10.50"),
                    created_at=datetime(2026, 6, 26, 18, 35, 0, tzinfo=UTC),
                    event_date=date(2026, 6, 26),
                    event_time=time(18, 35, 0),
                ),
                "ids": {UUID("87654321-4321-8765-4321-876543218765")},
                "amounts": [Decimal("1.25")],
            },
        )

        result = manager._finalize_mcp_result("example", "standard-types", raw_result)

        self.assertEqual(
            result["result"]["event"],
            {
                "id": "12345678-1234-5678-1234-567812345678",
                "amount": "10.50",
                "created_at": "2026-06-26T18:35:00+00:00",
                "event_date": "2026-06-26",
                "event_time": "18:35:00",
            },
        )
        self.assertEqual(result["result"]["amounts"], ["1.25"])
        self.assertEqual(result["result"]["ids"], ["87654321-4321-8765-4321-876543218765"])
        json.dumps(result)


@tag("batch_mcp_tools")
class MCPToolErrorNormalizationTests(SimpleTestCase):
    def _stdio_runtime(self) -> MCPServerRuntime:
        return MCPServerRuntime(
            config_id="cache-test-id",
            name="brightdata",
            display_name="Bright Data",
            description="",
            command="npx",
            args=["-y", "@brightdata/mcp@2.9.5"],
            url=None,
            auth_method="none",
            env={"API_TOKEN": "secret"},
            headers={},
            prefetch_apps=[],
            scope="platform",
            organization_id=None,
            user_id=None,
            updated_at=timezone.now(),
        )

    def _tool_error_result(self, message: str):
        return SimpleNamespace(
            is_error=True,
            content=[SimpleNamespace(text=message)],
        )

    def test_platform_brightdata_routes_to_isolated_mcp_execution(self):
        entry = ToolCatalogEntry(
            provider="mcp",
            full_name="mcp_brightdata_search_engine",
            description="Bright Data Search",
            parameters={"type": "object", "properties": {}},
            tool_server="brightdata",
            tool_name="search_engine",
            server_config_id="cache-test-id",
        )

        with patch("api.agent.tools.tool_manager.resolve_tool_entry", return_value=entry), patch(
            "api.agent.tools.tool_manager.PersistentAgentEnabledTool.objects.filter"
        ) as filter_mock, patch(
            "api.agent.tools.tool_manager.execute_mcp_tool_isolated",
            return_value={"status": "success", "result": "ok"},
        ) as isolated_mock, patch(
            "api.agent.tools.tool_manager.execute_mcp_tool",
            return_value={"status": "error", "message": "shared path should not run"},
        ) as shared_mock:
            filter_mock.return_value.exists.return_value = True

            with patch(
                "api.agent.tools.tool_manager._get_manager",
                return_value=SimpleNamespace(
                    is_platform_brightdata_config=lambda config_id: True,
                ),
            ):
                result = execute_enabled_tool(
                    SimpleNamespace(id="agent-id"),
                    "mcp_brightdata_search_engine",
                    {"query": "gobii"},
                )

        self.assertEqual(result["status"], "success")
        isolated_mock.assert_called_once()
        shared_mock.assert_not_called()

    def test_non_platform_brightdata_preserves_shared_mcp_execution(self):
        entry = ToolCatalogEntry(
            provider="mcp",
            full_name="mcp_brightdata_search_engine",
            description="User Bright Data Search",
            parameters={"type": "object", "properties": {}},
            tool_server="brightdata",
            tool_name="search_engine",
            server_config_id="user-brightdata-config-id",
        )

        with patch("api.agent.tools.tool_manager.resolve_tool_entry", return_value=entry), patch(
            "api.agent.tools.tool_manager.PersistentAgentEnabledTool.objects.filter"
        ) as filter_mock, patch(
            "api.agent.tools.tool_manager.execute_mcp_tool_isolated",
            return_value={"status": "error", "message": "isolated path should not run"},
        ) as isolated_mock, patch(
            "api.agent.tools.tool_manager.execute_mcp_tool",
            return_value={"status": "success", "result": "ok"},
        ) as shared_mock, patch(
            "api.agent.tools.tool_manager._get_manager",
            return_value=SimpleNamespace(
                is_platform_brightdata_config=lambda config_id: False,
            ),
        ):
            filter_mock.return_value.exists.return_value = True

            result = execute_enabled_tool(
                SimpleNamespace(id="agent-id"),
                "mcp_brightdata_search_engine",
                {"query": "gobii"},
            )

        self.assertEqual(result["status"], "success")
        isolated_mock.assert_not_called()
        shared_mock.assert_called_once()

    def test_mcp_session_death_response_evicts_stdio_execution_clients_only(self):
        manager = MCPToolManager()
        runtime = self._stdio_runtime()
        manager._clients[runtime.config_id] = object()
        manager._stdio_proxy_clients[f"{runtime.config_id}:agent:one:http://proxy"] = object()
        manager._tools_cache[runtime.config_id] = ["cached-tools"]
        manager._tool_cache_fingerprints[runtime.config_id] = "fingerprint"

        with patch.object(manager, "_close_client_sync") as close_mock:
            result = manager._handle_mcp_session_death_response(
                runtime,
                {"status": "error", "message": "Connection closed"},
                evict_client=True,
            )

        self.assertTrue(result["retryable"])
        self.assertNotIn(runtime.config_id, manager._clients)
        self.assertFalse(manager._stdio_proxy_clients)
        self.assertEqual(manager._tools_cache[runtime.config_id], ["cached-tools"])
        self.assertEqual(manager._tool_cache_fingerprints[runtime.config_id], "fingerprint")
        self.assertEqual(close_mock.call_count, 2)

    def test_non_session_brightdata_error_does_not_evict_stdio_execution_clients(self):
        manager = MCPToolManager()
        runtime = self._stdio_runtime()
        client = object()
        manager._clients[runtime.config_id] = client
        manager._tools_cache[runtime.config_id] = ["cached-tools"]

        result = manager._handle_mcp_session_death_response(
            runtime,
            {
                "status": "error",
                "message": "Bright Data returned an empty scrape_as_markdown result.",
            },
            evict_client=True,
        )

        self.assertNotIn("retryable", result)
        self.assertIs(manager._clients[runtime.config_id], client)
        self.assertEqual(manager._tools_cache[runtime.config_id], ["cached-tools"])

    def test_pipedream_google_sheets_retry_blob_is_normalized(self):
        manager = MCPToolManager()
        raw_error = json.dumps(
            {
                "os": [
                    {
                        "k": "error",
                        "err": {
                            "config": {
                                "userAgentDirectives": [
                                    {
                                        "product": "google-api-nodejs-client",
                                        "version": "8.0.1",
                                    }
                                ],
                                "retry": True,
                                "retryConfig": {
                                    "currentRetryAttempt": 3,
                                    "retry": 3,
                                    "httpMethodsToRetry": ["GET", "HEAD"],
                                },
                            }
                        },
                    }
                ]
            }
        )

        result = manager._finalize_mcp_result(
            "pipedream",
            "google_sheets-read-rows",
            self._tool_error_result(raw_error),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["retryable"], True)
        self.assertIn("Google Sheets read failed after upstream retries", result["message"])
        self.assertNotIn("google-api-nodejs-client", result["message"])
        self.assertNotIn("retryConfig", result["message"])

    def test_pipedream_google_sheets_permission_error_is_not_retryable(self):
        manager = MCPToolManager()
        raw_error = json.dumps(
            {
                "os": [
                    {
                        "k": "error",
                        "err": {
                            "response": {"status": 403},
                            "message": "The caller does not have permission",
                        },
                    }
                ]
            }
        )

        result = manager._finalize_mcp_result(
            "pipedream",
            "google_sheets-read-rows",
            self._tool_error_result(raw_error),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["status_code"], 403)
        self.assertEqual(result["retryable"], False)
        self.assertIn("authorization failed", result["message"])

    def test_pipedream_google_sheets_error_status_takes_priority_over_wrapper_status(self):
        manager = MCPToolManager()
        raw_error = json.dumps(
            {
                "status": 200,
                "os": [
                    {
                        "k": "error",
                        "err": {
                            "response": {"status": 403},
                            "message": "The caller does not have permission",
                        },
                    }
                ],
            }
        )

        result = manager._finalize_mcp_result(
            "pipedream",
            "google_sheets-read-rows",
            self._tool_error_result(raw_error),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["status_code"], 403)
        self.assertEqual(result["retryable"], False)
        self.assertIn("authorization failed", result["message"])
