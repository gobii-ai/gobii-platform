import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

from api.agent.tools.mcp_manager import MCPServerRuntime, MCPToolInfo, MCPToolManager
from api.agent.tools.mcp_result_adapters import (
    BrightDataSearchEngineAdapter,
    BrightDataSearchEngineBatchAdapter,
    BrightDataLinkedInPersonProfileAdapter,
    BrightDataScrapeAsMarkdownAdapter,
    BrightDataScrapeBatchAdapter,
    _strip_key_name,
    _parse_markdown_serp,
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
class BrightDataSearchEngineAdapterTests(SimpleTestCase):
    def test_transforms_organic_to_skeleton_format(self):
        """Organic results are transformed to skeleton format (images implicitly stripped)."""
        payload = {
            "organic": [
                {"title": "Example", "link": "https://example.com", "image": "http://example.com/a.png"},
                {"title": "Example 2", "link": "https://example2.com", "image": "http://example.com/b.png"},
            ]
        }
        adapter = BrightDataSearchEngineAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        cleaned = json.loads(adapted.content[0].text)

        # Output is skeleton format
        self.assertEqual(cleaned["kind"], "serp")
        self.assertIn("items", cleaned)
        # Items have t/u/p fields, no images
        self.assertEqual(cleaned["items"][0]["t"], "Example")
        self.assertEqual(cleaned["items"][0]["u"], "https://example.com")
        self.assertEqual(cleaned["items"][0]["p"], 1)
        self.assertNotIn("image", cleaned["items"][0])

    def test_skeleton_includes_position(self):
        """Skeleton items include position from source or auto-generated."""
        payload = {
            "organic": [
                {"title": "First", "link": "https://first.com", "position": 1},
                {"title": "Second", "link": "https://second.com"},  # No position, auto-generated
            ]
        }
        adapter = BrightDataSearchEngineAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        cleaned = json.loads(adapted.content[0].text)

        self.assertEqual(cleaned["items"][0]["p"], 1)
        self.assertEqual(cleaned["items"][1]["p"], 2)  # Auto-generated

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
        adapter = BrightDataSearchEngineBatchAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        cleaned = json.loads(adapted.content[0].text)

        organic = cleaned[0]["result"]["organic"]
        self.assertNotIn("image", organic[0])
        self.assertNotIn("image_base64", organic[0])
        self.assertNotIn("image", organic[1])
        self.assertEqual(organic[0]["title"], "One")

    def test_batch_adapter_strips_related_images(self):
        payload = [
            {
                "result": {
                    "related": [
                        {"title": "Rel One", "image": "http://example.com/r1.png", "image_base64": "abc"},
                        {"title": "Rel Two", "image": "http://example.com/r2.png"},
                    ]
                }
            }
        ]
        adapter = BrightDataSearchEngineBatchAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        cleaned = json.loads(adapted.content[0].text)

        related = cleaned[0]["result"]["related"]
        self.assertNotIn("image", related[0])
        self.assertNotIn("image_base64", related[0])
        self.assertNotIn("image", related[1])
        self.assertEqual(related[0]["title"], "Rel One")


@tag("batch_mcp_tools")
class StripKeyNameTests(SimpleTestCase):
    def test_strips_key_from_nested_structures(self):
        payload = {
            "remove_me": "top",
            "keep": {"nested": True, "remove_me": "nested", "list": [{"remove_me": "deep", "keep": 1}, {"keep": 2}]},
            "items": [
                {"keep": "a", "remove_me": "b"},
                ["no-op", {"remove_me": "c"}],
                ({"remove_me": "tuple"},),
            ],
        }

        _strip_key_name(payload, "remove_me")

        self.assertNotIn("remove_me", payload)
        self.assertNotIn("remove_me", payload["keep"])
        self.assertNotIn("remove_me", payload["keep"]["list"][0])
        self.assertNotIn("remove_me", payload["items"][0])
        self.assertNotIn("remove_me", payload["items"][1][1])
        self.assertNotIn("remove_me", payload["items"][2][0])
        self.assertEqual(payload["keep"]["nested"], True)
        self.assertEqual(payload["keep"]["list"][1]["keep"], 2)


@tag("batch_mcp_tools")
class BrightDataLinkedInPersonProfileAdapterTests(SimpleTestCase):
    def test_strips_html_and_image_fields_recursively(self):
        payload = {
            "headline": "Senior Engineer",
            "description_html": "<p>about</p>",
            "company": {"name": "Acme", "company_logo_url": "http://logo", "tagline_html": "<p>tag</p>"},
            "positions": [
                {"title": "X", "banner_image": "http://banner", "details": {"summary_html": "<p>sum</p>", "note_img": "img"}},
                {"title": "Y", "institute_logo_url": "http://inst", "extras_html": "<span>x</span>"},
            ],
            "default_avatar": "http://avatar",
            "misc_img": "http://misc",
            "notes": [{"content_html": "<p>c</p>", "text": "plain"}],
            "image": "http://example.com/image.png",
            "image_url": "http://example.com/image2.png",
            "people_also_viewed": [
                {"name": "Alice", "image_url": "http://example.com/alice.png"},
                {"name": "Bob", "image": "http://example.com/bob.png"},
            ],
        }
        adapter = BrightDataLinkedInPersonProfileAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        cleaned = json.loads(adapted.content[0].text)

        self.assertEqual(cleaned["headline"], "Senior Engineer")
        self.assertNotIn("description_html", cleaned)
        self.assertEqual(cleaned["company"]["name"], "Acme")
        self.assertNotIn("company_logo_url", cleaned["company"])
        self.assertNotIn("tagline_html", cleaned["company"])
        self.assertNotIn("banner_image", cleaned["positions"][0])
        self.assertNotIn("summary_html", cleaned["positions"][0]["details"])
        self.assertNotIn("note_img", cleaned["positions"][0]["details"])
        self.assertNotIn("institute_logo_url", cleaned["positions"][1])
        self.assertNotIn("extras_html", cleaned["positions"][1])
        self.assertNotIn("default_avatar", cleaned)
        self.assertNotIn("misc_img", cleaned)
        self.assertNotIn("content_html", cleaned["notes"][0])
        self.assertEqual(cleaned["notes"][0]["text"], "plain")
        self.assertNotIn("image", cleaned)
        self.assertNotIn("image_url", cleaned)
        self.assertNotIn("people_also_viewed", cleaned)


@tag("batch_mcp_tools")
class BrightDataScrapeAsMarkdownAdapterTests(SimpleTestCase):
    def test_scrubs_data_image_markdown(self):
        payload = (
            "Intro ![logo](data:image/png;base64,AAA) "
            "more ![icon](data:image/svg+xml;base64,BBB) "
            "keep ![ok](https://example.com/a.png)"
        )
        adapter = BrightDataScrapeAsMarkdownAdapter()
        result = DummyResult(payload)

        adapted = adapter.adapt(result)

        self.assertEqual(
            adapted.content[0].text,
            "Intro ![logo]() more ![icon]() keep ![ok](https://example.com/a.png)",
        )


@tag("batch_mcp_tools")
class BrightDataScrapeBatchAdapterTests(SimpleTestCase):
    def test_scrubs_data_images_inside_batch_payload(self):
        payload = [
            {"url": "https://example.com", "content": "![hero](data:image/jpeg;base64,CCC) text"},
            {"url": "https://example.com/2", "content": "No images here"},
            {"url": "https://example.com/3", "content": None},
        ]
        adapter = BrightDataScrapeBatchAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        cleaned = json.loads(adapted.content[0].text)

        self.assertEqual(cleaned[0]["content"], "![hero]() text")
        self.assertEqual(cleaned[1]["content"], "No images here")
        self.assertIsNone(cleaned[2]["content"])


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
        payload = {"organic": [{"title": "Example", "link": "https://example.com", "image": "http://example.com/a.png"}]}
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
        # Output is skeleton format
        self.assertEqual(cleaned["kind"], "serp")
        self.assertEqual(cleaned["items"][0]["t"], "Example")
        self.assertNotIn("image", cleaned["items"][0])

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

    def test_execute_mcp_tool_truncates_brightdata_amazon_product_search(self):
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
        payload = [{"id": 1}, {"id": 2}, {"id": 3}]
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
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(cleaned[0]["id"], 1)

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
        params = {"queries": ["first", "second", "third"]}

        with patch.object(manager, "_ensure_event_loop", return_value=MagicMock()), \
             patch.object(manager, "_execute_async", new_callable=MagicMock) as mock_exec, \
             patch.object(manager, "_select_agent_proxy_url", return_value=(None, None)):
            response = manager.execute_mcp_tool(
                self.agent,
                tool_info.full_name,
                params,
            )

        self.assertEqual(response.get("status"), "error")
        self.assertIn("Maximum number of queries", response.get("message", ""))
        mock_exec.assert_not_called()

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


@tag("batch_mcp_tools")
class MarkdownSerpParserTests(SimpleTestCase):
    """Tests for _parse_markdown_serp function."""

    def test_extracts_external_links(self):
        markdown = """
        # Search Results
        [Example Article](https://example.com/article-1)
        [Tech Blog Post](https://techblog.io/post/123)
        """
        results = _parse_markdown_serp(markdown)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Example Article")
        self.assertEqual(results[0]["link"], "https://example.com/article-1")
        self.assertEqual(results[0]["position"], 1)
        self.assertEqual(results[1]["position"], 2)

    def test_filters_google_internal_urls(self):
        markdown = """
        [Google Home](https://www.google.com/webhp)
        [Maps](https://maps.google.com/something)
        [Static](https://gstatic.com/images/logo.png)
        [Real Result](https://example.com/real)
        """
        results = _parse_markdown_serp(markdown)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Real Result")

    def test_filters_relative_urls(self):
        markdown = """
        [Home](/)
        [About](/about)
        [External](https://example.com/page)
        """
        results = _parse_markdown_serp(markdown)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["link"], "https://example.com/page")

    def test_short_titles_get_url_fallback(self):
        """Short/useless titles get replaced with URL-derived titles."""
        markdown = """
        [AB](https://example.com/short-page)
        [Real Title Here](https://example.com/real)
        """
        results = _parse_markdown_serp(markdown)
        # Both links included - short title gets URL fallback
        self.assertEqual(len(results), 2)
        self.assertIn("example.com", results[0]["title"])
        self.assertEqual(results[1]["title"], "Real Title Here")

    def test_deduplicates_urls(self):
        markdown = """
        [First Title](https://example.com/page)
        [Second Title](https://example.com/page)
        [Third Title](https://example.com/other)
        """
        results = _parse_markdown_serp(markdown)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "First Title")
        self.assertEqual(results[1]["title"], "Third Title")

    def test_limits_to_12_results(self):
        """ContentSkeleton limits SERP to 12 results for context efficiency."""
        links = "\n".join(
            f"[Result {i}](https://example{i}.com/page)" for i in range(20)
        )
        results = _parse_markdown_serp(links)
        self.assertEqual(len(results), 12)

    def test_truncates_long_titles(self):
        """ContentSkeleton truncates titles to 100 chars for context efficiency."""
        long_title = "A" * 300
        markdown = f"[{long_title}](https://example.com/page)"
        results = _parse_markdown_serp(markdown)
        self.assertEqual(len(results[0]["title"]), 100)

    def test_truncates_long_urls(self):
        """ContentSkeleton truncates URLs to 300 chars for context efficiency."""
        long_url = "https://example.com/" + "a" * 600
        markdown = f"[Title]({long_url})"
        results = _parse_markdown_serp(markdown)
        self.assertEqual(len(results[0]["link"]), 300)

    def test_empty_markdown_returns_empty_list(self):
        results = _parse_markdown_serp("")
        self.assertEqual(results, [])

    def test_no_links_returns_empty_list(self):
        markdown = "Just some text without any links."
        results = _parse_markdown_serp(markdown)
        self.assertEqual(results, [])


