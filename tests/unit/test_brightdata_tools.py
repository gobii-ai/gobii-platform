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
    BRIGHTDATA_DATASET_PROGRESS_URL,
    BRIGHTDATA_DATASET_SCRAPE_URL,
    BRIGHTDATA_DATASET_SNAPSHOT_URL,
    BRIGHTDATA_LINKEDIN_PERSON_PROFILE_DATASET_ID,
    BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME,
    BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME,
    BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME,
    execute_brightdata_linkedin_person_profile,
    execute_brightdata_scrape_as_markdown,
    execute_brightdata_search_engine,
    get_brightdata_linkedin_person_profile_tool,
    get_brightdata_scrape_as_markdown_tool,
    get_brightdata_search_engine_tool,
)
from api.agent.tools.mcp_manager import MCPToolInfo
from api.agent.tools.tool_manager import (
    ensure_default_tools_enabled,
    execute_enabled_tool,
    get_available_builtin_tool_entries,
    is_parallel_safe_tool_name,
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


def _bing_response(*results: dict):
    body = {
        "_type": "SearchResponse",
        "queryContext": {"originalQuery": "cats"},
        "webPages": {"totalEstimatedMatches": len(results), "value": list(results)},
    }
    return _response(
        json.dumps(
            {
                "status_code": 200,
                "headers": {"content-type": "application/json"},
                "body": json.dumps(body),
            }
        )
    )


@tag("batch_mcp_tools")
@override_settings(
    BRIGHT_DATA_TOKEN="test-token",
    BRIGHT_DATA_SERP_ZONE="test-serp-zone",
    BRIGHT_DATA_WEB_UNLOCKER_ZONE="test-zone",
    BRIGHT_DATA_WEB_UNLOCKER_ZONE_FALLBACK="",
    BRIGHT_DATA_REQUEST_TIMEOUT_SECONDS=42.0,
    BRIGHT_DATA_SEARCH_REQUEST_TIMEOUT_SECONDS=7.0,
    BRIGHT_DATA_DATASET_POLL_TIMEOUT_SECONDS=600.0,
)
class BrightDataNativeToolTests(SimpleTestCase):
    def test_tool_definitions_match_brightdata_mcp_parameters(self):
        search = get_brightdata_search_engine_tool()["function"]
        scrape = get_brightdata_scrape_as_markdown_tool()["function"]
        linkedin = get_brightdata_linkedin_person_profile_tool()["function"]

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
        self.assertEqual(linkedin["name"], BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME)
        self.assertEqual(linkedin["parameters"]["required"], ["url"])
        self.assertEqual(linkedin["parameters"]["properties"]["url"], {"type": "string", "format": "uri"})

    @override_settings(BRIGHT_DATA_WEB_UNLOCKER_ZONE="")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_linkedin_person_profile_uses_sync_dataset_api_and_cleans_result(self, mock_post):
        mock_post.return_value = _response(
            json.dumps(
                [
                    {
                        "name": "Ada Lovelace",
                        "about": None,
                        "description_html": "<p>Profile</p>",
                        "current_company": {
                            "name": "Analytical Engines",
                            "company_logo_url": "https://example.com/logo.png",
                            "tagline_html": "<p>Math</p>",
                        },
                        "positions": [
                            None,
                            {
                                "title": "Programmer",
                                "banner_image": "https://example.com/banner.png",
                                "details": {"summary_img": "image", "location": "London"},
                            },
                        ],
                        "people_also_viewed": [{"name": "Charles Babbage"}],
                    }
                ]
            )
        )

        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/ada-lovelace"},
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            json.loads(result["result"]),
            [
                {
                    "name": "Ada Lovelace",
                    "current_company": {"name": "Analytical Engines"},
                    "positions": [{"title": "Programmer", "details": {"location": "London"}}],
                }
            ],
        )
        call = mock_post.call_args
        self.assertEqual(call.args[0], BRIGHTDATA_DATASET_SCRAPE_URL)
        self.assertEqual(
            call.kwargs["params"],
            {
                "dataset_id": BRIGHTDATA_LINKEDIN_PERSON_PROFILE_DATASET_ID,
                "format": "json",
                "include_errors": True,
            },
        )
        self.assertEqual(
            call.kwargs["json"],
            {"input": [{"url": "https://www.linkedin.com/in/ada-lovelace"}]},
        )
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(call.kwargs["timeout"], 42.0)

    @patch("api.agent.tools.brightdata.requests.post", return_value=_response("[]"))
    def test_linkedin_person_profile_preserves_empty_json_collection(self, _mock_post):
        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/missing"},
        )

        self.assertEqual(result, {"status": "success", "result": "[]"})

    @patch("api.agent.tools.brightdata.requests.post")
    def test_linkedin_person_profile_rejects_invalid_url_before_request(self, mock_post):
        result = execute_brightdata_linkedin_person_profile(None, {"url": "linkedin.com/in/ada"})

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["retryable"])
        mock_post.assert_not_called()

    @patch("api.agent.tools.brightdata.requests.post")
    def test_linkedin_person_profile_rejects_non_linkedin_url_before_request(self, mock_post):
        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://example.com/in/ada"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("valid LinkedIn URL", result["message"])
        self.assertFalse(result["retryable"])
        mock_post.assert_not_called()

    @override_settings(BRIGHT_DATA_TOKEN="", BRIGHT_DATA_WEB_UNLOCKER_ZONE="test-zone")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_linkedin_person_profile_requires_token(self, mock_post):
        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/ada"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("BRIGHT_DATA_TOKEN", result["message"])
        self.assertNotIn("BRIGHT_DATA_WEB_UNLOCKER_ZONE", result["message"])
        mock_post.assert_not_called()

    @patch("api.agent.tools.brightdata._DATASET_POLL_INTERVAL_SECONDS", 0)
    @patch("api.agent.tools.brightdata.requests.get")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_linkedin_person_profile_polls_async_snapshot(self, mock_post, mock_get):
        mock_post.return_value = _response('{"snapshot_id": "s_profile"}', 202)
        mock_get.side_effect = [
            _response('{"status": "running"}'),
            _response('{"status": "ready"}'),
            _response('[{"name": "Grace Hopper", "image_url": "https://example.com/image.png"}]'),
        ]

        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/grace-hopper"},
        )

        self.assertEqual(result, {"status": "success", "result": '[{"name":"Grace Hopper"}]'})
        self.assertEqual(mock_get.call_args_list[0].args[0], f"{BRIGHTDATA_DATASET_PROGRESS_URL}/s_profile")
        self.assertEqual(mock_get.call_args_list[1].args[0], f"{BRIGHTDATA_DATASET_PROGRESS_URL}/s_profile")
        self.assertEqual(mock_get.call_args_list[2].args[0], f"{BRIGHTDATA_DATASET_SNAPSHOT_URL}/s_profile")
        self.assertEqual(mock_get.call_args_list[2].kwargs["params"], {"format": "json"})

    @patch("api.agent.tools.brightdata._DATASET_POLL_INTERVAL_SECONDS", 0)
    @patch("api.agent.tools.brightdata.requests.get")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_linkedin_person_profile_retries_transient_poll_error(self, mock_post, mock_get):
        mock_post.return_value = _response('{"snapshot_id": "s_retry"}', 202)
        mock_get.side_effect = [
            ConnectionError(),
            _response('{"status": "ready"}'),
            _response('[{"name": "Katherine Johnson"}]'),
        ]

        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/katherine-johnson"},
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(mock_get.call_args_list), 3)

    @patch("api.agent.tools.brightdata.requests.get")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_linkedin_person_profile_returns_failed_snapshot(self, mock_post, mock_get):
        mock_post.return_value = _response('{"snapshot_id": "s_failed"}', 202)
        mock_get.return_value = _response('{"status": "failed", "error_message": "Profile unavailable"}')

        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/private"},
        )

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["retryable"])
        self.assertIn("Profile unavailable", result["message"])

    @override_settings(BRIGHT_DATA_DATASET_POLL_TIMEOUT_SECONDS=0)
    @patch("api.agent.tools.brightdata.requests.get", return_value=_response('{"status": "running"}'))
    @patch("api.agent.tools.brightdata.requests.post", return_value=_response('{"snapshot_id": "s_slow"}', 202))
    def test_linkedin_person_profile_snapshot_timeout_is_retryable(self, _mock_post, _mock_get):
        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/slow"},
        )

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["retryable"])
        self.assertIn("timed out", result["message"])

    @patch("api.agent.tools.brightdata.requests.post", return_value=_response('{"message": "queued"}', 202))
    def test_linkedin_person_profile_requires_snapshot_id_for_accepted_request(self, _mock_post):
        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/queued"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("without returning a snapshot ID", result["message"])

    @patch("api.agent.tools.brightdata.requests.post", return_value=_response('{"snapshot_id": ""}'))
    def test_linkedin_person_profile_rejects_invalid_snapshot_id(self, _mock_post):
        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/invalid-snapshot"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("invalid LinkedIn person profile snapshot ID", result["message"])

    @patch("api.agent.tools.brightdata.requests.post", return_value=_response("not json"))
    def test_linkedin_person_profile_rejects_malformed_json(self, _mock_post):
        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/malformed"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("malformed JSON", result["message"])

    @patch("api.agent.tools.brightdata.requests.post", return_value=_response(""))
    def test_linkedin_person_profile_rejects_empty_response(self, _mock_post):
        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/empty"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("empty LinkedIn person profile response", result["message"])

    @patch("api.agent.tools.brightdata.requests.get")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_linkedin_person_profile_rejects_unknown_snapshot_status(self, mock_post, mock_get):
        mock_post.return_value = _response('{"snapshot_id": "s_unknown"}', 202)
        mock_get.return_value = _response('{"status": "paused"}')

        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/unknown"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("unknown dataset progress status", result["message"])

    @patch("api.agent.tools.brightdata.requests.get")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_linkedin_person_profile_rejects_malformed_snapshot(self, mock_post, mock_get):
        mock_post.return_value = _response('{"snapshot_id": "s_malformed"}', 202)
        mock_get.side_effect = [_response('{"status": "ready"}'), _response("not json")]

        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/malformed-snapshot"},
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("malformed JSON", result["message"])

    @patch("api.agent.tools.brightdata.requests.post", return_value=_response("too many jobs", 429))
    def test_linkedin_person_profile_rate_limit_is_retryable(self, _mock_post):
        result = execute_brightdata_linkedin_person_profile(
            None,
            {"url": "https://www.linkedin.com/in/rate-limited"},
        )

        self.assertEqual(result["status_code"], 429)
        self.assertTrue(result["retryable"])

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
        self.assertEqual(call.kwargs["timeout"], 7.0)
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(
            call.kwargs["json"],
            {
                "url": "https://www.google.com/search?q=native%20api&start=20&gl=us&brd_json=1",
                "zone": "test-serp-zone",
                "format": "raw",
                "data_format": "parsed_light",
            },
        )

    @patch("api.agent.tools.brightdata.requests.post")
    def test_bing_returns_unwrapped_json(self, mock_post):
        mock_post.return_value = _bing_response(
            {"name": "Cats", "url": "https://example.com/cats", "snippet": "Cat facts"},
        )

        result = execute_brightdata_search_engine(
            None,
            {"query": "cats", "engine": "bing", "cursor": "3"},
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(json.loads(result["result"])["webPages"]["value"][0]["name"], "Cats")
        self.assertEqual(
            mock_post.call_args.kwargs["json"],
            {
                "url": "https://www.bing.com/search?q=cats&first=31",
                "zone": "test-serp-zone",
                "format": "json",
                "data_format": "parsed_bing_api",
            },
        )
        self.assertEqual(mock_post.call_args.kwargs["timeout"], 7.0)

    @patch("api.agent.tools.brightdata.requests.post", return_value=_response("yandex markdown"))
    def test_yandex_returns_markdown(self, mock_post):
        result = execute_brightdata_search_engine(
            None,
            {"query": "cats", "engine": "yandex", "cursor": "3"},
        )

        self.assertEqual(result, {"status": "success", "result": "yandex markdown"})
        self.assertEqual(
            mock_post.call_args.kwargs["json"]["url"],
            "https://yandex.com/search/?text=cats&p=3",
        )
        self.assertEqual(mock_post.call_args.kwargs["json"]["data_format"], "markdown")

    @override_settings(BRIGHT_DATA_WEB_UNLOCKER_ZONE_FALLBACK="web-fallback-zone")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_google_timeout_uses_bing_json_without_web_unlocker_fallback(self, mock_post):
        mock_post.side_effect = [
            Timeout(),
            _bing_response({"name": "Cats", "url": "https://example.com/cats"}),
        ]

        result = execute_brightdata_search_engine(None, {"query": "cats"})

        self.assertEqual(result["status"], "success")
        self.assertEqual(json.loads(result["result"])["_type"], "SearchResponse")
        self.assertEqual(
            [call.kwargs["json"]["zone"] for call in mock_post.call_args_list],
            ["test-serp-zone", "test-serp-zone"],
        )
        self.assertEqual(mock_post.call_args_list[1].kwargs["json"]["data_format"], "parsed_bing_api")

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
        self.assertEqual(mock_post.call_args.kwargs["timeout"], 42.0)

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

    @override_settings(BRIGHT_DATA_TOKEN="", BRIGHT_DATA_SERP_ZONE="")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_missing_search_configuration_is_non_retryable(self, mock_post):
        result = execute_brightdata_search_engine(None, {"query": "cats"})

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["retryable"])
        self.assertIn("BRIGHT_DATA_TOKEN", result["message"])
        self.assertIn("BRIGHT_DATA_SERP_ZONE", result["message"])
        mock_post.assert_not_called()

    @override_settings(BRIGHT_DATA_WEB_UNLOCKER_ZONE="")
    @patch("api.agent.tools.brightdata.requests.post")
    def test_missing_scrape_configuration_is_non_retryable(self, mock_post):
        result = execute_brightdata_scrape_as_markdown(None, {"url": "https://example.com"})

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["retryable"])
        self.assertIn("BRIGHT_DATA_WEB_UNLOCKER_ZONE", result["message"])
        self.assertNotIn("BRIGHT_DATA_SERP_ZONE", result["message"])
        mock_post.assert_not_called()

    def test_rejects_invalid_cursor_and_geo_location(self):
        cursor_result = execute_brightdata_search_engine(None, {"query": "cats", "cursor": "next"})
        negative_cursor_result = execute_brightdata_search_engine(None, {"query": "cats", "cursor": "-1"})
        geo_result = execute_brightdata_search_engine(None, {"query": "cats", "geo_location": "usa"})

        self.assertEqual(cursor_result["status"], "error")
        self.assertEqual(negative_cursor_result["status"], "error")
        self.assertIn("non-negative", negative_cursor_result["message"])
        self.assertEqual(geo_result["status"], "error")


