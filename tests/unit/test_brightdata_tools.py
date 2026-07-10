import importlib
import json
from unittest.mock import MagicMock, patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings, tag
from requests.exceptions import ConnectionError, Timeout

from api.agent.browser_actions.web_search import register_web_search_action
from api.agent.tools.brightdata import (
    BRIGHTDATA_API_URL,
    BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME,
    BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME,
    execute_brightdata_scrape_as_markdown,
    execute_brightdata_search_engine,
    get_brightdata_scrape_as_markdown_tool,
    get_brightdata_search_engine_tool,
)
from api.agent.tools.mcp_manager import MCPToolInfo
from api.agent.tools.tool_manager import (
    ensure_default_tools_enabled,
    execute_enabled_tool,
    get_available_builtin_tool_entries,
)
from api.models import (
    BrowserUseAgent,
    MCPServerConfig,
    PersistentAgent,
    PersistentAgentEnabledTool,
)


def _response(text: str, status_code: int = 200):
    response = MagicMock()
    response.text = text
    response.status_code = status_code
    return response


@tag("batch_mcp_tools")
@override_settings(
    BRIGHT_DATA_TOKEN="test-token",
    BRIGHT_DATA_WEB_UNLOCKER_ZONE="test-zone",
    BRIGHT_DATA_WEB_UNLOCKER_ZONE_FALLBACK="",
    BRIGHT_DATA_REQUEST_TIMEOUT_SECONDS=42.0,
)
class BrightDataNativeToolTests(SimpleTestCase):
    def test_tool_definitions_match_brightdata_mcp_parameters(self):
        search = get_brightdata_search_engine_tool()["function"]
        scrape = get_brightdata_scrape_as_markdown_tool()["function"]

        self.assertEqual(search["name"], BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME)
        self.assertEqual(search["parameters"]["required"], ["query"])
        self.assertEqual(
            search["parameters"]["properties"]["engine"]["enum"],
            ["google", "bing", "yandex"],
        )
        self.assertEqual(search["parameters"]["properties"]["engine"]["default"], "google")
        self.assertEqual(search["parameters"]["properties"]["geo_location"]["minLength"], 2)
        self.assertEqual(scrape["name"], BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME)
        self.assertEqual(scrape["parameters"]["required"], ["url"])
        self.assertEqual(scrape["parameters"]["properties"]["url"]["format"], "uri")

    @patch("api.agent.tools.brightdata.requests.post")
    def test_google_search_uses_api_and_returns_cleaned_organic_json(self, mock_post):
        mock_post.return_value = _response(
            json.dumps(
                {
                    "organic": [
                        {
                            "link": " https://example.com/one ",
                            "title": " First result ",
                            "description": " Summary ",
                            "image": "discarded",
                            "position": 1,
                        },
                        {"link": "", "title": "Discarded"},
                    ]
                }
            )
        )

        result = execute_brightdata_search_engine(
            None,
            {
                "query": "native api",
                "cursor": "2",
                "geo_location": "us",
            },
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            json.loads(result["result"]),
            {
                "organic": [
                    {
                        "link": "https://example.com/one",
                        "title": "First result",
                        "description": "Summary",
                    }
                ]
            },
        )
        mock_post.assert_called_once()
        call = mock_post.call_args
        self.assertEqual(call.args[0], BRIGHTDATA_API_URL)
        self.assertEqual(call.kwargs["timeout"], 42.0)
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(
            call.kwargs["json"],
            {
                "url": "https://www.google.com/search?q=native%20api&start=20&gl=us&brd_json=1",
                "zone": "test-zone",
                "format": "raw",
                "data_format": "parsed_light",
            },
        )

    @patch("api.agent.tools.brightdata.requests.post")
    def test_bing_and_yandex_return_markdown(self, mock_post):
        mock_post.side_effect = [_response("bing markdown"), _response("yandex markdown")]

        bing = execute_brightdata_search_engine(
            None,
            {"query": "cats", "engine": "bing", "cursor": "3"},
        )
        yandex = execute_brightdata_search_engine(
            None,
            {"query": "cats", "engine": "yandex", "cursor": "3"},
        )

        self.assertEqual(bing, {"status": "success", "result": "bing markdown"})
        self.assertEqual(yandex, {"status": "success", "result": "yandex markdown"})
        self.assertEqual(
            mock_post.call_args_list[0].kwargs["json"]["url"],
            "https://www.bing.com/search?q=cats&first=31",
        )
        self.assertEqual(
            mock_post.call_args_list[1].kwargs["json"]["url"],
            "https://yandex.com/search/?text=cats&p=3",
        )
        self.assertEqual(mock_post.call_args_list[0].kwargs["json"]["data_format"], "markdown")

    @patch("api.agent.tools.brightdata.requests.post")
    def test_scrape_matches_mcp_markdown_processing(self, mock_post):
        mock_post.return_value = _response(
            "# Heading\n\nThis is **bold** and [a link](https://example.com) with `inline`.\n\n"
            "- one\n- two\n\n```python\nprint(\"x\")\n```\n\n![alt](https://example.com/a.png)\n"
        )

        result = execute_brightdata_scrape_as_markdown(
            None,
            {"url": "https://example.com/article"},
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["result"],
            "Heading\n\nThis is bold and [a link](https://example.com) with `inline`.\n\n"
            "one\n\ntwo\n\n```python\nprint(\"x\")\n```\n\nalt\n",
        )
        self.assertEqual(
            mock_post.call_args.kwargs["json"],
            {
                "url": "https://example.com/article",
                "zone": "test-zone",
                "format": "raw",
                "data_format": "markdown",
            },
        )

    @patch("api.agent.tools.brightdata.requests.post")
    def test_rejects_pdf_before_request(self, mock_post):
        result = execute_brightdata_scrape_as_markdown(None, {"url": "https://example.com/report.PDF?download=1"})

        self.assertEqual(result["status"], "error")
        self.assertIn("PDF", result["message"])
        mock_post.assert_not_called()

    @patch("api.agent.tools.brightdata.requests.post", return_value=_response(""))
    def test_empty_scrape_is_error(self, _mock_post):
        result = execute_brightdata_scrape_as_markdown(None, {"url": "https://example.com"})

        self.assertEqual(result["status"], "error")
        self.assertIn("empty scrape_as_markdown", result["message"])

    @patch("api.agent.tools.brightdata.requests.post", return_value=_response("not json"))
    def test_google_non_json_response_is_error(self, _mock_post):
        result = execute_brightdata_search_engine(None, {"query": "cats"})

        self.assertEqual(result["status"], "error")
        self.assertIn("Unexpected non-JSON", result["message"])

    @override_settings(BRIGHT_DATA_WEB_UNLOCKER_ZONE_FALLBACK="fallback-zone")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_google_search_retries_once_with_fallback_zone(self, mock_post):
        mock_post.side_effect = [
            _response("not json"),
            _response('{"organic": [{"link": "https://example.com", "title": "Fallback"}]}'),
        ]

        result = execute_brightdata_search_engine(None, {"query": "cats"})

        self.assertEqual(result["status"], "success")
        self.assertEqual(json.loads(result["result"])["organic"][0]["title"], "Fallback")
        self.assertEqual(
            [call.kwargs["json"]["zone"] for call in mock_post.call_args_list],
            ["test-zone", "fallback-zone"],
        )

    @override_settings(BRIGHT_DATA_WEB_UNLOCKER_ZONE_FALLBACK="fallback-zone")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_google_search_returns_last_error_when_both_zones_fail(self, mock_post):
        mock_post.side_effect = [_response("primary failed", 500), _response("fallback denied", 401)]

        result = execute_brightdata_search_engine(None, {"query": "cats"})

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["status_code"], 401)
        self.assertFalse(result["retryable"])
        self.assertIn("both primary and fallback zones", result["message"])
        self.assertIn("fallback denied", result["message"])

    @override_settings(BRIGHT_DATA_WEB_UNLOCKER_ZONE_FALLBACK="test-zone")
    @patch("api.agent.tools.brightdata.requests.post", return_value=_response("not json"))
    def test_google_search_does_not_retry_same_zone(self, mock_post):
        result = execute_brightdata_search_engine(None, {"query": "cats"})

        self.assertEqual(result["status"], "error")
        mock_post.assert_called_once()

    @override_settings(BRIGHT_DATA_WEB_UNLOCKER_ZONE_FALLBACK="fallback-zone")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_bing_search_retries_with_fallback_zone(self, mock_post):
        mock_post.side_effect = [_response("failed", 500), _response("fallback markdown")]

        result = execute_brightdata_search_engine(None, {"query": "cats", "engine": "bing"})

        self.assertEqual(result, {"status": "success", "result": "fallback markdown"})
        self.assertEqual(
            [call.kwargs["json"]["zone"] for call in mock_post.call_args_list],
            ["test-zone", "fallback-zone"],
        )

    @override_settings(BRIGHT_DATA_WEB_UNLOCKER_ZONE_FALLBACK="fallback-zone")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_scrape_retries_empty_response_with_fallback_zone(self, mock_post):
        mock_post.side_effect = [_response(""), _response("# Fallback page")]

        result = execute_brightdata_scrape_as_markdown(None, {"url": "https://example.com"})

        self.assertEqual(result, {"status": "success", "result": "Fallback page\n"})
        self.assertEqual(
            [call.kwargs["json"]["zone"] for call in mock_post.call_args_list],
            ["test-zone", "fallback-zone"],
        )

    @patch("api.agent.tools.brightdata.requests.post", return_value=_response("invalid token", 401))
    def test_auth_error_is_not_retryable(self, _mock_post):
        result = execute_brightdata_search_engine(None, {"query": "cats"})

        self.assertEqual(result["status_code"], 401)
        self.assertFalse(result["retryable"])

    @patch("api.agent.tools.brightdata.requests.post", return_value=_response("slow down", 429))
    def test_rate_limit_is_retryable(self, _mock_post):
        result = execute_brightdata_search_engine(None, {"query": "cats"})

        self.assertEqual(result["status_code"], 429)
        self.assertTrue(result["retryable"])

    @patch("api.agent.tools.brightdata.requests.post", side_effect=Timeout())
    def test_timeout_is_retryable(self, _mock_post):
        result = execute_brightdata_search_engine(None, {"query": "cats"})

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["retryable"])
        self.assertIn("timed out", result["message"])

    @patch("api.agent.tools.brightdata.requests.post", side_effect=ConnectionError())
    def test_connection_error_is_retryable(self, _mock_post):
        result = execute_brightdata_search_engine(None, {"query": "cats"})

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["retryable"])

    @override_settings(BRIGHT_DATA_TOKEN="", BRIGHT_DATA_WEB_UNLOCKER_ZONE="")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_missing_configuration_is_non_retryable(self, mock_post):
        result = execute_brightdata_search_engine(None, {"query": "cats"})

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["retryable"])
        self.assertIn("BRIGHT_DATA_TOKEN", result["message"])
        self.assertIn("BRIGHT_DATA_WEB_UNLOCKER_ZONE", result["message"])
        mock_post.assert_not_called()

    def test_rejects_invalid_cursor_and_geo_location(self):
        cursor_result = execute_brightdata_search_engine(None, {"query": "cats", "cursor": "next"})
        geo_result = execute_brightdata_search_engine(None, {"query": "cats", "geo_location": "usa"})

        self.assertEqual(cursor_result["status"], "error")
        self.assertEqual(geo_result["status"], "error")


