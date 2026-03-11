import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, tag, override_settings
from django.utils import timezone

from api.agent.tools.mcp_manager import (
    MCPServerRuntime,
    MCPToolInfo,
    MCPToolManager,
    PipedreamToolCacheContext,
)
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

    def test_ensure_runtime_registered_allows_pipedream_without_shared_client(self):
        manager = MCPToolManager()
        runtime = replace(self._runtime(), name="pipedream")
        manager._tools_cache[runtime.config_id] = [self._tool(runtime.config_id, "google_sheets-create-spreadsheet")]

        with patch.object(manager, "_get_pipedream_access_token", return_value="token"):
            self.assertTrue(manager._ensure_runtime_registered(runtime, require_client=True))

    def test_ensure_runtime_registered_requires_pipedream_credentials(self):
        manager = MCPToolManager()
        runtime = replace(self._runtime(), name="pipedream")
        manager._tools_cache[runtime.config_id] = [self._tool(runtime.config_id, "google_sheets-create-spreadsheet")]

        with patch.object(manager, "_get_pipedream_access_token", return_value=None):
            self.assertFalse(manager._ensure_runtime_registered(runtime, require_client=True))

    def test_ensure_runtime_registered_requires_shared_client_for_non_pipedream(self):
        manager = MCPToolManager()
        runtime = self._runtime()
        manager._tools_cache[runtime.config_id] = [self._tool(runtime.config_id, "mcp_example_first")]

        with patch.object(manager, "_register_server") as register_mock:
            self.assertFalse(manager._ensure_runtime_registered(runtime, require_client=True))
        register_mock.assert_called_once()

    def test_ensure_runtime_registered_forces_local_register_when_shared_client_required(self):
        manager = MCPToolManager()
        runtime = self._runtime()

        def _fake_register(server, *, agent=None, force_local=False, prefer_cache=True, pipedream_context=None):
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

    def test_invalidate_pipedream_owner_cache_removes_only_matching_slot(self):
        manager = MCPToolManager()
        runtime = replace(self._runtime(), name="pipedream", config_id="pd-config")
        keep_context = PipedreamToolCacheContext(owner_cache_key="user:keep", effective_app_slugs=["slack"])
        drop_context = PipedreamToolCacheContext(owner_cache_key="user:drop", effective_app_slugs=["trello"])
        keep_key = manager._tool_cache_slot_key(runtime, keep_context)
        drop_key = manager._tool_cache_slot_key(runtime, drop_context)
        manager._tools_cache[keep_key] = [self._tool(runtime.config_id, "slack-send-message")]
        manager._tools_cache[drop_key] = [self._tool(runtime.config_id, "trello-create-card")]

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
