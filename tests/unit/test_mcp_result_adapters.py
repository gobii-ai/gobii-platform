import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

from api.agent.tools.mcp_manager import MCPServerRuntime, MCPToolInfo, MCPToolManager
from api.agent.tools.mcp_result_adapters import BrightDataSearchEngineAdapter
from api.models import (
    BrowserUseAgent,
    MCPServerConfig,
    PersistentAgent,
    PersistentAgentEnabledTool,
)


class DummyContent:
    def __init__(self, text: str):
        self.text = text


class DummyResult:
    def __init__(self, text: str):
        self.content = [DummyContent(text)]
        self.data = None
        self.is_error = False


@tag("batch_mcp_tools")
class BrightDataSearchEngineAdapterTests(SimpleTestCase):
    def test_strips_image_fields_from_organic_results(self):
        payload = {
            "organic": [
                {"title": "Example", "image": "http://example.com/a.png", "image_base64": "abc"},
                {"title": "Example 2", "image": "http://example.com/b.png"},
            ]
        }
        adapter = BrightDataSearchEngineAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        cleaned = json.loads(adapted.content[0].text)

        self.assertNotIn("image", cleaned["organic"][0])
        self.assertNotIn("image_base64", cleaned["organic"][0])
        self.assertNotIn("image", cleaned["organic"][1])
        self.assertEqual(cleaned["organic"][0]["title"], "Example")

    def test_batch_adapter_strips_nested_images(self):
        payload = [
            {
                "result": {
                    "organic": [
                        {"title": "One", "image": "http://example.com/1.png", "image_base64": "abc"},
                        {"title": "Two", "image": "http://example.com/2.png"},
                    ]
                }
            }
        ]
        from api.agent.tools.mcp_result_adapters import BrightDataSearchEngineBatchAdapter

        adapter = BrightDataSearchEngineBatchAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        cleaned = json.loads(adapted.content[0].text)

        organic = cleaned[0]["result"]["organic"]
        self.assertNotIn("image", organic[0])
        self.assertNotIn("image_base64", organic[0])
        self.assertNotIn("image", organic[1])
        self.assertEqual(organic[0]["title"], "One")