@tag("batch_mcp_tools")
@override_settings(
    BRIGHT_DATA_TOKEN="test-token",
    BRIGHT_DATA_WEB_UNLOCKER_ZONE="test-zone",
    BRIGHT_DATA_WEB_UNLOCKER_ZONE_FALLBACK="",
    BRIGHT_DATA_REQUEST_TIMEOUT_SECONDS=42.0,
)
class BrightDataToolManagerTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="brightdata-native@example.com")
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Bright Data Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Bright Data Agent",
            charter="Test native Bright Data tools.",
            browser_use_agent=browser_agent,
        )

    def test_native_catalog_owns_legacy_mcp_names(self):
        entries = get_available_builtin_tool_entries(self.agent)

        self.assertEqual(entries[BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME].provider, "builtin")
        self.assertEqual(entries[BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME].provider, "builtin")

    @patch("api.agent.tools.tool_manager._get_manager")
    def test_native_tools_are_default_enabled(self, mock_get_manager):
        mock_get_manager.return_value = MagicMock()

        ensure_default_tools_enabled(self.agent)

        rows = {
            row.tool_full_name: row
            for row in PersistentAgentEnabledTool.objects.filter(
                agent=self.agent,
                tool_full_name__in=[
                    BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME,
                    BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME,
                ],
            )
        }
        self.assertEqual(set(rows), {BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME, BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME})
        self.assertTrue(all(row.tool_server == "builtin" for row in rows.values()))

    @patch("api.agent.tools.brightdata.requests.post")
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_execute_enabled_tool_routes_duplicate_name_to_native(self, mock_get_manager, mock_post):
        manager = MagicMock()
        manager.get_tools_for_agent.return_value = [
            MCPToolInfo(
                config_id="11111111-1111-1111-1111-111111111111",
                full_name=BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME,
                server_name="brightdata",
                tool_name="search_engine",
                description="Legacy MCP search",
                parameters={},
            )
        ]
        mock_get_manager.return_value = manager
        mock_post.return_value = _response('{"organic": []}')
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name=BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME,
            tool_server="builtin",
            tool_name=BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME,
        )

        result = execute_enabled_tool(self.agent, BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME, {"query": "cats"})

        self.assertEqual(result["status"], "success")
        self.assertEqual(json.loads(result["result"]), {"organic": []})
        manager.execute_mcp_tool.assert_not_called()

    def test_data_migration_reclassifies_existing_rows(self):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="brightdata",
            display_name="Bright Data",
            command="npx",
        )
        row = PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name=BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME,
            tool_server="brightdata",
            tool_name="scrape_as_markdown",
            server_config=server,
        )
        migration = importlib.import_module("api.migrations.0421_migrate_brightdata_base_tools_to_builtin")

        migration.migrate_brightdata_tools_to_builtin(apps, None)

        row.refresh_from_db()
        self.assertEqual(row.tool_server, "builtin")
        self.assertEqual(row.tool_name, BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME)
        self.assertIsNone(row.server_config_id)