@tag("batch_mcp_tools")
@override_settings(
    BRIGHT_DATA_TOKEN="test-token",
    BRIGHT_DATA_SERP_ZONE="test-serp-zone",
    BRIGHT_DATA_WEB_UNLOCKER_ZONE="test-zone",
    BRIGHT_DATA_WEB_UNLOCKER_ZONE_FALLBACK="",
    BRIGHT_DATA_REQUEST_TIMEOUT_SECONDS=42.0,
    BRIGHT_DATA_SEARCH_REQUEST_TIMEOUT_SECONDS=7.0,
    BRIGHT_DATA_DATASET_POLL_TIMEOUT_SECONDS=600.0,
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
        self.assertEqual(entries[BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME].provider, "builtin")

    def test_only_native_brightdata_tools_are_parallel_safe(self):
        self.assertTrue(is_parallel_safe_tool_name(BRIGHTDATA_SEARCH_ENGINE_TOOL_NAME))
        self.assertTrue(is_parallel_safe_tool_name(BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME))
        self.assertTrue(is_parallel_safe_tool_name(BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME))
        self.assertFalse(is_parallel_safe_tool_name("mcp_brightdata_search_engine_batch"))

    @patch("api.agent.tools.tool_manager._get_manager")
    def test_native_tools_are_default_enabled_without_mcp_discovery(self, mock_get_manager):
        ensure_default_tools_enabled(self.agent)

        mock_get_manager.assert_not_called()
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
        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent,
                tool_full_name=BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME,
            ).exists()
        )

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

    @patch("api.agent.tools.brightdata.requests.post")
    @patch("api.agent.tools.tool_manager._get_manager")
    def test_execute_enabled_linkedin_profile_routes_duplicate_name_to_native(self, mock_get_manager, mock_post):
        manager = MagicMock()
        manager.get_tools_for_agent.return_value = [
            MCPToolInfo(
                config_id="11111111-1111-1111-1111-111111111112",
                full_name=BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME,
                server_name="brightdata",
                tool_name="web_data_linkedin_person_profile",
                description="Legacy MCP LinkedIn profile",
                parameters={},
            )
        ]
        mock_get_manager.return_value = manager
        mock_post.return_value = _response('[{"name": "Ada Lovelace"}]')
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name=BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME,
            tool_server="builtin",
            tool_name=BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME,
        )

        result = execute_enabled_tool(
            self.agent,
            BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME,
            {"url": "https://www.linkedin.com/in/ada-lovelace"},
        )

        self.assertEqual(result, {"status": "success", "result": '[{"name":"Ada Lovelace"}]'})
        manager.execute_mcp_tool.assert_not_called()

    def test_data_migration_reclassifies_existing_rows(self):
        server = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="brightdata",
            display_name="Bright Data",
            command="npx",
        )
        rows = [
            PersistentAgentEnabledTool.objects.create(
                agent=self.agent,
                tool_full_name=tool_name,
                tool_server="brightdata",
                tool_name=legacy_tool_name,
                server_config=server,
            )
            for tool_name, legacy_tool_name in [
                (BRIGHTDATA_SCRAPE_AS_MARKDOWN_TOOL_NAME, "scrape_as_markdown"),
                (BRIGHTDATA_LINKEDIN_PERSON_PROFILE_TOOL_NAME, "web_data_linkedin_person_profile"),
            ]
        ]
        migration = importlib.import_module("api.migrations.0421_migrate_brightdata_base_tools_to_builtin")

        migration.migrate_brightdata_tools_to_builtin(apps, None)

        for row in rows:
            row.refresh_from_db()
            self.assertEqual(row.tool_server, "builtin")
            self.assertEqual(row.tool_name, row.tool_full_name)
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