@tag("batch_mcp_tools")
class BrightDataSearchEngineMarkdownSerpTests(SimpleTestCase):
    """Tests for BrightDataSearchEngineAdapter skeleton transformation."""

    def test_transforms_markdown_serp_to_skeleton(self):
        """Markdown SERP should be transformed to universal skeleton format."""
        filler = "x" * 400
        payload = {
            "status": "success",
            "result": f"""
            Google Search

            Skip to main content
            {filler}

            [Article One](https://news.example.com/article-1)
            [Article Two](https://blog.example.com/post-2)
            """
        }
        adapter = BrightDataSearchEngineAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        output = json.loads(adapted.content[0].text)

        # Should use universal skeleton format
        self.assertEqual(output["kind"], "serp")
        self.assertIn("items", output)
        self.assertEqual(len(output["items"]), 2)
        # Items use compact keys: t=title, u=url, p=position
        self.assertEqual(output["items"][0]["t"], "Article One")
        self.assertEqual(output["items"][0]["u"], "https://news.example.com/article-1")
        self.assertEqual(output["items"][0]["p"], 1)

    def test_converts_existing_organic_to_skeleton(self):
        """Existing organic array should be converted to skeleton format."""
        payload = {
            "organic": [
                {"title": "Existing Result", "link": "https://example.com/existing", "position": 1}
            ]
        }
        adapter = BrightDataSearchEngineAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        output = json.loads(adapted.content[0].text)

        # Should convert to skeleton format
        self.assertEqual(output["kind"], "serp")
        self.assertEqual(len(output["items"]), 1)
        self.assertEqual(output["items"][0]["t"], "Existing Result")
        self.assertEqual(output["items"][0]["u"], "https://example.com/existing")

    def test_includes_compression_meta_for_large_results(self):
        """Large results should include compression metadata."""
        large_content = (
            "Google Search\n\nSkip to main content\n\n"
            "[Real Article](https://example.com/article)\n\n"
            + "x" * 8000
        )
        payload = {
            "status": "success",
            "result": large_content
        }
        adapter = BrightDataSearchEngineAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        output = json.loads(adapted.content[0].text)

        # Should have skeleton with compression stats
        self.assertEqual(output["kind"], "serp")
        self.assertIn("items", output)
        self.assertIn("_meta", output)
        self.assertIn("ratio", output["_meta"])
        # Raw markdown should NOT be included (skeleton replaces it)
        self.assertNotIn("result", output)

    def test_skips_parsing_when_no_serp_indicators(self):
        """When result doesn't look like SERP, pass through unchanged."""
        payload = {
            "status": "success",
            "result": "Just some random markdown content without SERP indicators."
        }
        adapter = BrightDataSearchEngineAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        output = json.loads(adapted.content[0].text)

        # Should pass through unchanged
        self.assertNotIn("kind", output)
        self.assertNotIn("items", output)
        self.assertEqual(output["result"], payload["result"])

    def test_handles_empty_parsed_results_gracefully(self):
        """When SERP has indicators but no extractable links, pass through."""
        payload = {
            "status": "success",
            "result": """
            Google Search
            Skip to main content
            No actual links here, just text.
            """
        }
        adapter = BrightDataSearchEngineAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        output = json.loads(adapted.content[0].text)

        # Should pass through without skeleton (no items to extract)
        self.assertNotIn("kind", output)
        self.assertNotIn("items", output)


