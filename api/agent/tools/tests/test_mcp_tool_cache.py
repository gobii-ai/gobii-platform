from dataclasses import replace

from django.test import SimpleTestCase, tag, override_settings
from django.utils import timezone

from api.agent.tools.mcp_manager import MCPServerRuntime, MCPToolInfo, MCPToolManager
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
