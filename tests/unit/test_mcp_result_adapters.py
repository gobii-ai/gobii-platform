import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

from api.agent.tools.mcp_manager import MCPServerRuntime, MCPToolInfo, MCPToolManager
from api.agent.tools.mcp_result_adapters import (
    BrightDataScrapeBatchAdapter,
    BrightDataSearchEngineBatchAdapter,
)
from api.models import (
    BrowserUseAgent,
    MCPServerConfig,
    PersistentAgent,
    PersistentAgentEnabledTool,
    ToolConfig,
)
from constants.plans import PlanNames


class DummyContent:
    def __init__(self, text: str):
        self.text = text


class DummyResult:
    def __init__(self, text: str):
        self.content = [DummyContent(text)]
        self.data = None
        self.is_error = False


@tag("batch_mcp_tools")
class BrightDataSearchEngineBatchAdapterTests(SimpleTestCase):
    def test_strips_nested_images(self):
        payload = [
            {
                "result": {
                    "organic": [
                        {"title": "One", "image": "http://example.com/1.png", "image_base64": "abc"},
                        {"title": "Two", "image": "http://example.com/2.png"},
                    ],
                    "related": [
                        {"title": "Related", "image": "http://example.com/r.png", "image_base64": "abc"}
                    ],
                }
            }
        ]
        result = DummyResult(json.dumps(payload))

        adapted = BrightDataSearchEngineBatchAdapter().adapt(result)

        cleaned = json.loads(adapted.content[0].text)
        organic = cleaned[0]["result"]["organic"]
        related = cleaned[0]["result"]["related"]
        self.assertNotIn("image", organic[0])
        self.assertNotIn("image_base64", organic[0])
        self.assertNotIn("image", organic[1])
        self.assertNotIn("image", related[0])
        self.assertNotIn("image_base64", related[0])


@tag("batch_mcp_tools")
class BrightDataScrapeBatchAdapterTests(SimpleTestCase):
    def test_scrubs_data_images_inside_batch_payload(self):
        payload = [
            {"url": "https://example.com", "content": "![hero](data:image/jpeg;base64,CCC) text"},
            {"url": "https://example.com/2", "content": "No images here"},
            {"url": "https://example.com/3", "content": None},
        ]
        result = DummyResult(json.dumps(payload))

        adapted = BrightDataScrapeBatchAdapter().adapt(result)

        cleaned = json.loads(adapted.content[0].text)
        self.assertEqual(cleaned[0]["content"], "![hero]() text")
        self.assertEqual(cleaned[1]["content"], "No images here")
        self.assertIsNone(cleaned[2]["content"])


@tag("batch_mcp_tools")
class MCPToolManagerAdapterIntegrationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="bd-adapters@example.com")
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="bd-adapters-browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="bd-adapters-agent",
            charter="c",
            browser_use_agent=browser_agent,
        )
        self.config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="brightdata",
            display_name="Bright Data",
            command="echo",
        )
        self.runtime = MCPServerRuntime(
            config_id=str(self.config.id),
            name=self.config.name,
            display_name=self.config.display_name,
            description=self.config.description,
            command=self.config.command,
            args=[],
            url="",
            auth_method=self.config.auth_method,
            env={},
            headers={},
            prefetch_apps=[],
            scope=self.config.scope,
            organization_id=None,
            user_id=None,
            updated_at=self.config.updated_at,
        )

    def _build_manager(self, tool_info: MCPToolInfo) -> MCPToolManager:
        manager = MCPToolManager()
        manager._initialized = True
        manager._server_cache = {self.runtime.config_id: self.runtime}
        manager._clients = {self.runtime.config_id: MagicMock()}
        manager._tools_cache = {self.runtime.config_id: [tool_info]}
        return manager

    def _enable_tool(self, tool_info: MCPToolInfo):
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name=tool_info.full_name,
            tool_server=tool_info.server_name,
            tool_name=tool_info.tool_name,
            server_config=self.config,
        )

    def test_execute_mcp_tool_strips_linkedin_text_html(self):
        tool_info = MCPToolInfo(
            config_id=self.runtime.config_id,
            full_name="mcp_brightdata_web_data_linkedin_company_profile",
            server_name="brightdata",
            tool_name="web_data_linkedin_company_profile",
            description="LinkedIn company profile",
            parameters={},
        )
        manager = self._build_manager(tool_info)
        self._enable_tool(tool_info)
        dummy_result = DummyResult(
            json.dumps([{"updates": [{"text_html": "<p>html</p>", "text": "plain", "id": "u1"}]}])
        )

        with patch.object(manager, "_execute_async", return_value=dummy_result), patch.object(
            manager, "_select_agent_proxy_url", return_value=(None, None)
        ):
            response = manager.execute_mcp_tool(self.agent, tool_info.full_name, {"company": "acme"})

        cleaned = json.loads(response["result"])
        self.assertNotIn("text_html", cleaned[0]["updates"][0])
        self.assertEqual(cleaned[0]["updates"][0]["text"], "plain")

    def test_execute_mcp_tool_truncates_amazon_product_search(self):
        ToolConfig.objects.update_or_create(
            plan_name=PlanNames.FREE,
            defaults={"brightdata_amazon_product_search_limit": 2},
        )
        tool_info = MCPToolInfo(
            config_id=self.runtime.config_id,
            full_name="mcp_brightdata_web_data_amazon_product_search",
            server_name="brightdata",
            tool_name="web_data_amazon_product_search",
            description="Amazon product search",
            parameters={},
        )
        manager = self._build_manager(tool_info)
        self._enable_tool(tool_info)
        dummy_result = DummyResult(json.dumps([{"id": 1}, {"id": 2}, {"id": 3}]))

        with patch.object(manager, "_execute_async", return_value=dummy_result), patch.object(
            manager, "_select_agent_proxy_url", return_value=(None, None)
        ):
            response = manager.execute_mcp_tool(self.agent, tool_info.full_name, {"query": "test"})

        cleaned = json.loads(response["result"])
        self.assertEqual(cleaned, [{"id": 1}, {"id": 2}])

    def test_execute_mcp_tool_blocks_search_engine_batch_over_limit(self):
        ToolConfig.objects.update_or_create(
            plan_name=PlanNames.FREE,
            defaults={"search_engine_batch_query_limit": 2},
        )
        tool_info = MCPToolInfo(
            config_id=self.runtime.config_id,
            full_name="mcp_brightdata_search_engine_batch",
            server_name="brightdata",
            tool_name="search_engine_batch",
            description="Search batch",
            parameters={},
        )
        manager = self._build_manager(tool_info)
        self._enable_tool(tool_info)

        with patch.object(manager, "_execute_async") as mock_exec, patch.object(
            manager, "_select_agent_proxy_url", return_value=(None, None)
        ):
            response = manager.execute_mcp_tool(
                self.agent,
                tool_info.full_name,
                {"queries": ["first", "second", "third"]},
            )

        self.assertEqual(response["status"], "error")
        self.assertIn("Maximum number of queries", response["message"])
        mock_exec.assert_not_called()