@tag("batch_mcp_tools")
class BrightDataScrapeAsMarkdownCleanupTests(SimpleTestCase):
    """Tests for BrightDataScrapeAsMarkdownAdapter skeleton extraction."""

    def test_extracts_article_structure(self):
        """Article markdown should be extracted to structured items."""
        markdown = """
# Main Article Title

This is the introduction paragraph.

## First Section

Content of the first section.

## Second Section

Content of the second section.
"""
        payload = {"status": "success", "result": markdown, "url": "https://example.com/article"}
        adapter = BrightDataScrapeAsMarkdownAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        output = json.loads(adapted.content[0].text)

        # Should have skeleton structure
        self.assertEqual(output["kind"], "article")
        self.assertIn("items", output)
        self.assertGreater(len(output["items"]), 0)
        # First item should be the title heading
        self.assertEqual(output["items"][0]["h"], "Main Article Title")

    def test_strips_navigation_noise(self):
        """Navigation patterns should be stripped before extraction."""
        markdown = """
Skip to main content
Menu
Home
About
Contact

# Real Content Here

This is the actual page content.
"""
        payload = {"status": "success", "result": markdown}
        adapter = BrightDataScrapeAsMarkdownAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        output = json.loads(adapted.content[0].text)

        # Navigation should be gone, content preserved
        self.assertEqual(output["title"], "Real Content Here")

    def test_shows_compression_stats(self):
        """Large content should show compression stats in _meta."""
        markdown = "# Big Page\n\n" + "Lorem ipsum dolor sit amet. " * 500
        payload = {"status": "success", "result": markdown}
        adapter = BrightDataScrapeAsMarkdownAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        output = json.loads(adapted.content[0].text)

        # Should have compression metadata
        self.assertIn("_meta", output)
        self.assertIn("original_bytes", output["_meta"])
        self.assertIn("ratio", output["_meta"])

    def test_preserves_url_and_status(self):
        """URL and status should be preserved in output."""
        markdown = "# Test\n\nContent here."
        payload = {
            "status": "success",
            "result": markdown,
            "url": "https://example.com/page"
        }
        adapter = BrightDataScrapeAsMarkdownAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        output = json.loads(adapted.content[0].text)

        self.assertEqual(output["status"], "success")
        self.assertEqual(output["url"], "https://example.com/page")

    def test_handles_raw_text_fallback(self):
        """When result isn't JSON wrapped, should still scrub data images."""
        raw_markdown = "# Page\n\n![img](data:image/png;base64,abc123) content"
        adapter = BrightDataScrapeAsMarkdownAdapter()
        result = DummyResult(raw_markdown)

        adapted = adapter.adapt(result)

        # Should have scrubbed the data image
        self.assertNotIn("data:image", adapted.content[0].text)
        self.assertIn("# Page", adapted.content[0].text)

    def test_raw_fallback_for_unstructured_content(self):
        """Content without headings should get raw excerpt."""
        markdown = "Just some plain text without any structure at all. " * 10
        payload = {"status": "success", "result": markdown}
        adapter = BrightDataScrapeAsMarkdownAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        output = json.loads(adapted.content[0].text)

        # Should have raw kind and excerpt
        self.assertEqual(output["kind"], "raw")
        self.assertIn("excerpt", output)
        self.assertIn("plain text", output["excerpt"])

    def test_strips_cookie_banners(self):
        """Cookie consent patterns should be stripped."""
        markdown = """
We use cookies to improve your experience. Accept cookies preferences.

# Actual Content

The real page content here.
"""
        payload = {"status": "success", "result": markdown}
        adapter = BrightDataScrapeAsMarkdownAdapter()
        result = DummyResult(json.dumps(payload))

        adapted = adapter.adapt(result)
        output = json.loads(adapted.content[0].text)

        self.assertEqual(output["title"], "Actual Content")
        # Cookie banner should not appear in excerpt
        if "excerpt" in output:
            self.assertNotIn("cookies", output["excerpt"].lower())