class _Controller:
    def action(self, _description):
        def decorator(function):
            self.function = function
            return function

        return decorator


@tag("batch_mcp_tools")
class BrightDataBrowserActionTests(SimpleTestCase):
    @patch("api.agent.browser_actions.web_search.execute_brightdata_search_engine")
    def test_browser_action_uses_native_executor(self, mock_execute):
        mock_execute.return_value = {
            "status": "success",
            "result": json.dumps(
                {
                    "organic": [
                        {
                            "title": "Example",
                            "link": "https://example.com",
                            "description": "Example result",
                        }
                    ]
                }
            ),
        }
        controller = _Controller()
        register_web_search_action(controller)

        result = controller.function("example query")

        mock_execute.assert_called_once_with(None, {"query": "example query"})
        self.assertIn("Example", result.extracted_content)
        self.assertIn("https://example.com", result.extracted_content)
        self.assertTrue(result.include_in_memory)

    @patch("api.agent.browser_actions.web_search.execute_brightdata_search_engine")
    def test_browser_action_formats_native_error(self, mock_execute):
        mock_execute.return_value = {"status": "error", "message": "missing Bright Data configuration"}
        controller = _Controller()
        register_web_search_action(controller)

        result = controller.function("example query")

        self.assertIn("missing Bright Data configuration", result.extracted_content)
        self.assertFalse(result.include_in_memory)