@tag("batch_mcp_tools")
class MCPToolManagerAdapterIntegrationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="bd@example.com")
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="bd-browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="bd-agent",
            charter="c",
            browser_use_agent=self.browser_agent,
        )
        self.config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="brightdata",
            display_name="Bright Data",
            description="",
            command="echo",
            command_args=[],
            url="https://brightdata.example.com",
            auth_method=MCPServerConfig.AuthMethod.NONE,
        )
        self.runtime = MCPServerRuntime(
            config_id=str(self.config.id),
            name=self.config.name,
            display_name=self.config.display_name,
            description=self.config.description,
            command=self.config.command or None,
            args=list(self.config.command_args or []),
            url=self.config.url or "",
            auth_method=self.config.auth_method,
            env=self.config.environment or {},
            headers=self.config.headers or {},
            prefetch_apps=list(self.config.prefetch_apps or []),
            scope=self.config.scope,
            organization_id=str(self.config.organization_id) if self.config.organization_id else None,
            user_id=str(self.config.user_id) if self.config.user_id else None,
            updated_at=self.config.updated_at,
        )
        self.search_tool_info = MCPToolInfo(
            config_id=self.runtime.config_id,
            full_name="mcp_brightdata_search_engine",
            server_name="brightdata",
            tool_name="search_engine",
            description="Search",
            parameters={},
        )
        self.company_tool_info = MCPToolInfo(
            config_id=self.runtime.config_id,
            full_name="mcp_brightdata_web_data_linkedin_company_profile",
            server_name="brightdata",
            tool_name="web_data_linkedin_company_profile",
            description="LinkedIn company profile",
            parameters={},
        )

    def _build_manager(self, tool_info: MCPToolInfo) -> MCPToolManager:
        manager = MCPToolManager()
        manager._initialized = True
        manager._server_cache = {self.runtime.config_id: self.runtime}
        manager._clients = {self.runtime.config_id: MagicMock()}
        manager._tools_cache = {self.runtime.config_id: [tool_info]}
        return manager

    def _enable_tool(self, tool_info: MCPToolInfo):
        return PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name=tool_info.full_name,
            tool_server=tool_info.server_name,
            tool_name=tool_info.tool_name,
            server_config=self.config,
        )

    def test_execute_mcp_tool_runs_brightdata_adapter(self):
        tool_info = self.search_tool_info
        manager = self._build_manager(tool_info)
        self._enable_tool(tool_info)
        payload = {"organic": [{"title": "Example", "image": "http://example.com/a.png", "image_base64": "abc"}]}
        dummy_result = DummyResult(json.dumps(payload))
        loop = MagicMock()
        loop.run_until_complete.side_effect = lambda _: dummy_result

        with patch.object(manager, "_ensure_event_loop", return_value=loop), \
             patch.object(manager, "_execute_async", new_callable=MagicMock, return_value=dummy_result), \
             patch.object(manager, "_select_agent_proxy_url", return_value=(None, None)):
            response = manager.execute_mcp_tool(
                self.agent,
                tool_info.full_name,
                {"query": "test"},
            )

        self.assertEqual(response.get("status"), "success")
        cleaned = json.loads(response.get("result"))
        self.assertNotIn("image", cleaned["organic"][0])
        self.assertNotIn("image_base64", cleaned["organic"][0])
        self.assertEqual(cleaned["organic"][0]["title"], "Example")

    def test_execute_mcp_tool_strips_linkedin_text_html(self):
        tool_info = self.company_tool_info
        manager = self._build_manager(tool_info)
        self._enable_tool(tool_info)
        payload = [
            {
                "updates": [
                    {"text_html": "<p>html</p>", "text": "plain", "id": "u1"},
                    {"text_html": "<p>another</p>", "text": "plain2"},
                ]
            }
        ]
        dummy_result = DummyResult(json.dumps(payload))
        loop = MagicMock()
        loop.run_until_complete.side_effect = lambda _: dummy_result

        with patch.object(manager, "_ensure_event_loop", return_value=loop), \
             patch.object(manager, "_execute_async", new_callable=MagicMock, return_value=dummy_result), \
             patch.object(manager, "_select_agent_proxy_url", return_value=(None, None)):
            response = manager.execute_mcp_tool(
                self.agent,
                tool_info.full_name,
                {"company": "acme"},
            )

        self.assertEqual(response.get("status"), "success")
        cleaned = json.loads(response.get("result"))
        self.assertEqual(len(cleaned[0]["updates"]), 2)
        self.assertNotIn("text_html", cleaned[0]["updates"][0])
        self.assertEqual(cleaned[0]["updates"][0]["text"], "plain")

    def test_brightdata_pdf_urls_rejected(self):
        tool_info = MCPToolInfo(
            config_id=self.runtime.config_id,
            full_name="mcp_brightdata_scrape_as_markdown",
            server_name="brightdata",
            tool_name="scrape_as_markdown",
            description="Scrape",
            parameters={},
        )
        manager = self._build_manager(tool_info)
        self._enable_tool(tool_info)
        params = {"url": "https://example.com/doc.pdf"}

        with patch.object(manager, "_ensure_event_loop", return_value=MagicMock()), \
             patch.object(manager, "_execute_async", new_callable=MagicMock) as mock_exec, \
             patch.object(manager, "_select_agent_proxy_url", return_value=(None, None)):
            response = manager.execute_mcp_tool(
                self.agent,
                tool_info.full_name,
                params,
            )

        self.assertEqual(response.get("status"), "error")
        self.assertIn("PDF", response.get("message", ""))
        self.assertIn("spawn_web_task", response.get("message", ""))
        mock_exec.assert_not_called()
